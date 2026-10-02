"""Tenant/owner controls with isolated databases and mocked external HTTP only."""

import hashlib
import json
import uuid

import httpx
import pytest
from fastapi import Depends, FastAPI, Header
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from database import get_session
from models import ApiKey, IntegrationSetting, MeetingSession, Project, Role, Tenant, User
from routers.auth import get_current_user
from routers.bot_control import BotControlLink, audio_router, router, v1_router
from services.bot_contract import ActenBotEvent
from services.bot_ingest import BotInbox, ingest


@pytest.fixture
def control(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'control.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        for number in (1, 2):
            db.add(Tenant(id=number, slug=f"control-{number}", name="Test"))
        for number, name in ((1, "admin"), (2, "validator"), (3, "viewer")):
            db.add(Role(id=number, name=name))
        db.commit()
        for number, tenant, role in (
            (1, 1, 1),
            (2, 1, 2),
            (3, 1, 3),
            (4, 2, 1),
            (5, 1, 2),
        ):
            db.add(
                User(
                    id=number,
                    tenant_id=tenant,
                    role_id=role,
                    full_name="Test",
                    email=f"u{number}@example.test",
                    hashed_password="unused",
                )
            )
        db.commit()

    def sessions():
        with Session(engine) as db:
            yield db

    def principal(
        x_test_user: int = Header(default=1), db: Session = Depends(get_session)
    ):
        return db.get(User, x_test_user)

    app = FastAPI()
    app.include_router(router)
    app.include_router(audio_router)
    app.include_router(v1_router)
    app.dependency_overrides[get_session] = sessions
    app.dependency_overrides[get_current_user] = principal
    calls, ids = [], {}

    async def remote(self, request):
        calls.append(request)
        if request.url.path == "/v1/capabilities":
            return httpx.Response(
                200, json={"acten_tenant_id": 1, "browser_ready": True}, request=request
            )
        if request.url.path in {"/v1/recordings", "/v1/meetings"} and request.method == "POST":
            body = json.loads(request.content)
            mid = ids.setdefault(body["external_id"], str(uuid.uuid4()))
            return httpx.Response(
                202,
                json={"id": mid, "external_id": body["external_id"]},
                request=request,
            )
        if request.url.path.startswith("/v1/meetings/") and request.method == "GET":
            mid = request.url.path.rsplit("/", 1)[-1]
            external = next((e for e, m in ids.items() if m == mid), None)
            if external is None:
                return httpx.Response(404, json={"detail": "x"}, request=request)
            return httpx.Response(
                200,
                json={"id": mid, "external_id": external, "state": "joining",
                      "title": "Reunión", "error_code": None, "provider_id": "interno"},
                request=request,
            )
        if request.url.path == "/v1/mail-policy":
            if request.method == "PUT":
                ids["policy"] = json.loads(request.content)
            return httpx.Response(200, json={"ok": True, "policy": ids.get("policy")}, request=request)
        return httpx.Response(200, json={"ok": True}, request=request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", remote)
    with TestClient(app) as client:
        response = client.put(
            "/api/owned-bot/config",
            json={"service_url": "https://bot.example.test", "client_key": "a" * 40},
        )
        assert response.status_code == 200
        yield client, engine, calls
    engine.dispose()


def start(client, user=1, external="browser-test"):
    return client.post(
        "/api/owned-bot/start/browser",
        headers={"X-Test-User": str(user)},
        json={"external_id": external, "recording_authorized": True},
    )


def test_config_secret_encrypted_and_role_restricted(control):
    client, engine, calls = control
    result = client.get("/api/owned-bot/config")
    assert result.json() == {
        "configured": True,
        "service_url": "https://bot.example.test",
        "bot_name": "Asistente Acten",
    }
    assert (
        client.get("/api/owned-bot/config", headers={"X-Test-User": "2"}).status_code
        == 403
    )
    assert start(client, user=3).status_code == 403
    with Session(engine) as db:
        saved = db.exec(select(IntegrationSetting)).first().config_json
        assert "a" * 40 not in saved and "fer1:" in saved
    assert calls == []


def test_company_mapping_checked_before_upload(control):
    client, _, calls = control
    assert (
        client.put(
            "/api/owned-bot/config",
            headers={"X-Test-User": "4"},
            json={"service_url": "https://bot.example.test", "client_key": "b" * 40},
        ).status_code
        == 200
    )
    assert start(client, user=4).status_code == 503
    assert [r.url.path for r in calls] == ["/v1/capabilities"]


def test_capture_owner_idempotency_and_binary_preservation(control):
    client, engine, calls = control
    first = start(client, user=2)
    assert first.status_code == 202, first.text
    mid = first.json()["id"]
    assert start(client, user=2).json()["id"] == mid
    assert start(client, user=5).status_code == 409
    path = f"/api/owned-bot/recordings/{mid}/chunks/0"
    assert (
        client.put(
            path, content=b"\x00\xffaudio", headers={"X-Test-User": "5"}
        ).status_code
        == 404
    )
    assert (
        client.put(
            path, content=b"\x00\xffaudio", headers={"X-Test-User": "2"}
        ).status_code
        == 200
    )
    assert calls[-1].content == b"\x00\xffaudio"
    assert calls[-1].headers["authorization"] == "Bearer " + "a" * 40
    assert client.put(path, content=b"x" * (4 * 1024 * 1024 + 1)).status_code == 413
    with Session(engine) as db:
        assert len(db.exec(select(BotControlLink)).all()) == 1


def test_playback_grant_requires_owner_and_expires(control):
    client, _, _ = control
    mid = start(client, user=2).json()["id"]
    path = f"/api/owned-bot/recordings/{mid}/playback"
    assert client.post(path, headers={"X-Test-User": "5"}).status_code == 404
    grant = client.post(path, headers={"X-Test-User": "2"}).json()
    assert "Bearer" not in grant["path"] and "a" * 40 not in grant["path"]
    assert client.get(f"/api/owned-bot/audio/{mid}?grant=wrong").status_code == 401
    assert client.get(grant["path"].replace(mid, str(uuid.uuid4()))).status_code == 401


def test_playback_forwards_range_and_streams_without_exposing_service_key(control, monkeypatch):
    client, _, _ = control
    mid = start(client, user=2).json()["id"]
    grant = client.post(f"/api/owned-bot/recordings/{mid}/playback", headers={"X-Test-User": "2"}).json()
    closed = []
    class AudioStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"abc"
            yield b"def"
        async def aclose(self):
            closed.append(True)
    async def remote(self, request):
        if request.url.path.endswith("/capabilities"):
            return httpx.Response(200, json={"acten_tenant_id": 1}, request=request)
        assert request.headers["range"] == "bytes=0-5"
        assert request.headers["authorization"] == "Bearer " + "a" * 40
        return httpx.Response(206, headers={"Content-Type": "audio/webm", "Content-Range": "bytes 0-5/20", "Content-Length": "6"}, stream=AudioStream(), request=request)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", remote)
    response = client.get(grant["path"], headers={"Range": "bytes=0-5"})
    assert response.status_code == 206 and response.content == b"abcdef"
    assert response.headers["content-range"] == "bytes 0-5/20"
    assert "no-store" in response.headers["cache-control"]
    assert "authorization" not in response.headers and closed


def test_confirmed_name_preserves_manual_curation(control):
    client, engine, _ = control
    mid = start(client).json()["id"]
    event = ActenBotEvent.model_validate(
        {
            "id": f"meeting.completed:{mid}:v1",
            "meeting_id": mid,
            "tenant_id": 1,
            "occurred_at": "2026-09-12T00:00:00Z",
            "data": {
                "external_id": "browser-test",
                "title": "Reunión",
                "date": "2026-09-12T00:00:00Z",
                "date_source": "bot_requested_at",
                "language_hint": "es",
                "timebase": "seconds_from_recording_start",
                "transcript": [
                    {
                        "id": "s1",
                        "start": 0,
                        "end": 2,
                        "text": "Hola.",
                        "speaker_id": "0",
                        "speaker_name": None,
                    }
                ],
                "summary": [{"text": "Saludo", "evidence_ids": ["s1"]}],
                "key_points": [],
                "timeline": [],
                "participants": [],
                "recording": {},
                "warnings": [],
                "provenance": {},
            },
        }
    )
    with Session(engine) as db:
        inbox = ingest(db, event)
        sid = inbox.session_id
        original = inbox.payload
    endpoint = f"/api/owned-bot/meetings/{mid}/speakers/0"
    assert (
        client.put(endpoint, json={"display_name": "Ana"}).json()[
            "transcript_preserved"
        ]
        is False
    )
    with Session(engine) as db:
        assert db.get(MeetingSession, sid).raw_transcript == "[Ana] Hola."
        meeting = db.get(MeetingSession, sid)
        meeting.raw_transcript = "Corrección manual que no debe perderse."
        db.add(meeting)
        db.commit()
    response = client.put(endpoint, json={"display_name": "Ana María"})
    assert response.json()["transcript_preserved"] is True
    with Session(engine) as db:
        assert (
            db.get(MeetingSession, sid).raw_transcript
            == "Corrección manual que no debe perderse."
        )
        assert db.exec(select(BotInbox)).first().payload == original
    assert (
        client.put(
            endpoint,
            json={"display_name": "Ana", "person_external_id": "foreign-person"},
        ).status_code
        == 422
    )


def test_mail_policy_is_admin_only_and_forwarded_to_bot(control):
    client, _, calls = control
    body = {"allowed_senders": ["ana@example.test"], "recording_authorized": True,
            "timezone": "Europe/Madrid"}
    denied = client.put("/api/owned-bot/mail-policy", headers={"X-Test-User": "2"}, json=body)
    assert denied.status_code == 403
    invalid = client.put("/api/owned-bot/mail-policy", json={**body, "timezone": "Marte/Base"})
    assert invalid.status_code == 422
    invalid = client.put("/api/owned-bot/mail-policy", json={**body, "allowed_senders": ["ana"]})
    assert invalid.status_code == 422
    response = client.put("/api/owned-bot/mail-policy", json=body)
    assert response.status_code == 200
    forwarded = [c for c in calls if c.url.path == "/v1/mail-policy"]
    assert forwarded[-1].method == "PUT"
    # Origen «fireflies» (el de la empresa de prueba): los usuarios no se
    # sincronizan; solo viajan los remitentes extra que escribió el admin.
    assert json.loads(forwarded[-1].content) == {
        **body, "extra_senders": ["ana@example.test"], "bot_name": "Asistente Acten",
        "video": False,
    }
    assert client.get("/api/owned-bot/mail-policy").status_code == 200
    assert client.get("/api/owned-bot/mail-policy", headers={"X-Test-User": "3"}).status_code == 403


def test_bot_name_is_per_company_and_reaches_meetings_and_mail_policy(control):
    client, _, calls = control
    saved = client.put(
        "/api/owned-bot/config",
        json={"service_url": "https://bot.example.test", "bot_name": "  Notas   de Acme "},
    )
    assert saved.status_code == 200
    assert client.get("/api/owned-bot/config").json()["bot_name"] == "Notas de Acme"
    response = client.post(
        "/api/owned-bot/start/meeting",
        json={
            "external_id": "meet-1",
            "meeting_url": "https://meet.google.com/abc-defg-hij",
            "recording_authorized": True,
        },
    )
    assert response.status_code == 202
    sent = [c for c in calls if c.url.path == "/v1/meetings" and c.method == "POST"][-1]
    assert json.loads(sent.content)["bot_name"] == "Notas de Acme"
    client.put(
        "/api/owned-bot/mail-policy",
        json={"allowed_senders": ["ana@example.test"], "recording_authorized": True,
              "timezone": "UTC"},
    )
    policy = [c for c in calls if c.url.path == "/v1/mail-policy" and c.method == "PUT"][-1]
    assert json.loads(policy.content)["bot_name"] == "Notas de Acme"
    # Cambiar el nombre reescribe la política sin perder remitentes extra ni vídeo.
    client.put("/api/owned-bot/config", json={"service_url": "https://bot.example.test", "bot_name": "Otro"})
    renamed = json.loads([c for c in calls if c.url.path == "/v1/mail-policy" and c.method == "PUT"][-1].content)
    assert renamed == {**json.loads(policy.content), "bot_name": "Otro"}
    too_long = client.put(
        "/api/owned-bot/config", json={"service_url": "https://bot.example.test", "bot_name": "x" * 51}
    )
    assert too_long.status_code == 422


def test_company_users_become_senders_when_the_bot_is_an_accepted_source(control):
    client, engine, calls = control
    with Session(engine) as db:
        t = db.get(Tenant, 1)
        t.meeting_source = "both"
        db.add(t)
        db.commit()
    response = client.put(
        "/api/owned-bot/mail-policy",
        json={"allowed_senders": ["Calendario@Example.test"], "recording_authorized": True,
              "timezone": "UTC"},
    )
    assert response.status_code == 200
    sent = json.loads([c for c in calls if c.url.path == "/v1/mail-policy" and c.method == "PUT"][-1].content)
    # Usuarios activos de la empresa 1 (u1, u2, u3, u5) más el remitente extra.
    assert sent["allowed_senders"] == sorted(
        ["calendario@example.test", "u1@example.test", "u2@example.test", "u3@example.test", "u5@example.test"]
    )
    assert sent["extra_senders"] == ["calendario@example.test"]
    # Sin remitentes extra también vale: los usuarios bastan.
    response = client.put("/api/owned-bot/mail-policy", json={"recording_authorized": True})
    assert response.status_code == 200
    sent = json.loads([c for c in calls if c.url.path == "/v1/mail-policy" and c.method == "PUT"][-1].content)
    assert "u1@example.test" in sent["allowed_senders"] and sent["extra_senders"] == []


def test_video_in_email_invitations_requires_the_plan_feature(control):
    from datetime import datetime, timedelta

    from models import Subscription
    from services import billing_catalog as cat

    client, engine, calls = control
    body = {"allowed_senders": ["ana@example.test"], "recording_authorized": True, "video": True}
    # Empresa recién creada = en prueba, con todo incluido.
    assert client.put("/api/owned-bot/mail-policy", json=body).status_code == 200
    sent = [c for c in calls if c.url.path == "/v1/mail-policy" and c.method == "PUT"][-1]
    assert json.loads(sent.content)["video"] is True
    # Con un plan sin vídeo, 402 y nada llega al bot.
    with Session(engine) as db:
        cat.sembrar_catalogo(db)
        t = db.get(Tenant, 1)
        t.created_at = (datetime.now() - timedelta(days=60)).isoformat()
        db.add(t)
        db.add(Subscription(tenant_id=1, plan_key="business", status="active",
                            billing_mode="manual", addons_json=json.dumps(["owned_bot"])))
        db.commit()
    antes = len(calls)
    r = client.put("/api/owned-bot/mail-policy", json=body)
    assert r.status_code == 402 and r.json()["detail"]["feature"] == cat.F_VIDEO
    assert not [c for c in calls[antes:] if c.method == "PUT"]
    assert client.put("/api/owned-bot/mail-policy", json={**body, "video": False}).status_code == 200


def event(tenant_id, *, mid):
    return ActenBotEvent.model_validate({
        "id": f"meeting.completed:{mid}:v1", "meeting_id": mid, "tenant_id": tenant_id,
        "occurred_at": "2026-09-12T00:00:00Z",
        "data": {
            "external_id": "altum-1", "title": "Comité", "date": "2026-09-12T00:00:00Z",
            "date_source": "bot_requested_at", "language_hint": "es",
            "timebase": "seconds_from_recording_start",
            "transcript": [{"id": "s1", "start": 0, "end": 2, "text": "Hola.",
                            "speaker_id": "0", "speaker_name": None}],
            "summary": [{"text": "Saludo", "evidence_ids": ["s1"]}],
            "key_points": [], "timeline": [], "participants": [], "recording": {},
            "warnings": [], "provenance": {},
        },
    })


def _api_key(engine, scopes, source="both"):
    clave = "acten_" + uuid.uuid4().hex * 2
    with Session(engine) as db:
        t = db.get(Tenant, 1)
        t.meeting_source = source
        db.add(t)
        db.add(ApiKey(tenant_id=1, user_id=1, name="t", scopes=json.dumps(scopes),
                      hashed_key=hashlib.sha256(clave.encode()).hexdigest()))
        if not db.exec(select(Project).where(Project.external_ref == "ext-p")).first():
            db.add(Project(name="P", tenant_id=1, external_ref="ext-p"))
            db.add(Project(name="Ajeno", tenant_id=2, external_ref="ext-ajeno"))
        db.commit()
    return {"X-API-Key": clave, "X-On-Behalf-Of": "*"}


def test_public_api_sends_the_bot_to_a_live_meeting(control):
    client, engine, calls = control
    h = _api_key(engine, ["sessions:read", "sessions:write", "org:read"])
    body = {"meeting_url": "https://meet.google.com/abc-defg-hij", "recording_authorized": True,
            "external_id": "altum-1", "title": "Comité", "project_external_id": "ext-p"}
    r = client.post("/api/v1/meetings/live", headers=h, json=body)
    assert r.status_code == 202, r.text
    live = r.json()
    assert live["external_id"] == "altum-1" and live["project_external_id"] == "ext-p"
    assert live["acten_session_id"] is None and "provider_id" not in live
    sent = json.loads([c for c in calls if c.url.path == "/v1/meetings" and c.method == "POST"][-1].content)
    assert sent["meeting_url"] == body["meeting_url"] and sent["title"] == "Comité"
    assert sent["bot_name"] == "Asistente Acten" and "project_id" not in sent
    # Repetir con el mismo external_id no crea otra captura.
    assert client.post("/api/v1/meetings/live", headers=h, json=body).json()["id"] == live["id"]
    with Session(engine) as db:
        links = db.exec(select(BotControlLink).where(BotControlLink.external_id == "altum-1")).all()
        project = db.exec(select(Project).where(Project.external_ref == "ext-p")).first()
        assert len(links) == 1 and links[0].project_id == project.id
        # Al llegar la reunión, la sesión nace en ese proyecto.
        row = ingest(db, event(1, mid=live["id"]))
        assert db.get(MeetingSession, row.session_id).project_id == project.id
        session_id = row.session_id
    estado = client.get(f"/api/v1/meetings/live/{live['id']}", headers=h)
    assert estado.status_code == 200, estado.text
    assert estado.json() == {"id": live["id"], "external_id": "altum-1", "state": "joining",
                             "title": "Reunión", "error_code": None,
                             "project_external_id": "ext-p", "acten_session_id": session_id}
    assert client.get(f"/api/v1/meetings/live/{uuid.uuid4()}", headers=h).status_code == 404


def test_public_live_meeting_validates_consent_project_scope_and_source(control):
    client, engine, _ = control
    h = _api_key(engine, ["sessions:read", "sessions:write", "org:read"])
    ok = {"meeting_url": "https://meet.google.com/abc-defg-hij", "recording_authorized": True}
    post = lambda body, headers=h: client.post("/api/v1/meetings/live", headers=headers, json=body)  # noqa: E731
    assert post({"meeting_url": ok["meeting_url"]}).status_code == 422                 # sin consentimiento
    assert post({**ok, "recording_authorized": False}).status_code == 422
    assert post({**ok, "project_external_id": "ext-ajeno"}).status_code == 422         # proyecto de otra empresa
    assert post({**ok, "desconocido": 1}).status_code == 422
    solo_lectura = _api_key(engine, ["sessions:read", "org:read"])
    assert post(ok, solo_lectura).status_code == 403
    assert post(ok).status_code == 202                                                # sin external_id: se genera
    # Empresa que sigue en Fireflies: la API no manda el bot.
    fireflies = _api_key(engine, ["sessions:write", "org:read"], source="fireflies")
    assert post(ok, fireflies).status_code == 409
