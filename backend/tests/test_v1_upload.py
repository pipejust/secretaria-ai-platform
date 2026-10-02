"""POST /api/v1/sessions: otra plataforma sube una grabación o un texto y Acten lo vuelve sesión."""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from sqlmodel import Session, select

import database
from auth_utils import create_access_token, get_password_hash
from models import ApiKey, MeetingSession, Project, Role, Tenant, User
from routers import sessions_upload
from services.llm_groq import GroqLLMService


@pytest.fixture()
def api(test_engine, monkeypatch, db_session: Session):
    monkeypatch.setattr(database, "engine", test_engine)
    procesadas: list[int] = []

    async def pipeline(session_id: int) -> None:
        procesadas.append(session_id)

    monkeypatch.setattr(sessions_upload, "_process_uploaded_session_background", pipeline)
    suf = uuid.uuid4().hex[:8]
    t = Tenant(slug=f"sube-{suf}", name="Sube")
    otra = Tenant(slug=f"otra-{suf}", name="Otra")
    db_session.add(t); db_session.add(otra); db_session.commit()
    db_session.refresh(t); db_session.refresh(otra)

    rol = db_session.exec(select(Role).where(Role.name == "admin")).first()
    if not rol:
        rol = Role(name="admin"); db_session.add(rol); db_session.commit(); db_session.refresh(rol)
    u = User(email=f"a-{suf}@s.test", full_name="A", hashed_password=get_password_hash("x"),
             role_id=rol.id, tenant_id=t.id, is_active=True)
    db_session.add(u); db_session.commit(); db_session.refresh(u)

    def clave(scopes):
        valor = "acten_" + uuid.uuid4().hex * 2
        db_session.add(ApiKey(tenant_id=t.id, user_id=u.id, name="t", scopes=json.dumps(scopes),
                              hashed_key=hashlib.sha256(valor.encode()).hexdigest()))
        db_session.commit()
        return {"X-API-Key": valor}

    p = Project(name=f"P-{suf}", tenant_id=t.id, external_ref=f"ext-{suf}")
    ajeno = Project(name=f"A-{suf}", tenant_id=otra.id, external_ref=f"ajeno-{suf}")
    db_session.add(p); db_session.add(ajeno); db_session.commit(); db_session.refresh(p)
    jwt = {"Authorization": "Bearer " + create_access_token({"sub": u.email, "tenant_id": t.id})}
    return {"jwt": jwt, "h": clave(["sessions:read", "sessions:write"]), "lectura": clave(["sessions:read"]),
            "empresa": {**clave(["sessions:read", "org:read"]), "X-On-Behalf-Of": "*"},
            "ref": p.external_ref, "pid": p.id, "ajeno": ajeno.external_ref, "tenant": t.id,
            "procesadas": procesadas}


def test_texto_se_vuelve_sesion_en_su_proyecto(client, api, db_session):
    r = client.post("/api/v1/sessions", headers=api["h"], data={
        "title": "  Comité de obra ", "text_content": "Ana: revisamos el cronograma de la obra.",
        "language": "es", "date": "2026-10-02T09:00:00-05:00", "project_external_id": api["ref"],
    })
    assert r.status_code == 202, r.text
    cuerpo = r.json()
    assert cuerpo["status"] == "processing" and cuerpo["title"] == "Comité de obra"
    assert cuerpo["project_external_id"] == api["ref"]
    s = db_session.get(MeetingSession, cuerpo["id"])
    assert s.tenant_id == api["tenant"] and s.project_id == api["pid"]
    assert s.language == "Español" and "cronograma" in s.raw_transcript
    assert api["procesadas"] == [s.id]  # el pipeline IA quedó lanzado


def test_audio_se_transcribe(client, api, db_session, monkeypatch):
    visto = {}

    async def transcribir(self, contenido, nombre, language=None):
        visto.update(nombre=nombre, bytes=len(contenido), tenant=self.tenant_id)
        return "Transcripción del audio de la reunión."

    monkeypatch.setattr(GroqLLMService, "transcribe_audio", transcribir)
    r = client.post("/api/v1/sessions", headers=api["h"], data={"title": "Audio"},
                    files={"file": ("reunion.mp3", b"ID3-audio", "audio/mpeg")})
    assert r.status_code == 202, r.text
    assert visto == {"nombre": "reunion.mp3", "bytes": 9, "tenant": api["tenant"]}
    s = db_session.get(MeetingSession, r.json()["id"])
    assert s.raw_transcript == "Transcripción del audio de la reunión." and s.project_id is None
    assert r.json()["project_external_id"] is None


def test_validaciones(client, api, monkeypatch):
    post = lambda data, files=None, h=None: client.post(  # noqa: E731
        "/api/v1/sessions", headers=h or api["h"], data=data, files=files)
    texto = {"title": "T", "text_content": "Texto suficiente para una sesión."}
    archivo = {"file": ("notas.txt", b"Texto suficiente para una sesion.", "text/plain")}
    assert post({"title": "T"}).status_code == 422                                  # ni archivo ni texto
    assert post(texto, archivo).status_code == 422                                  # los dos
    assert post({"text_content": "x" * 20}).status_code == 422                      # sin título
    assert post({**texto, "date": "mañana"}).status_code == 422
    assert post({**texto, "language": "fr"}).status_code == 422
    assert post({**texto, "project_external_id": api["ajeno"]}).status_code == 422  # proyecto de otra empresa
    assert post(texto, h=api["lectura"]).status_code == 403
    assert post(texto, h={"X-API-Key": "acten_no_existe"}).status_code == 401
    assert post({"title": "T"}, archivo).status_code == 202                         # archivo de texto
    monkeypatch.setattr(sessions_upload, "MAX_UPLOAD_BYTES", 8)
    assert post({"title": "T"}, archivo).status_code == 413


def test_la_subida_desde_la_pantalla_sigue_igual(client, api, db_session):
    r = client.post("/api/sessions/upload", headers=api["jwt"], data={
        "title": "Desde la pantalla", "text_content": "Texto suficiente para una sesión.",
        "language": "Español", "project_id": str(api["pid"]),
    })
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "success"
    s = db_session.get(MeetingSession, r.json()["session_id"])
    assert s.project_id == api["pid"] and s.language == "Español" and s.status == "processing"
    assert api["procesadas"] == [s.id]


def test_capacidades_reflejan_la_suscripcion(client, api, db_session):
    r = client.get("/api/v1/capabilities", headers=api["empresa"])
    assert r.status_code == 200, r.text
    # Empresa recién creada: en prueba, todavía por Fireflies.
    assert r.json() == {"plan": None, "status": "trialing", "meeting_source": "fireflies",
                        "fireflies": True, "owned_bot": False, "video": False,
                        "video_retention_days": 30, "upload": True}
    t = db_session.get(Tenant, api["tenant"])
    t.meeting_source = "both"
    db_session.add(t); db_session.commit()
    caps = client.get("/api/v1/capabilities", headers=api["empresa"]).json()
    assert caps["owned_bot"] is True and caps["video"] is True and caps["meeting_source"] == "both"
