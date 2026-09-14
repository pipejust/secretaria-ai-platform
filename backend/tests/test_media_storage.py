"""Vídeo de reuniones en bucket propio: config cifrada, copia al ingerir, gating por plan y URL firmada.

No hay `boto3`/`moto` en el entorno de pruebas: el cliente S3 se sustituye por
`FakeS3` vía `media_storage._cliente`, y la descarga de la grabación usa un
`httpx.MockTransport`. Ninguna prueba toca la red.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta

import httpx
import pytest
from sqlmodel import Session, select

import database
from auth_utils import create_access_token, get_password_hash
from models import IntegrationSetting, MeetingSession, Role, Subscription, Tenant, User
from services import billing_catalog as cat
from services import media_storage
from services.bot_contract import ActenBotEvent
from services.bot_ingest import BotInbox, ingest, process_one

RECORDING_URL = "https://files.skribby.test/bots/abc/recording.webm"


class FakeS3:
    def __init__(self, *, fail_list=False, fail_upload=False):
        self.fail_list, self.fail_upload = fail_list, fail_upload
        self.uploaded: list[tuple] = []
        self.deleted: list[tuple] = []

    def list_objects_v2(self, Bucket, MaxKeys):
        if self.fail_list:
            raise RuntimeError("AccessDenied")
        return {"KeyCount": 0}

    def upload_fileobj(self, fileobj, bucket, key, ExtraArgs=None):
        data = fileobj.read()
        if self.fail_upload:
            raise RuntimeError("upload exploded")
        self.uploaded.append((bucket, key, data, ExtraArgs))

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        return f"https://s3.test/{Params['Bucket']}/{Params['Key']}?X-Amz-Expires={ExpiresIn}"

    def delete_object(self, Bucket, Key):
        self.deleted.append((Bucket, Key))


def _rol(db, nombre):
    r = db.exec(select(Role).where(Role.name == nombre)).first()
    if not r:
        r = Role(name=nombre); db.add(r); db.commit(); db.refresh(r)
    return r


def _empresa(db, *, dias=0, slug=None):
    suf = uuid.uuid4().hex[:8]
    t = Tenant(slug=slug or f"vid-{suf}", name="Vídeo",
               created_at=(datetime.now() - timedelta(days=dias)).isoformat())
    db.add(t); db.commit(); db.refresh(t)
    return t


def _duenio(db):
    return db.exec(select(Tenant).where(Tenant.slug == "acten")).first() or _empresa(db, slug="acten")


def _token(db, t, rol="admin", superadmin=False):
    suf = uuid.uuid4().hex[:6]
    u = User(email=f"{rol}-{suf}@v.test", full_name=rol, hashed_password=get_password_hash("x"),
             role_id=_rol(db, rol).id, tenant_id=t.id, is_active=True, is_superadmin=superadmin)
    db.add(u); db.commit(); db.refresh(u)
    return {"Authorization": "Bearer " + create_access_token({"sub": u.email, "tenant_id": t.id})}


def _configurar(db, secret="s3cr3t-key-test"):
    _duenio(db)
    return media_storage.guardar_config(
        db, endpoint_url="https://fsn1.your-objectstorage.com", region="fsn1",
        bucket="acten-video", access_key="AK123", secret_key=secret,
    )


def _evento(tenant_id, *, kind="video", url=RECORDING_URL):
    mid = str(uuid.uuid4())
    return ActenBotEvent.model_validate({
        "id": f"meeting.completed:{mid}:v1",
        "meeting_id": mid,
        "tenant_id": tenant_id,
        "occurred_at": "2026-09-12T17:01:00Z",
        "data": {
            "external_id": "agenda-video",
            "title": "Piloto con vídeo",
            "date": "2026-09-12T08:00:00-05:00",
            "date_source": "scheduled_start",
            "language_hint": "es",
            "timebase": "seconds_from_recording_start",
            "transcript": [{"id": "seg_1", "start": 0, "end": 4, "text": "Revisamos el piloto.",
                            "speaker_id": "0", "speaker_name": "Ana"}],
            "summary": [{"text": "Revisión del piloto", "evidence_ids": ["seg_1"]}],
            "key_points": [],
            "timeline": [],
            "participants": [],
            "recording": {"url": url, "kind": kind, "format": "webm",
                          "expires_at": "2026-09-19T17:01:00+00:00"},
            "warnings": [],
            "provenance": {"analysis_scope": "base"},
        },
    })


@pytest.fixture(autouse=True)
def _sin_config_previa(db_session):
    # El engine de pruebas es uno por sesión y `guardar_config` hace commit:
    # sin esto, la configuración de una prueba se cuela en la siguiente.
    for row in db_session.exec(select(IntegrationSetting).where(
            IntegrationSetting.provider_name == media_storage.PROVIDER)).all():
        db_session.delete(row)
    db_session.commit()


@pytest.fixture()
def s3(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(media_storage, "_cliente", lambda cfg: fake)
    # El host de prueba no resuelve; el filtro anti-SSRF real se prueba en calendarios.
    monkeypatch.setattr(media_storage, "_origen_permitido", lambda url: None)
    return fake


@pytest.fixture()
def descarga(monkeypatch):
    def handler(request):
        assert request.url == RECORDING_URL
        return httpx.Response(200, content=b"webm-bytes", headers={"Content-Type": "video/webm"})
    monkeypatch.setattr(media_storage, "_transport_descarga", httpx.MockTransport(handler))


async def _pipeline_ok(db, session_id):
    meeting = db.get(MeetingSession, session_id)
    meeting.processing_completed_at = datetime.now().isoformat()
    db.add(meeting)
    db.commit()


def _procesar(engine, db, tenant_id, **evento):
    row = ingest(db, _evento(tenant_id, **evento))
    assert asyncio.run(process_one(engine, row.id, pipeline=_pipeline_ok)) is True
    with Session(engine) as fresh:
        return fresh.get(BotInbox, row.id), fresh.get(MeetingSession, row.session_id)


# ─────────────────────────── configuración ───────────────────────────


def test_config_cifrada_solo_superadmin_y_secreto_conservado(client, db_session, monkeypatch):
    monkeypatch.setattr(database, "engine", db_session.get_bind())
    duenio = _duenio(db_session)
    sa = _token(db_session, duenio, superadmin=True)
    admin = _token(db_session, _empresa(db_session))
    assert client.get("/api/media-storage/config", headers=admin).status_code == 403
    assert client.get("/api/media-storage/config", headers=sa).json()["configured"] is False

    body = {"endpoint_url": "https://fsn1.your-objectstorage.com", "region": "fsn1",
            "bucket": "acten-video", "access_key": "AK123", "secret_key": "s3cr3t-key-test"}
    r = client.put("/api/media-storage/config", headers=sa, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["configured"] is True and r.json()["has_secret_key"] is True
    assert "s3cr3t-key-test" not in r.text
    row = db_session.exec(select(IntegrationSetting).where(
        IntegrationSetting.provider_name == "object_storage")).one()
    assert row.tenant_id == duenio.id and row.user_id is None
    assert "s3cr3t-key-test" not in row.config_json and "fer1:" in row.config_json
    assert media_storage.leer_config(db_session).secret_key == "s3cr3t-key-test"

    # Vacío = conservar la secret guardada; el resto sí se actualiza.
    r = client.put("/api/media-storage/config", headers=sa, json={**body, "bucket": "otro", "secret_key": ""})
    assert r.status_code == 200 and r.json()["bucket"] == "otro"
    assert media_storage.leer_config(db_session).secret_key == "s3cr3t-key-test"
    estado = client.get("/api/media-storage/config", headers=sa).json()
    assert estado["access_key"] == "AK123" and "secret" not in json.dumps(estado).replace("has_secret_key", "")

    r = client.put("/api/media-storage/config", headers=sa, json={**body, "endpoint_url": "http://interno:9000"})
    assert r.status_code == 422


def test_probar_lista_el_bucket_y_devuelve_502_si_falla(client, db_session, s3, monkeypatch):
    monkeypatch.setattr(database, "engine", db_session.get_bind())
    sa = _token(db_session, _duenio(db_session), superadmin=True)
    assert client.post("/api/media-storage/test", headers=sa).status_code == 502  # sin configurar
    _configurar(db_session)
    r = client.post("/api/media-storage/test", headers=sa)
    assert r.status_code == 200 and r.json() == {
        "ok": True, "bucket": "acten-video", "endpoint_url": "https://fsn1.your-objectstorage.com"}
    s3.fail_list = True
    r = client.post("/api/media-storage/test", headers=sa)
    assert r.status_code == 502 and "acten-video" in r.json()["detail"]


# ─────────────────────────── ingesta ───────────────────────────


def test_ingesta_copia_el_video_con_funcion_y_bucket(test_engine, db_session, s3, descarga):
    _configurar(db_session)
    t = _empresa(db_session)  # nueva → prueba con todas las funciones
    assert cat.F_VIDEO in __import__("services.entitlements", fromlist=["x"]).de_empresa(db_session, t.id).features
    row, meeting = _procesar(test_engine, db_session, t.id)
    key = f"tenants/{t.id}/sessions/{meeting.id}/recording.webm"
    assert row.state == "completed" and row.error_code == ""
    assert meeting.recording_video_key == key
    assert s3.uploaded == [("acten-video", key, b"webm-bytes", {"ContentType": "video/webm"})]
    # Volver a procesar no vuelve a subir.
    with Session(test_engine) as db:
        db.get(BotInbox, row.id).state = "queued"; db.commit()
    assert asyncio.run(process_one(test_engine, row.id, pipeline=_pipeline_ok)) is True
    assert len(s3.uploaded) == 1


def test_ingesta_sin_bucket_no_falla_y_avisa(test_engine, db_session, s3, descarga, caplog):
    t = _empresa(db_session)
    with caplog.at_level(logging.WARNING, logger="services.bot_ingest"):
        row, meeting = _procesar(test_engine, db_session, t.id)
    assert row.state == "completed" and row.error_code == ""
    assert meeting.recording_video_key is None
    assert s3.uploaded == []
    assert any("no está configurado" in r.getMessage() for r in caplog.records)


def test_ingesta_sin_funcion_o_solo_audio_no_copia(test_engine, db_session, s3, descarga):
    _configurar(db_session)
    t = _empresa(db_session, dias=cat.DIAS_PRUEBA + 1)  # prueba vencida, sin plan
    row, meeting = _procesar(test_engine, db_session, t.id)
    assert row.state == "completed" and meeting.recording_video_key is None
    t2 = _empresa(db_session)
    row, meeting = _procesar(test_engine, db_session, t2.id, kind="audio")
    assert row.state == "completed" and meeting.recording_video_key is None
    assert s3.uploaded == []


def test_fallo_de_copia_deja_error_visible_pero_la_sesion_queda(test_engine, db_session, s3, descarga):
    _configurar(db_session)
    s3.fail_upload = True
    t = _empresa(db_session)
    row, meeting = _procesar(test_engine, db_session, t.id)
    assert row.state == "completed" and row.error_code == "video_copy_failed"
    assert meeting.recording_video_key is None
    assert meeting.processing_error == ""


# ─────────────────────────── plan ───────────────────────────


def test_pedir_video_sin_la_funcion_devuelve_402(client, db_session, monkeypatch):
    monkeypatch.setattr(database, "engine", db_session.get_bind())
    cat.sembrar_catalogo(db_session)
    t = _empresa(db_session, dias=cat.DIAS_PRUEBA + 1)
    admin = _token(db_session, t)
    sub = Subscription(tenant_id=t.id, plan_key="business", status="active", billing_mode="manual",
                       addons_json=json.dumps(["owned_bot"]))
    db_session.add(sub); db_session.commit()
    body = {"external_id": "agenda-9", "meeting_url": "https://meet.google.com/abc-defg-hij",
            "recording_authorized": True, "video": True}
    r = client.post("/api/owned-bot/start/meeting", headers=admin, json=body)
    assert r.status_code == 402, r.text
    assert r.json()["detail"]["feature"] == cat.F_VIDEO
    # Con el add-on de vídeo pasa la puerta del plan (y falla después por no
    # tener el bot conectado, que es otra cosa).
    sub.addons_json = json.dumps(["owned_bot", "video_recording"]); db_session.add(sub); db_session.commit()
    r = client.post("/api/owned-bot/start/meeting", headers=admin, json=body)
    assert r.status_code == 503


# ─────────────────────────── reproducción ───────────────────────────


def test_url_firmada_solo_para_la_empresa_y_404_sin_video(client, db_session, s3, monkeypatch):
    monkeypatch.setattr(database, "engine", db_session.get_bind())
    t, otra = _empresa(db_session), _empresa(db_session)
    con = MeetingSession(tenant_id=t.id, fireflies_id="BOT-1", title="Con vídeo", date="2026-09-12",
                         recording_video_key=f"tenants/{t.id}/sessions/1/recording.webm")
    sin = MeetingSession(tenant_id=t.id, fireflies_id="BOT-2", title="Sin vídeo", date="2026-09-12")
    db_session.add(con); db_session.add(sin); db_session.commit()
    db_session.refresh(con); db_session.refresh(sin)
    mia, ajena = _token(db_session, t, rol="viewer"), _token(db_session, otra)

    assert client.get(f"/api/sessions/{con.id}/video", headers=mia).status_code == 503  # bucket sin configurar
    _configurar(db_session)
    r = client.get(f"/api/sessions/{con.id}/video", headers=mia)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "url": f"https://s3.test/acten-video/{con.recording_video_key}?X-Amz-Expires=900",
        "expires_in": 900,
    }
    assert client.get(f"/api/sessions/{sin.id}/video", headers=mia).status_code == 404
    assert client.get(f"/api/sessions/{con.id}/video", headers=ajena).status_code == 404


def test_borrar_y_lector_en_streaming(db_session, s3):
    _configurar(db_session)
    media_storage.borrar(db_session, "tenants/1/sessions/1/recording.webm")
    assert s3.deleted == [("acten-video", "tenants/1/sessions/1/recording.webm")]
    lector = media_storage._Lector(iter([b"abc", b"def", b"g"]), limite=100)
    assert lector.read(4) == b"abcd" and lector.read() == b"efg" and lector.bytes == 7
    grande = media_storage._Lector(iter([b"x" * 10]), limite=5)
    with pytest.raises(media_storage.StorageError):
        grande.read()
