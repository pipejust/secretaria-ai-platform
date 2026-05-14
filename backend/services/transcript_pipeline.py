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

CONTRATO DE COMPLETITUD (Sprint Estabilidad — fix 'procesos a medias'):

Una sesión sólo se considera COMPLETA si todos estos pasos terminaron OK:

  1. Groq insights (decisiones, riesgos, acuerdos, asistentes, temas).
  2. OpenAI action_items extraction.

Si alguno falla incluso después de los reintentos, la sesión queda con:
  - `processing_error` = descripción del fallo
  - `status` = 'pending' igualmente (para que el admin pueda revisar lo que sí
     se logró), pero queda marcada para reintento automático.

Los embeddings y las notificaciones son best-effort (no bloquean la
completitud, pero sí se loguean).
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional, TypeVar

from sqlmodel import Session, select

from models import ActionItem, MeetingSession, Project, ProjectContact
from services.groq_service import OpenAIService
from services.llm_groq import GroqLLMService

logger = logging.getLogger(__name__)

# ---- Configuración de reintentos ---------------------------------------
# 3 intentos con backoff exponencial + jitter. Total de espera en el peor
# caso: ~2 + ~4 = 6 s. Suficiente para superar timeouts puntuales o rate
# limits de OpenAI/Groq, sin alargar excesivamente el background task.
_MAX_RETRIES = 3
_BASE_BACKOFF_SEC = 2.0

T = TypeVar("T")


async def _call_with_retry(
    label: str,
    fn: Callable[[], Awaitable[T]],
    *,
    max_retries: int = _MAX_RETRIES,
    base_backoff: float = _BASE_BACKOFF_SEC,
) -> T:
    """Ejecuta `fn()` con reintentos exponenciales + jitter.

    Levanta la última excepción si todos los intentos fallan.
    """
    last_exc: Optional[BaseException] = None
    for attempt in range(1, max_retries + 1):
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt == max_retries:
                logger.error(
                    "%s: agotados %s intentos, último error: %s",
                    label, max_retries, exc,
                )
                break
            sleep_for = base_backoff * (2 ** (attempt - 1))
            sleep_for += random.uniform(0, base_backoff / 2)
            logger.warning(
                "%s: intento %s/%s falló (%s). Reintentando en %.1fs.",
                label, attempt, max_retries, exc, sleep_for,
            )
            await asyncio.sleep(sleep_for)
    assert last_exc is not None  # mypy: garantizado por el for-else lógico
    raise last_exc


