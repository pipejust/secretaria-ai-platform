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


async def _send_initial_admin_email(session_id: int, tenant_id: int) -> None:
    """Envía el correo inicial al/los admin/s del tenant en cuanto Fireflies
    deja una sesión nueva. Informa el modo (automático con timeout en minutos
    o manual) para que sepan cuánto tiempo tienen para curar.
    """
    import os
    from sqlmodel import Session
    from database import engine
    from models import MeetingSession, Project, User, Role, IntegrationSetting
    from services.email_service import EmailService

    try:
        with Session(engine) as db:
            ms = db.get(MeetingSession, session_id)
            if not ms:
                return
            project_name = "General"
            if ms.project_id:
                p = db.get(Project, ms.project_id)
                if p:
                    project_name = p.name

            # Cargar config de envío automático del tenant.
            ac_setting = db.exec(
                select(IntegrationSetting)
                .where(IntegrationSetting.tenant_id == tenant_id)
                .where(IntegrationSetting.provider_name == "autoCuration")
            ).first()
            auto_enabled = False
            timeout_minutes = 60
            if ac_setting:
                try:
                    cfg = json.loads(ac_setting.config_json or "{}")
                    auto_enabled = bool(cfg.get("isEnabled", False))
                    # Prioridad: minutos > horas legacy
                    if cfg.get("timeoutMinutes") is not None:
                        timeout_minutes = int(float(cfg.get("timeoutMinutes")))
                    elif cfg.get("timeoutHours") is not None:
                        timeout_minutes = int(float(cfg.get("timeoutHours")) * 60)
                except Exception:
                    pass

            # Override por proyecto (mismo patrón que cron_service).
            if ms.project_id:
                p = db.get(Project, ms.project_id)
                if p:
                    if p.auto_dispatch_enabled is not None:
                        auto_enabled = bool(p.auto_dispatch_enabled)
                    if p.auto_dispatch_timeout_hours is not None:
                        timeout_minutes = int(float(p.auto_dispatch_timeout_hours) * 60)

            # Buscar admins activos del tenant — usuarios con role "admin"
            # (por nombre o por flag is_platform_admin).
            admins = db.exec(
                select(User)
                .where(User.tenant_id == tenant_id)
                .where(User.is_active == True)
            ).all()
            admin_recipients: list[tuple[str, str]] = []
            for u in admins:
                role_name = ""
                if u.role_id:
                    r = db.get(Role, u.role_id)
                    role_name = (r.name if r else "").lower()
                # Heurística: cualquier rol que contenga "admin" o el flag de plataforma.
                if "admin" in role_name or getattr(u, "is_platform_admin", False):
                    if u.email:
                        admin_recipients.append((u.email, u.full_name or ""))
            if not admin_recipients:
                logger.info(
                    "Sesión %s: no se encontraron admins activos en tenant %s para correo inicial.",
                    session_id, tenant_id,
                )
                return

            frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:4200").rstrip("/")
            session_url = f"{frontend_url}/admin/curation/{session_id}"

            email_svc = EmailService(db=db, tenant_id=tenant_id)
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
                    )
                except Exception:
                    logger.exception(
                        "Falló envío de correo inicial a %s para sesión %s",
                        email, session_id,
                    )
    except Exception:
        logger.exception(
            "_send_initial_admin_email crash inesperado para sesión %s", session_id,
        )


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

            # Resumen ejecutivo NATIVO de Fireflies (no se genera con IA).
            # Fireflies genera el summary asincrónicamente — cuando el webhook
            # llega muy rápido tras la reunión, el summary puede no estar listo
            # todavía. Si la primera lectura viene vacía, reintentamos un par
            # de veces con backoff antes de aceptar que no hay summary.
            raw_summary = _extract_native_summary(ff_data.get("summary"))
            if not raw_summary and ff_api_key:
                logger.info(
                    "Summary vacío en primera lectura de %s, reintentando con backoff…",
                    transcript_id,
                )
                from services.fireflies_service import fetch_native_summary
                raw_summary = await fetch_native_summary(
                    transcript_id,
                    api_key=ff_api_key,
                    max_attempts=3,
                    backoff_base_sec=10.0,  # 10s, 20s, 40s = ~70s total worst case
                )

            # Limpieza cosmética del summary con Groq (traduce headers, quita
            # asteriscos, elimina referencias tipo [Fuente: ...]). Si Groq
            # falla, NO perdemos el summary original — preferimos mostrarlo
            # crudo a perderlo.
            groq = GroqLLMService()
            summary_clean_error = ""
            if raw_summary:
                try:
                    raw_summary = await groq.clean_native_summary(raw_summary)
                except Exception as exc:  # noqa: BLE001
                    summary_clean_error = (
                        f"Groq clean_native_summary falló (se mantiene crudo): {exc}"
                    )
                    logger.warning(summary_clean_error)

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

    # Correo INICIAL al admin del proyecto/tenant: "Sesión recibida, tienes
    # N minutos para curarla antes del envío automático". Se hace en background
    # para no bloquear el ack del webhook.
    background_tasks.add_task(
        _send_initial_admin_email, new_session.id, tenant_id,
    )

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
            else "Fireflies aún no tiene summary para esta sesión. "
            "Esperá unos minutos y volvé a intentar."
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
