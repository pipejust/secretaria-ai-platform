import os
import resend
import json
from jinja2 import Environment, FileSystemLoader
from sqlmodel import Session, select
from models import IntegrationSetting

# En un entorno real, manejar la config via `config.py/settings`
DEFAULT_RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
DEFAULT_FROM_EMAIL = os.environ.get("FROM_EMAIL", "no-reply@notiva.com")
DEFAULT_TO_EMAIL = os.environ.get("TO_EMAIL", "felipesof@gmail.com")
class EmailService:
    def __init__(self, db: Session = None):
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
        
        self.api_key = DEFAULT_RESEND_API_KEY
        self.from_email = DEFAULT_FROM_EMAIL

        if db:
            setting = db.exec(select(IntegrationSetting).where(IntegrationSetting.provider_name == 'smtp')).first()
            if setting and setting.is_active:
                try:
                    config = json.loads(setting.config_json)
                    if config.get("apiKey"):
                        self.api_key = config.get("apiKey")
                    if config.get("senderEmail"):
                        self.from_email = config.get("senderEmail")
                except Exception as e:
                    print(f"Error parsing local SMTP settings: {e}")

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
                print(f"✅ Email enviado a {to_email} (ID: {response.get('id', 'Unknown')})")
                return True
            except Exception as e:
                print(f"❌ Error enviando email: {str(e)}")
                raise e
        

        else:
            # Modo Desarrollo: Simular envío e imprimir HTML en consola
            print(f"--- 📧 SIMULACIÓN DE ENVÍO DE EMAIL ---")
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
            current_year=2026
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
        agreements: str = None
    ):
        template = self.jinja_env.get_template('email_action_items_batch.html')
        
        # Prepare tasks for rendering
        rendered_tasks = []
        for t in tasks:
            rendered_tasks.append({
                "title": getattr(t, 'title', ''),
                "description": getattr(t, 'description', ''),
                "due_date": getattr(t, 'due_date', None)
            })
            
        task_count = len(rendered_tasks)
        plural = "s" if task_count > 1 else ""

        html_content = template.render(
            owner_name=owner_name,
            tasks=rendered_tasks,
            task_count=task_count,
            project_name=project_name,
            summary=summary,
            decisions=decisions,
            risks=risks,
            agreements=agreements,
            current_year=2026
        )
        await self._send_html_email(to_email, f"Tienes {task_count} nueva{plural} tarea{plural} asignada{plural} en: {project_name}", html_content, attachments=attachments)
        
    async def send_welcome_email(self, to_email: str, user_name: str, role: str, login_url: str = ""):
        template = self.jinja_env.get_template('email_welcome.html')
        frontend_url = getattr(settings, "frontend_url", "http://localhost:4200").rstrip('/')
        html_content = template.render(
            user_name=user_name,
            email=to_email,
            role=role,
            login_url=login_url or f"{frontend_url}/login",
            current_year=2026
        )
        await self._send_html_email(to_email, f"¡Bienvenido a Notiva!", html_content)

    async def send_forgot_password_email(self, to_email: str, user_name: str, reset_token: str):
        frontend_url = getattr(settings, "frontend_url", "http://localhost:4200").rstrip('/')
        # En el front-end crearemos la ruta /reset-password
        reset_url = f"{frontend_url}/reset-password?token={reset_token}"
        html_content = template.render(
            user_name=user_name,
            reset_url=reset_url,
            current_year=2026
        )
        await self._send_html_email(to_email, f"Restablecer Contraseña - Notiva", html_content)
