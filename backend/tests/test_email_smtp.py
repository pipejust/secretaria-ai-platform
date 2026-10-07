"""Los correos de la plataforma pueden salir por el servidor SMTP de la empresa."""

from __future__ import annotations

import asyncio
import json
import smtplib
import uuid

import pytest

from models import IntegrationSetting, Tenant
from services.cifrado import cifrar
from services.email_service import EmailService


class ServidorFalso:
    enviados: list = []
    ultimo: dict = {}

    def __init__(self, host, port, timeout=None):
        ServidorFalso.ultimo = {"host": host, "port": port, "clase": type(self).__name__}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        ServidorFalso.ultimo["starttls"] = True

    def login(self, user, password):
        ServidorFalso.ultimo["login"] = (user, password)

    def send_message(self, msg):
        ServidorFalso.enviados.append(msg)


class ServidorSSL(ServidorFalso):
    pass


@pytest.fixture()
def empresa_smtp(db_session, monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP_SSL", ServidorSSL)
    monkeypatch.setattr(smtplib, "SMTP", ServidorFalso)
    ServidorFalso.enviados, ServidorFalso.ultimo = [], {}
    t = Tenant(slug=f"smtp-{uuid.uuid4().hex[:8]}", name="SMTP")
    db_session.add(t); db_session.commit(); db_session.refresh(t)
    db_session.add(IntegrationSetting(tenant_id=t.id, provider_name="smtp", is_active=True, config_json=json.dumps({
        "provider": "SMTP", "host": "smtp.zoho.com", "port": 465, "security": "ssl",
        "username": "bot@acten.app", "password": cifrar("clave-de-prueba"), "senderEmail": "bot@acten.app"})))
    db_session.commit()
    return t


def test_envia_por_smtp_con_adjunto(db_session, empresa_smtp):
    servicio = EmailService(db=db_session, tenant_id=empresa_smtp.id)
    assert servicio.configured and servicio.api_key is None
    ok = asyncio.run(servicio._send_html_email(
        "ana@cliente.test", "Sesión lista", "<p>Hola</p>",
        attachments=[{"filename": "acta.pdf", "content": list(b"%PDF-1.4 prueba")}]))
    assert ok is True
    assert ServidorFalso.ultimo["clase"] == "ServidorSSL" and ServidorFalso.ultimo["port"] == 465
    assert ServidorFalso.ultimo["login"] == ("bot@acten.app", "clave-de-prueba")
    msg = ServidorFalso.enviados[-1]
    assert msg["From"] == "bot@acten.app" and msg["To"] == "ana@cliente.test" and msg["Subject"] == "Sesión lista"
    partes = {p.get_content_type(): p for p in msg.walk()}
    assert "text/html" in partes and partes["application/pdf"].get_filename() == "acta.pdf"
    assert partes["application/pdf"].get_payload(decode=True) == b"%PDF-1.4 prueba"


def test_conexion_bloqueada_explica_el_puerto(db_session, empresa_smtp, monkeypatch):
    def bloqueado(*a, **k):
        raise TimeoutError("timed out")
    monkeypatch.setattr(smtplib, "SMTP_SSL", bloqueado)
    servicio = EmailService(db=db_session, tenant_id=empresa_smtp.id)
    with pytest.raises(RuntimeError, match="smtp.zoho.com:465 .*587 con STARTTLS"):
        asyncio.run(servicio._send_html_email("ana@cliente.test", "Hola", "<p>x</p>"))


def test_starttls_y_sin_servidor_no_hay_envio(db_session, empresa_smtp, monkeypatch):
    fila = db_session.exec(__import__("sqlmodel").select(IntegrationSetting).where(
        IntegrationSetting.tenant_id == empresa_smtp.id)).first()
    cfg = json.loads(fila.config_json); cfg.update(port=587, security="starttls")
    fila.config_json = json.dumps(cfg); db_session.add(fila); db_session.commit()
    servicio = EmailService(db=db_session, tenant_id=empresa_smtp.id)
    asyncio.run(servicio._send_html_email("ana@cliente.test", "Hola", "<p>x</p>"))
    assert ServidorFalso.ultimo["clase"] == "ServidorFalso" and ServidorFalso.ultimo.get("starttls") is True
    # Sin host no cuenta como configurado: en producción no se envía nada.
    cfg.update(host=""); fila.config_json = json.dumps(cfg); db_session.add(fila); db_session.commit()
    monkeypatch.setenv("ENVIRONMENT", "production")
    servicio = EmailService(db=db_session, tenant_id=empresa_smtp.id)
    assert not servicio.configured
    assert asyncio.run(servicio._send_html_email("ana@cliente.test", "Hola", "<p>x</p>")) is False
