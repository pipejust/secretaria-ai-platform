"""Durable inbox for the owned meeting bot. Never calls Fireflies."""

from __future__ import annotations

import asyncio
import hashlib
import logging
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
    session = MeetingSession(
        tenant_id=event.tenant_id,
        fireflies_id=f"BOT-{event.meeting_id}",
        title=event.data.title,
        date=event.data.date.isoformat(),
        raw_transcript=event.data.transcript_text(),
        raw_summary=event.data.summary_text(),
        status="processing",
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
            row.state = "completed" if success else "failed"
            row.error_code = "" if success else "acten_pipeline_incomplete"
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
