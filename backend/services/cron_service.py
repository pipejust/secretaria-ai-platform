"""Cron de auto-curación.

Cada 5 minutos revisa todas las sesiones en estado `pending`. Si
`IntegrationSetting(provider_name='autoCuration')` está activa y la sesión
lleva más de `timeoutHours` horas en `pending`, dispara automáticamente:

  1. POST /api/sessions/{id}/dispatch_emails  (a todos los action_items)
  2. POST /api/sessions/{id}/dispatch_platforms (a Trello/Jira/ClickUp/Azure)

Si ambos dispatches terminan sin excepción, marca la sesión como
`processed`. Si alguno falla, la sesión se queda en `pending` para que
el cron lo reintente en la siguiente corrida (con cap implícito por
`processed_at` para no spammear).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session, select

from database import engine
from models import ActionItem, IntegrationSetting, MeetingSession, Project

logger = logging.getLogger(__name__)


def _load_auto_curation_config(session: Session) -> Optional[tuple[bool, float]]:
    """Devuelve (is_enabled, timeout_hours) o None si la configuración no existe/es inválida."""
    setting = session.exec(
        select(IntegrationSetting).where(
            IntegrationSetting.provider_name == "autoCuration"
        )
    ).first()
    if not setting:
        return None
    try:
        config = json.loads(setting.config_json or "{}")
    except (json.JSONDecodeError, TypeError):
        logger.error("autoCuration: config_json inválido")
        return None
    is_enabled = bool(config.get("isEnabled", False))
    try:
        timeout_hours = float(config.get("timeoutHours", 1))
    except (TypeError, ValueError):
        timeout_hours = 1.0
    return is_enabled, max(timeout_hours, 0.0)


def _hours_since(created_at_iso: str) -> Optional[float]:
    """Diferencia en horas entre `now()` (sin tz) y la fecha ISO de creación."""
    if not created_at_iso:
        return None
    try:
        created = datetime.fromisoformat(created_at_iso)
    except ValueError:
        return None
    now = datetime.now()
    if created.tzinfo and now.tzinfo is None:
        now = now.astimezone(created.tzinfo)
    elif created.tzinfo is None and now.tzinfo:
        created = created.astimezone(now.tzinfo)
    return (now - created).total_seconds() / 3600.0


async def _auto_dispatch_session(session_id: int) -> None:
    """Llama a los endpoints internos de dispatch usando un Session DB nuevo.

    Crea instancias reales de los Pydantic request models para que el endpoint
    las procese. Si alguno falla, deja la sesión en `pending`.
    """
    # Imports adentro de la función para evitar ciclo: cron_service ←→ sessions_upload.
    from routers.sessions_upload import (
        DispatchEmailsRequest,
        DispatchPlatformsRequest,
        dispatch_emails,
        dispatch_platforms,
    )

    with Session(engine) as db:
        action_item_ids = [
            row.id
            for row in db.exec(
                select(ActionItem.id).where(ActionItem.session_id == session_id)
            ).all()
        ]
        if not action_item_ids:
            logger.info(
                "Sesión %s sin action_items. Marcando como processed sin dispatch.",
                session_id,
            )
            session_obj = db.get(MeetingSession, session_id)
            if session_obj:
                session_obj.status = "processed"
                db.add(session_obj)
                db.commit()
            return

        emails_ok = False
        platforms_ok = False

        try:
            await dispatch_emails(
                session_id,
                DispatchEmailsRequest(
                    action_item_ids=action_item_ids, attach_document=True
                ),
                db,
            )
            emails_ok = True
        except Exception:
            logger.exception(
                "auto-curación: dispatch_emails falló para sesión %s", session_id
            )

        try:
            await dispatch_platforms(
                session_id,
                DispatchPlatformsRequest(action_item_ids=action_item_ids),
                db,
            )
            platforms_ok = True
        except Exception:
            logger.exception(
                "auto-curación: dispatch_platforms falló para sesión %s", session_id
            )

        if emails_ok and platforms_ok:
            session_obj = db.get(MeetingSession, session_id)
            if session_obj:
                session_obj.status = "processed"
                db.add(session_obj)
                db.commit()
            logger.info(
                "auto-curación: sesión %s despachada y marcada processed.",
                session_id,
            )
        else:
            logger.warning(
                "auto-curación: sesión %s queda en pending (emails_ok=%s, platforms_ok=%s)",
                session_id,
                emails_ok,
                platforms_ok,
            )


def _run_async(coro):
    """Corre una coroutina dentro del thread de APScheduler.

    Si ya hay un loop corriendo (improbable en BackgroundScheduler), lo
    reutiliza. Si no, crea uno nuevo y lo cierra al final.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("loop cerrado")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        # No cerramos el loop aquí: APScheduler reutiliza el thread.
        pass


def _resolve_dispatch_policy(
    session: Session,
    ms: MeetingSession,
    global_enabled: bool,
    global_timeout_hours: float,
) -> tuple[bool, float]:
    """Devuelve (enabled, timeout_hours) aplicables a esta sesión.

    Si el proyecto asociado tiene `auto_dispatch_enabled` set (no NULL),
    usa los valores del proyecto. Si no, cae al global.
    """
    if ms.project_id is None:
        return global_enabled, global_timeout_hours

    project = session.get(Project, ms.project_id)
    if project is None:
        return global_enabled, global_timeout_hours

    enabled = (
        project.auto_dispatch_enabled
        if project.auto_dispatch_enabled is not None
        else global_enabled
    )
    timeout = (
        project.auto_dispatch_timeout_hours
        if project.auto_dispatch_timeout_hours is not None
        else global_timeout_hours
    )
    return bool(enabled), max(float(timeout), 0.0)


def check_and_dispatch_pending_sessions() -> None:
    """Loop de auto-curación. Se ejecuta cada N minutos."""
    with Session(engine) as session:
        config = _load_auto_curation_config(session)
        # Si no hay setting global, usamos defaults conservadores: solo activan
        # quien lo haya marcado a nivel proyecto.
        if config is None:
            global_enabled, global_timeout_hours = False, 24.0
        else:
            global_enabled, global_timeout_hours = config

        pending_sessions = session.exec(
            select(MeetingSession).where(MeetingSession.status == "pending")
        ).all()

        for ms in pending_sessions:
            enabled, timeout_hours = _resolve_dispatch_policy(
                session, ms, global_enabled, global_timeout_hours
            )
            if not enabled:
                continue

            delta = _hours_since(ms.created_at)
            if delta is None or delta < timeout_hours:
                continue

            logger.info(
                "Auto-curación: sesión %s (proyecto=%s) lleva %.2fh >= %sh. Despachando...",
                ms.id,
                ms.project_id,
                delta,
                timeout_hours,
            )
            try:
                _run_async(_auto_dispatch_session(ms.id))
            except Exception:
                logger.exception(
                    "Auto-curación: error inesperado en sesión %s", ms.id
                )


scheduler = BackgroundScheduler()
scheduler.add_job(
    check_and_dispatch_pending_sessions, "interval", minutes=5, max_instances=1
)


def start_cron() -> None:
    scheduler.start()
    logger.info("Auto-curación cron iniciado (cada 5 min).")


def stop_cron() -> None:
    scheduler.shutdown()
    logger.info("Auto-curación cron detenido.")
