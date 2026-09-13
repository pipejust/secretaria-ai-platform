"""Por dónde entran las reuniones de cada empresa: Fireflies, bot propio o ambos.

Lo que se protege: que una empresa que eligió el bot propio no siga
recibiendo cada reunión dos veces por el webhook de Fireflies que nunca
apagó, y que la decisión sea de un administrador de esa empresa.
"""

from __future__ import annotations

import uuid

import pytest
from sqlmodel import Session, select

import database
from auth_utils import create_access_token, get_password_hash
from models import Role, Tenant, User
from routers import fireflies as fireflies_router
from services import meeting_source


@pytest.fixture()
def empresa(test_engine, monkeypatch, db_session: Session):
    monkeypatch.setattr(database, "engine", test_engine)
    suf = uuid.uuid4().hex[:8]
    t = Tenant(slug=f"fuente-{suf}", name="Fuente")
    db_session.add(t); db_session.commit(); db_session.refresh(t)

    def rol(nombre):
        r = db_session.exec(select(Role).where(Role.name == nombre)).first()
        if not r:
            r = Role(name=nombre); db_session.add(r); db_session.commit(); db_session.refresh(r)
        return r

    def usuario(email, r):
        u = User(email=email, full_name=email, hashed_password=get_password_hash("x"),
                 role_id=r.id, tenant_id=t.id, is_active=True)
        db_session.add(u); db_session.commit(); db_session.refresh(u)
        return {"Authorization": "Bearer " + create_access_token(
            {"sub": u.email, "tenant_id": t.id})}

    return t, usuario(f"admin-{suf}@f.test", rol("admin")), usuario(f"user-{suf}@f.test", rol("user"))


def test_por_defecto_es_fireflies_y_no_admite_el_bot(client, empresa, db_session):
    t, admin, _ = empresa
    r = client.get("/api/settings/meeting-source", headers=admin)
    assert r.status_code == 200
    assert r.json()["source"] == "fireflies"
    assert meeting_source.admite(db_session, t.id, "fireflies") is True
    assert meeting_source.admite(db_session, t.id, "owned_bot") is False


def test_cambiar_al_bot_apaga_fireflies(client, empresa, db_session):
    t, admin, _ = empresa
    r = client.put("/api/settings/meeting-source", json={"source": "owned_bot"}, headers=admin)
    assert r.status_code == 200
    assert r.json()["accepts_owned_bot"] is True
    assert r.json()["accepts_fireflies"] is False
    db_session.expire_all()
    assert meeting_source.fuente_de(db_session, t.id) == "owned_bot"


def test_ambos_admite_las_dos_entradas(client, empresa, db_session):
    t, admin, _ = empresa
    client.put("/api/settings/meeting-source", json={"source": "both"}, headers=admin)
    db_session.expire_all()
    assert meeting_source.admite(db_session, t.id, "fireflies")
    assert meeting_source.admite(db_session, t.id, "owned_bot")


def test_una_fuente_desconocida_se_rechaza(client, empresa):
    _, admin, _ = empresa
    r = client.put("/api/settings/meeting-source", json={"source": "zoom"}, headers=admin)
    assert r.status_code == 422


def test_solo_un_administrador_decide(client, empresa):
    _, _, normal = empresa
    assert client.get("/api/settings/meeting-source", headers=normal).status_code == 403
    assert client.put("/api/settings/meeting-source", json={"source": "both"},
                      headers=normal).status_code == 403


def test_el_webhook_de_fireflies_se_ignora_si_la_empresa_usa_el_bot(
        client, empresa, db_session, monkeypatch):
    """El caso que motiva todo esto: la reunión no puede entrar dos veces."""
    t, admin, _ = empresa
    client.put("/api/settings/meeting-source", json={"source": "owned_bot"}, headers=admin)

    async def verificado(request, db):
        return t.id
    monkeypatch.setattr(fireflies_router, "verify_fireflies_webhook", verificado)

    r = client.post("/api/webhook/fireflies?token=x", json={"transcriptId": "ff-123"})
    # 200 y no 4xx: Fireflies reintenta los errores, y aquí no hay nada
    # que reintentar.
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ignored"
    assert r.json()["reason"] == "meeting_source"


def test_el_perfil_lleva_la_fuente_para_el_menu(client, empresa):
    _, admin, _ = empresa
    client.put("/api/settings/meeting-source", json={"source": "both"}, headers=admin)
    r = client.get("/auth/me", headers=admin)
    assert r.status_code == 200
    assert r.json()["tenant"]["meeting_source"] == "both"
