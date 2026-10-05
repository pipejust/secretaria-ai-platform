"""Personas alineadas con el proyecto, participantes por API y descargas por separado."""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from sqlmodel import Session, select

import database
from auth_utils import create_access_token, get_password_hash
from models import ActionItem, ApiKey, MeetingSession, Project, ProjectContact, Role, Tenant, User
from services import media_storage


@pytest.fixture()
def mundo(test_engine, monkeypatch, db_session: Session):
    monkeypatch.setattr(database, "engine", test_engine)
    suf = uuid.uuid4().hex[:8]
    t = Tenant(slug=f"sp-{suf}", name="SP")
    db_session.add(t); db_session.commit(); db_session.refresh(t)
    rol = db_session.exec(select(Role).where(Role.name == "admin")).first()
    if not rol:
        rol = Role(name="admin"); db_session.add(rol); db_session.commit(); db_session.refresh(rol)
    u = User(email=f"a-{suf}@sp.test", full_name="Admin", hashed_password=get_password_hash("x"),
             role_id=rol.id, tenant_id=t.id, is_active=True)
    db_session.add(u); db_session.commit(); db_session.refresh(u)
    clave = "acten_" + uuid.uuid4().hex * 2
    db_session.add(ApiKey(tenant_id=t.id, user_id=u.id, name="t", hashed_key=hashlib.sha256(clave.encode()).hexdigest(),
                          scopes=json.dumps(["sessions:read", "sessions:write", "org:read"])))
    obra = Project(name=f"Obra-{suf}", tenant_id=t.id, external_ref=f"obra-{suf}")
    sistemas = Project(name=f"Sistemas-{suf}", tenant_id=t.id, external_ref=f"sis-{suf}")
    db_session.add(obra); db_session.add(sistemas); db_session.commit()
    db_session.refresh(obra); db_session.refresh(sistemas)
    db_session.add(ProjectContact(project_id=sistemas.id, name="Ana Ruiz", role="Líder técnica", entity="Acme",
                                  email="ana@acme.test"))
    db_session.add(ProjectContact(project_id=sistemas.id, name="Luis Pérez", role="Gerente", entity="Acme",
                                  email="luis@acme.test"))
    s = MeetingSession(tenant_id=t.id, fireflies_id=f"BOT-{suf}", title="Comité", date="2026-10-05T09:00:00+00:00",
                       project_id=obra.id, status="pending",
                       raw_transcript="[Ana Ruiz] Revisamos el plan.\n[Hablante 2] De acuerdo.\n[Luis Pérez] Cerramos el presupuesto.",
                       raw_summary="### Resumen\n- Se cerró el presupuesto.",
                       processed_attendees=json.dumps([{"name": "Ana Ruiz", "role": "Participante", "entity": "—", "email": ""},
                                                       {"name": "Hablante 2", "role": "—", "entity": "—", "email": ""}]))
    db_session.add(s); db_session.commit(); db_session.refresh(s)
    db_session.add(ActionItem(session_id=s.id, tenant_id=t.id, title="Cerrar presupuesto", owner_name="Luis Perez", owner_email=""))
    db_session.add(ActionItem(session_id=s.id, tenant_id=t.id, title="Sin dueño", owner_name="Por asignar", owner_email=""))
    db_session.commit()
    return {"h": {"X-API-Key": clave, "X-On-Behalf-Of": "*"},
            "jwt": {"Authorization": "Bearer " + create_access_token({"sub": u.email, "tenant_id": t.id})},
            "s": s.id, "obra": obra, "sistemas": sistemas, "t": t}


def _asistentes(db, sid):
    db.expire_all()
    return {a["name"]: a for a in json.loads(db.get(MeetingSession, sid).processed_attendees)}


def test_cambiar_de_proyecto_realinea_personas_y_responsables(client, mundo, db_session):
    r = client.patch(f"/api/v1/sessions/{mundo['s']}", headers=mundo["h"],
                     json={"project_external_id": mundo["sistemas"].external_ref})
    assert r.status_code == 200, r.text
    a = _asistentes(db_session, mundo["s"])
    assert a["Ana Ruiz"]["role"] == "Líder técnica" and a["Ana Ruiz"]["email"] == "ana@acme.test"
    duenos = {t.title: (t.owner_name, t.owner_email) for t in db_session.exec(
        select(ActionItem).where(ActionItem.session_id == mundo["s"])).all()}
    assert duenos["Cerrar presupuesto"] == ("Luis Pérez", "luis@acme.test")  # «Luis Perez» casó con el miembro
    assert duenos["Sin dueño"] == ("Por asignar", "")
    # La pantalla hace lo mismo al cambiar el proyecto.
    r = client.put(f"/api/sessions/{mundo['s']}", headers=mundo["jwt"], json={"project_id": mundo["obra"].id})
    assert r.status_code == 200, r.text
    r = client.put(f"/api/sessions/{mundo['s']}", headers=mundo["jwt"], json={"project_id": mundo["sistemas"].id})
    assert r.status_code == 200 and _asistentes(db_session, mundo["s"])["Ana Ruiz"]["entity"] == "Acme"


