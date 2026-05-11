"""Webhook entrante de Fireflies.

Flujo automático al llegar el POST de Fireflies:
1. Crear MeetingSession en estado 'processing'.
2. Background task:
   a. Trae transcript + summary nativo de Fireflies (no se genera con IA).
   b. Limpia el summary nativo con Groq (solo formato; sin reemplazar contenido).
   c. Groq Llama 3.3 70B → fundamentals + insights (idioma, asistentes, temas,
      decisiones, riesgos, acuerdos).
   d. OpenAI gpt-4o → action_items (tareas).
   e. Disparar routing a Trello/Jira/ClickUp/Azure según IntegrationSetting.
"""

import json
import logging
import time
import traceback

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlmodel import Session, select

from database import get_session
from models import ActionItem, MeetingSession, Routing
from services.fireflies_service import FirefliesService
from services.integrations import (
    IntegrationConfigError,
    get_service_for_destination,
)
from services.llm_groq import GroqLLMService
from services.transcript_pipeline import process_session_with_ai
from services.webhook_security import verify_fireflies_webhook

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/webhook/fireflies",
    tags=["Webhooks Fireflies"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_native_summary(summary_obj) -> str:
    """Compone el resumen ejecutivo a partir de los campos nativos de Fireflies."""
    if not isinstance(summary_obj, dict):
        return str(summary_obj or "").strip()

    parts: list[str] = []

    overview = (summary_obj.get("overview") or "").strip()
    if overview:
        parts.append(f"### Resumen General\n{overview}")

    bullet_gist = (summary_obj.get("bullet_gist") or "").strip()
    if bullet_gist:
        parts.append(f"### Puntos Clave\n{bullet_gist}")

    notes = (summary_obj.get("notes") or "").strip()
    if notes:
        parts.append(f"### Notas\n{notes}")

    short_summary = (summary_obj.get("short_summary") or "").strip()
    if short_summary and not parts:
        parts.append(f"### Resumen\n{short_summary}")

    return "\n\n".join(parts).strip()


async def _dispatch_routing(
    db: Session,
    routing: Routing,
    action_items: list[ActionItem],
) -> None:
    """Envía las tareas curadas al destino externo configurado en el routing."""
    try:
        config = json.loads(routing.destination_config or "{}")
    except json.JSONDecodeError as exc:
        logger.error(
            "destination_config inválido para routing %s: %s", routing.id, exc
        )
        return

    try:
        service = get_service_for_destination(db, routing.destination_type)
    except IntegrationConfigError as exc:
        logger.error(
            "No se pudo construir servicio para routing %s (%s): %s",
            routing.id,
            routing.destination_type,
            exc,
        )
        return

    if service is None:
        return

    dest_type = (routing.destination_type or "").lower()

    # Sprint 05/06 — destinos de tipo "broadcast" (Slack/Notion/Teams/GDocs/CRM)
    # NO crean una tarea por action_item, sino que despachan UNA notificación
    # consolidada por sesión.
    if any(k in dest_type for k in ("slack", "notion", "teams", "msteams", "gdocs", "google_docs")):
        try:
            from sqlmodel import select as _sel
            sess = db.get(MeetingSession, action_items[0].session_id) if action_items else None
            title = sess.title if sess else "Sesión Notiva"
            summary = (sess.raw_summary if sess else "")[:1500]
            count = len(action_items)
            if "slack" in dest_type:
                await service.post_summary(channel=config.get("channel"), title=title,
                                           summary_md=summary, action_items_count=count)
            elif "notion" in dest_type:
                await service.create_page(title=title, summary_md=summary,
                                          project_name=(sess.project.name if sess and sess.project else None) if sess else None)
            elif "teams" in dest_type or "msteams" in dest_type:
                await service.post_card(title=title, summary_md=summary)
            elif "gdocs" in dest_type or "google_docs" in dest_type:
                await service.create_doc(title=title, body_md=summary)
        except Exception:
            logger.exception("Broadcast routing %s falló", routing.destination_type)
        return

    if any(k in dest_type for k in ("hubspot", "salesforce", "pipedrive")):
        # CRM: requiere identificar deal por email. Best-effort por primer attendee
        # con email del action_item.
        primary_email = next((a.owner_email for a in action_items if a.owner_email), None)
        if not primary_email:
            logger.info("Routing CRM %s: sin email de attendee, no se attachea.", dest_type)
            return
        body_html = f"<p>{(action_items[0].description or '').replace(chr(10), '<br>')}</p>"
        try:
            if "hubspot" in dest_type:
                deal = await service.find_deal_by_email(primary_email)
                if deal: await service.attach_note(deal["id"], body_html)
            elif "salesforce" in dest_type:
                opp = await service.find_opportunity_by_email(primary_email)
                if opp: await service.attach_task(opp["Id"],
                                                  subject=(action_items[0].title or "Notiva")[:255],
                                                  description=body_html)
            elif "pipedrive" in dest_type:
                deal = await service.find_deal_by_email(primary_email)
                if deal: await service.add_note(deal["id"], body_html)
        except Exception:
            logger.exception("CRM routing %s falló", dest_type)
        return

    # Resto: tareas por action_item (Trello/Jira/ClickUp/Azure)
    for act in action_items:
        try:
            if "trello" in dest_type:
                await service.create_card(
                    config.get("board_id"),
                    config.get("list_id"),
                    act.title,
                    act.description,
                    act.due_date,
                )
            elif "jira" in dest_type:
                await service.create_issue(
                    config.get("project_key"),
                    act.title,
                    act.description,
                    due_date=act.due_date,
                    owner_email=act.owner_email,
                )
            elif "clickup" in dest_type:
                await service.create_task(
                    config.get("list_id"),
                    act.title,
                    act.description,
                    due_date=act.due_date,
                    owner_email=act.owner_email,
                )
            elif "azure" in dest_type or "devops" in dest_type:
                await service.create_work_item(
                    title=act.title,
                    description=act.description,
                    due_date=act.due_date,
                    owner_email=act.owner_email,
                )
        except Exception:
            logger.exception(
                "Error creando tarea externa en %s para action_item %s",
                routing.destination_type,
                act.id,
            )


# ---------------------------------------------------------------------------
# Background processing
# ---------------------------------------------------------------------------


async def process_transcript_background(
    session_id: int, transcript_id: str, payload_data: dict
) -> None:
    """Llamado vía BackgroundTask. Trae datos nativos de Fireflies, los
    persiste en la sesión y luego invoca el pipeline IA común."""
    from database import engine

    with Session(engine) as db:
        try:
            new_session = db.get(MeetingSession, session_id)
            if not new_session:
                logger.error("No se encontró la sesión %s para actualizar.", session_id)
                return

            data_obj = (
                payload_data.get("data", {})
                if isinstance(payload_data.get("data"), dict)
                else {}
            )
            title = (
                payload_data.get("title")
                or data_obj.get("title")
                or "Reunión Sin Título"
            )
            date_val = (
                payload_data.get("date")
                or payload_data.get("createdAt")
                or data_obj.get("date")
                or data_obj.get("createdAt")
            )
            date_str = str(date_val) if date_val else str(int(time.time() * 1000))

            raw_transcript = str(payload_data.get("transcript", "")).strip()
            raw_summary = ""

            # ---------- 1. Pull native data from Fireflies ----------
            fireflies = FirefliesService()
            try:
                ff_data = await fireflies.get_transcript_data(transcript_id)
            except Exception:
                logger.exception(
                    "Fireflies API falló al traer transcript %s", transcript_id
                )
                ff_data = {}

            title = ff_data.get("title") or title
            if ff_data.get("dateString"):
                date_str = str(ff_data["dateString"])

            if not raw_transcript or len(raw_transcript) < 10:
                sentences = ff_data.get("sentences") or []
                raw_transcript = "\n".join(
                    f"[{s.get('speaker_name', 'Speaker')}] {s.get('text', '')}"
                    for s in sentences
                ).strip()

            # Resumen ejecutivo NATIVO de Fireflies (no se genera con IA).
            raw_summary = _extract_native_summary(ff_data.get("summary"))

            # Limpieza cosmética del summary con Groq (traduce headers, quita
            # asteriscos, elimina referencias tipo [Fuente: ...]).
            groq = GroqLLMService()
            if raw_summary:
                raw_summary = await groq.clean_native_summary(raw_summary)

            new_session.title = title
            new_session.date = date_str
            new_session.raw_transcript = raw_transcript
            new_session.raw_summary = raw_summary
            new_session.status = "pending"
            db.add(new_session)
            db.commit()
            db.refresh(new_session)

            # ---------- 2. Pipeline IA común (Groq insights + OpenAI tareas) ----------
            await process_session_with_ai(db, new_session.id)

            # ---------- 3. Routing externo (Trello/Jira/ClickUp/Azure) ----------
            db.refresh(new_session)
            matched_project_id = new_session.project_id
            if matched_project_id:
                routings = db.exec(
                    select(Routing).where(Routing.project_id == matched_project_id)
                ).all()
                if routings:
                    items = db.exec(
                        select(ActionItem).where(
                            ActionItem.session_id == new_session.id
                        )
                    ).all()
                    for routing in routings:
                        if not routing.is_active:
                            continue
                        await _dispatch_routing(db, routing, items)
        except Exception:
            logger.exception(
                "Error procesando transcript %s en background", transcript_id
            )
            logger.debug(traceback.format_exc())


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("")
async def receive_fireflies_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    """Endpoint para recibir el evento 'Transcription complete' desde Fireflies.

    Multi-tenant: el token del query param identifica QUÉ EMPRESA. La sesión
    creada se stampea con su `tenant_id` para garantizar el aislamiento.
    """
    tenant_id = await verify_fireflies_webhook(request, db)

    raw_body = await request.body()
    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except json.JSONDecodeError as exc:
        logger.warning("Webhook con JSON inválido: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    logger.info(
        "Fireflies webhook recibido: %s",
        json.dumps(payload, ensure_ascii=False)[:500],
    )

    transcript_id = payload.get("transcriptId") or payload.get("meetingId")
    if not transcript_id:
        transcript_id = payload.get("id") or payload.get("meeting_id")
        if not transcript_id and isinstance(payload.get("data"), dict):
            data_obj = payload["data"]
            transcript_id = (
                data_obj.get("transcriptId")
                or data_obj.get("id")
                or data_obj.get("meetingId")
            )

    if not transcript_id:
        logger.info("Webhook sin transcript ID. Ignorando.")
        return {
            "status": "ignored",
            "message": "Falta transcriptId o meetingId en el payload, ignorando.",
        }

    data_obj = (
        payload.get("data", {})
        if isinstance(payload.get("data"), dict)
        else {}
    )
    title = (
        payload.get("title") or data_obj.get("title") or "Reunión Procesando..."
    )
    date_val = (
        payload.get("date")
        or payload.get("createdAt")
        or data_obj.get("date")
        or data_obj.get("createdAt")
    )
    date_str = str(date_val) if date_val else str(int(time.time() * 1000))

    new_session = MeetingSession(
        tenant_id=tenant_id,
        fireflies_id=transcript_id,
        title=title,
        date=date_str,
        raw_transcript="",
        raw_summary="",
        status="processing",
    )
    db.add(new_session)
    db.commit()
    db.refresh(new_session)

    background_tasks.add_task(
        process_transcript_background, new_session.id, transcript_id, payload
    )

    return {
        "status": "accepted",
        "message": (
            f"Transcript/Meeting {transcript_id} programado para procesar en background."
        ),
    }
