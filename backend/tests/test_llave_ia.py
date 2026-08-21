"""La llave del motor de IA, guardada por empresa.

Lo que se protege aquí, por orden de gravedad:

1. **Que la llave de una empresa no se use para otra.** Es dinero: si el
   resolutor cayera en la primera llave que encontrara, una empresa
   gastaría contra la cuenta de quien no pidió nada.
2. **Que no salga en claro por la API ni quede en claro en la base.**
3. Que guardarla vacía no borre por accidente la que ya había, y que
   borrarla a propósito devuelva el respaldo del entorno.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlmodel import Session, select

import database
from auth_utils import create_access_token, get_password_hash
from models import IntegrationSetting, Role, Tenant, User
from services import llm_keys


@pytest.fixture()
def dos_empresas(test_engine, monkeypatch, db_session: Session):
    """Dos empresas con su administrador, y el resolutor mirando a la base de test."""
    monkeypatch.setattr(database, "engine", test_engine)
    monkeypatch.setattr(llm_keys, "engine", test_engine)
    llm_keys._cache.clear()

    rol = db_session.exec(select(Role).where(Role.name == "admin")).first()
    if not rol:
        rol = Role(name="admin")
        db_session.add(rol); db_session.commit(); db_session.refresh(rol)

    # El engine de pruebas se comparte entre tests, así que los slugs tienen
    # que ser únicos o el segundo test choca con el UNIQUE del primero.
    sufijo = uuid.uuid4().hex[:8]
    creados = {}
    for nombre in ("llave-a", "llave-b"):
        slug = f"{nombre}-{sufijo}"
        t = Tenant(slug=slug, name=slug)
        db_session.add(t); db_session.commit(); db_session.refresh(t)
        u = User(email=f"admin@{slug}.test", full_name="Admin",
                 hashed_password=get_password_hash("x"), role_id=rol.id,
                 tenant_id=t.id, is_active=True)
        db_session.add(u); db_session.commit(); db_session.refresh(u)
        creados[nombre] = (t.id, {"Authorization": "Bearer " + create_access_token(
            {"sub": u.email, "tenant_id": t.id})})
    return creados


def test_la_llave_de_una_empresa_no_se_usa_para_otra(client, dos_empresas, monkeypatch):
    monkeypatch.setattr(llm_keys.settings, "groq_api_key", "")
    (id_a, cab_a), (id_b, _) = dos_empresas["llave-a"], dos_empresas["llave-b"]

    client.put("/api/v1/ai/proveedores/groq",
               json={"api_key": "gsk_solo_de_A"}, headers=cab_a)

    assert llm_keys.clave_groq(id_a) == "gsk_solo_de_A"
    assert llm_keys.clave_groq(id_b) != "gsk_solo_de_A"
    # Y sin empresa tampoco se hereda la de nadie.
    assert llm_keys.clave_groq(None) != "gsk_solo_de_A"


def test_no_sale_en_claro_ni_por_la_api_ni_en_la_base(client, dos_empresas, db_session):
    id_a, cab_a = dos_empresas["llave-a"]
    secreta = "gsk_esto_no_puede_verse_1234"

    r = client.put("/api/v1/ai/proveedores/groq",
                   json={"api_key": secreta}, headers=cab_a)
    assert r.status_code == 200
    assert secreta not in r.text
    assert "…" in r.json()["pista"]

    fila = db_session.exec(
        select(IntegrationSetting)
        .where(IntegrationSetting.tenant_id == id_a)
        .where(IntegrationSetting.provider_name == "groq")
    ).first()
    assert fila is not None
    assert secreta not in fila.config_json
    assert json.loads(fila.config_json)["api_key"].startswith("fer1:")


def test_borrarla_devuelve_el_respaldo_del_entorno(client, dos_empresas, monkeypatch):
    monkeypatch.setattr(llm_keys.settings, "groq_api_key", "gsk_del_entorno")
    id_a, cab_a = dos_empresas["llave-a"]

    client.put("/api/v1/ai/proveedores/groq",
               json={"api_key": "gsk_propia"}, headers=cab_a)
    assert llm_keys.clave_groq(id_a) == "gsk_propia"

    r = client.put("/api/v1/ai/proveedores/groq", json={"api_key": ""}, headers=cab_a)
    assert r.status_code == 200
    assert r.json()["origen"] == "entorno"
    assert llm_keys.clave_groq(id_a) == "gsk_del_entorno"


def test_solo_administradores(client, dos_empresas, db_session, test_engine):
    id_a, _ = dos_empresas["llave-a"]
    rol = db_session.exec(select(Role).where(Role.name == "user")).first()
    if not rol:
        rol = Role(name="user")
        db_session.add(rol); db_session.commit(); db_session.refresh(rol)
    u = User(email=f"curioso-{uuid.uuid4().hex[:8]}@llave-a.test", full_name="Curioso",
             hashed_password=get_password_hash("x"), role_id=rol.id,
             tenant_id=id_a, is_active=True)
    db_session.add(u); db_session.commit()
    cab = {"Authorization": "Bearer " + create_access_token(
        {"sub": u.email, "tenant_id": id_a})}

    assert client.get("/api/v1/ai/proveedores", headers=cab).status_code == 403
    assert client.put("/api/v1/ai/proveedores/groq",
                      json={"api_key": "x"}, headers=cab).status_code == 403


def test_proveedor_desconocido(client, dos_empresas):
    _, cab_a = dos_empresas["llave-a"]
    r = client.put("/api/v1/ai/proveedores/inventado",
                   json={"api_key": "x"}, headers=cab_a)
    assert r.status_code == 404
