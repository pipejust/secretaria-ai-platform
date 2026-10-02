"""Durable inbox for the owned meeting bot. Never calls Fireflies."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from typing import Optional

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Field, Session, SQLModel, UniqueConstraint, select

from models import MeetingSession
from services.bot_contract import ActenBotEvent

logger = logging.getLogger(__name__)


class BotInbox(SQLModel, table=True):
    __tablename__ = "botinbox"
    __table_args__ = (
        UniqueConstraint("tenant_id", "event_id", name="uq_botinbox_tenant_event"),
        UniqueConstraint("tenant_id", "meeting_id", name="uq_botinbox_tenant_meeting"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    session_id: int = Field(
        foreign_key="meetingsession.id", ondelete="CASCADE", index=True
    )
    event_id: str = Field(index=True)
    meeting_id: str = Field(index=True)
    fingerprint: str
    payload: str
    state: str = Field(default="queued", index=True)
    attempts: int = Field(default=0)
    started_at: float = Field(default=0)
    created_at: float = Field(default_factory=time.time)
    error_code: str = Field(default="")


def receipt(row: BotInbox) -> dict:
    return {
        "event_id": row.event_id,
        "meeting_id": row.meeting_id,
        "tenant_id": row.tenant_id,
        "session_id": row.session_id,
        "state": row.state,
    }


def ingest(db: Session, event: ActenBotEvent) -> BotInbox:
    payload = event.model_dump_json()
    fingerprint = hashlib.sha256(payload.encode()).hexdigest()

    def existing():
        row = db.exec(
            select(BotInbox).where(
                BotInbox.tenant_id == event.tenant_id,
                BotInbox.meeting_id == str(event.meeting_id),
            )
        ).first()
        if row is not None and row.fingerprint != fingerprint:
            # Optional v1 fields may have been added since the stored delivery.
            # Normalize both contracts, preserving the immutable original payload.
            if (
                ActenBotEvent.model_validate_json(row.payload).model_dump_json()
                != payload
            ):
                raise ValueError("bot_event_conflict")
        return row

    row = existing()
    if row is not None:
        return row
    # Proyecto elegido al pedir la captura (pantalla o API); sin él lo deduce el pipeline.
    from routers.bot_control import BotControlLink

    project_id = db.exec(
        select(BotControlLink.project_id).where(
            BotControlLink.tenant_id == event.tenant_id,
            BotControlLink.meeting_id == str(event.meeting_id),
        )
    ).first()
    session = MeetingSession(
        tenant_id=event.tenant_id,
        fireflies_id=f"BOT-{event.meeting_id}",
        title=event.data.title,
        date=event.data.date.isoformat(),
        raw_transcript=event.data.transcript_text(),
        raw_summary=event.data.summary_text(),
        status="processing",
        project_id=project_id,
    )
    try:
        db.add(session)
        db.flush()
        row = BotInbox(
            tenant_id=event.tenant_id,
            session_id=session.id,
            event_id=event.id,
            meeting_id=str(event.meeting_id),
            fingerprint=fingerprint,
            payload=payload,
        )
        db.add(row)
        # Session and inbox must commit together before acknowledging receipt.
        db.commit()
        db.refresh(row)
        return row
    except IntegrityError:
        db.rollback()
        row = existing()
        if row is None:
            raise
        return row


# ponytail: grabadores automáticos reconocidos por nombre; si el proveedor
# llega a marcar los bots en `participants`, usar esa marca en su lugar.
_GRABADORES = re.compile(
    r"notetaker|note taker|fireflies|otter\.ai|read\.ai|tl;dv|fathom|meetgeek|\bbot\b", re.I
)


def asistentes_de(db: Session, session_id: int) -> list[str]:
    """Personas que el bot vio en la sala de esta sesión, sin los grabadores.

    La sala sabe quién estuvo aunque la transcripción no separe voces. Lista
    vacía si la sesión no vino del bot o el evento no trae participantes.
    """
    from routers.bot_control import bot_name_of

    row = db.exec(select(BotInbox).where(BotInbox.session_id == session_id)).first()
    if row is None:
        return []
    try:
        participants = ActenBotEvent.model_validate_json(row.payload).data.participants
    except ValueError:
        return []
    propio = bot_name_of(db, row.tenant_id).casefold()
    nombres: list[str] = []
    vistos: set[str] = set()
    for item in participants:
        crudo = item.get("name") if isinstance(item, dict) else item
        nombre = " ".join(crudo.split()) if isinstance(crudo, str) else ""
        clave = nombre.casefold()
        if not nombre or clave == propio or clave in vistos or _GRABADORES.search(nombre):
            continue
        vistos.add(clave)
        nombres.append(nombre)
    return nombres


async def process_one(engine, inbox_id: int, pipeline=None) -> bool:
    if pipeline is None:
        from services.transcript_pipeline import process_session_with_ai

        pipeline = process_session_with_ai
    with Session(engine) as db:
        result = db.execute(
            update(BotInbox)
            .where(
                BotInbox.id == inbox_id,
                BotInbox.state == "queued",
            )
            .values(
                state="processing",
                started_at=time.time(),
                attempts=BotInbox.attempts + 1,
            )
        )
        db.commit()
        if result.rowcount != 1:
            return False
        row = db.get(BotInbox, inbox_id)
        meeting = db.get(MeetingSession, row.session_id)
        if not meeting or meeting.tenant_id != row.tenant_id:
            row.state, row.error_code = "failed", "session_missing_or_mismatch"
            db.add(row)
            db.commit()
            return True
        try:
            await pipeline(db, row.session_id)
            db.refresh(meeting)
            success = (
                bool(meeting.processing_completed_at) and not meeting.processing_error
            )
            # El vídeo no se copia aquí: descargarlo y comprimirlo tarda
            # minutos y no debe retrasar esta acta ni las de la cola. Queda
            # anotado y `copy_pending_videos` lo recoge en su propio cron.
            row.state = "completed" if success else "failed"
            if not success:
                row.error_code = "acten_pipeline_incomplete"
            else:
                row.error_code = VIDEO_PENDING if _video_por_copiar(db, row, meeting) else ""
        except Exception:
            db.rollback()
            row = db.get(BotInbox, inbox_id)
            meeting = db.get(MeetingSession, row.session_id)
            row.state, row.error_code = "failed", "acten_pipeline_error"
            if meeting:
                meeting.processing_error = "bot_ingest: acten_pipeline_error"
                meeting.status = "pending"
                db.add(meeting)
            logger.error("Bot pipeline failed for inbox %s", inbox_id)
        db.add(row)
        db.commit()
        return True


VIDEO_PENDING = "video_pending"


def _video_por_copiar(db: Session, row: BotInbox, meeting: MeetingSession) -> str:
    """URL de la grabación de vídeo que toca copiar a nuestro bucket, o ''.

    Solo si el evento trae `recording.kind == "video"`, la empresa tiene la
    función `meetings.video` y el superadministrador configuró el bucket.
    """
    from services import billing_catalog, entitlements, media_storage

    if meeting.recording_video_key:
        return ""
    try:
        recording = ActenBotEvent.model_validate_json(row.payload).data.recording
    except ValueError:
        return ""
    if recording.kind != "video" or not recording.url:
        return ""
    if not entitlements.tiene(db, row.tenant_id, billing_catalog.F_VIDEO):
        logger.info(
            "Sesión %s: el bot trajo vídeo pero la empresa %s no tiene la función; no se copia.",
            meeting.id, row.tenant_id,
        )
        return ""
    if not media_storage.configurado(db):
        logger.warning(
            "Sesión %s: el bot trajo vídeo pero el almacenamiento (object_storage) "
            "no está configurado; la grabación se queda solo en el proveedor.",
            meeting.id,
        )
        return ""
    return recording.url


def copy_pending_videos(engine=None) -> None:
    """Cron: copia a nuestro bucket, comprimido, el vídeo de UNA reunión pendiente.

    De uno en uno porque ffmpeg ocupa la CPU del servidor. Si el proceso muere
    a medias (un despliegue) la fila sigue en `video_pending` y se reintenta
    sola: subir dos veces la misma clave no hace daño. Un fallo de verdad deja
    `video_copy_failed` y no se reintenta; el acta vale igual sin vídeo. La
    URL de Skribby caduca (`recording.expires_at`), de ahí copiarlo.
    """
    from services import media_storage

    if engine is None:
        from database import engine
    with Session(engine) as db:
        row = db.exec(
            select(BotInbox)
            .where(BotInbox.state == "completed", BotInbox.error_code == VIDEO_PENDING)
            .order_by(BotInbox.created_at)
        ).first()
        if row is None:
            return
        meeting = db.get(MeetingSession, row.session_id)
        url = _video_por_copiar(db, row, meeting) if meeting else ""
        inbox_id, tenant_id, session_id = row.id, row.tenant_id, row.session_id
        cfg = media_storage.leer_config(db)
    # Sin sesión de base abierta: lo que sigue tarda minutos.
    key, error = "", ""
    if url:
        try:
            subida = media_storage.subir_video(cfg, url, tenant_id, session_id)
            key = subida["key"]
            logger.info(
                "Sesión %s: vídeo copiado a %s (%s bytes; el original pesaba %s)",
                session_id, key, subida["bytes"], subida["original_bytes"],
            )
        except Exception as exc:  # noqa: BLE001 — cualquier fallo sin marcar se reintentaría cada minuto
            logger.error("Sesión %s: no se pudo copiar el vídeo al bucket: %s", session_id, exc)
            error = "video_copy_failed"
    with Session(engine) as db:
        row = db.get(BotInbox, inbox_id)
        meeting = db.get(MeetingSession, session_id)
        if row is None or meeting is None:
            # Borraron la sesión mientras se copiaba: no dejar el vídeo huérfano.
            if key:
                media_storage.borrar(db, key)
            return
        row.error_code = error
        if key:
            meeting.recording_video_key = key
            db.add(meeting)
        db.add(row)
        db.commit()


# Cuánto puede llevar «processing» antes de darlo por interrumpido. El
# pipeline de una reunión larga tarda minutos, no horas: si lleva media
# hora ahí, el proceso que lo tomó murió (un despliegue, un OOM).
PROCESSING_STALE_SECONDS = 30 * 60


def marcar_interrumpidos(db: Session) -> int:
    """Los trabajos que se quedaron en `processing` por una caída pasan a `failed`.

    Antes no había ningún camino de vuelta: el cron solo tomaba `queued` y
    `/retry` solo aceptaba `failed`, así que una reunión cuyo procesamiento
    coincidió con un despliegue quedaba huérfana para siempre, sin aviso.

    Se marca `failed` y NO se vuelve a encolar sola: el pipeline hace
    escrituras y efectos externos entre pasos, y repetirlo a ciegas podría
    duplicar tareas o correos. Queda visible, con motivo, y `/retry` lo
    reactiva cuando alguien lo ha mirado.
    """
    limite = time.time() - PROCESSING_STALE_SECONDS
    result = db.execute(
        update(BotInbox)
        .where(BotInbox.state == "processing", BotInbox.started_at < limite)
        .values(state="failed", error_code="processing_interrupted")
    )
    db.commit()
    if result.rowcount:
        logger.warning(
            "%s trabajo(s) del bot llevaban más de %s min en processing: "
            "marcados como interrumpidos, reintentables con /retry.",
            result.rowcount, PROCESSING_STALE_SECONDS // 60,
        )
    return result.rowcount or 0


def process_bot_inbox() -> None:
    """Cron entry point; work is durable even when the API process restarts.

    A crashed processing job is deliberately NOT stolen: the Acten pipeline
    performs commits and external effects. Inspect it before recovering it.
    """
    from database import engine

    with Session(engine) as db:
        marcar_interrumpidos(db)
        ids = list(
            db.exec(
                select(BotInbox.id)
                .where(
                    BotInbox.state == "queued",
                )
                .order_by(BotInbox.created_at)
                .limit(20)
            ).all()
        )

    async def run():
        semaphore = asyncio.Semaphore(4)

        async def limited(inbox_id):
            async with semaphore:
                try:
                    await process_one(engine, inbox_id)
                except Exception:
                    logger.error("Bot inbox scheduler failed for job %s", inbox_id)

        await asyncio.gather(*(limited(inbox_id) for inbox_id in ids))

    asyncio.run(run())
