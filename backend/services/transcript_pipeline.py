"""Pipeline común de procesamiento IA para una sesión.

Reutilizado por:
- routers/fireflies.py        → cuando llega un webhook de Fireflies
- routers/sessions_upload.py  → cuando el usuario sube audio o pega texto

División de responsabilidades:
- Groq (Llama 3.3 70B) → idioma, asistentes, temas, decisiones, riesgos, acuerdos
- Groq (Llama 3.3 70B) → deducción de proyecto por contexto (nivel 2)
- OpenAI (gpt-4o)      → action_items (tareas)

NO toca `raw_summary`. El summary lo gestiona quien llama:
- Webhook lo trae nativo de Fireflies (pre-cleaned con Groq).
- Upload manual lo deja vacío para que el admin lo escriba o lo derive.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from sqlmodel import Session, select

from models import ActionItem, MeetingSession, Project, ProjectContact
from services.groq_service import OpenAIService
from services.llm_groq import GroqLLMService

logger = logging.getLogger(__name__)


def _match_project_by_text(projects: list[Project], *texts: str) -> Optional[int]:
    haystack = "\n".join(t for t in texts if t).lower()
    for p in projects:
        if p.name and p.name.lower() in haystack:
            return p.id
    return None


async def process_session_with_ai(
    db: Session,
    session_id: int,
    *,
    auto_match_project: bool = True,
) -> None:
    """Ejecuta el pipeline completo sobre la sesión `session_id`.

    Asume que la sesión ya tiene `raw_transcript` cargado. No bloquea: si
    falla algún paso, loguea y deja la sesión en `pending` con lo que se
    pudo procesar.
    """
    session_obj = db.get(MeetingSession, session_id)
    if not session_obj:
        logger.error("process_session_with_ai: sesión %s no existe", session_id)
        return

    transcript = (session_obj.raw_transcript or "").strip()
    if not transcript:
        logger.warning(
            "process_session_with_ai: sesión %s sin transcript, saltando IA.",
            session_id,
        )
        return

    groq = GroqLLMService()
    openai = OpenAIService()

    # ---------- 1. Project matching nivel 1 (literal) ----------
    matched_project_id = session_obj.project_id
    projects = db.exec(select(Project)).all()
    if auto_match_project and not matched_project_id:
        matched_project_id = _match_project_by_text(
            projects, session_obj.title or "", transcript
        )

    project_contacts: list[dict] = []
    if matched_project_id:
        db_contacts = db.exec(
            select(ProjectContact).where(ProjectContact.project_id == matched_project_id)
        ).all()
        project_contacts = [
            {"name": c.name, "email": c.email, "role": c.role} for c in db_contacts
        ]

    # ---------- 2. Groq → fundamentals + insights ----------
    insights = await groq.process_fundamentals_and_insights(
        transcript, project_contacts
    )

    # ---------- 3. Project matching nivel 2 (Groq deduce por contexto) ----------
    if auto_match_project and not matched_project_id:
        proj_dict_list = [
            {"id": p.id, "name": p.name, "description": p.description}
            for p in projects
        ]
        deduced_id = await groq.deduce_project(
            session_obj.raw_summary or transcript[:6000], proj_dict_list
        )
        if deduced_id:
            matched_project_id = deduced_id
            # Recargar contactos del proyecto deducido
            db_contacts = db.exec(
                select(ProjectContact).where(
                    ProjectContact.project_id == matched_project_id
                )
            ).all()
            project_contacts = [
                {"name": c.name, "email": c.email, "role": c.role}
                for c in db_contacts
            ]

    # ---------- 4. Persistir insights ----------
    session_obj.project_id = matched_project_id
    session_obj.language = insights.get("language") or session_obj.language or "Español"
    session_obj.processed_decisions = insights.get("decisions", "")
    session_obj.processed_risks = insights.get("risks", "")
    session_obj.processed_agreements = insights.get("agreements", "")
    session_obj.processed_attendees = json.dumps(
        insights.get("attendees", []), ensure_ascii=False
    )
    session_obj.processed_themes = json.dumps(
        insights.get("themes", []), ensure_ascii=False
    )
    db.add(session_obj)
    db.commit()

    # ---------- 5. OpenAI → action_items ----------
    # Pasamos también las secciones ya procesadas (decisions, agreements,
    # summary) para que la extracción de tareas tenga TODA la información
    # estructurada a su disposición. Esto evita que se pierdan
    # compromisos que estaban claros en los acuerdos pero diluidos en el
    # transcript ruidoso.
    try:
        tasks_payload = await openai.process_transcript_for_tasks_only(
            transcript,
            project_contacts,
            decisions=session_obj.processed_decisions or "",
            agreements=session_obj.processed_agreements or "",
            summary=session_obj.raw_summary or "",
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
                tenant_id=session_obj.tenant_id,
                session_id=session_id,
                owner_name=owner_name,
                owner_email=owner_email,
                title=title_v or "Tarea sin título",
                description=description,
                due_date=due_date,
                is_approved=False,
            )
        )
    db.commit()

    # ---------- 5b. Notificar al owner si tiene cuenta en el workspace ----
    # Releemos las tareas recién creadas y disparamos una notif por cada
    # owner_email que matchee con un User. Si el owner es externo (no
    # tiene cuenta), simplemente se ignora — no rompe el pipeline.
    try:
        from services.notification_service import (
            notify_owner_email,
            KIND_TASK_ASSIGNED,
        )
        new_items = db.exec(
            select(ActionItem)
            .where(ActionItem.session_id == session_id)
            .where(ActionItem.tenant_id == session_obj.tenant_id)
        ).all()
        for it in new_items:
            notify_owner_email(
                db,
                tenant_id=session_obj.tenant_id,
                owner_email=it.owner_email,
                kind=KIND_TASK_ASSIGNED,
                title=f"Te asignaron una tarea: {it.title[:120]}",
                body=(it.description or "")[:300],
                link_to=f"/admin/curation/{session_id}",
                entity_type="action_item",
                entity_id=it.id,
            )
    except Exception:
        logger.exception(
            "No se pudieron emitir notificaciones de tareas para sesión %s",
            session_id,
        )

    # ---------- 6. Embeddings (Sprint 00 — RAG foundation) ----------
    try:
        from services.embedding_service import embed_session
        chunks = await embed_session(db, session_id)
        logger.info("Sesión %s: %s chunks de embeddings indexados.", session_id, chunks)
    except Exception:
        logger.exception("embed_session falló para sesión %s (no bloquea curación).", session_id)

    # Marca la sesión como pendiente de curación humana (sale de "processing").
    # Si auto-curación está activa, el cron la moverá a "processed" tras N horas.
    db.refresh(session_obj)
    if session_obj.status == "processing":
        session_obj.status = "pending"
        db.add(session_obj)
        db.commit()

    # ---------- 7. Notificar a los admins: "Nueva sesión analizada" ----
    try:
        from services.notification_service import (
            notify_admins,
            KIND_SESSION_PROCESSED,
        )
        notify_admins(
            db,
            tenant_id=session_obj.tenant_id,
            kind=KIND_SESSION_PROCESSED,
            title=f"Sesión analizada: {session_obj.title[:120]}",
            body=(
                f"La IA terminó de procesar la sesión. "
                f"{len(insights.get('attendees', []) or [])} asistentes detectados."
            ),
            link_to=f"/admin/curation/{session_id}",
            entity_type="session",
            entity_id=session_id,
        )
    except Exception:
        logger.exception(
            "No se pudo emitir notif de session_processed para %s", session_id,
        )

    logger.info(
        "Pipeline IA completado para sesión %s (proyecto=%s).",
        session_id,
        matched_project_id,
    )
