"""PATCH /api/v1/tasks/{id}: reasignar el proyecto de una tarea y rechazar campos desconocidos."""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from sqlmodel import Session, select

import database
from auth_utils import get_password_hash
from models import ActionItem, ApiKey, MeetingSession, Project, Role, Tenant, User


@pytest.fixture()
def escenario(test_engine, monkeypatch, db_session: Session):
    monkeypatch.setattr(database, "engine", test_engine)
    suf = uuid.uuid4().hex[:8]
    t = Tenant(slug=f"reasig-{suf}", name="Reasignar")
    db_session.add(t); db_session.commit(); db_session.refresh(t)
    rol = db_session.exec(select(Role).where(Role.name == "admin")).first()
    if not rol:
        rol = Role(name="admin"); db_session.add(rol); db_session.commit(); db_session.refresh(rol)
    u = User(email=f"a-{suf}@r.test", full_name="A", hashed_password=get_password_hash("x"),
             role_id=rol.id, tenant_id=t.id, is_active=True)
    db_session.add(u); db_session.commit(); db_session.refresh(u)
    clave = "acten_" + uuid.uuid4().hex * 2
    db_session.add(ApiKey(tenant_id=t.id, user_id=u.id, name="t",
                          hashed_key=hashlib.sha256(clave.encode()).hexdigest(),
                          scopes=json.dumps(["tasks:read", "tasks:write", "org:read"])))
    pa = Project(name=f"A-{suf}", tenant_id=t.id, external_ref=f"ext-a-{suf}")
    pb = Project(name=f"B-{suf}", tenant_id=t.id, external_ref=f"ext-b-{suf}")
    db_session.add(pa); db_session.add(pb); db_session.commit()
    db_session.refresh(pa); db_session.refresh(pb)
    s = MeetingSession(fireflies_id=f"MANUAL-{suf}", title="Mixta", date="2026-09-01",
                       project_id=pa.id, tenant_id=t.id)
    db_session.add(s); db_session.commit(); db_session.refresh(s)
    item = ActionItem(session_id=s.id, tenant_id=t.id, title="Mal clasificada", owner_name="", owner_email="")
    db_session.add(item); db_session.commit(); db_session.refresh(item)
    return {"h": {"X-API-Key": clave, "X-On-Behalf-Of": "*"}, "a": pa.external_ref, "b": pb.external_ref,
            "task": item.id, "session": s.id, "pa": pa.id}


def test_reasignar_proyecto_sin_tocar_la_sesion(client, escenario, db_session):
    e = escenario
    url = f"/api/v1/tasks/{e['task']}"
    r = client.patch(url, headers=e["h"], json={"project_external_id": e["b"]})
    assert r.status_code == 200, r.text
    assert r.json()["project_external_id"] == e["b"]
    assert r.json()["source_session_id"] == e["session"]
    db_session.expire_all()
    assert db_session.get(MeetingSession, e["session"]).project_id == e["pa"]  # la sesión no se movió

    ids = lambda ref: [t["id"] for t in client.get(  # noqa: E731
        "/api/v1/tasks", headers=e["h"], params={"project_external_id": ref}).json()["items"]]
    assert e["task"] in ids(e["b"]) and e["task"] not in ids(e["a"])

    # null deshace la reasignación: vuelve al proyecto de su sesión
    r = client.patch(url, headers=e["h"], json={"project_external_id": None})
    assert r.status_code == 200 and r.json()["project_external_id"] == e["a"]
    assert e["task"] in ids(e["a"]) and e["task"] not in ids(e["b"])

    # un PATCH que no menciona el proyecto no lo toca
    client.patch(url, headers=e["h"], json={"project_external_id": e["b"]})
    r = client.patch(url, headers=e["h"], json={"title": "Otro título"})
    assert r.json()["project_external_id"] == e["b"]


def test_proyecto_inexistente_y_campos_desconocidos_son_422(client, escenario):
    e = escenario
    url = f"/api/v1/tasks/{e['task']}"
    r = client.patch(url, headers=e["h"], json={"project_external_id": "no-existe"})
    assert r.status_code == 422
    r = client.patch(url, headers=e["h"], json={"title": "x", "proyecto": e["b"]})
    assert r.status_code == 422
    assert "proyecto" in r.text  # el error nombra el campo que sobra
    assert client.get("/api/v1/tasks", headers=e["h"]).json()["items"][0]["title"] == "Mal clasificada"
