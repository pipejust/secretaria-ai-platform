"""Direct ingestion from the owned bot, alongside the existing Fireflies input."""

import time
import zlib

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import update
from sqlmodel import Session, select

from routers.bot_control import apply_labels
from database import get_session
from models import MeetingSession
from services.api_key_auth import IntegrationContext, require_scopes
from services.bot_contract import ActenBotEvent, verify_signature
from services.bot_ingest import BotInbox, ingest, receipt

router = APIRouter(prefix="/api/v1", tags=["Bot propio"])
MAX_BODY = 16 * 1024 * 1024
MAX_EXPANDED_BODY = 64 * 1024 * 1024
MAX_ATTEMPTS = 5


@router.post("/bot/meetings", status_code=202)
async def receive(
    request: Request,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("sessions:write")),
):
    # Authenticate the tenant via Acten's existing API key, never via the body.
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY:
            raise HTTPException(413, "El evento excede 16 MiB.")
        chunks.append(chunk)
    body = b"".join(chunks)
    try:
        verify_signature(
            request.headers.get("X-API-Key", ""),
            request.headers.get("X-Bot-Timestamp", ""),
            request.headers.get("X-Bot-Signature", ""),
            body,
            time.time(),
        )
    except ValueError as exc:
        raise HTTPException(401, "Firma del bot inválida o vencida.") from exc
    encoding = request.headers.get("Content-Encoding", "identity").lower()
    if encoding == "gzip":
        try:
            inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
            body = inflater.decompress(body, MAX_EXPANDED_BODY + 1)
            if len(body) > MAX_EXPANDED_BODY or inflater.unconsumed_tail:
                raise HTTPException(413, "El evento descomprimido excede 64 MiB.")
            if not inflater.eof or inflater.unused_data:
                raise ValueError("Invalid gzip framing")
        except (zlib.error, ValueError) as exc:
            raise HTTPException(422, "Compresión del evento inválida.") from exc
    elif encoding != "identity":
        raise HTTPException(415, "Compresión no admitida.")
    try:
        event = ActenBotEvent.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(422, "Contrato de evento del bot inválido.") from exc
    if event.tenant_id != ctx.tenant.id:
        raise HTTPException(403, "El evento no corresponde a la empresa de la clave.")
    if request.headers.get("X-Bot-Event-Id") != event.id:
        raise HTTPException(422, "ID de evento inconsistente.")
    try:
        row = ingest(db, event)
    except ValueError as exc:
        raise HTTPException(409, "La reunión ya existe con otro contenido.") from exc
    return receipt(row)


@router.get("/sessions/{session_id}/bot-source")
def source(
    session_id: int,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("sessions:read")),
):
    meeting = db.get(MeetingSession, session_id)
    if (
        not meeting
        or meeting.tenant_id != ctx.tenant.id
        or (ctx.on_behalf_of and meeting.project_id not in ctx.visible_project_ids)
    ):
        raise HTTPException(404, "Sesión no encontrada.")
    row = db.exec(
        select(BotInbox).where(
            BotInbox.tenant_id == ctx.tenant.id,
            BotInbox.session_id == session_id,
        )
    ).first()
    if row is None:
        raise HTTPException(404, "La sesión no tiene una captura del bot propio.")
    source_event = ActenBotEvent.model_validate_json(row.payload).model_dump(
        mode="json"
    )
    source_event["data"] = apply_labels(
        db, ctx.tenant.id, row.meeting_id, source_event["data"]
    )
    source_event["data"].pop("acten_session_id", None)
    return {
        **receipt(row),
        "error_code": row.error_code,
        "source": source_event,
    }


@router.post("/bot/meetings/{meeting_id}/retry", status_code=202)
def retry(
    meeting_id: str,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("sessions:write")),
):
    row = db.exec(
        select(BotInbox).where(
            BotInbox.tenant_id == ctx.tenant.id,
            BotInbox.meeting_id == meeting_id,
        )
    ).first()
    if row is None:
        raise HTTPException(404, "Sesión no encontrada.")
    meeting = db.get(MeetingSession, row.session_id)
    if (
        not meeting
        or meeting.tenant_id != ctx.tenant.id
        or (ctx.on_behalf_of and meeting.project_id not in ctx.visible_project_ids)
    ):
        raise HTTPException(404, "Sesión no encontrada.")
    if row.state != "failed":
        raise HTTPException(
            409, "Solo se reintentan trabajos fallidos; revisar los interrumpidos."
        )
    if row.attempts >= MAX_ATTEMPTS:
        # Si falla cinco veces seguidas no es un tropiezo: es la llave de
        # Groq de esa empresa, el contrato o el pipeline. Reintentar más no
        # lo arregla y esconde el problema detrás de un botón.
        raise HTTPException(
            409,
            f"Ya se intentó {row.attempts} veces (motivo: {row.error_code or 'desconocido'}). "
            "Corrige la causa antes de volver a reintentar.",
        )
    result = db.execute(
        update(BotInbox)
        .where(
            BotInbox.id == row.id,
            BotInbox.state == "failed",
        )
        .values(state="queued", error_code="")
    )
    db.commit()
    if result.rowcount != 1:
        raise HTTPException(409, "El trabajo cambió de estado.")
    db.refresh(row)
    return receipt(row)
