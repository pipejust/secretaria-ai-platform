import os
import resend
import json
from typing import Optional
from jinja2 import Environment, FileSystemLoader
from sqlmodel import Session, select
from models import IntegrationSetting
from services import branding_service

# Resend se configura SIEMPRE desde /admin/settings (UI) →
# IntegrationSetting('smtp').config_json.{apiKey, senderEmail}.
# Sin DB, no se envían correos: se imprime el HTML en consola (modo dev).
DEFAULT_FROM_EMAIL = "no-reply@acten.local"
DEFAULT_TO_EMAIL = os.environ.get("TO_EMAIL", "felipesof@gmail.com")


# ────────────────────────────────────────────────────────────────────────────
# Resolución de URLs absolutas para imágenes en emails.
#
# IMPORTANTE: Gmail, Outlook y la mayoría de clientes de email BLOQUEAN
# `<img src="data:image/png;base64,...">` por seguridad anti-spam. Si embebés
# el logo como data URL, el email llega SIN logo (placeholder roto).
#
# Solución: servir el logo desde una URL pública HTTPS y referenciarla
# absolutamente en el template. El cliente de email descarga la imagen
# normalmente.
#
# `EMAIL_ASSETS_BASE_URL` permite override (útil si los assets viven en un
# CDN distinto al frontend). Default: `FRONTEND_URL` (que en prod apunta a
# `https://acten.app`). En dev local, usar el default público de prod
# garantiza que los emails de prueba tengan logo visible aunque se reciban
# en Gmail.
# ────────────────────────────────────────────────────────────────────────────
_PUBLIC_LOGO_BASE_URL_DEFAULT = "https://acten.app"


def _resolve_public_base_url() -> str:
    """URL absoluta donde están servidos los assets públicos del frontend.

    Orden de precedencia:
      1. `EMAIL_ASSETS_BASE_URL` (override explícito)
      2. `FRONTEND_URL` si es HTTPS (los clientes de mail rechazan HTTP)
      3. `https://acten.app` como fallback duro (siempre vivo en prod)
    """
    override = (os.environ.get("EMAIL_ASSETS_BASE_URL") or "").strip().rstrip("/")
    if override:
        return override
    fe = (os.environ.get("FRONTEND_URL") or "").strip().rstrip("/")
    if fe.startswith("https://"):
        return fe
    return _PUBLIC_LOGO_BASE_URL_DEFAULT


def _get_default_logo_url() -> str:
    """URL pública absoluta del logo default de Acten para emails.
    El archivo vive en `frontend/public/email-logo.png` y se sirve en
    `{base}/email-logo.png`. Garantiza compat con todos los clientes de mail.
    """
    return f"{_resolve_public_base_url()}/email-logo.png"


