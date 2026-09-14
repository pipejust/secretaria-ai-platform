"""Suscripción por empresa: catálogo, prueba, gating por plan, checkout y eventos de Wompi."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, select

import database
from auth_utils import create_access_token, get_password_hash
from models import IntegrationSetting, Payment, Role, Subscription, Tenant, User
from services import billing_catalog as cat
from services import entitlements, meeting_source, wompi
from services.cifrado import cifrar


def _rol(db, nombre):
    r = db.exec(select(Role).where(Role.name == nombre)).first()
    if not r:
        r = Role(name=nombre); db.add(r); db.commit(); db.refresh(r)
    return r


def _empresa(db, *, dias=0, slug=None):
    suf = uuid.uuid4().hex[:8]
    t = Tenant(slug=slug or f"bill-{suf}", name="Bill",
               created_at=(datetime.now() - timedelta(days=dias)).isoformat())
    db.add(t); db.commit(); db.refresh(t)
    return t


def _token(db, t, rol="admin", superadmin=False):
    suf = uuid.uuid4().hex[:6]
    u = User(email=f"{rol}-{suf}@b.test", full_name=rol, hashed_password=get_password_hash("x"),
             role_id=_rol(db, rol).id, tenant_id=t.id, is_active=True, is_superadmin=superadmin)
    db.add(u); db.commit(); db.refresh(u)
    return {"Authorization": "Bearer " + create_access_token({"sub": u.email, "tenant_id": t.id})}


@pytest.fixture()
def catalogo(test_engine, monkeypatch, db_session: Session):
    monkeypatch.setattr(database, "engine", test_engine)
    cat.sembrar_catalogo(db_session)
    return db_session


def test_catalogo_sembrado_y_editable(client, catalogo, db_session):
    duenio = db_session.exec(select(Tenant).where(Tenant.slug == "acten")).first() or _empresa(db_session, slug="acten")
    sa = _token(db_session, duenio, superadmin=True)
    r = client.get("/api/billing/catalog/all", headers=sa)
    assert r.status_code == 200
    claves = {p["key"] for p in r.json()["plans"]}
    assert {"starter", "business", "enterprise"} <= claves
    assert {a["key"] for a in r.json()["addons"]} >= {"owned_bot", "video_recording"}
    r = client.put("/api/billing/catalog/plans/starter", headers=sa, json={"price_cop_cents": 19900000})
    assert r.status_code == 200 and r.json()["price_cop_cents"] == 19900000
    r = client.put("/api/billing/catalog/plans/starter", headers=sa, json={"features": ["no.existe"]})
    assert r.status_code == 422
    # Un administrador normal ve solo los planes públicos
    t = _empresa(db_session)
    r = client.get("/api/billing/catalog", headers=_token(db_session, t))
    assert "enterprise" not in {p["key"] for p in r.json()["plans"]}


def test_empresa_nueva_en_prueba_y_luego_sin_plan(catalogo, db_session):
    nueva = _empresa(db_session)
    e = entitlements.de_empresa(db_session, nueva.id)
    assert e.status == "trialing" and e.tiene(cat.F_OWNED_BOT) and e.tiene(cat.F_FIREFLIES)
    vieja = _empresa(db_session, dias=cat.DIAS_PRUEBA + 1)
    e = entitlements.de_empresa(db_session, vieja.id)
    assert e.status == "none" and not e.features
    # Las empresas anteriores a la facturación reciben Business manual
    creadas = cat.asegurar_suscripciones_existentes(db_session)
    assert creadas >= 1
    e = entitlements.de_empresa(db_session, vieja.id)
    assert e.plan_key == "business" and e.tiene(cat.F_ASK) and not e.tiene(cat.F_OWNED_BOT)
    assert entitlements.de_empresa(db_session, nueva.id).status == "trialing"


def test_plan_limita_el_origen_de_reuniones_y_el_bot(client, catalogo, db_session):
    t = _empresa(db_session, dias=cat.DIAS_PRUEBA + 1)
    admin = _token(db_session, t)
    db_session.add(Subscription(tenant_id=t.id, plan_key="starter", status="active", billing_mode="manual"))
    db_session.commit()
    estado = client.get("/api/settings/meeting-source", headers=admin).json()
    assert estado["allowed"] == ["fireflies"]
    assert [o["available"] for o in estado["options"]] == [True, False, False]
    r = client.put("/api/settings/meeting-source", json={"source": "owned_bot"}, headers=admin)
    assert r.status_code == 402
    assert client.get("/api/owned-bot/config", headers=admin).status_code == 402
    assert client.get("/api/owned-bot/config", headers=admin).json()["detail"]["feature"] == cat.F_OWNED_BOT
    # Con el add-on, todo se abre
    sub = db_session.exec(select(Subscription).where(Subscription.tenant_id == t.id)).first()
    sub.addons_json = json.dumps(["owned_bot"]); db_session.add(sub); db_session.commit()
    estado = client.get("/api/settings/meeting-source", headers=admin).json()
    assert estado["allowed"] == ["both", "fireflies", "owned_bot"]
    assert client.put("/api/settings/meeting-source", json={"source": "both"}, headers=admin).status_code == 200
    assert client.get("/api/owned-bot/config", headers=admin).status_code == 200
    assert meeting_source.admite(db_session, t.id, "owned_bot") is True


def test_plan_vencido_apaga_fireflies_tras_la_gracia(catalogo, db_session):
    t = _empresa(db_session, dias=100)
    fin = (datetime.now() - timedelta(days=entitlements.DIAS_GRACIA + 1)).isoformat()
    db_session.add(Subscription(tenant_id=t.id, plan_key="business", status="active",
                                billing_mode="wompi", current_period_end=fin))
    db_session.commit()
    assert entitlements.de_empresa(db_session, t.id).status == "expired"
    assert meeting_source.admite(db_session, t.id, "fireflies") is False
    reciente = (datetime.now() - timedelta(days=1)).isoformat()
    sub = db_session.exec(select(Subscription).where(Subscription.tenant_id == t.id)).first()
    sub.current_period_end = reciente; db_session.add(sub); db_session.commit()
    e = entitlements.de_empresa(db_session, t.id)
    assert e.status == "past_due" and e.tiene(cat.F_FIREFLIES)


def _config_wompi(db_session):
    duenio = db_session.exec(select(Tenant).where(Tenant.slug == "acten")).first() or _empresa(db_session, slug="acten")
    row = db_session.exec(select(IntegrationSetting).where(
        IntegrationSetting.tenant_id == duenio.id, IntegrationSetting.provider_name == "wompi")).first()
    if not row:
        row = IntegrationSetting(tenant_id=duenio.id, provider_name="wompi")
    row.config_json = json.dumps({"environment": "sandbox", "public_key": "pub_test_abc",
                                  "private_key": cifrar("prv_test_abc"),
                                  "events_secret": cifrar("test_events_secret"),
                                  "integrity_secret": cifrar("test_integrity_secret")})
    row.is_active = True
    db_session.add(row); db_session.commit()
    return duenio


def _evento(tx, secret):
    body = {"event": "transaction.updated", "data": {"transaction": tx}, "environment": "test",
            "signature": {"properties": ["transaction.id", "transaction.status", "transaction.amount_in_cents"]},
            "timestamp": 1700000000, "sent_at": "2026-09-13T00:00:00.000Z"}
    cadena = f"{tx['id']}{tx['status']}{tx['amount_in_cents']}{body['timestamp']}{secret}"
    body["signature"]["checksum"] = hashlib.sha256(cadena.encode()).hexdigest().upper()
    return body


def test_checkout_firma_y_evento_activan_la_suscripcion(client, catalogo, db_session):
    _config_wompi(db_session)
    t = _empresa(db_session, dias=cat.DIAS_PRUEBA + 1)
    admin = _token(db_session, t)
    r = client.post("/api/billing/checkout", headers=admin,
                    json={"plan_key": "business", "addons": ["video_recording"]})
    assert r.status_code == 422  # video requiere owned_bot
    r = client.post("/api/billing/checkout", headers=admin,
                    json={"plan_key": "business", "addons": ["owned_bot", "video_recording"]})
    assert r.status_code == 201, r.text
    c = r.json()
    esperado = 14900 * 4000 + (2900 + 1900) * 4000
    assert c["amount_in_cents"] == esperado and c["currency"] == "COP"
    assert c["public_key"] == "pub_test_abc" and "prv_" not in r.text and "secret" not in r.text
    assert c["signature_integrity"] == hashlib.sha256(
        f"{c['reference']}{esperado}COPtest_integrity_secret".encode()).hexdigest()
    # Evento con firma mala → 401; con firma buena → activa
    tx = {"id": "1234-tx", "reference": c["reference"], "status": "APPROVED",
          "amount_in_cents": esperado, "currency": "COP", "payment_method_type": "CARD"}
    malo = _evento(tx, "otro"); assert client.post("/api/webhook/wompi", json=malo).status_code == 401
    assert client.post("/api/webhook/wompi", json=_evento(tx, "test_events_secret")).status_code == 200
    db_session.expire_all()
    pago = db_session.exec(select(Payment).where(Payment.reference == c["reference"])).first()
    assert pago.status == "approved" and pago.wompi_transaction_id == "1234-tx"
    e = entitlements.de_empresa(db_session, t.id)
    assert e.status == "active" and e.plan_key == "business" and e.tiene(cat.F_VIDEO) and e.tiene(cat.F_OWNED_BOT)
    assert e.billing_mode == "wompi" and e.period_end > datetime.now().isoformat()
    # Evento repetido: idempotente
    assert client.post("/api/webhook/wompi", json=_evento(tx, "test_events_secret")).status_code == 200
    me = client.get("/api/billing/me", headers=admin).json()
    assert me["subscription"]["addons"] == ["owned_bot", "video_recording"]
    assert me["payments_enabled"] is True
    assert client.get("/api/billing/payments", headers=admin).json()["items"][0]["status"] == "approved"


def test_evento_con_monto_distinto_no_activa(client, catalogo, db_session):
    _config_wompi(db_session)
    t = _empresa(db_session, dias=cat.DIAS_PRUEBA + 1)
    admin = _token(db_session, t)
    c = client.post("/api/billing/checkout", headers=admin, json={"plan_key": "starter"}).json()
    tx = {"id": "9-tx", "reference": c["reference"], "status": "APPROVED",
          "amount_in_cents": 100, "currency": "COP"}
    assert client.post("/api/webhook/wompi", json=_evento(tx, "test_events_secret")).status_code == 200
    db_session.expire_all()
    assert db_session.exec(select(Payment).where(Payment.reference == c["reference"])).first().status == "error"
    assert entitlements.de_empresa(db_session, t.id).status == "none"


def test_superadmin_asigna_plan_manual_y_configura_wompi(client, catalogo, db_session):
    duenio = db_session.exec(select(Tenant).where(Tenant.slug == "acten")).first() or _empresa(db_session, slug="acten")
    sa = _token(db_session, duenio, superadmin=True)
    t = _empresa(db_session, dias=100)
    admin = _token(db_session, t)
    assert client.put(f"/api/billing/tenants/{t.id}", headers=admin,
                      json={"plan_key": "enterprise"}).status_code == 403
    r = client.put(f"/api/billing/tenants/{t.id}", headers=sa,
                   json={"plan_key": "enterprise", "addons": [], "notes": "Contrato anual"})
    assert r.status_code == 200 and r.json()["entitlements"]["plan"] == "enterprise"
    assert cat.F_VIDEO in r.json()["entitlements"]["features"]
    listado = client.get("/api/billing/tenants", headers=sa).json()["items"]
    assert any(x["tenant_id"] == t.id and x["plan"] == "enterprise" for x in listado)
    r = client.put("/api/billing/wompi-config", headers=sa, json={
        "environment": "production", "public_key": "pub_test_x"})
    assert r.status_code == 422
    r = client.put("/api/billing/wompi-config", headers=sa, json={
        "environment": "sandbox", "public_key": "pub_test_x", "private_key": "prv_test_x",
        "events_secret": "ev", "integrity_secret": "integ"})
    assert r.status_code == 200 and r.json()["configured"] is True
    assert "prv_test_x" not in r.text
    row = db_session.exec(select(IntegrationSetting).where(IntegrationSetting.provider_name == "wompi")).first()
    assert "prv_test_x" not in row.config_json and "fer1:" in row.config_json
    # /auth/me expone las funciones para que el front oculte lo no incluido
    me = client.get("/auth/me", headers=admin).json()
    assert me["tenant"]["entitlements"]["plan"] == "enterprise"


def test_firma_de_evento_con_valores_anidados_y_booleanos():
    body = {"data": {"transaction": {"id": "a", "status": "APPROVED", "amount_in_cents": 5,
                                     "extra": {"ok": True}}},
            "signature": {"properties": ["transaction.id", "transaction.extra.ok", "transaction.amount_in_cents"]},
            "timestamp": 7}
    body["signature"]["checksum"] = hashlib.sha256(b"atrue57s3cr3t").hexdigest()
    assert wompi.verificar_evento(body, "s3cr3t") is True
    assert wompi.verificar_evento(body, "otro") is False
