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
from models import ActionItem, MeetingSession, Project, ProjectContact, Routing
from services.fireflies_service import FirefliesService
from services.groq_service import OpenAIService
from services.integrations import (
    IntegrationConfigError,
    get_service_for_destination,
)
from services.llm_groq import GroqLLMService
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

            # Title / date desde Fireflies si no vinieron en el payload.
            title = ff_data.get("title") or title
            if ff_data.get("dateString"):
                date_str = str(ff_data["dateString"])

            # Transcripción nativa (sentencias).
            if not raw_transcript or len(raw_transcript) < 10:
                sentences = ff_data.get("sentences") or []
                raw_transcript = "\n".join(
                    f"[{s.get('speaker_name', 'Speaker')}] {s.get('text', '')}"
                    for s in sentences
                ).strip()

            # Resumen ejecutivo NATIVO de Fireflies (no se genera con IA).
            raw_summary = _extract_native_summary(ff_data.get("summary"))

            # Limpiamos formato del summary con Groq (sólo cosmética: traduce
            # encabezados, quita asteriscos, elimina basura tipo [Fuente: ...]).
            groq = GroqLLMService()
            if raw_summary:
                raw_summary = await groq.clean_native_summary(raw_summary)

            # ---------- 2. Project matching nivel 1 (texto literal) ----------
            projects = db.exec(select(Project)).all()
            matched_project_id: int | None = None
            haystack = f"{title}\n{raw_transcript}".lower()
            for p in projects:
                if p.name and p.name.lower() in haystack:
                    matched_project_id = p.id
                    break

            new_session.title = title
            new_session.date = date_str
            new_session.project_id = matched_project_id
            new_session.raw_transcript = raw_transcript
            new_session.raw_summary = raw_summary
            new_session.status = "pending"
            db.add(new_session)
            db.commit()
            db.refresh(new_session)

            if not raw_transcript:
                logger.warning(
                    "Sesión %s sin transcript (Fireflies vacío). Saltando IA.",
                    session_id,
                )
                return

            # ---------- 3. Groq → fundamentals + insights ----------
            project_contacts: list[dict] = []
            if matched_project_id:
                db_contacts = db.exec(
                    select(ProjectContact).where(
                        ProjectContact.project_id == matched_project_id
                    )
                ).all()
                project_contacts = [
                    {"name": c.name, "email": c.email, "role": c.role}
                    for c in db_contacts
                ]

            insights = await groq.process_fundamentals_and_insights(
                raw_transcript, project_contacts
            )

            # ---------- 4. Project matching nivel 2 (Groq deduce por contexto) ----------
            if not matched_project_id:
                proj_dict_list = [
                    {"id": p.id, "name": p.name, "description": p.description}
                    for p in projects
                ]
                deduced_id = await groq.deduce_project(
                    raw_summary or raw_transcript[:6000], proj_dict_list
                )
                if deduced_id:
                    matched_project_id = deduced_id
                    new_session.project_id = matched_project_id

            new_session.language = insights.get("language") or "Español"
            new_session.processed_decisions = insights.get("decisions", "")
            new_session.processed_risks = insights.get("risks", "")
            new_session.processed_agreements = insights.get("agreements", "")
            new_session.processed_attendees = json.dumps(
                insights.get("attendees", []), ensure_ascii=False
            )
            new_session.processed_themes = json.dumps(
                insights.get("themes", []), ensure_ascii=False
            )
            db.add(new_session)
            db.commit()

            # ---------- 5. OpenAI → action_items ----------
            openai = OpenAIService()
            try:
                tasks_payload = await openai.process_transcript_for_tasks_only(
                    raw_transcript, project_contacts
                )
            except Exception:
                logger.exception(
                    "OpenAI falló extrayendo tareas para sesión %s", session_id
                )
                tasks_payload = {"action_items": []}

            for item_data in tasks_payload.get("action_items", []) or []:
                if isinstance(item_data, str):
                    title_v = "Tarea Detectada"
                    description = item_data.strip()
                    owner_name = "Unknown"
                    owner_email = ""
                    due_date = None
                elif isinstance(item_data, dict):
                    title_v = str(item_data.get("title") or "").strip()
                    description = str(item_data.get("description") or "").strip()
                    owner_name = str(item_data.get("owner_name") or "Unknown")
                    owner_email = str(item_data.get("owner_email") or "")
                    due_date = item_data.get("due_date")
                else:
                    continue

                if not title_v and not description:
                    continue

                db.add(
                    ActionItem(
                        session_id=new_session.id,
                        owner_name=owner_name,
                        owner_email=owner_email,
                        title=title_v or "Tarea sin título",
                        description=description,
                        due_date=due_date,
                        is_approved=False,
                    )
                )
            db.commit()

            # ---------- 6. Routing externo (Trello/Jira/ClickUp/Azure) ----------
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
    """Endpoint para recibir el evento 'Transcription complete' desde Fireflies."""
    await verify_fireflies_webhook(request, db)

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
