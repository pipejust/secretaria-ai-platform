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


def _load_auto_curation_config(session: Session, tenant_id: int) -> Optional[tuple[bool, float]]:
    """Devuelve (is_enabled, timeout_hours) del autoCuration setting del tenant
    indicado, o None si la configuración no existe/es inválida.

    Soporta tanto `timeoutMinutes` (nuevo, preferido) como `timeoutHours`
    (legacy, convertido). Si ambos existen, gana minutos.
    """
    setting = session.exec(
        select(IntegrationSetting)
        .where(IntegrationSetting.provider_name == "autoCuration")
        .where(IntegrationSetting.tenant_id == tenant_id)
    ).first()
    if not setting:
        return None
    try:
        config = json.loads(setting.config_json or "{}")
    except (json.JSONDecodeError, TypeError):
        logger.error("autoCuration: config_json inválido")
        return None
    is_enabled = bool(config.get("isEnabled", False))
    # Prioridad: minutos nuevo > horas legacy.
    timeout_minutes = config.get("timeoutMinutes")
    if timeout_minutes is None:
        try:
            timeout_hours = float(config.get("timeoutHours", 1))
        except (TypeError, ValueError):
            timeout_hours = 1.0
        timeout_minutes = timeout_hours * 60
    try:
        timeout_minutes = float(timeout_minutes)
    except (TypeError, ValueError):
        timeout_minutes = 60.0
    return is_enabled, max(timeout_minutes / 60.0, 0.0)  # devolvemos horas para no romper firmas existentes


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


# Cuánto esperar entre re-envíos del correo "auto-dispatch bloqueado".
# Si se manda cada 1 min del cron sería spam; 24h da al admin tiempo de
# entrar a corregir emails antes de recibir otro recordatorio.
WARNING_REPEAT_HOURS = 24


def _count_missing_emails(db: Session, session_id: int) -> tuple[int, int]:
    """Espejo del helper de fireflies.py para no acoplar el cron al router.
    Returns (missing_task_emails, missing_participants).
    """
    import json as _json
    import re

    items = db.exec(
        select(ActionItem).where(ActionItem.session_id == session_id)
    ).all()
    email_re = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
    missing_tasks = sum(
        1 for it in items
        if not (it.owner_email or "").strip()
        or not email_re.match((it.owner_email or "").strip())
    )

    ms = db.get(MeetingSession, session_id)
    missing_participants = 0
    if ms and ms.processed_attendees:
        raw = (ms.processed_attendees or "").strip()
        parsed = None
        if raw.startswith(("[", "{")):
            try:
                parsed = _json.loads(raw)
            except (_json.JSONDecodeError, TypeError):
                parsed = None
        if isinstance(parsed, list):
            for entry in parsed:
                if isinstance(entry, dict):
                    em = (entry.get("email") or "").strip()
                    if em and email_re.match(em):
                        continue
                    if entry.get("name") or entry.get("display_name"):
                        missing_participants += 1
                elif isinstance(entry, str) and entry.strip() and "@" not in entry:
                    missing_participants += 1
    return missing_tasks, missing_participants


async def _send_auto_dispatch_blocked_notice(
    session_id: int,
    tenant_id: int,
    missing_task_emails: int,
    missing_participants: int,
    timeout_minutes: int,
) -> None:
    """Email + notif in-app cuando el cron decide NO despachar por emails
    faltantes. Dedupe vía `auto_dispatch_warning_at` (re-envío ≥24h)."""
    import os
    from services.email_service import EmailService
    from services.notification_service import notify_admins

    with Session(engine) as db:
        ms = db.get(MeetingSession, session_id)
        if not ms:
            return

        # Dedup: si ya hubo un warning hace menos de WARNING_REPEAT_HOURS, skip.
        last = (ms.auto_dispatch_warning_at or "").strip()
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
                hours_since = (datetime.now() - last_dt).total_seconds() / 3600.0
                if hours_since < WARNING_REPEAT_HOURS:
                    return
            except ValueError:
                pass  # parse error → tratar como "nunca enviado"

        # Identifica destinatarios admin.
        from models import User, Role
        admins = db.exec(
            select(User)
            .where(User.tenant_id == tenant_id)
            .where(User.is_active == True)  # noqa: E712
        ).all()
        recipients: list[tuple[str, str]] = []
        for u in admins:
            role_name = ""
            if u.role_id:
                r = db.get(Role, u.role_id)
                role_name = (r.name if r else "").lower()
            if "admin" in role_name or getattr(u, "is_platform_admin", False):
                if u.email:
                    recipients.append((u.email, u.full_name or ""))
        if not recipients:
            return

        project_name = "General"
        if ms.project_id:
            p = db.get(Project, ms.project_id)
            if p:
                project_name = p.name

        frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:4200").rstrip("/")
        session_url = f"{frontend_url}/admin/curation/{session_id}"

        email_svc = EmailService(db=db, tenant_id=tenant_id)
        sent_any = False
        for email, name in recipients:
            try:
                await email_svc.send_auto_dispatch_blocked_email(
                    to_email=email,
                    admin_name=name,
                    session_title=ms.title or "Sin título",
                    project_name=project_name,
                    session_url=session_url,
                    missing_task_emails=missing_task_emails,
                    missing_participants=missing_participants,
                    timeout_minutes=timeout_minutes,
                )
                sent_any = True
            except Exception:
                logger.exception(
                    "Falló auto_dispatch_blocked_email para %s sesión %s",
                    email, session_id,
                )

        if sent_any:
            # Marca dedup + razón legible.
            ms2 = db.get(MeetingSession, session_id)
            if ms2:
                ms2.auto_dispatch_warning_at = datetime.now().isoformat()
                ms2.auto_dispatch_blocked_reason = (
                    "missing_task_emails" if missing_task_emails
                    else "missing_participants"
                )
                db.add(ms2)
                db.commit()

        # Notif in-app.
        try:
            bits = []
            if missing_task_emails:
                bits.append(f"{missing_task_emails} tarea(s) sin correo")
            if missing_participants:
                bits.append(f"{missing_participants} participante(s) sin correo")
            notify_admins(
                db,
                tenant_id=tenant_id,
                kind="auto_dispatch_blocked",
                title=f"No se pueden enviar tareas automáticamente: «{ms.title or 'Sin título'}»",
                body="El envío automático está bloqueado. Falta: " + "; ".join(bits) + ".",
                link_to=f"/admin/curation/{session_id}",
                entity_type="session",
                entity_id=session_id,
            )
        except Exception:
            logger.exception(
                "Falló notif in-app auto_dispatch_blocked para sesión %s", session_id,
            )


