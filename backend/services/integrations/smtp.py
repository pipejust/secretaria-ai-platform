import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
import os

class EmailNotificationService:
    def __init__(self, smtp_server: str, smtp_port: int, smtp_user: str, smtp_pass: str):
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_pass = smtp_pass

    def send_email_with_attachment(self, to_email: str, subject: str, body_html: str, attachment_path: str = None):
        """Envía un correo con el acta adjunta y las tareas asignadas."""
        msg = MIMEMultipart()
        msg['From'] = self.smtp_user
        msg['To'] = to_email
        msg['Subject'] = subject

        msg.attach(MIMEText(body_html, 'html'))
        
        if attachment_path and os.path.exists(attachment_path):
            with open(attachment_path, "rb") as f:
                part = MIMEApplication(f.read(), Name=os.path.basename(attachment_path))
                part['Content-Disposition'] = f'attachment; filename="{os.path.basename(attachment_path)}"'
                msg.attach(part)
        
        # En producción descomentar:
        try:
            # server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            # server.starttls()
            # server.login(self.smtp_user, self.smtp_pass)
            # server.send_message(msg)
            # server.quit()
            print(f"Mock SMTP: Correo enviado a {to_email} con asunto '{subject}'")
        except Exception as e:
            print(f"Error enviando correo SMTP: {e}")
