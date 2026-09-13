"""Owned-bot ingress: real API auth/SQL persistence, no external services."""

import asyncio
import hashlib
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from database import get_session
from models import ApiKey, MeetingSession, Tenant, User
from routers.bot_ingest import router
from services.bot_contract import ActenBotEvent, event_signature
from services.bot_ingest import BotInbox, process_one

KEY_A, KEY_B, KEY_READ = "bot-test-a" * 5, "bot-test-b" * 5, "bot-read" * 6


@pytest.fixture
def bot_api(tmp_path, monkeypatch):
    def forbid_network(*args, **kwargs):
        raise AssertionError("External HTTP forbidden in bot ingress tests")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbid_network)
    monkeypatch.setattr(
        httpx.AsyncHTTPTransport, "handle_async_request", forbid_network
    )
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ingest.db'}", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        for number, key in [(1, KEY_A), (2, KEY_B)]:
            db.add(Tenant(id=number, slug=f"bot-{number}", name=f"Bot {number}"))
            db.add(
                User(
                    id=number,
                    tenant_id=number,
                    email=f"bot{number}@example.test",
                    full_name="Bot integration",
                    hashed_password="unused-test-only",
                )
            )
            db.commit()
            db.add(
                ApiKey(
                    tenant_id=number,
                    user_id=number,
                    name="Bot test",
                    hashed_key=hashlib.sha256(key.encode()).hexdigest(),
                    scopes=json.dumps(["sessions:write", "sessions:read", "org:read"]),
                )
            )
        db.add(
            ApiKey(
                tenant_id=1,
                user_id=1,
                name="Read only",
                hashed_key=hashlib.sha256(KEY_READ.encode()).hexdigest(),
                scopes=json.dumps(["sessions:read", "org:read"]),
            )
        )
        db.commit()

    def session_dep():
        with Session(engine) as session:
            yield session

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = session_dep
    with TestClient(app) as client:
        yield client, engine
    engine.dispose()


@pytest.fixture
def bot_event():
    mid = str(uuid.uuid4())
    return {
        "schema_version": "acten.bot.v1",
        "type": "meeting.completed",
        "id": f"meeting.completed:{mid}:v1",
        "meeting_id": mid,
        "tenant_id": 1,
        "occurred_at": "2026-09-12T17:01:00Z",
        "data": {
            "external_id": "agenda-1",
            "title": "Piloto",
            "date": "2026-09-12T08:00:00-05:00",
            "date_source": "scheduled_start",
            "language_hint": "es",
            "timebase": "seconds_from_recording_start",
            "transcript": [
                {
                    "id": "seg_1",
                    "start": 0,
                    "end": 4,
                    "text": "Revisamos el piloto.",
                    "speaker_id": "0",
                    "speaker_name": "Ana",
                }
            ],
            "summary": [{"text": "Revisión del piloto", "evidence_ids": ["seg_1"]}],
            "key_points": [],
            "timeline": [
                {
                    "title": "Piloto",
                    "description": "Revisión",
                    "evidence_ids": ["seg_1"],
                    "start": 0,
                    "end": 4,
                }
            ],
            "participants": [],
            "recording": {"url": None},
            "warnings": [],
            "provenance": {"analysis_scope": "base"},
        },
    }


def signed(event, key=KEY_A, stamp=None):
    body = json.dumps(event, ensure_ascii=False).encode()
    stamp = str(int(time.time())) if stamp is None else str(stamp)
    return body, {
        "Content-Type": "application/json",
        "X-API-Key": key,
        "X-Bot-Event-Id": event["id"],
        "X-Bot-Timestamp": stamp,
        "X-Bot-Signature": event_signature(key, stamp, body),
    }


def post(client, event, key=KEY_A):
    raw, headers = signed(event, key)
    return client.post("/api/v1/bot/meetings", content=raw, headers=headers)


def test_compressed_source_is_verified_before_decoding(bot_api, bot_event):
    import gzip

    client, _ = bot_api
    raw, headers = signed(bot_event)
    compressed = gzip.compress(raw, mtime=0)
    headers["Content-Encoding"] = "gzip"
    # A signature of the uncompressed JSON is invalid for the bytes on the wire.
    assert (
        client.post(
            "/api/v1/bot/meetings", content=compressed, headers=headers
        ).status_code
        == 401
    )
    headers["X-Bot-Signature"] = event_signature(
        KEY_A, headers["X-Bot-Timestamp"], compressed
    )
    assert (
        client.post(
            "/api/v1/bot/meetings", content=compressed, headers=headers
        ).status_code
        == 202
    )
    assert post(client, bot_event).status_code == 202