async def _send_auto_dispatch_done_notice(
    session_id: int, tenant_id: int, total_tasks: int,
) -> None:
    """Email + notif in-app post-dispatch exitoso. Se envía UNA VEZ por
    sesión (justo antes de marcarla 'processed')."""
    import os
    from services.email_service import EmailService
    from services.notification_service import notify_admins

    with Session(engine) as db:
        ms = db.get(MeetingSession, session_id)
        if not ms:
            return
        from models import User, Role
        admins = db.exec(
            select(User)
            .where(User.tenant_id == tenant_id)
            .where(User.is_active == True)  # noqa: E712
        ).all()
        recipients: list[tuple[str, str]] = []
        for u in admins:
            role_name = ""
            if u.role_id:
                r = db.get(Role, u.role_id)
                role_name = (r.name if r else "").lower()
            if "admin" in role_name or getattr(u, "is_platform_admin", False):
                if u.email:
                    recipients.append((u.email, u.full_name or ""))
        if not recipients:
            return

        project_name = "General"
        if ms.project_id:
            p = db.get(Project, ms.project_id)
            if p:
                project_name = p.name

        frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:4200").rstrip("/")
        session_url = f"{frontend_url}/admin/curation/{session_id}"

        email_svc = EmailService(db=db, tenant_id=tenant_id)
        for email, name in recipients:
            try:
                await email_svc.send_auto_dispatch_done_email(
                    to_email=email,
                    admin_name=name,
                    session_title=ms.title or "Sin título",
                    project_name=project_name,
                    session_url=session_url,
                    total_tasks=total_tasks,
                )
            except Exception:
                logger.exception(
                    "Falló auto_dispatch_done_email para %s sesión %s",
                    email, session_id,
                )

        try:
            notify_admins(
                db,
                tenant_id=tenant_id,
                kind="auto_dispatch_done",
                title=f"Tareas enviadas automáticamente: «{ms.title or 'Sin título'}»",
                body=f"{total_tasks} tarea(s) notificadas a responsables y plataformas.",
                link_to=f"/admin/curation/{session_id}",
                entity_type="session",
                entity_id=session_id,
            )
        except Exception:
            logger.exception(
                "Falló notif in-app auto_dispatch_done para sesión %s", session_id,
            )


