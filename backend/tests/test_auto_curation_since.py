"""Encender la curación automática no despacha las sesiones pendientes anteriores."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

from sqlmodel import Session, select

from auth_utils import create_access_token, get_password_hash
from models import IntegrationSetting, MeetingSession, Role, Tenant, User
from services import cron_service


def _empresa(db):
    t = Tenant(slug=f"ac-{uuid.uuid4().hex[:8]}", name="AC")
    db.add(t); db.commit(); db.refresh(t)
    rol = db.exec(select(Role).where(Role.name == "admin")).first()
    if not rol:
        rol = Role(name="admin"); db.add(rol); db.commit(); db.refresh(rol)
    u = User(email=f"ana-{t.slug}@ac.test", full_name="Ana", hashed_password=get_password_hash("x"),
             role_id=rol.id, tenant_id=t.id, is_active=True)
    db.add(u); db.commit()
    return t, {"Authorization": "Bearer " + create_access_token({"sub": u.email, "tenant_id": t.id})}


def test_al_encenderla_queda_la_fecha_y_se_conserva(client, db_session):
    t, h = _empresa(db_session)
    client.post("/api/settings", headers=h, json={"autoCuration": {"isEnabled": False, "timeoutMinutes": 60}})
    fila = lambda: json.loads(db_session.exec(select(IntegrationSetting).where(  # noqa: E731
        IntegrationSetting.tenant_id == t.id, IntegrationSetting.provider_name == "autoCuration")).first().config_json)
    db_session.expire_all()
    assert "enabledSince" not in fila()
    client.post("/api/settings", headers=h, json={"autoCuration": {"isEnabled": True, "timeoutMinutes": 60}})
    db_session.expire_all()
    desde = fila()["enabledSince"]
    assert desde[:4] == str(datetime.now().year)
    client.post("/api/settings", headers=h, json={"autoCuration": {"isEnabled": True, "timeoutMinutes": 30}})
    db_session.expire_all()
    assert fila()["enabledSince"] == desde and fila()["timeoutMinutes"] == 30
    assert cron_service._auto_curation_since(db_session, t.id) == desde


def test_el_cron_salta_lo_anterior_a_la_activacion(db_session, test_engine, monkeypatch):
    t, _ = _empresa(db_session)
    ahora = datetime.now()
    db_session.add(IntegrationSetting(tenant_id=t.id, provider_name="autoCuration", is_active=True, config_json=json.dumps(
        {"isEnabled": True, "timeoutMinutes": 0, "enabledSince": (ahora - timedelta(hours=1)).isoformat()})))
    vieja = MeetingSession(tenant_id=t.id, fireflies_id=f"v-{t.slug}", title="vieja", date="2026-10-01T10:00:00", status="pending", raw_transcript="", raw_summary="",
                           processing_error="", created_at=(ahora - timedelta(days=3)).isoformat())
    nueva = MeetingSession(tenant_id=t.id, fireflies_id=f"n-{t.slug}", title="nueva", date="2026-10-01T10:00:00", status="pending", raw_transcript="", raw_summary="",
                           processing_error="", created_at=(ahora - timedelta(minutes=5)).isoformat())
    db_session.add(vieja); db_session.add(nueva); db_session.commit()

    despachadas = []

    async def falso(session_id):
        despachadas.append(session_id)
    monkeypatch.setattr(cron_service, "_auto_dispatch_session", falso)
    monkeypatch.setattr(cron_service, "engine", test_engine)
    cron_service.check_and_dispatch_pending_sessions()
    assert despachadas == [nueva.id]