def test_compressed_source_size_is_bounded(bot_api, bot_event, monkeypatch):
    import gzip
    import routers.bot_ingest as ingress

    client, _ = bot_api
    monkeypatch.setattr(ingress, "MAX_EXPANDED_BODY", 100)
    raw, headers = signed(bot_event)
    compressed = gzip.compress(raw, mtime=0)
    headers.update(
        {
            "Content-Encoding": "gzip",
            "X-Bot-Signature": event_signature(
                KEY_A, headers["X-Bot-Timestamp"], compressed
            ),
        }
    )
    assert (
        client.post(
            "/api/v1/bot/meetings", content=compressed, headers=headers
        ).status_code
        == 413
    )


def test_durable_receipt_and_duplicate_do_not_overwrite_curation(bot_api, bot_event):
    client, engine = bot_api
    first = post(client, bot_event)
    assert first.status_code == 202, first.text
    sid = first.json()["session_id"]
    with Session(engine) as db:
        meeting = db.get(MeetingSession, sid)
        assert meeting.raw_transcript == "[Ana] Revisamos el piloto."
        assert "Revisión del piloto" in meeting.raw_summary
        assert meeting.status == "processing"
        meeting.raw_summary = "Revisión humana"
        db.add(meeting)
        db.commit()
    second = post(client, bot_event)
    assert second.json()["session_id"] == sid
    with Session(engine) as db:
        assert len(db.exec(select(MeetingSession)).all()) == 1
        assert len(db.exec(select(BotInbox)).all()) == 1
        assert db.get(MeetingSession, sid).raw_summary == "Revisión humana"
        row = db.exec(select(BotInbox)).one()
        source = ActenBotEvent.model_validate_json(row.payload)
        assert source.data.timeline[0].end == 4


@pytest.mark.parametrize(
    "case,status",
    [
        ("missing_key", 401),
        ("wrong_scope", 403),
        ("other_tenant", 403),
        ("modified_body", 401),
        ("expired", 401),
        ("event_id", 422),
        ("evidence", 422),
        ("inactive", 403),
        ("revoked", 401),
    ],
)
def test_ingress_rejects_invalid_auth_and_contract(bot_api, bot_event, case, status):
    client, engine = bot_api
    key = (
        KEY_READ
        if case == "wrong_scope"
        else KEY_B
        if case == "other_tenant"
        else KEY_A
    )
    if case == "evidence":
        bot_event["data"]["summary"][0]["evidence_ids"] = ["invented"]
    raw, headers = signed(
        bot_event, key, int(time.time()) - 301 if case == "expired" else None
    )
    if case == "missing_key":
        headers.pop("X-API-Key")
    if case == "modified_body":
        raw += b" "
    if case == "event_id":
        headers["X-Bot-Event-Id"] = "different"
    if case in {"inactive", "revoked"}:
        with Session(engine) as db:
            if case == "inactive":
                row = db.get(Tenant, 1)
                row.is_active = False
            else:
                row = db.exec(select(ApiKey).where(ApiKey.name == "Bot test")).first()
                row.revoked_at = "2026-09-12"
            db.add(row)
            db.commit()
    response = client.post("/api/v1/bot/meetings", content=raw, headers=headers)
    assert response.status_code == status, response.text
    with Session(engine) as db:
        assert db.exec(select(BotInbox)).first() is None


def test_changed_payload_for_existing_bot_conflicts(bot_api, bot_event):
    client, _ = bot_api
    assert post(client, bot_event).status_code == 202
    bot_event["data"]["title"] = "Changed"
    assert post(client, bot_event).status_code == 409


def test_source_requires_tenant_and_person_visibility(bot_api, bot_event):
    client, _ = bot_api
    sid = post(client, bot_event).json()["session_id"]
    path = f"/api/v1/sessions/{sid}/bot-source"
    assert (
        client.get(
            path, headers={"X-API-Key": KEY_B, "X-On-Behalf-Of": "*"}
        ).status_code
        == 404
    )
    assert (
        client.get(
            path, headers={"X-API-Key": KEY_A, "X-On-Behalf-Of": "unknown-employee"}
        ).status_code
        == 404
    )
    response = client.get(path, headers={"X-API-Key": KEY_A, "X-On-Behalf-Of": "*"})
    assert response.status_code == 200
    assert response.json()["source"]["data"]["timeline"][0]["end"] == 4