async def _auto_dispatch_session(session_id: int) -> None:
    """Llama a los endpoints internos de dispatch.

    PRE-GATE: antes de despachar, contamos cuántas tareas tienen email del
    responsable. Si faltan correos, NO despachamos y mandamos un correo +
    notif al admin diciendo "completá los correos para que esto pueda salir
    solo". Si todo está OK, despachamos y marcamos status='processed'.
    """
    # Imports adentro de la función para evitar ciclo: cron_service ←→ sessions_upload.
    from routers.sessions_upload import (
        DispatchEmailsRequest,
        DispatchPlatformsRequest,
        dispatch_emails,
        dispatch_platforms,
    )

    with Session(engine) as db:
        ms_for_tenant = db.get(MeetingSession, session_id)
        if not ms_for_tenant:
            return
        tenant_id = ms_for_tenant.tenant_id

        action_items = db.exec(
            select(ActionItem).where(ActionItem.session_id == session_id)
        ).all()
        action_item_ids = [it.id for it in action_items]

        # Resolución de timeout_minutes para el correo de bloqueo.
        # Default 60; el correo lo usa solo para la copia.
        timeout_minutes_for_copy = 60
        if ms_for_tenant.project_id:
            p = db.get(Project, ms_for_tenant.project_id)
            if p and p.auto_dispatch_timeout_hours is not None:
                timeout_minutes_for_copy = max(
                    1, int(float(p.auto_dispatch_timeout_hours) * 60),
                )

        # --- PRE-GATE: ¿faltan correos en tareas o participantes? ---
        missing_tasks, missing_participants = _count_missing_emails(db, session_id)
        if missing_tasks > 0 or missing_participants > 0:
            logger.warning(
                "Auto-dispatch sesión %s BLOQUEADO: %d tareas y %d participantes sin correo. "
                "Mandando aviso al admin.",
                session_id, missing_tasks, missing_participants,
            )
            await _send_auto_dispatch_blocked_notice(
                session_id, tenant_id,
                missing_tasks, missing_participants,
                timeout_minutes_for_copy,
            )
            return  # No despachamos. Esperamos a que el admin complete.

        # Si ya había warning previo y ahora los correos están completos,
        # limpiamos el flag para no confundir el estado.
        if (ms_for_tenant.auto_dispatch_blocked_reason or "").strip():
            ms_for_tenant.auto_dispatch_blocked_reason = ""
            ms_for_tenant.auto_dispatch_warning_at = ""
            db.add(ms_for_tenant)
            db.commit()

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

        from models import Tenant
        tenant = db.get(Tenant, tenant_id)
        if not tenant:
            logger.warning("auto-curación: sesión %s sin tenant resoluble.", session_id)
            return

        try:
            await dispatch_emails(
                session_id,
                DispatchEmailsRequest(
                    action_item_ids=action_item_ids, attach_document=True
                ),
                db,
                tenant,
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
                tenant,
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
            # Correo + notif "todo enviado, no tenés que hacer nada".
            await _send_auto_dispatch_done_notice(
                session_id, tenant_id, len(action_item_ids),
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
    """Loop de auto-curación. Se ejecuta cada N minutos.

    Multi-tenant: cada tenant tiene su propio setting `autoCuration` (puede
    estar enabled en uno y disabled en otro). Resolvemos por tenant_id de
    cada sesión pendiente. Cacheamos por tenant para no consultar lo mismo
    N veces.
    """
    from models import Tenant
    with Session(engine) as session:
        pending_sessions = session.exec(
            select(MeetingSession).where(MeetingSession.status == "pending")
        ).all()

        # Cache de configuración por tenant (evita N queries cuando hay muchas
        # sesiones del mismo tenant en cola).
        config_cache: dict[int, tuple[bool, float]] = {}

        for ms in pending_sessions:
            # GATE de seguridad: si el pipeline IA dejó error, la sesión
            # NO se auto-despacha hasta que el admin reintente y termine OK.
            # Esto evita mandar tareas/decisiones incompletas a Trello/Jira
            # o disparar correos a owners con info parcial.
            if (ms.processing_error or "").strip():
                logger.debug(
                    "Auto-dispatch SKIP sesión %s: processing_error='%s'",
                    ms.id, ms.processing_error[:120],
                )
                continue

            tid = ms.tenant_id
            if tid not in config_cache:
                cfg = _load_auto_curation_config(session, tid)
                config_cache[tid] = cfg or (False, 24.0)
            global_enabled, global_timeout_hours = config_cache[tid]

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


def check_overdue_action_items() -> None:
    """Sprint 10 — log de tareas vencidas no completadas. Backbone para
    push notifications futuras (Firebase). Por ahora solo loguea para que
    el operador vea las pendientes en los logs.
    """
    from datetime import datetime as _dt
    with Session(engine) as session:
        items = session.exec(
            select(ActionItem).where(ActionItem.status.in_(["pending", "blocked"]))
        ).all()
        now = _dt.now()
        overdue = 0
        for it in items:
            if not it.due_date:
                continue
            try:
                due = _dt.fromisoformat(str(it.due_date).replace("Z", "+00:00"))
            except ValueError:
                try:
                    due = _dt.strptime(str(it.due_date)[:10], "%Y-%m-%d")
                except ValueError:
                    continue
            if due.tzinfo:
                due = due.replace(tzinfo=None)
            if due < now:
                overdue += 1
        if overdue:
            logger.info("OVERDUE_TASKS=%s", overdue)


scheduler = BackgroundScheduler()
# Cada 1 minuto — el usuario configura el delay en MINUTOS, así que el cron
# debe poder actuar en granularidad de minutos.
scheduler.add_job(
    check_and_dispatch_pending_sessions, "interval", minutes=1, max_instances=1
)
# Sprint 10 — alertas de vencimiento (cada 1 hora)
scheduler.add_job(
    check_overdue_action_items, "interval", hours=1, max_instances=1
)


def start_cron() -> None:
    scheduler.start()
    logger.info("Cron iniciado: envío automático cada 1 min + overdue check cada 1h.")


def stop_cron() -> None:
    scheduler.shutdown()
    logger.info("Cron detenido.")
