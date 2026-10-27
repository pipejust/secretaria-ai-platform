from pathlib import Path

from jinja2 import Environment, FileSystemLoader

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def render(**extra):
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)))
    return env.get_template("email_session_received.html").render(
        session_title="Reunión", project_name="General", total_tasks=2, timeout_minutes=60,
        session_url="https://x/s/1", brand={}, **extra,
    )


def test_missing_recipients_are_reported_only_in_the_admin_notice():
    html = render(auto_dispatch_enabled=True, missing_task_emails=2, missing_participants=1)
    assert "Faltan datos" not in html
    assert "sin correo del" not in html
    assert "envío automático" in html