def test_worker_claim_runs_pipeline_once_and_keeps_native_summary(bot_api, bot_event):
    client, engine = bot_api
    post(client, bot_event)
    calls = []

    async def pipeline(db, session_id):
        calls.append(session_id)
        assert not await process_one(engine, 1, pipeline=pipeline)
        meeting = db.get(MeetingSession, session_id)
        assert "Revisión del piloto" in meeting.raw_summary
        meeting.processing_completed_at = "2026-09-12T17:02:00Z"
        meeting.status = "pending"
        db.add(meeting)
        db.commit()

    assert asyncio.run(process_one(engine, 1, pipeline=pipeline))
    assert not asyncio.run(process_one(engine, 1, pipeline=pipeline))
    assert len(calls) == 1
    with Session(engine) as db:
        assert db.get(BotInbox, 1).state == "completed"


def test_failed_processing_retries_without_creating_another_session(bot_api, bot_event):
    client, engine = bot_api
    first = post(client, bot_event).json()

    async def fail(db, session_id):
        raise RuntimeError("simulated private error")

    asyncio.run(process_one(engine, 1, pipeline=fail))
    path = f"/api/v1/bot/meetings/{bot_event['meeting_id']}/retry"
    assert client.post(path, headers={"X-API-Key": KEY_B}).status_code == 404
    retried = client.post(path, headers={"X-API-Key": KEY_A})
    assert retried.status_code == 202
    assert retried.json()["session_id"] == first["session_id"]
    assert retried.json()["state"] == "queued"
    assert client.post(path, headers={"X-API-Key": KEY_A}).status_code == 409


def test_interrupted_processing_is_not_automatically_repeated(bot_api, bot_event):
    client, engine = bot_api
    post(client, bot_event)
    with Session(engine) as db:
        row = db.get(BotInbox, 1)
        row.state = "processing"
        row.started_at = 1
        db.add(row)
        db.commit()

    async def forbidden(*args):
        raise AssertionError("Interrupted job must be reconciled first")

    assert not asyncio.run(process_one(engine, 1, pipeline=forbidden))
    response = client.post(
        f"/api/v1/bot/meetings/{bot_event['meeting_id']}/retry",
        headers={"X-API-Key": KEY_A},
    )
    assert response.status_code == 409


def test_real_bot_sender_delivers_to_acten_receiver(bot_api, bot_event):
    sender = pytest.importorskip("conversacionalbot.acten")
    from conversacionalbot.config import ActenTarget

    client, _ = bot_api
    data = bot_event["data"]
    event = {
        "id": bot_event["id"],
        "meeting_id": bot_event["meeting_id"],
        "external_id": data["external_id"],
        "tenant_id": "bot-company-a",
        "result": {
            **data,
            "completed_at": bot_event["occurred_at"],
            "transcription_model": "test",
            "analysis_usage": [],
            "analysis_version": "0.1.0",
            "analysis_scope": "base",
            "stop_reason": "meeting_ended",
        },
    }

    def transport(request):
        return client.post(
            "/api/v1/bot/meetings",
            content=request.content,
            headers=dict(request.headers),
        )

    delivery = sender.ActenDelivery(
        ActenTarget(
            url="https://acten.example.test/api/v1/bot/meetings",
            api_key=KEY_A,
            tenant_id=1,
        ),
        httpx.MockTransport(transport),
    )
    delivery.send(event)
    delivery.send(event)


def test_concurrent_delivery_creates_only_one_session(bot_api, bot_event):
    client, engine = bot_api
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda _: post(client, bot_event), range(6)))
    assert [r.status_code for r in responses] == [202] * 6
    assert len({r.json()["session_id"] for r in responses}) == 1
    with Session(engine) as db:
        assert len(db.exec(select(MeetingSession)).all()) == 1


