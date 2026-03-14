import os
import resend
from jinja2 import Environment, FileSystemLoader

# En un entorno real, manejar la config via `config.py/settings`
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "no-reply@secretaria-ai.com")

class EmailService:
    def __init__(self):
        # Configurar Jinja2 para cargar plantillas desde el directorio local `templates`
        current_dir = os.path.dirname(os.path.abspath(__file__))
        templates_dir = os.path.join(os.path.dirname(current_dir), 'templates')
        self.jinja_env = Environment(loader=FileSystemLoader(templates_dir))
        if RESEND_API_KEY:
            resend.api_key = RESEND_API_KEY

    async def _send_html_email(self, to_email: str, subject: str, html_content: str):
        """Método interno para despachar el correo utilizando Resend. Imprime el HTML en modo dev."""
        if RESEND_API_KEY:
            try:
                response = resend.Emails.send({
                    "from": FROM_EMAIL,
                    "to": to_email,
                    "subject": subject,
                    "html": html_content
                })
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

    async def send_action_item_email(self, to_email: str, owner_name: str, task_title: str, task_description: str, project_name: str, platform_url: str = ""):
        template = self.jinja_env.get_template('email_action_item.html')
        html_content = template.render(
            owner_name=owner_name,
            task_title=task_title,
            task_description=task_description,
            project_name=project_name,
            platform_url=platform_url or "https://secretaria.moshwasi.com",
            current_year=2026
        )
        await self._send_html_email(to_email, f"Nueva tarea asignada: {task_title}", html_content)
        
    async def send_welcome_email(self, to_email: str, user_name: str, role: str, login_url: str = ""):
        template = self.jinja_env.get_template('email_welcome.html')
        html_content = template.render(
            user_name=user_name,
            email=to_email,
            role=role,
            login_url=login_url or "https://secretaria.moshwasi.com/login",
            current_year=2026
        )
        await self._send_html_email(to_email, f"¡Bienvenido a Secretaría AI!", html_content)

    async def send_forgot_password_email(self, to_email: str, user_name: str, reset_token: str):
        template = self.jinja_env.get_template('email_forgot_password.html')
        # En el front-end crearemos la ruta /reset-password
        reset_url = f"https://secretaria.moshwasi.com/reset-password?token={reset_token}"
        html_content = template.render(
            user_name=user_name,
            reset_url=reset_url,
            current_year=2026
        )
        await self._send_html_email(to_email, f"Restablecer Contraseña - Secretaría AI", html_content)