def _resolve_tenant_logo_url(branding: dict, tenant_id: int | None) -> str:
    """Convierte el `logo_data_url` del tenant (que en DB puede ser un
    `data:image/...;base64,...`) en una URL HTTPS pública apta para email.

    - Si el branding YA trae una URL absoluta http(s), se devuelve tal cual.
    - Si trae un data URL, se devuelve el endpoint backend que sirve ese
      logo decodificado: `{api_base}/branding/{tenant_id}/logo.png`.
    - Si no hay logo del tenant, fallback al logo default público de Acten.
    """
    raw = (branding.get("logo_data_url") or "").strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    if raw.startswith("data:") and tenant_id is not None:
        # Servimos el binario vía endpoint público — los clientes de email
        # NO renderizan data URLs.
        api_base = (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
        if api_base.startswith("https://"):
            return f"{api_base}/branding/{tenant_id}/logo.png"
        # Fallback: si el API no está accesible públicamente (dev), usamos
        # el logo default de Acten para que el email igual tenga marca.
        return _get_default_logo_url()
    return _get_default_logo_url()


class EmailService:
    def __init__(self, db: Session = None, tenant_id: int | None = None):
        """Multi-tenant: si `tenant_id` se pasa, las credenciales SMTP y la
        marca se leen de ESE tenant. Si no, fallback al tenant default.
        """
        self.tenant_id = tenant_id
        # Configurar Jinja2 para cargar plantillas desde el directorio local `templates`
        current_dir = os.path.dirname(os.path.abspath(__file__))
        templates_dir = os.path.join(os.path.dirname(current_dir), 'templates')
        self.jinja_env = Environment(loader=FileSystemLoader(templates_dir))
        
        import re
        import html
        def filter_linkify(text):
            if not text: return text
            safe_text = html.escape(str(text)).replace('\n', '<br>')
            # Python 3.11+ exige los flags inline `(?i)` al INICIO del patrón global.
            # Tenerlo en medio de un grupo (como estaba) lanza
            # `re.error: global flags not at the start of the expression at position 85`
            # y rompía /api/sessions/{id}/dispatch_emails (los correos quedaban en
            # status="failed" sin razón clara para el operador).
            regex = r'(?i)(?P<email>[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})|(?P<whatsapp>(?P<wa_prefix>whatsapp|wpp|wa\b|ws\b)(?P<wa_sep>\s*[:\-#]*\s*)(?P<wa_num>\+?[\d][\d\s\-\.]{6,15}\d))|(?P<phone>(?<!\w)\+?[\d][\d\s\-\.]{6,15}\d(?!\w))'
            def replacer(m):
                if m.group('email'):
                    addr = m.group('email')
                    return f'<a href="mailto:{addr}" style="color: #2563eb; text-decoration: underline;">{addr}</a>'
                elif m.group('whatsapp'):
                    prefix = m.group('wa_prefix')
                    sep = m.group('wa_sep')
                    raw_num = m.group('wa_num')
                    digits = re.sub(r"[^\d]", "", raw_num)
                    return f'{prefix}{sep}<a href="https://wa.me/{digits}" style="color: #16a34a; text-decoration: underline; font-weight: bold;">{raw_num}</a>'
                elif m.group('phone'):
                    raw_num = m.group('phone')
                    digits = re.sub(r"[^\d]", "", raw_num)
                    if len(digits) < 7: return raw_num
                    return f'<a href="tel:{digits}" style="color: #2563eb; text-decoration: underline; font-weight: bold;">{raw_num}</a>'
                return m.group(0)
            return re.sub(regex, replacer, safe_text)
            
        self.jinja_env.filters['linkify'] = filter_linkify
        
        self.api_key = None
        self.from_email = DEFAULT_FROM_EMAIL
        # Branding (white-label) — siempre presente en el contexto de Jinja
        # incluso si la DB no está disponible.
        self.branding = dict(branding_service.DEFAULT_BRANDING)

        if db:
            # Resuelve tenant: el explícito o, si no vino, el default ('acten').
            from database import DEFAULT_TENANT_SLUG
            from models import Tenant
            tid = self.tenant_id
            if tid is None:
                t = db.exec(select(Tenant).where(Tenant.slug == DEFAULT_TENANT_SLUG)).first()
                tid = t.id if t else None
                self.tenant_id = tid

            if tid is not None:
                smtp_query = select(IntegrationSetting).where(
                    IntegrationSetting.provider_name == 'smtp'
                ).where(IntegrationSetting.tenant_id == tid)
                setting = db.exec(smtp_query).first()
                if setting and setting.is_active:
                    try:
                        config = json.loads(setting.config_json)
                        if config.get("apiKey"):
                            self.api_key = config.get("apiKey")
                        if config.get("senderEmail"):
                            self.from_email = config.get("senderEmail")
                    except Exception as e:
                        print(f"Error parsing local SMTP settings: {e}")
                # Cargamos branding del tenant correcto.
                try:
                    self.branding = branding_service.get_branding(db, tid)
                except Exception as exc:
                    print(f"Error cargando branding para email: {exc}")

        # Logo: resolvemos a una URL HTTPS PÚBLICA porque los clientes de
        # email (Gmail/Outlook/etc.) bloquean `<img src="data:...">`.
        # `_resolve_tenant_logo_url`:
        #   - si el tenant tiene logo custom (data URL en DB) → URL del
        #     endpoint backend que lo sirve binario.
        #   - si no tiene → URL pública del logo default de Acten.
        self.branding["logo_data_url"] = _resolve_tenant_logo_url(
            self.branding, self.tenant_id
        )

        if self.api_key:
            resend.api_key = self.api_key

    async def _send_html_email(self, to_email: str, subject: str, html_content: str, attachments: list = None):
        """Método interno para despachar el correo utilizando Resend. Imprime el HTML en modo dev."""
        if self.api_key:
            try:
                payload = {
                    "from": self.from_email,
                    "to": to_email,
                    "subject": subject,
                    "html": html_content
                }
                if attachments:
                    payload["attachments"] = attachments
                    
                response = resend.Emails.send(payload)
                print(f"[OK] Email enviado a {to_email} (ID: {response.get('id', 'Unknown')})")
                return True
            except Exception as e:
                print(f"[FAIL] Error enviando email: {str(e)}")
                raise e
        

        else:
            # Modo Desarrollo: Simular envío e imprimir HTML en consola
            print(f"--- [SIM] SIMULACIÓN DE ENVÍO DE EMAIL ---")
            print(f"To: {to_email}")
            print(f"Subject: {subject}")
            print(f"Body (HTML):")
            print(html_content)
            print("---------------------------------------")
            return True

    async def send_action_item_email(
        self, 
        to_email: str, 
        owner_name: str, 
        task_title: str, 
        task_description: str, 
        project_name: str, 
        due_date: str = None, 
        attachments: list = None,
        summary: str = None,
        decisions: str = None,
        risks: str = None,
        agreements: str = None
    ):
        template = self.jinja_env.get_template('email_action_item.html')
        html_content = template.render(
            owner_name=owner_name,
            task_title=task_title,
            task_description=task_description,
            project_name=project_name,
            due_date=due_date,
            summary=summary,
            decisions=decisions,
            risks=risks,
            agreements=agreements,
            current_year=2026,
            brand=self.branding,
        )
        await self._send_html_email(to_email, f"Nueva tarea asignada: {task_title}", html_content, attachments=attachments)

    async def send_action_items_batch_email(
        self,
        to_email: str,
        owner_name: str,
        tasks: list,
        project_name: str,
        attachments: list = None,
        summary: str = None,
        decisions: str = None,
        risks: str = None,
        agreements: str = None,
        raw_transcript: str = None,
        session_title: str = None,
    ):
        """Email completo al responsable. Incluye:
          - Sus tareas (sólo las suyas, no de otros responsables)
          - Botones "Añadir al calendario" por tarea (Google + Outlook + .ics)
          - Resumen ejecutivo, decisiones, riesgos y acuerdos del acta
          - Transcripción completa de la reunión (en el cuerpo del correo)
          - PDF/Word del acta adjunto (sin transcripción)
        """
        from urllib.parse import quote
        import datetime as _dt

        template = self.jinja_env.get_template('email_action_items_batch.html')

        def _to_ics_date(due: str) -> Optional[str]:
            """Devuelve `YYYYMMDD` si la fecha es válida, si no None."""
            if not due:
                return None
            s = str(due).strip()
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S"):
                try:
                    return _dt.datetime.strptime(s[:len(fmt)+2 if 'T' in fmt else 10], fmt).strftime("%Y%m%d")
                except (ValueError, TypeError):
                    continue
            # ISO con timezone
            try:
                return _dt.datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%Y%m%d")
            except (ValueError, TypeError):
                return None

        # Prepare tasks for rendering with calendar URLs
        rendered_tasks = []
        for t in tasks:
            title = getattr(t, 'title', '') or ''
            description = getattr(t, 'description', '') or ''
            due_date = getattr(t, 'due_date', None)
            ics_date = _to_ics_date(due_date) if due_date else None

            # Google Calendar quick-add — fechas all-day si tenemos due_date.
            gcal_url = ""
            outlook_url = ""
            if ics_date:
                gcal_url = (
                    "https://calendar.google.com/calendar/render?action=TEMPLATE"
                    f"&text={quote(title)}"
                    f"&dates={ics_date}/{ics_date}"
                    f"&details={quote((description + chr(10) + chr(10) + 'Proyecto: ' + project_name)[:1500])}"
                )
                # Outlook web — usa formato ISO yyyy-MM-ddT00:00:00.
                iso_day = f"{ics_date[:4]}-{ics_date[4:6]}-{ics_date[6:8]}"
                outlook_url = (
                    "https://outlook.live.com/calendar/0/deeplink/compose?path=/calendar/action/compose"
                    "&rru=addevent"
                    f"&subject={quote(title)}"
                    f"&startdt={iso_day}T09:00:00"
                    f"&enddt={iso_day}T10:00:00"
                    f"&body={quote(description[:1500])}"
                    "&allday=false"
                )
            else:
                # Sin fecha: igual creamos un evento "ahora" para que el clic
                # abra el form pre-rellenado y el usuario fije la fecha.
                gcal_url = (
                    "https://calendar.google.com/calendar/render?action=TEMPLATE"
                    f"&text={quote(title)}"
                    f"&details={quote((description + chr(10) + 'Proyecto: ' + project_name)[:1500])}"
                )

            rendered_tasks.append({
                "title": title,
                "description": description,
                "due_date": due_date,
                "gcal_url": gcal_url,
                "outlook_url": outlook_url,
                "has_date": bool(ics_date),
            })

        task_count = len(rendered_tasks)
        plural = "s" if task_count > 1 else ""

        html_content = template.render(
            owner_name=owner_name,
            tasks=rendered_tasks,
            task_count=task_count,
            project_name=project_name,
            session_title=session_title or project_name,
            summary=summary,
            decisions=decisions,
            risks=risks,
            agreements=agreements,
            raw_transcript=raw_transcript or "",
            current_year=2026,
            brand=self.branding,
        )
        await self._send_html_email(to_email, f"Tienes {task_count} nueva{plural} tarea{plural} asignada{plural} en: {project_name}", html_content, attachments=attachments)

    async def send_session_received_email(
        self,
        to_email: str,
        admin_name: str,
        session_title: str,
        project_name: str,
        session_url: str,
        auto_dispatch_enabled: bool,
        timeout_minutes: int,
        total_tasks: int = 0,
        missing_task_emails: int = 0,
        missing_participants: int = 0,
        pipeline_failed: bool = False,
        pipeline_error_summary: str = "",
    ):
        """Email POST-pipeline al admin del proyecto.

        Flujo (alineado con la regla del producto):
          1. Fireflies entrega la sesión vía webhook.
          2. El pipeline IA corre PRIMERO (transcribe + extrae tareas + decisiones).
          3. CUANDO TERMINA el pipeline, ESTE correo se envía indicando si:
             - El pipeline corrió OK o falló (`pipeline_failed`, `pipeline_error_summary`).
             - Hay tareas sin email de responsable (`missing_task_emails`).
             - Hay participantes sin email (`missing_participants`).
             - El modo es manual o automático (con su timeout).
        El admin sabe en un vistazo qué tiene que arreglar antes de que el cron
        intente el auto-dispatch.
        """
        platform_name = self.branding.get("platform_name") or "Acten"
        if pipeline_failed:
            subject = f"[Error] Procesando sesión en {platform_name}: {session_title or 'Sin título'}"
        else:
            subject = f"Sesión lista en {platform_name}: {session_title or 'Sin título'}"
        template = self.jinja_env.get_template('email_session_received.html')
        html_content = template.render(
            admin_name=admin_name or '',
            session_title=session_title or 'Sin título',
            project_name=project_name or 'General',
            session_url=session_url or '#',
            auto_dispatch_enabled=bool(auto_dispatch_enabled),
            timeout_minutes=int(timeout_minutes or 60),
            total_tasks=int(total_tasks or 0),
            missing_task_emails=int(missing_task_emails or 0),
            missing_participants=int(missing_participants or 0),
            pipeline_failed=bool(pipeline_failed),
            pipeline_error_summary=(pipeline_error_summary or "").strip()[:400],
            current_year=2026,
            brand=self.branding,
        )
        await self._send_html_email(to_email, subject, html_content)

    async def send_auto_dispatch_blocked_email(
        self,
        to_email: str,
        admin_name: str,
        session_title: str,
        project_name: str,
        session_url: str,
        missing_task_emails: int,
        missing_participants: int,
        timeout_minutes: int,
    ):
        """Email cuando el cron llega al timeout y NO puede auto-despachar
        porque faltan correos en tareas o en participantes.

        Lo envía el job `check_and_dispatch_pending_sessions` y se manda una
        sola vez cada `WARNING_REPEAT_HOURS` (default 24h) por sesión, para no
        spamear al admin. El admin tiene que entrar a la curación, agregar los
        correos faltantes, y entonces el cron va a poder auto-despachar.
        """
        platform_name = self.branding.get("platform_name") or "Acten"
        subject = f"[Bloqueado] No se puede auto-enviar tareas: {session_title or 'Sin título'}"
        template = self.jinja_env.get_template('email_auto_dispatch_blocked.html')
        html_content = template.render(
            admin_name=admin_name or '',
            session_title=session_title or 'Sin título',
            project_name=project_name or 'General',
            session_url=session_url or '#',
            missing_task_emails=int(missing_task_emails or 0),
            missing_participants=int(missing_participants or 0),
            timeout_minutes=int(timeout_minutes or 60),
            current_year=2026,
            brand=self.branding,
        )
        await self._send_html_email(to_email, subject, html_content)

    async def send_auto_dispatch_done_email(
        self,
        to_email: str,
        admin_name: str,
        session_title: str,
        project_name: str,
        session_url: str,
        total_tasks: int,
    ):
        """Email cuando el cron despacha exitosamente (correos a responsables +
        tickets en plataformas conectadas).

        Le da al admin trazabilidad: "esto se envió solo a las HH:MM, mira
        cuántas tareas y correos salieron, abrí la sesión si querés ver el
        detalle". Se manda UNA SOLA VEZ por sesión (status → 'processed').
        """
        platform_name = self.branding.get("platform_name") or "Acten"
        subject = f"Tareas y correos enviados: {session_title or 'Sin título'}"
        template = self.jinja_env.get_template('email_auto_dispatch_done.html')
        html_content = template.render(
            admin_name=admin_name or '',
            session_title=session_title or 'Sin título',
            project_name=project_name or 'General',
            session_url=session_url or '#',
            total_tasks=int(total_tasks or 0),
            current_year=2026,
            brand=self.branding,
        )
        await self._send_html_email(to_email, subject, html_content)

    async def send_welcome_email(self, to_email: str, user_name: str, role: str, login_url: str = ""):
        template = self.jinja_env.get_template('email_welcome.html')
        frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:4200").rstrip('/')
        html_content = template.render(
            user_name=user_name,
            email=to_email,
            role=role,
            login_url=login_url or f"{frontend_url}/login",
            current_year=2026,
            brand=self.branding,
        )
        company = self.branding.get("company_name") or self.branding.get("platform_name") or "Acten"
        await self._send_html_email(to_email, f"¡Bienvenido a {company}!", html_content)

    async def send_two_factor_code_email(
        self,
        to_email: str,
        user_name: str,
        code: str,
        purpose: str = "login",
        ttl_minutes: int = 10,
        max_attempts: int = 5,
    ):
        """Email con el código OTP para activar o desafiar 2FA. Usa el
        template branded como TODOS los demás correos (logo + colores)."""
        template = self.jinja_env.get_template('email_two_factor_code.html')
        html_content = template.render(
            user_name=user_name,
            code=code,
            purpose=purpose,
            ttl_minutes=ttl_minutes,
            max_attempts=max_attempts,
            current_year=2026,
            brand=self.branding,
        )
        company = self.branding.get("company_name") or self.branding.get("platform_name") or "Acten"
        if purpose == "enable":
            subject = f"Código para activar 2FA en {company}"
        else:
            subject = f"Código de verificación · {company}"
        await self._send_html_email(to_email, subject, html_content)

    async def send_forgot_password_email(self, to_email: str, user_name: str, reset_token: str):
        # Bug histórico: faltaba cargar el template — el render usaba `template`
        # del scope previo (NameError en el primer envío). Lo cargamos aquí.
        template = self.jinja_env.get_template('email_forgot_password.html')
        frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:4200").rstrip('/')
        reset_url = f"{frontend_url}/reset-password?token={reset_token}"
        html_content = template.render(
            user_name=user_name,
            reset_url=reset_url,
            current_year=2026,
            brand=self.branding,
        )
        company = self.branding.get("company_name") or self.branding.get("platform_name") or "Acten"
        await self._send_html_email(to_email, f"Restablecer Contraseña - {company}", html_content)