def test_regenerar_y_anadir_participantes_por_api(client, mundo, db_session):
    client.patch(f"/api/v1/sessions/{mundo['s']}", headers=mundo["h"],
                 json={"project_external_id": mundo["sistemas"].external_ref})
    r = client.post(f"/api/v1/sessions/{mundo['s']}/regenerate-participants", headers=mundo["h"])
    assert r.status_code == 200, r.text
    nombres = {p["name"]: p for p in r.json()["participants"]}
    # Luis hablaba y no estaba en la lista; «Hablante 2» no es nadie.
    assert set(nombres) == {"Ana Ruiz", "Luis Pérez"} and nombres["Luis Pérez"]["role"] == "Gerente"

    r = client.post(f"/api/v1/sessions/{mundo['s']}/participants", headers=mundo["h"],
                    json={"name": "Marta Gil", "role": "Invitada", "entity": "Cliente"})
    assert r.status_code == 201, r.text
    assert {p["name"] for p in r.json()["participants"]} == {"Ana Ruiz", "Luis Pérez", "Marta Gil"}
    # Repetir completa la ficha en vez de duplicar.
    r = client.post(f"/api/v1/sessions/{mundo['s']}/participants", headers=mundo["h"],
                    json={"name": "marta gil", "email": "marta@cliente.test"})
    marta = next(p for p in r.json()["participants"] if p["name"] == "Marta Gil")
    assert len(r.json()["participants"]) == 3 and marta == {"name": "Marta Gil", "role": "Invitada",
                                                               "entity": "Cliente", "email": "marta@cliente.test"}
    assert client.post(f"/api/v1/sessions/{mundo['s']}/participants", headers=mundo["h"], json={"name": " "}).status_code == 422
    r = client.delete(f"/api/v1/sessions/{mundo['s']}/participants", headers=mundo["h"], params={"name": "Marta Gil"})
    assert r.status_code == 200 and {p["name"] for p in r.json()["participants"]} == {"Ana Ruiz", "Luis Pérez"}


def test_descargas_por_separado(client, mundo, db_session, monkeypatch):
    sid = mundo["s"]
    r = client.get(f"/api/v1/sessions/{sid}/transcript", headers=mundo["h"], params={"format": "txt"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert "attachment" in r.headers["content-disposition"] and ".txt" in r.headers["content-disposition"]
    assert "[Luis Pérez] Cerramos el presupuesto." in r.text and r.text.startswith("Comité\n")
    assert client.get(f"/api/v1/sessions/{sid}/transcript", headers=mundo["h"]).json()["session_id"] == sid

    r = client.get(f"/api/v1/sessions/{sid}/summary", headers=mundo["h"])
    assert r.status_code == 200 and r.json()["summary"].startswith("### Resumen")
    r = client.get(f"/api/v1/sessions/{sid}/summary", headers=mundo["h"], params={"format": "md"})
    assert r.headers["content-type"].startswith("text/markdown") and r.text.startswith("# Comité\n")
    # Lo mismo desde la pantalla.
    r = client.get(f"/api/sessions/{sid}/download/summary", headers=mundo["jwt"])
    assert r.status_code == 200 and "Resumen_" in r.headers["content-disposition"]
    assert client.get(f"/api/sessions/{sid}/download/otra_cosa", headers=mundo["jwt"]).status_code == 404

    # Vídeo: la misma URL firmada, pero con orden de descargar como archivo.
    class S3:
        def generate_presigned_url(self, op, Params, ExpiresIn):
            return "https://s3.test/" + Params["Key"] + ("?guardar=" + Params["ResponseContentDisposition"]
                                                            if "ResponseContentDisposition" in Params else "")
    monkeypatch.setattr(media_storage, "_cliente", lambda cfg: S3())
    duenio = db_session.exec(select(Tenant).where(Tenant.slug == "acten")).first() or Tenant(slug="acten", name="Acten")
    if not duenio.id:
        db_session.add(duenio); db_session.commit()
    media_storage.guardar_config(db_session, endpoint_url="https://fsn1.your-objectstorage.com", region="fsn1",
                                 bucket="b", access_key="AK", secret_key="s3cr3t-key-test")
    s = db_session.get(MeetingSession, sid)
    s.recording_video_key = f"tenants/{mundo['t'].id}/sessions/{sid}/recording.mp4"
    db_session.add(s); db_session.commit()
    ver = client.get(f"/api/v1/sessions/{sid}/video", headers=mundo["h"]).json()
    bajar = client.get(f"/api/v1/sessions/{sid}/video", headers=mundo["h"], params={"download": "true"}).json()
    assert "guardar" not in ver["url"] and 'attachment; filename="Sesion_' in bajar["url"] and bajar["url"].endswith('.mp4"')
    interno = client.get(f"/api/sessions/{sid}/video", headers=mundo["jwt"], params={"download": "true"}).json()
    assert "attachment" in interno["url"]
