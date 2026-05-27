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
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlmodel import Session, select

from database import get_session
from models import ActionItem, MeetingSession, Routing, Tenant, User
from routers.auth import get_current_tenant, require_admin
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


def _normalize_meeting_date(value) -> Optional[str]:
    """Convierte cualquier representación de fecha que mande Fireflies a ISO-8601.

    Inputs aceptados:
      - int / float / numeric str: epoch en milisegundos o segundos.
      - ISO string: '2025-05-13T15:30:00Z' o '2025-05-13T15:30:00+00:00'.
      - Fecha sola: '2025-05-13'.
      - Cualquier dict raro o None: devuelve None.

    Devuelve siempre ISO-8601 con timezone (UTC si no se proporciona) o None
    si el valor es inválido. NUNCA cae a "ahora" silenciosamente — eso lo
    decide el caller para que el caso quede explícito en logs.
    """
    # Umbral mínimo para epochs: 2000-01-01 UTC. Cualquier cosa antes son
    # datos corruptos (ej. -1, 0) y los descartamos para no persistir 1970.
    _MIN_EPOCH_S = 946684800  # 2000-01-01T00:00:00Z

    def _from_epoch(value_num: float) -> Optional[str]:
        try:
            ms = float(value_num)
            if ms > 1e12:
                ms = ms / 1000.0
            if ms < _MIN_EPOCH_S:
                return None
            return datetime.fromtimestamp(ms, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None

    if value is None:
        return None
    # Bool subclass de int → descartar antes de tratar como número.
    if isinstance(value, bool):
        return None
    # Caso int/float directo: epoch.
    if isinstance(value, (int, float)):
        return _from_epoch(value)
    s = str(value).strip()
    if not s:
        return None
    # Epoch numérico empaquetado como string (incluye signo y decimales).
    if s.lstrip("-").replace(".", "", 1).isdigit():
        try:
            return _from_epoch(float(s))
        except ValueError:
            return None
    # ISO con Z final que fromisoformat no entiende en python < 3.11.
    try:
        candidate = s.replace("Z", "+00:00") if s.endswith("Z") else s
        dt = datetime.fromisoformat(candidate)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError:
        pass
    # Última oportunidad: fecha en formato YYYY-MM-DD.
    try:
        dt = datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError:
        return None


def _pick_first_date(*candidates) -> Optional[str]:
    """Itera candidatos y devuelve la primera fecha normalizada válida.

    Mantiene el orden de precedencia que defina el caller (ej: dateString de
    Fireflies API > date del webhook > createdAt del webhook).
    """
    for c in candidates:
        norm = _normalize_meeting_date(c)
        if norm:
            return norm
    return None


def _count_missing_emails_for_session(db, session_id: int) -> tuple[int, int]:
    """Cuenta tareas SIN owner_email válido y participantes SIN correo.

    Usado por (a) el correo post-pipeline para avisar al admin qué falta y
    (b) el cron de auto-dispatch para gate-ar el envío automático.
    Returns: (missing_task_emails, missing_participants)
    """
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

    # processed_attendees puede ser JSON list, JSON dict, o texto plano.
    ms = db.get(MeetingSession, session_id)
    missing_participants = 0
    if ms and ms.processed_attendees:
        raw = (ms.processed_attendees or "").strip()
        parsed = None
        if raw.startswith(("[", "{")):
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
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


def _resolve_auto_dispatch_policy(db, tenant_id: int, project_id: int | None) -> tuple[bool, int]:
    """Resuelve (auto_enabled, timeout_minutes) para una sesión específica.

    Precedencia:
      1. Override por proyecto (Project.auto_dispatch_enabled/timeout_hours).
      2. Setting global del tenant (IntegrationSetting('autoCuration')).
      3. Default seguro: (False, 60).
    """
    from models import IntegrationSetting, Project

    auto_enabled = False
    timeout_minutes = 60
    ac_setting = db.exec(
        select(IntegrationSetting)
        .where(IntegrationSetting.tenant_id == tenant_id)
        .where(IntegrationSetting.provider_name == "autoCuration")
    ).first()
    if ac_setting:
        try:
            cfg = json.loads(ac_setting.config_json or "{}")
            auto_enabled = bool(cfg.get("isEnabled", False))
            if cfg.get("timeoutMinutes") is not None:
                timeout_minutes = max(1, int(float(cfg.get("timeoutMinutes"))))
            elif cfg.get("timeoutHours") is not None:
                timeout_minutes = max(1, int(float(cfg.get("timeoutHours")) * 60))
        except Exception:
            pass

    if project_id is not None:
        p = db.get(Project, project_id)
        if p:
            if p.auto_dispatch_enabled is not None:
                auto_enabled = bool(p.auto_dispatch_enabled)
            if p.auto_dispatch_timeout_hours is not None:
                timeout_minutes = max(1, int(float(p.auto_dispatch_timeout_hours) * 60))
    return auto_enabled, timeout_minutes


def _resolve_admin_recipients(db, tenant_id: int) -> list[tuple[str, str]]:
    """Lista de (email, name) de admins activos del tenant — destinatarios
    del correo post-pipeline y del warning de auto-dispatch bloqueado.
    """
    from models import User, Role

    admins = db.exec(
        select(User)
        .where(User.tenant_id == tenant_id)
        .where(User.is_active == True)
    ).all()
    out: list[tuple[str, str]] = []
    for u in admins:
        role_name = ""
        if u.role_id:
            r = db.get(Role, u.role_id)
            role_name = (r.name if r else "").lower()
        if "admin" in role_name or getattr(u, "is_platform_admin", False):
            if u.email:
                out.append((u.email, u.full_name or ""))
    return out


# Hasta cuánto sigue intentando el cron pull de summary desde Fireflies
# para una sesión recién creada. Pasado este umbral, el cron deja de
# probar — Fireflies entrega el summary en menos de 5 min casi siempre,
# y casi nunca tarda más de eso. 30 min es un buffer cómodo para
# meetings muy largas o lentitud puntual de su lado. Si tras 30 min
# sigue vacío, asumimos que no va a llegar y paramos.
_PAID_SUMMARY_RETRY_WINDOW_MINUTES = 30


def _hours_since_iso(value: str) -> Optional[float]:
    """Diferencia en horas entre `now()` y un ISO timestamp. None si invalid."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    now = datetime.now(timezone.utc) if dt.tzinfo else datetime.now()
    return max(0.0, (now - dt).total_seconds() / 3600.0)


async def _send_session_ready_email(
    session_id: int,
    tenant_id: int,
    *,
    force: bool = False,
) -> bool:
    """Envía el correo POST-pipeline al/los admin/s del tenant.

    Se llama desde `process_transcript_background` DESPUÉS de que la IA
    terminó (con o sin error). NO se llama antes — la regla del producto
    es: "primero se procesa, se analiza y luego sí se envía el primer
    correo". El correo informa:
      - Estado del pipeline (OK / failed con detalle del error).
      - Tareas detectadas + cuántas sin email del responsable.
      - Participantes sin correo registrado.
      - Modo de envío (automático con timeout o manual).

    Returns True si envió, False si fue skipeado (idempotencia, gating o
    sin destinatarios).

    Gating:
      - Pipeline failed → enviar siempre (avisar del error al admin).
      - Pipeline OK + summary presente → enviar (caso normal).
      - Pipeline OK + summary vacío → SKIP. NUNCA mandar correo sin
        summary. El cron `check_pending_summaries` reintentará y disparará
        el correo cuando Fireflies entregue. Si nunca entrega, el correo
        nunca sale (la regla del producto es: no notificar a medias).
      - `force=True` → bypassa el gating de summary (uso interno: cron
        cuando confirmó que summary llegó).

    Idempotente: si `session_ready_email_sent_at` ya está set, NO reenvía.
    """
    import os
    from datetime import datetime
    from sqlmodel import Session
    from database import engine
    from services.email_service import EmailService

    try:
        with Session(engine) as db:
            ms = db.get(MeetingSession, session_id)
            if not ms:
                return False

            # Idempotencia: ya se envió antes → no reenviar.
            if (ms.session_ready_email_sent_at or "").strip():
                logger.debug(
                    "Sesión %s: session_ready_email ya fue enviado el %s. Skip.",
                    session_id, ms.session_ready_email_sent_at,
                )
                return False

            pipeline_failed_check = bool((ms.processing_error or "").strip())
            summary_present = bool((ms.raw_summary or "").strip())

            # GATING: si el pipeline está OK pero el summary aún no llegó,
            # NO enviamos el correo. Punto. La regla del producto es no
            # notificar a medias: el admin recibirá el correo cuando el
            # summary esté completo (vía el cron `check_pending_summaries`
            # que dispara este mismo método con force=True al confirmar
            # llegada). Si Fireflies nunca entrega, el correo nunca sale.
            if not force and not pipeline_failed_check and not summary_present:
                logger.info(
                    "Sesión %s: pipeline OK pero summary aún vacío. "
                    "Correo NO enviado — esperando que el cron confirme "
                    "summary nativo desde Fireflies.",
                    session_id,
                )
                return False

            project_name = "General"
            if ms.project_id:
                from models import Project
                p = db.get(Project, ms.project_id)
                if p:
                    project_name = p.name

            auto_enabled, timeout_minutes = _resolve_auto_dispatch_policy(
                db, tenant_id, ms.project_id,
            )

            # Conteo de tareas + faltantes de email.
            total_tasks = db.exec(
                select(ActionItem).where(ActionItem.session_id == session_id)
            ).all()
            total_task_count = len(total_tasks)
            missing_task_emails, missing_participants = _count_missing_emails_for_session(
                db, session_id,
            )

            pipeline_failed = bool((ms.processing_error or "").strip())
            pipeline_error_summary = (ms.processing_error or "").strip()[:400]

            admin_recipients = _resolve_admin_recipients(db, tenant_id)
            if not admin_recipients:
                logger.info(
                    "Sesión %s: no hay admins activos en tenant %s para correo post-pipeline.",
                    session_id, tenant_id,
                )
                return False

            frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:4200").rstrip("/")
            session_url = f"{frontend_url}/admin/curation/{session_id}"

            email_svc = EmailService(db=db, tenant_id=tenant_id)
            ok_any = False
            for email, name in admin_recipients:
                try:
                    await email_svc.send_session_received_email(
                        to_email=email,
                        admin_name=name,
                        session_title=ms.title or "Sin título",
                        project_name=project_name,
                        session_url=session_url,
                        auto_dispatch_enabled=auto_enabled,
                        timeout_minutes=timeout_minutes,
                        total_tasks=total_task_count,
                        missing_task_emails=missing_task_emails,
                        missing_participants=missing_participants,
                        pipeline_failed=pipeline_failed,
                        pipeline_error_summary=pipeline_error_summary,
                    )
                    ok_any = True
                except Exception:
                    logger.exception(
                        "Falló envío de correo post-pipeline a %s para sesión %s",
                        email, session_id,
                    )

            # Marca idempotente: con que UNO haya salido, no reintentamos.
            if ok_any:
                ms2 = db.get(MeetingSession, session_id)
                if ms2:
                    ms2.session_ready_email_sent_at = datetime.now().isoformat()
                    db.add(ms2)
                    db.commit()

            # Notificación in-app a los admins también.
            try:
                from services.notification_service import notify_admins_session_processed
                notify_admins_session_processed(
                    db=db,
                    tenant_id=tenant_id,
                    session_id=session_id,
                    session_title=ms.title or "Sin título",
                    pipeline_failed=pipeline_failed,
                    missing_task_emails=missing_task_emails,
                    missing_participants=missing_participants,
                )
            except Exception:
                logger.exception(
                    "Falló notificación in-app post-pipeline para sesión %s", session_id,
                )
            return ok_any
    except Exception:
        logger.exception(
            "_send_session_ready_email crash inesperado para sesión %s", session_id,
        )
        return False


# Alias retrocompat — algunos imports legacy todavía usan el nombre viejo.
_send_initial_admin_email = _send_session_ready_email


def _extract_native_summary(summary_obj, lang: Optional[str] = None) -> str:
    """Compone el resumen ejecutivo a partir de los campos nativos de Fireflies.

    `lang` localiza los section headers (es/ca/en). Default 'es' por compat
    con call sites que aún no resuelven el idioma del tenant.
    """
    from services.i18n_pipeline import section_headers
    h = section_headers(lang)

    if not isinstance(summary_obj, dict):
        return str(summary_obj or "").strip()

    parts: list[str] = []

    overview = (summary_obj.get("overview") or "").strip()
    if overview:
        parts.append(f"### {h['general_summary']}\n{overview}")

    bullet_gist = (summary_obj.get("bullet_gist") or "").strip()
    if bullet_gist:
        parts.append(f"### {h['key_points']}\n{bullet_gist}")

    notes = (summary_obj.get("notes") or "").strip()
    if notes:
        parts.append(f"### {h['notes']}\n{notes}")

    short_summary = (summary_obj.get("short_summary") or "").strip()
    if short_summary and not parts:
        parts.append(f"### {h['executive_summary']}\n{short_summary}")

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

    # Resto: tareas por action_item (Trello/Jira/ClickUp/Azure). Si falla
    # algún despacho, emitimos UNA sola notif al tenant (no una por tarea
    # — se vuelve ruido) usando el routing como entity para deduplicar.
    routing_failures = 0
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
            routing_failures += 1

    if routing_failures > 0 and action_items:
        try:
            from services.notification_service import (
                notify_admins,
                KIND_ROUTING_FAILED,
            )
            tenant_id_local = action_items[0].tenant_id
            notify_admins(
                db,
                tenant_id=tenant_id_local,
                kind=KIND_ROUTING_FAILED,
                title=f"Falló el envío a {routing.destination_type}",
                body=(
                    f"{routing_failures} de {len(action_items)} tareas no pudieron "
                    f"sincronizarse. Revisa la configuración de la integración."
                ),
                link_to="/admin/settings",
                entity_type="routing",
                entity_id=routing.id,
            )
        except Exception:
            logger.exception("No se pudo emitir notif routing_failed")


# ---------------------------------------------------------------------------
# Background processing
# ---------------------------------------------------------------------------


async def process_transcript_background(
    session_id: int,
    transcript_id: str,
    payload_data: dict,
    *,
    suppress_email: bool = False,
) -> None:
    """Llamado vía BackgroundTask. Trae datos nativos de Fireflies, los
    persiste en la sesión y luego invoca el pipeline IA común.

    `suppress_email=True` evita que esta función llame a
    _send_session_ready_email al terminar. Usado por el cron de retry
    para que solo se mande UN correo cuando el cron determine éxito,
    en lugar de potencialmente uno por cada retry fallido.
    """
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
            # Precedencia para el título:
            # 1. Lo que diga el payload del webhook / el caller (retry).
            # 2. Lo que ya tenía la sesión en DB (preserve en retries cuando
            #    el caller no manda nada explícito).
            # 3. Lo que devuelva Fireflies más adelante (línea ~466).
            # 4. Default genérico solo si ninguna fuente trajo nada.
            title = (
                payload_data.get("title")
                or data_obj.get("title")
                or (new_session.title if new_session else "")
                or "Reunión Sin Título"
            )

            # Fecha que ya tenía la sesión (set en el handler inicial). La
            # respetamos como base — el webhook entrante normalmente la trae,
            # y solo la PISAMOS si la API de Fireflies devuelve un dateString
            # válido (más confiable que el payload del webhook).
            existing_date = new_session.date or ""
            date_from_payload = _pick_first_date(
                payload_data.get("dateString"),
                payload_data.get("date"),
                payload_data.get("createdAt"),
                payload_data.get("meeting_date"),
                data_obj.get("dateString"),
                data_obj.get("date"),
                data_obj.get("createdAt"),
                data_obj.get("meeting_date"),
            )

            raw_transcript = str(payload_data.get("transcript", "")).strip()
            raw_summary = ""

            # ---------- 1. Pull native data from Fireflies ----------
            # API key viene del IntegrationSetting del tenant (lo que configura
            # el admin por la UI). Fallback a env var para dev.
            from services.fireflies_service import get_fireflies_api_key
            ff_api_key = get_fireflies_api_key(db, new_session.tenant_id)
            if not ff_api_key:
                logger.error(
                    "No hay Fireflies API key para tenant_id=%s (ni en UI ni en env). "
                    "El transcript %s se procesará solo con lo que ya esté en DB.",
                    new_session.tenant_id, transcript_id,
                )
            fireflies = FirefliesService(api_key=ff_api_key)
            ff_fetch_error = ""
            try:
                ff_data = await fireflies.get_transcript_data(transcript_id)
            except Exception as exc:  # noqa: BLE001
                ff_fetch_error = (
                    f"Fireflies API falló al traer transcript {transcript_id}: {exc}"
                )
                logger.exception(ff_fetch_error)
                ff_data = {}

            title = ff_data.get("title") or title

            # Precedencia FINAL para la fecha de la reunión:
            # 1) dateString que devuelve la API de Fireflies (fuente de verdad).
            # 2) Lo que ya teníamos en DB (set por el handler inicial).
            # 3) Lo que pueda venir en este payload de fondo.
            # 4) Como último recurso 'ahora' UTC + log de error explícito.
            date_str = _pick_first_date(
                ff_data.get("dateString"),
                existing_date,
                date_from_payload,
            )
            if not date_str:
                date_str = datetime.now(timezone.utc).isoformat()
                logger.error(
                    "No se pudo determinar fecha real para transcript %s. "
                    "Fireflies dateString=%r, payload date=%r, existing=%r. "
                    "Guardando 'ahora' como placeholder.",
                    transcript_id,
                    ff_data.get("dateString"),
                    date_from_payload,
                    existing_date,
                )

            if not raw_transcript or len(raw_transcript) < 10:
                sentences = ff_data.get("sentences") or []
                raw_transcript = "\n".join(
                    f"[{s.get('speaker_name', 'Speaker')}] {s.get('text', '')}"
                    for s in sentences
                ).strip()

            # Resumen ejecutivo NATIVO de Fireflies (cuando aplica).
            # Política diferenciada por tier:
            # - paid : Fireflies eventualmente entrega → reintentar con backoff
            #          hasta 70s. Si igual viene vacío, NO generamos con Groq —
            #          dejamos que el cliente espere (Fireflies tarda minutos
            #          a veces) o use el botón refetch después.
            # - free : Fireflies NUNCA va a generar el summary → ni siquiera
            #          gastamos 70s esperando. Pasamos directo al fallback Groq.
            # - unknown (primera vez): tratamos como paid (espera + sin Groq).
            #   El probe de tier se hace abajo como side effect del refetch.
            # Idioma del tenant para localizar los section headers del
            # summary nativo ("Resumen General"/"Resum General"/"General
            # Summary"). Sin esto los tenants en catalán/inglés veían el
            # header hardcoded en español al inicio del acta.
            _tenant_lang = None
            try:
                from models import Tenant as _Tenant
                _t = db.get(_Tenant, new_session.tenant_id)
                if _t:
                    _tenant_lang = getattr(_t, "default_language", None)
            except Exception:
                _tenant_lang = None
            raw_summary = _extract_native_summary(
                ff_data.get("summary"), lang=_tenant_lang,
            )

            from services.fireflies_service import (
                fetch_native_summary,
                get_or_detect_fireflies_tier,
            )

            tier = "unknown"
            if ff_api_key:
                tier = await get_or_detect_fireflies_tier(
                    db, new_session.tenant_id, ff_api_key,
                    transcript_id_for_probe=transcript_id,
                )
                logger.info("Fireflies tier para tenant %s: %s", new_session.tenant_id, tier)

            if not raw_summary and ff_api_key:
                if tier == "free":
                    logger.info(
                        "Tenant %s en plan free → saltando retries de Fireflies, "
                        "Groq generará el summary desde transcript.",
                        new_session.tenant_id,
                    )
                    # raw_summary se queda vacío → el bloque Groq fallback abajo lo genera.
                else:
                    logger.info(
                        "Tier=%s — esperando summary de Fireflies con backoff…",
                        tier,
                    )
                    raw_summary = await fetch_native_summary(
                        transcript_id,
                        api_key=ff_api_key,
                        max_attempts=3,
                        backoff_base_sec=10.0,  # 10s, 20s, 40s = ~70s
                        lang=_tenant_lang,
                    )

            # Limpieza cosmética del summary con Groq (traduce headers, quita
            # asteriscos, elimina referencias tipo [Fuente: ...]). Si Groq
            # falla, NO perdemos el summary original — preferimos mostrarlo
            # crudo a perderlo.
            groq = GroqLLMService()
            summary_clean_error = ""
            if raw_summary:
                try:
                    raw_summary = await groq.clean_native_summary(
                        raw_summary, target_lang=_tenant_lang,
                    )
                except Exception as exc:  # noqa: BLE001
                    summary_clean_error = (
                        f"Groq clean_native_summary falló (se mantiene crudo): {exc}"
                    )
                    logger.warning(summary_clean_error)
            elif tier == "free":
                # Plan free de Fireflies → el summary NUNCA va a llegar de
                # ahí. Generamos desde cero con Groq usando el transcript.
                if raw_transcript and len(raw_transcript) > 50:
                    logger.info(
                        "Tenant en plan free, generando summary con Groq desde transcript (%s chars).",
                        len(raw_transcript),
                    )
                    try:
                        # Mapeamos código de tenant ('es'/'ca'/'en') al nombre
                        # humano que el LLM espera para inyectar en el prompt.
                        _ln = {"es": "Español", "ca": "Català", "en": "English"}.get(
                            (_tenant_lang or "es").lower()[:2], "Español"
                        )
                        raw_summary = await groq.generate_summary_from_transcript(
                            raw_transcript,
                            title=title,
                            language=_ln,
                        )
                        if raw_summary:
                            logger.info(
                                "Summary generado por Groq para %s (free tier): %s chars.",
                                transcript_id, len(raw_summary),
                            )
                    except Exception as exc:  # noqa: BLE001
                        summary_clean_error = (
                            f"Groq generate_summary_from_transcript falló: {exc}"
                        )
                        logger.warning(summary_clean_error)
            else:
                # Plan paid (o tier unknown) y Fireflies devolvió vacío tras
                # retries: NO generamos con Groq. La cuenta paga eventualmente
                # entregará el summary nativo (puede tardar varios minutos
                # para reuniones largas). El admin puede usar el botón
                # "Traer resumen de Fireflies" más tarde.
                if not raw_transcript:
                    logger.info(
                        "Sin transcript ni summary de Fireflies para %s; "
                        "se queda vacío hasta que el admin reintente.",
                        transcript_id,
                    )
                else:
                    logger.info(
                        "Tier=%s para %s, Fireflies aún sin summary; "
                        "se preserva vacío para esperar entrega nativa.",
                        tier, transcript_id,
                    )

            # Doble-check: el valor que estamos a punto de persistir DEBE
            # poder leerse de vuelta. Si por algún motivo se rompió, lo
            # forzamos a un ISO válido antes de tocar DB para que el frontend
            # nunca reciba basura.
            verified = _normalize_meeting_date(date_str)
            if not verified:
                logger.error(
                    "Fecha final inválida para transcript %s: %r. Persistiendo "
                    "'ahora' UTC para evitar fila corrupta.",
                    transcript_id,
                    date_str,
                )
                verified = datetime.now(timezone.utc).isoformat()

            new_session.title = title
            new_session.date = verified
            new_session.raw_transcript = raw_transcript
            new_session.raw_summary = raw_summary
            new_session.status = "pending"

            # Si Fireflies o el cleanup del summary fallaron, lo dejamos
            # registrado en la sesión para que el admin lo vea y/o el cron
            # de retry lo pueda reintentar después. NO bloquea el pipeline IA
            # porque puede que el transcript ya venga en el payload del
            # webhook (caso común para tests / re-envíos).
            pre_pipeline_errors = []
            if ff_fetch_error:
                pre_pipeline_errors.append(f"fireflies_api: {ff_fetch_error[:300]}")
            if summary_clean_error:
                pre_pipeline_errors.append(
                    f"summary_clean: {summary_clean_error[:300]}"
                )
            if not raw_transcript or len(raw_transcript) < 10:
                pre_pipeline_errors.append(
                    "transcript: vacío tras pull de Fireflies y payload"
                )
            if pre_pipeline_errors:
                new_session.processing_error = "; ".join(pre_pipeline_errors)[:2000]

            db.add(new_session)
            db.commit()
            db.refresh(new_session)

            # ---------- 2. Pipeline IA común (Groq insights + OpenAI tareas) ----------
            # process_session_with_ai sobreescribe processing_error con el
            # resultado de SUS pasos, así que cualquier error pre-pipeline
            # solo cuenta si la sesión no llega a ejecutar IA (ej. transcript
            # vacío). Eso es lo que queremos: el estado final refleja el
            # estado más reciente del pipeline.
            await process_session_with_ai(db, new_session.id)

            # ---------- 3. Routing externo (Trello/Jira/ClickUp/Azure) ----------
            # Política de seguridad: si el pipeline IA dejó algún error
            # (`processing_error` no vacío), NO despachamos automáticamente
            # nada. El admin tiene que entrar, presionar el botón de
            # "Reintentar análisis IA" y, sólo cuando todo termine OK, podrá
            # aprobar manualmente el envío desde la curación o esperar al
            # cron de auto-dispatch (que también respeta este flag).
            db.refresh(new_session)
            if (new_session.processing_error or "").strip():
                logger.warning(
                    "Sesión %s incompleta (processing_error='%s'). "
                    "Bloqueando auto-dispatch — el admin debe reintentar el "
                    "pipeline antes de que se envíe la data a integraciones.",
                    new_session.id,
                    new_session.processing_error[:200],
                )
            else:
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
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Error procesando transcript %s en background", transcript_id
            )
            logger.debug(traceback.format_exc())
            # NUNCA dejar la sesión colgada en status='processing' si algo se
            # cayó arriba del pipeline. Marcamos error explícito y volvemos a
            # 'pending' para que el frontend pueda mostrar el banner rojo +
            # botón de reintentar otra vez.
            try:
                stuck = db.get(MeetingSession, session_id)
                if stuck:
                    stuck.processing_error = (
                        f"background_task_crashed: {type(exc).__name__}: {str(exc)[:300]}"
                    )
                    if stuck.status == "processing":
                        stuck.status = "pending"
                    db.add(stuck)
                    db.commit()
            except Exception:
                logger.exception(
                    "No pude marcar la sesión %s como fallida tras crash.",
                    session_id,
                )

    # Correo + notif POST-pipeline (regla del producto).
    # Se ejecuta SIEMPRE: con pipeline OK avisa "lista para enviar/curar",
    # con pipeline fallido avisa "hubo un error, entrá a reintentar". El
    # método es idempotente (no reenvía si ya se envió antes).
    #
    # `suppress_email=True` desactiva esto cuando el cron retry nos llama
    # — el cron se encarga del email post-success por su cuenta para evitar
    # races entre procesamiento y idempotencia.
    if suppress_email:
        logger.debug(
            "Sesión %s: suppress_email=True, no se llama a _send_session_ready_email "
            "desde process_transcript_background.",
            session_id,
        )
        return

    try:
        # tenant_id real puede no estar en scope si crasheó muy temprano —
        # lo resolvemos desde la sesión.
        with Session(engine) as db_done:
            ms_done = db_done.get(MeetingSession, session_id)
            if ms_done and ms_done.tenant_id:
                await _send_session_ready_email(session_id, ms_done.tenant_id)
    except Exception:
        logger.exception(
            "No pude enviar session_ready_email para sesión %s", session_id,
        )


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
    # Normalizamos a ISO-8601 desde TODOS los lugares posibles donde Fireflies
    # podría haber puesto la fecha. Solo si NINGÚN candidato es válido caemos
    # explícitamente a "ahora" y lo dejamos registrado en logs para auditar.
    date_str = _pick_first_date(
        payload.get("dateString"),
        payload.get("date"),
        payload.get("createdAt"),
        payload.get("meeting_date"),
        data_obj.get("dateString"),
        data_obj.get("date"),
        data_obj.get("createdAt"),
        data_obj.get("meeting_date"),
    )
    if not date_str:
        fallback = datetime.now(timezone.utc).isoformat()
        logger.warning(
            "Webhook Fireflies SIN fecha legible (transcript_id=%s). Cae a 'ahora' "
            "como placeholder; el background task intentará reemplazarla con dateString.",
            transcript_id,
        )
        date_str = fallback

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

    # Notificación inmediata: "Nueva sesión recibida". Después, cuando
    # el background_task complete, dispara session_processed (otra notif).
    try:
        from services.notification_service import (
            notify_admins,
            KIND_SESSION_RECEIVED,
        )
        notify_admins(
            db,
            tenant_id=tenant_id,
            kind=KIND_SESSION_RECEIVED,
            title=f"Nueva sesión recibida: {title[:120]}",
            body="La IA está procesando la transcripción. Te avisaremos cuando termine.",
            link_to=f"/admin/curation/{new_session.id}",
            entity_type="session",
            entity_id=new_session.id,
        )
    except Exception:
        logger.exception("No se pudo emitir notif de session_received para %s", new_session.id)

    # Regla del producto: NO enviar correo al admin apenas llega el webhook.
    # Primero se procesa la sesión con IA, y SÓLO al terminar el pipeline se
    # envía el correo "Sesión procesada y lista" con la info real (tareas
    # detectadas, correos faltantes, modo automático/manual). Ese correo lo
    # dispara `process_transcript_background` al final, vía
    # `_send_session_ready_email`.
    background_tasks.add_task(
        process_transcript_background, new_session.id, transcript_id, payload
    )

    return {
        "status": "accepted",
        "message": (
            f"Transcript/Meeting {transcript_id} programado para procesar en background."
        ),
    }


# ---------------------------------------------------------------------------
# Retry / recovery endpoints (Sprint Estabilidad — fix 'procesos a medias')
# ---------------------------------------------------------------------------


@router.post("/sessions/{session_id}/retry")
async def retry_session_pipeline(
    session_id: int,
    background_tasks: BackgroundTasks,
    rehydrate_from_fireflies: bool = False,
    db: Session = Depends(get_session),
    tenant: Tenant = Depends(get_current_tenant),
    _admin: User = Depends(require_admin),
):
    """Reintenta el pipeline IA sobre una sesión existente.

    Modos:
      - default (`rehydrate_from_fireflies=false`): re-ejecuta solo
        `process_session_with_ai` con lo que ya hay en DB. Útil cuando solo
        falló la extracción de tareas (OpenAI) y no queremos volver a
        consultar Fireflies.
      - `rehydrate_from_fireflies=true`: vuelve a traer transcript +
        summary nativo desde Fireflies y luego re-ejecuta IA. Útil cuando
        la sesión llegó incompleta (ej. el transcript estaba vacío).
    """
    session_obj = db.get(MeetingSession, session_id)
    if not session_obj or session_obj.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")

    # Marcamos la sesión como "en proceso" para que el frontend pueda
    # distinguir "estoy esperando que termine el retry" vs "retry terminó".
    # status='processing' es el signal de in-flight; processing_error y
    # processing_completed_at se limpian para que el resultado del pipeline
    # quede inequívoco.
    session_obj.status = "processing"
    session_obj.processing_error = ""
    session_obj.processing_completed_at = ""
    db.add(session_obj)
    db.commit()

    if rehydrate_from_fireflies:
        if not session_obj.fireflies_id:
            raise HTTPException(
                status_code=400,
                detail="La sesión no tiene fireflies_id; no se puede rehidratar.",
            )
        # Reusamos exactamente el mismo flujo del webhook (background task)
        # con un payload que preserva el título actual (para no clobbearlo
        # si Fireflies devuelve algo distinto o vacío).
        preserve_payload = {"title": session_obj.title} if session_obj.title else {}
        background_tasks.add_task(
            process_transcript_background,
            session_obj.id,
            session_obj.fireflies_id,
            preserve_payload,
        )
        return {
            "status": "queued",
            "mode": "rehydrate_from_fireflies",
            "session_id": session_obj.id,
        }

    # Re-ejecutar solo el pipeline IA con lo que ya hay en DB.
    background_tasks.add_task(_run_ai_pipeline_in_background, session_obj.id)
    return {
        "status": "queued",
        "mode": "ai_only",
        "session_id": session_obj.id,
    }


@router.get("/sessions/incomplete")
async def list_incomplete_sessions(
    db: Session = Depends(get_session),
    tenant: Tenant = Depends(get_current_tenant),
    _admin: User = Depends(require_admin),
):
    """Lista las sesiones del tenant que el pipeline NO terminó OK.

    Una sesión se considera incompleta si:
      - `processing_error` no está vacío, O
      - `processing_completed_at` está vacío (el pipeline nunca terminó).
    """
    q = (
        select(MeetingSession)
        .where(MeetingSession.tenant_id == tenant.id)
        .order_by(MeetingSession.id.desc())
    )
    rows = db.exec(q).all()
    incomplete_rows = [
        s for s in rows
        if (s.processing_error or "") or not (s.processing_completed_at or "")
    ]
    incomplete = []
    for s in incomplete_rows:
        tasks = db.exec(
            select(ActionItem).where(ActionItem.session_id == s.id)
        ).all()
        incomplete.append({
            "id": s.id,
            "title": s.title,
            "date": s.date,
            "fireflies_id": s.fireflies_id,
            "status": s.status,
            "processing_error": s.processing_error or "",
            "processing_attempts": s.processing_attempts or 0,
            "processing_completed_at": s.processing_completed_at or "",
            "has_transcript": bool((s.raw_transcript or "").strip()),
            "has_summary": bool((s.raw_summary or "").strip()),
            "has_decisions": bool((s.processed_decisions or "").strip()),
            "tasks_count": len(tasks),
        })
    return {"count": len(incomplete), "sessions": incomplete}


@router.post("/sessions/{session_id}/refetch-summary")
async def refetch_session_summary(
    session_id: int,
    db: Session = Depends(get_session),
    tenant: Tenant = Depends(get_current_tenant),
    _admin: User = Depends(require_admin),
):
    """Re-baja SOLO el resumen ejecutivo nativo desde Fireflies.

    Endpoint focalizado: no toca transcript, decisiones, riesgos, asistentes
    ni tareas. Útil cuando el summary llegó vacío del webhook (caso típico:
    Fireflies aún no había generado el summary asincrónico cuando el webhook
    se disparó). Mucho más barato que `rehydrate_from_fireflies` porque no
    re-corre el pipeline IA.
    """
    from services.fireflies_service import (
        get_fireflies_api_key,
        refetch_summary_for_session,
    )

    session_obj = db.get(MeetingSession, session_id)
    if not session_obj or session_obj.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    if not session_obj.fireflies_id:
        raise HTTPException(
            status_code=400,
            detail="La sesión no tiene fireflies_id; no se puede refetchear.",
        )

    api_key = get_fireflies_api_key(db, tenant.id)
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail=(
                "No hay Fireflies API key configurada para este tenant. "
                "Configurala en Configuración → Integraciones."
            ),
        )

    try:
        ok = await refetch_summary_for_session(
            db, session_obj, api_key=api_key, clean_with_groq=True, max_attempts=3,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("refetch_summary falló para sesión %s", session_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    db.refresh(session_obj)
    return {
        "status": "ok" if ok else "empty",
        "session_id": session_id,
        "summary_chars": len(session_obj.raw_summary or ""),
        "message": (
            "Summary actualizado correctamente."
            if ok
            else "Ni Fireflies ni Groq pudieron generar summary. "
            "Verifica que la sesión tenga transcript cargado."
        ),
    }


async def _run_ai_pipeline_in_background(session_id: int) -> None:
    """Wrapper para correr `process_session_with_ai` con su propio Session.

    Necesario porque BackgroundTasks no comparte el Session de la request.
    """
    from database import engine

    with Session(engine) as db:
        try:
            await process_session_with_ai(db, session_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Retry de pipeline IA falló para sesión %s", session_id,
            )
            # Igual que en process_transcript_background: nunca dejar la
            # sesión colgada en status='processing'.
            try:
                stuck = db.get(MeetingSession, session_id)
                if stuck:
                    stuck.processing_error = (
                        f"pipeline_crashed: {type(exc).__name__}: {str(exc)[:300]}"
                    )
                    if stuck.status == "processing":
                        stuck.status = "pending"
                    db.add(stuck)
                    db.commit()
            except Exception:
                logger.exception(
                    "No pude marcar la sesión %s como fallida tras crash.",
                    session_id,
                )