def test_owned_bot_never_enters_fireflies_retry(bot_api, bot_event, monkeypatch):
    from services import cron_service, fireflies_service

    client, engine = bot_api
    sid = post(client, bot_event).json()["session_id"]
    with Session(engine) as db:
        meeting = db.get(MeetingSession, sid)
        meeting.raw_summary = ""
        meeting.processing_error = "no_transcript"
        db.add(meeting)
        db.commit()
    called = []

    def forbidden(*args, **kwargs):
        called.append(True)
        raise AssertionError("Owned capture must not use Fireflies")

    monkeypatch.setattr(cron_service, "engine", engine)
    monkeypatch.setattr(fireflies_service, "get_fireflies_api_key", forbidden)
    cron_service.check_pending_summaries()
    assert called == []


def test_complete_bot_flow_into_acten(bot_api, tmp_path, monkeypatch):
    pytest.importorskip("conversacionalbot")
    from conversacionalbot import worker as bot_worker
    from conversacionalbot.acten import ActenDelivery
    from conversacionalbot.api import create_app
    from conversacionalbot.config import Settings
    from conversacionalbot.providers import GroqAnalysis, Skribby
    from conversacionalbot.store import Store

    acten_client, acten_engine = bot_api
    bot_key, remote_id = "bot-client-key" * 4, str(uuid.uuid4())
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'bot.db'}",
        bot_clients_json=json.dumps({bot_key: "company-a"}),
        skribby_api_key="test-only-capture",
        groq_api_key="test-only-analysis",
        acten_targets_json=json.dumps(
            {
                "company-a": {
                    "url": "https://acten.example.test/api/v1/bot/meetings",
                    "api_key": KEY_A,
                    "tenant_id": 1,
                }
            }
        ),
    )
    capture_calls, analysis_calls = [], []

    def capture_http(request):
        capture_calls.append(request.method)
        if request.method == "POST":
            assert json.loads(request.content)["service"] == "gmeet"
            return httpx.Response(201, json={"id": remote_id, "status": "booting"})
        return httpx.Response(
            200,
            json={
                "id": remote_id,
                "status": "finished",
                "stop_reason": "meeting_ended",
                "transcript": [
                    {
                        "start": 0,
                        "end": 4,
                        "transcript": "Revisamos el piloto.",
                        "speaker": 0,
                        "speaker_name": "Ana",
                    }
                ],
            },
        )

    def groq_http(request):
        payload = json.loads(request.content)
        analysis_calls.append(payload["response_format"]["json_schema"]["name"])
        segment_id = json.loads(payload["messages"][1]["content"])["evidence"][0]["id"]
        extraction = {
            "summary": [{"text": "Revisión del piloto", "evidence_ids": [segment_id]}],
            "key_points": [],
            "timeline": [],
        }
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(extraction)},
                    }
                ]
            },
        )

    def delivery_http(request):
        return acten_client.post(
            "/api/v1/bot/meetings",
            content=request.content,
            headers=dict(request.headers),
        )

    monkeypatch.setattr(
        bot_worker,
        "ActenDelivery",
        lambda target: ActenDelivery(target, httpx.MockTransport(delivery_http)),
    )
    store = Store(settings)
    capture = Skribby(settings, httpx.MockTransport(capture_http))
    analysis = GroqAnalysis(settings, httpx.MockTransport(groq_http))
    with TestClient(create_app(settings, store, capture)) as bot_client:
        response = bot_client.post(
            "/v1/meetings",
            json={
                "external_id": "calendar-event-1",
                "title": "Piloto integrado",
                "scheduled_start": "2026-09-12T08:00:00-05:00",
                "analysis_scope": "base",
                "meeting_url": "https://meet.google.com/abc-defg-hij",
                "recording_authorized": True,
            },
            headers={"Authorization": "Bearer " + bot_key},
        )
        assert response.status_code == 202
        mid = response.json()["id"]
        worker = bot_worker.Worker(store, capture, analysis)
        for _ in range(6):
            worker.step()
        assert store.get(mid).delivery_status == "sent"
        assert capture_calls == ["POST", "GET"]
        assert analysis_calls == ["BaseExtraction"]
        with Session(acten_engine) as db:
            meeting = db.exec(select(MeetingSession)).one()
            assert meeting.title == "Piloto integrado"
            assert meeting.fireflies_id == "BOT-" + mid
            assert meeting.tenant_id == 1
            assert meeting.raw_transcript == "[Ana] Revisamos el piloto."
            assert "Revisión del piloto" in meeting.raw_summary
            assert db.exec(select(BotInbox)).one().state == "queued"
