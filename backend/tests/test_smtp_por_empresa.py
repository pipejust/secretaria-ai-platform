"""El SMTP es de cada empresa, y su llave no vive en claro en la base.

Dos cosas se protegen aquí:

1. **Un envío sin empresa no sale.** Antes caía en el tenant por defecto:
   el correo se mandaba con la cuenta de Resend y el remitente de OTRA
   empresa, y quien lo recibía veía una marca que no era la suya.
2. **La llave se guarda cifrada.** Estaba en claro: cualquiera con acceso
   de lectura a la base —un backup, una consola— veía la API key de
   Resend de cada empresa.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlmodel import Session, select

import database
from models import IntegrationSetting, Tenant
from services import cifrado
from services.email_service import EmailService


@pytest.fixture()
def empresa_con_smtp(test_engine, monkeypatch, db_session: Session):
    monkeypatch.setattr(database, "engine", test_engine)
    monkeypatch.setattr(cifrado, "_MARCA", cifrado._MARCA, raising=False)

    t = Tenant(slug=f"correo-{uuid.uuid4().hex[:8]}", name="Correo")
    db_session.add(t); db_session.commit(); db_session.refresh(t)
    return t


def test_sin_empresa_no_toma_la_configuracion_del_tenant_por_defecto(
    empresa_con_smtp, db_session: Session):
    """El caso que de verdad se rompía.

    Existe una empresa con el slug por defecto y con SMTP configurado. Un
    envío sin `tenant_id` NO puede acabar usando esa cuenta: es la de otra
    empresa, y el correo saldría con su remitente y su marca.
    """
    porDefecto = db_session.exec(
        select(Tenant).where(Tenant.slug == database.DEFAULT_TENANT_SLUG)
    ).first()
    if not porDefecto:
        porDefecto = Tenant(slug=database.DEFAULT_TENANT_SLUG, name="Por defecto")
        db_session.add(porDefecto); db_session.commit(); db_session.refresh(porDefecto)

    ya = db_session.exec(
        select(IntegrationSetting)
        .where(IntegrationSetting.tenant_id == porDefecto.id)
        .where(IntegrationSetting.provider_name == "smtp")
    ).first()
    if not ya:
        db_session.add(IntegrationSetting(
            tenant_id=porDefecto.id, provider_name="smtp", is_active=True,
            config_json=json.dumps({"apiKey": cifrado.cifrar("re_del_tenant_por_defecto"),
                                    "senderEmail": "no-soy-tuyo@otra-empresa.com"})))
        db_session.commit()

    svc = EmailService(db=db_session)
    assert svc.api_key is None, "adoptó la llave de otra empresa"
    assert svc.from_email != "no-soy-tuyo@otra-empresa.com"


def test_cada_empresa_usa_la_suya(empresa_con_smtp, db_session: Session):
    db_session.add(IntegrationSetting(
        tenant_id=empresa_con_smtp.id, provider_name="smtp", is_active=True,
        config_json=json.dumps({"apiKey": cifrado.cifrar("re_de_esta_empresa"),
                                "senderEmail": "hola@esta-empresa.com"})))
    db_session.commit()

    svc = EmailService(db=db_session, tenant_id=empresa_con_smtp.id)
    assert svc.api_key == "re_de_esta_empresa"
    assert svc.from_email == "hola@esta-empresa.com"


def test_lo_guardado_en_claro_antes_se_sigue_leyendo(
    empresa_con_smtp, db_session: Session):
    """Las filas viejas no se rompen: se leen igual mientras se convierten."""
    db_session.add(IntegrationSetting(
        tenant_id=empresa_con_smtp.id, provider_name="smtp", is_active=True,
        config_json=json.dumps({"apiKey": "re_en_claro_de_antes"})))
    db_session.commit()

    svc = EmailService(db=db_session, tenant_id=empresa_con_smtp.id)
    assert svc.api_key == "re_en_claro_de_antes"


def test_la_migracion_cifra_lo_que_estaba_en_claro(
    empresa_con_smtp, db_session: Session, test_engine, monkeypatch):
    monkeypatch.setattr(database, "engine", test_engine)
    fila = IntegrationSetting(
        tenant_id=empresa_con_smtp.id, provider_name="smtp", is_active=True,
        config_json=json.dumps({"apiKey": "re_secreta_en_claro"}))
    db_session.add(fila); db_session.commit(); db_session.refresh(fila)

    cifrado.cifrar_secretos_pendientes()

    db_session.refresh(fila)
    guardado = json.loads(fila.config_json)["apiKey"]
    assert "re_secreta_en_claro" not in guardado
    assert guardado.startswith("fer1:")
    # Y se sigue pudiendo usar.
    assert cifrado.descifrar(guardado) == "re_secreta_en_claro"

    # Idempotente: una segunda pasada no la cifra dos veces.
    antes = fila.config_json
    cifrado.cifrar_secretos_pendientes()
    db_session.refresh(fila)
    assert fila.config_json == antes


# ─────────────────────────────────────────────────────────────────────────────
# Sin llave, un correo no puede darse por enviado
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_en_produccion_sin_llave_el_envio_falla(
    empresa_con_smtp, db_session: Session, monkeypatch):
    """El caso que dejó a cuatro empresas sin correo y sin enterarse.

    La empresa tiene SMTP «configurado» —proveedor y remitente— pero sin
    llave. Antes se imprimía el correo en el log y se devolvía `True`: el
    llamador marcaba la tarea como notificada y nadie sabía que no había
    salido.
    """
    monkeypatch.setenv("ENVIRONMENT", "production")
    db_session.add(IntegrationSetting(
        tenant_id=empresa_con_smtp.id, provider_name="smtp", is_active=True,
        config_json=json.dumps({"provider": "Resend",
                                "senderEmail": "no-reply@empresa.com"})))
    db_session.commit()

    svc = EmailService(db=db_session, tenant_id=empresa_con_smtp.id)
    assert svc.api_key is None

    salio = await svc._send_html_email("alguien@empresa.com", "Prueba", "<p>hola</p>")
    assert salio is False, "un correo sin llave no puede darse por enviado"


@pytest.mark.asyncio
async def test_en_desarrollo_se_sigue_simulando(
    empresa_con_smtp, db_session: Session, monkeypatch):
    """En local, imprimir el correo es útil y no engaña a nadie."""
    monkeypatch.setenv("ENVIRONMENT", "development")
    svc = EmailService(db=db_session, tenant_id=empresa_con_smtp.id)

    assert await svc._send_html_email("yo@local", "Prueba", "<p>hola</p>") is True


@pytest.mark.asyncio
async def test_los_metodos_publicos_devuelven_si_salio(
    empresa_con_smtp, db_session: Session, monkeypatch):
    """Sin esto el resultado no llegaba a nadie: los ocho lo descartaban."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    svc = EmailService(db=db_session, tenant_id=empresa_con_smtp.id)

    salio = await svc.send_action_item_email(
        to_email="alguien@empresa.com", owner_name="Alguien",
        task_title="Tarea", task_description="Descripción",
        project_name="Proyecto",
    )
    assert salio is False