def _match_project_by_text(projects: list[Project], *texts: str) -> Optional[int]:
    haystack = "\n".join(t for t in texts if t).lower()
    for p in projects:
        if p.name and p.name.lower() in haystack:
            return p.id
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def process_session_with_ai(
    db: Session,
    session_id: int,
    *,
    auto_match_project: bool = True,
) -> None:
    """Ejecuta el pipeline completo sobre la sesión `session_id`.

    Asume que la sesión ya tiene `raw_transcript` cargado.

    Marca completitud al final:
    - Si todos los pasos críticos OK → `processing_error = ''` y
      `processing_completed_at = now`.
    - Si alguno falla incluso tras reintentos → `processing_error` describe
      qué se rompió, y la sesión queda disponible para retry manual o
      automático.
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
        # Sin transcript no hay nada que reintentar — esto no es un fallo
        # del pipeline, es input vacío.
        session_obj.processing_error = "no_transcript"
        session_obj.processing_attempts = (session_obj.processing_attempts or 0) + 1
        db.add(session_obj)
        db.commit()
        return

    # Sumamos intento al inicio para que incluso si todo se cae el contador
    # refleje que se intentó.
    session_obj.processing_attempts = (session_obj.processing_attempts or 0) + 1
    db.add(session_obj)
    db.commit()

    groq = GroqLLMService()
    openai = OpenAIService()

    # Dict de errores por paso. Si al final tiene algo, la sesión queda
    # marcada como incompleta.
    errors: dict[str, str] = {}

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

    # ---------- 2. Groq → fundamentals + insights (CRÍTICO, con retry) ----------
    insights: dict = {}
    try:
        insights = await _call_with_retry(
            f"groq.insights[session={session_id}]",
            lambda: groq.process_fundamentals_and_insights(
                transcript, project_contacts
            ),
        )
    except Exception as exc:  # noqa: BLE001
        errors["insights"] = f"Groq insights falló tras reintentos: {exc}"
        logger.exception("Groq insights agotó reintentos para sesión %s", session_id)

    # ---------- 3. Project matching nivel 2 (Groq deduce por contexto) ----------
    if auto_match_project and not matched_project_id:
        proj_dict_list = [
            {"id": p.id, "name": p.name, "description": p.description}
            for p in projects
        ]
        try:
            deduced_id = await _call_with_retry(
                f"groq.deduce_project[session={session_id}]",
                lambda: groq.deduce_project(
                    session_obj.raw_summary or transcript[:6000], proj_dict_list
                ),
                max_retries=2,  # menos crítico
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Groq deduce_project falló para sesión %s (no bloquea): %s",
                session_id, exc,
            )
            deduced_id = None
        if deduced_id:
            matched_project_id = deduced_id
            db_contacts = db.exec(
                select(ProjectContact).where(
                    ProjectContact.project_id == matched_project_id
                )
            ).all()
            project_contacts = [
                {"name": c.name, "email": c.email, "role": c.role}
                for c in db_contacts
            ]

    # ---------- 4. Persistir insights (incluso parciales) ----------
    session_obj.project_id = matched_project_id
    if insights:
        session_obj.language = (
            insights.get("language") or session_obj.language or "Español"
        )
        session_obj.processed_decisions = insights.get("decisions", "") or ""
        session_obj.processed_risks = insights.get("risks", "") or ""
        session_obj.processed_agreements = insights.get("agreements", "") or ""
        session_obj.processed_attendees = json.dumps(
            insights.get("attendees", []) or [], ensure_ascii=False
        )
        session_obj.processed_themes = json.dumps(
            insights.get("themes", []) or [], ensure_ascii=False
        )
    db.add(session_obj)
    db.commit()

    # ---------- 5. OpenAI → action_items (CRÍTICO, con retry) ----------
    # Pasamos también las secciones ya procesadas (decisions, agreements,
    # summary) para que la extracción de tareas tenga TODA la información
    # estructurada a su disposición. Esto evita que se pierdan
    # compromisos que estaban claros en los acuerdos pero diluidos en el
    # transcript ruidoso.
    tasks_payload: dict = {"action_items": []}
    try:
        tasks_payload = await _call_with_retry(
            f"openai.tasks[session={session_id}]",
            lambda: openai.process_transcript_for_tasks_only(
                transcript,
                project_contacts,
                decisions=session_obj.processed_decisions or "",
                agreements=session_obj.processed_agreements or "",
                summary=session_obj.raw_summary or "",
            ),
        )
    except Exception as exc:  # noqa: BLE001
        errors["tasks"] = f"OpenAI tasks falló tras reintentos: {exc}"
        logger.exception(
            "OpenAI tasks agotó reintentos para sesión %s", session_id,
        )

    # IDEMPOTENCIA EN REINTENTOS: si el pipeline ya había corrido antes y
    # creado tareas (parciales), las borramos para que el re-run sea la
    # fuente de verdad. Solo borramos las que NO han sido aprobadas/editadas
    # por un humano (`is_approved == False`), para no perder trabajo manual
    # del admin sobre las tareas que sí estaban bien.
    if (session_obj.processing_attempts or 0) > 1 and "tasks" not in errors:
        prev_items = db.exec(
            select(ActionItem)
            .where(ActionItem.session_id == session_id)
            .where(ActionItem.tenant_id == session_obj.tenant_id)
            .where(ActionItem.is_approved == False)  # noqa: E712
        ).all()
        for prev in prev_items:
            db.delete(prev)
        if prev_items:
            db.commit()
            logger.info(
                "Sesión %s: borradas %s tareas previas no aprobadas antes de re-IA.",
                session_id, len(prev_items),
            )

    created_tasks = 0
    for item_data in tasks_payload.get("action_items", []) or []:
        if isinstance(item_data, str):
            title_v = "Tarea Detectada"
            description = item_data.strip()
            owner_name = "Unknown"
            owner_email = ""
            due_date = None
            due_time = None
            priority = "media"
        elif isinstance(item_data, dict):
            title_v = str(item_data.get("title") or "").strip()
            description = str(item_data.get("description") or "").strip()
            owner_name = str(item_data.get("owner_name") or "Unknown")
            owner_email = str(item_data.get("owner_email") or "")
            due_date = item_data.get("due_date") or None
            due_time = (item_data.get("due_time") or "").strip() or None
            priority = (item_data.get("priority") or "media").lower().strip()
            if priority not in ("alta", "media", "baja"):
                priority = "media"
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
                due_time=due_time,
                priority=priority,
                is_approved=False,
            )
        )
        created_tasks += 1
    db.commit()

    # ---------- 5b. Notificar al owner si tiene cuenta en el workspace ----
    # Releemos las tareas recién creadas y disparamos una notif por cada
    # owner_email que matchee con un User. Si el owner es externo (no
    # tiene cuenta), simplemente se ignora — no rompe el pipeline.
    #
    # GATE de seguridad: si el pipeline tuvo errores, NO notificamos a
    # los owners — porque la lista de tareas puede estar incompleta o
    # mezclada (ej. Groq falló asignando responsables). El admin tiene
    # que reintentar primero. Cuando el pipeline termine sin errores,
    # las notificaciones salen normalmente.
    if errors:
        logger.warning(
            "Sesión %s incompleta — saltando notificaciones a owners de tareas.",
            session_id,
        )
    else:
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

    # ---------- 6. Embeddings (Sprint 00 — RAG foundation, NO crítico) ----
    try:
        from services.embedding_service import embed_session
        chunks = await embed_session(db, session_id)
        logger.info(
            "Sesión %s: %s chunks de embeddings indexados.", session_id, chunks
        )
    except Exception:
        logger.exception(
            "embed_session falló para sesión %s (no bloquea curación).", session_id
        )

    # ---------- 7. Marcar completitud + estado final ----------
    db.refresh(session_obj)

    if errors:
        # Pipeline incompleto. Persistimos el detalle para que el admin lo vea
        # y el cron de retry lo rescate.
        session_obj.processing_error = "; ".join(
            f"{step}: {msg}" for step, msg in errors.items()
        )[:2000]
        # NO seteamos processing_completed_at — sigue vacío, lo cual marca
        # la sesión como "no terminada".
    else:
        session_obj.processing_error = ""
        session_obj.processing_completed_at = _now_iso()

    if session_obj.status == "processing":
        session_obj.status = "pending"
    db.add(session_obj)
    db.commit()

    # ---------- 8. Notificar a los admins ---------------------------------
    try:
        from services.notification_service import (
            notify_admins,
            KIND_SESSION_PROCESSED,
        )
        if errors:
            notify_admins(
                db,
                tenant_id=session_obj.tenant_id,
                kind=KIND_SESSION_PROCESSED,
                title=f"⚠️ Sesión incompleta: {session_obj.title[:120]}",
                body=(
                    "El pipeline IA terminó con errores. "
                    f"Pasos fallidos: {', '.join(errors.keys())}. "
                    "Reintentar desde la sesión."
                ),
                link_to=f"/admin/curation/{session_id}",
                entity_type="session",
                entity_id=session_id,
            )
        else:
            notify_admins(
                db,
                tenant_id=session_obj.tenant_id,
                kind=KIND_SESSION_PROCESSED,
                title=f"Sesión analizada: {session_obj.title[:120]}",
                body=(
                    f"La IA terminó de procesar la sesión. "
                    f"{len(insights.get('attendees', []) or [])} asistentes, "
                    f"{created_tasks} tareas detectadas."
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
        "Pipeline IA terminado para sesión %s (proyecto=%s, tareas=%s, errores=%s).",
        session_id,
        matched_project_id,
        created_tasks,
        list(errors.keys()) or "ninguno",
    )
