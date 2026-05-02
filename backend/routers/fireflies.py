import json
import logging
import time
import traceback

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlmodel import Session, select

from database import get_session
from models import ActionItem, MeetingSession, Project, ProjectContact, Routing
from services.fireflies_service import FirefliesService
from services.groq_service import GroqService
from services.integrations import (
    IntegrationConfigError,
    get_service_for_destination,
)
from services.webhook_security import verify_fireflies_signature

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/webhook/fireflies",
    tags=["Webhooks Fireflies"],
)


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

            raw_transcript = str(payload_data.get("transcript", ""))
            raw_summary = str(payload_data.get("summary", ""))

            if not raw_transcript or len(raw_transcript) < 10:
                service = FirefliesService()
                data = await service.get_transcript_data(transcript_id)
                title = data.get("title", title)
                date_str = str(data.get("date")) if data.get("date") else date_str
                sentences = [
                    f"{s.get('speaker_name', 'Anon')}: {s.get('text', '')}"
                    for s in data.get("sentences", [])
                ]
                raw_transcript = "\n".join(sentences)
                summary_obj = data.get("summary")
                if isinstance(summary_obj, dict):
                    raw_summary = summary_obj.get("overview", "")
                else:
                    raw_summary = str(summary_obj or "")

            projects = db.exec(select(Project)).all()
            matched_project_id = None

            for p in projects:
                if p.name.lower() in title.lower():
                    matched_project_id = p.id
                    break

            if not matched_project_id and raw_transcript:
                for p in projects:
                    if p.name.lower() in raw_transcript.lower():
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
                return

            groq_svc = GroqService()
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

            structured_data = await groq_svc.process_transcript(
                raw_transcript, project_contacts
            )

            groq_summary = structured_data.get("summary", "")
            if (
                not new_session.raw_summary
                and groq_summary
                and len(groq_summary) > 20
            ):
                new_session.raw_summary = groq_summary

            if not matched_project_id and new_session.raw_summary:
                proj_dict_list = [
                    {"id": p.id, "name": p.name, "description": p.description}
                    for p in projects
                ]
                deduced_id = await groq_svc.deduce_project(
                    new_session.raw_summary, proj_dict_list
                )
                if deduced_id:
                    matched_project_id = deduced_id
                    new_session.project_id = matched_project_id

            new_session.language = structured_data.get("language", "Español")
            new_session.processed_decisions = structured_data.get("decisions", "")
            new_session.processed_risks = structured_data.get("risks", "")
            new_session.processed_agreements = structured_data.get("agreements", "")
            new_session.processed_attendees = json.dumps(
                structured_data.get("attendees", []), ensure_ascii=False
            )
            new_session.processed_themes = json.dumps(
                structured_data.get("themes", []), ensure_ascii=False
            )
            db.add(new_session)
            db.commit()

            for item_data in structured_data.get("action_items", []):
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

                action_item = ActionItem(
                    session_id=new_session.id,
                    owner_name=owner_name,
                    owner_email=owner_email,
                    title=title_v or "Tarea sin título",
                    description=description,
                    due_date=due_date,
                    is_approved=False,
                )
                db.add(action_item)
            db.commit()

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


@router.post("")
async def receive_fireflies_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    """Endpoint para recibir el evento 'Transcription complete' desde Fireflies."""
    raw_body = await request.body()
    await verify_fireflies_signature(request, raw_body, db)

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
    event_type = payload.get("eventType") or payload.get("event")
    logger.info(
        "Event type: %s, Transcript ID inicial: %s", event_type, transcript_id
    )

    if not transcript_id:
        transcript_id = payload.get("id") or payload.get("meeting_id")
        if not transcript_id and isinstance(payload.get("data"), dict):
            data_obj = payload["data"]
            transcript_id = (
                data_obj.get("transcriptId")
                or data_obj.get("id")
                or data_obj.get("meetingId")
            )
            logger.info("Extraído de data anidada: %s", transcript_id)

    if not transcript_id:
        logger.info("Webhook sin transcript ID. Ignorando.")
        # Devolvemos 200 para que Fireflies no desactive el hook por pings de validación.
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
