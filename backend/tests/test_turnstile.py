"""Tests del verificador de Cloudflare Turnstile.

Cubre:
- Modo dev (sin secret) se salta.
- Modo producción exige secret.
- Token ausente → 422 `turnstile_required`.
- siteverify falla (success:false) → 422 `turnstile_failed`.
- action mismatch → 422 `turnstile_action_mismatch`.
- hostname no permitido en prod → 422 `turnstile_hostname_invalid`.
- siteverify timeout / HTTP error → 422 `turnstile_unavailable`.
- Happy path: success + action + hostname → no levanta.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException

from services import turnstile as ts


class _FakeReq:
    """Request mínimo con lo que usa verificar_turnstile."""

    def __init__(self, headers: dict | None = None, client_host: str | None = "1.2.3.4"):
        self.headers = headers or {}
        self.client = type("C", (), {"host": client_host})() if client_host else None


@pytest.mark.asyncio
async def test_sin_secret_en_dev_se_salta(monkeypatch):
    monkeypatch.delenv("TURNSTILE_SECRET_KEY", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    # No levanta — se salta.
    await ts.verificar_turnstile(_FakeReq(), ts.ACTION_CONTACT)


@pytest.mark.asyncio
async def test_sin_secret_en_prod_rechaza(monkeypatch):
    monkeypatch.delenv("TURNSTILE_SECRET_KEY", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(HTTPException) as exc:
        await ts.verificar_turnstile(_FakeReq(), ts.ACTION_CONTACT)
    assert exc.value.status_code == 422
    assert exc.value.detail["error_code"] == "turnstile_misconfigured"


@pytest.mark.asyncio
async def test_token_ausente_rechaza(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "x")
    with pytest.raises(HTTPException) as exc:
        await ts.verificar_turnstile(_FakeReq(), ts.ACTION_CONTACT)
    assert exc.value.detail["error_code"] == "turnstile_required"


@pytest.mark.asyncio
async def test_siteverify_rechazado_rechaza(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "x")

    async def fake(token, secret, ip):
        return {"success": False, "error-codes": ["invalid-input-response"]}
    monkeypatch.setattr(ts, "_siteverify", fake)
    req = _FakeReq(headers={"cf-turnstile-token": "tok"})
    with pytest.raises(HTTPException) as exc:
        await ts.verificar_turnstile(req, ts.ACTION_CONTACT)
    assert exc.value.detail["error_code"] == "turnstile_failed"


@pytest.mark.asyncio
async def test_action_mismatch_rechaza(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "x")

    async def fake(token, secret, ip):
        return {"success": True, "action": "login", "hostname": "acten.app"}
    monkeypatch.setattr(ts, "_siteverify", fake)
    req = _FakeReq(headers={"cf-turnstile-token": "tok"})
    with pytest.raises(HTTPException) as exc:
        await ts.verificar_turnstile(req, ts.ACTION_CONTACT)
    assert exc.value.detail["error_code"] == "turnstile_action_mismatch"


@pytest.mark.asyncio
async def test_hostname_no_permitido_en_prod_rechaza(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "x")
    monkeypatch.setenv("ENVIRONMENT", "production")

    async def fake(token, secret, ip):
        return {"success": True, "action": "contact", "hostname": "phishing-site.ru"}
    monkeypatch.setattr(ts, "_siteverify", fake)
    req = _FakeReq(headers={"cf-turnstile-token": "tok"})
    with pytest.raises(HTTPException) as exc:
        await ts.verificar_turnstile(req, ts.ACTION_CONTACT)
    assert exc.value.detail["error_code"] == "turnstile_hostname_invalid"


@pytest.mark.asyncio
async def test_hostname_no_se_valida_fuera_de_prod(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "x")
    monkeypatch.setenv("ENVIRONMENT", "development")

    async def fake(token, secret, ip):
        return {"success": True, "action": "contact", "hostname": "cualquier-dominio.dev"}
    monkeypatch.setattr(ts, "_siteverify", fake)
    req = _FakeReq(headers={"cf-turnstile-token": "tok"})
    # No levanta: en dev no se valida hostname.
    await ts.verificar_turnstile(req, ts.ACTION_CONTACT)


@pytest.mark.asyncio
async def test_happy_path_header(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "x")
    monkeypatch.setenv("ENVIRONMENT", "production")
    recibido = {}

    async def fake(token, secret, ip):
        recibido.update(token=token, secret=secret, ip=ip)
        return {"success": True, "action": "contact", "hostname": "acten.app"}
    monkeypatch.setattr(ts, "_siteverify", fake)
    req = _FakeReq(headers={"cf-turnstile-token": " tok-123 ", "cf-connecting-ip": "9.9.9.9"})
    await ts.verificar_turnstile(req, ts.ACTION_CONTACT)
    assert recibido == {"token": "tok-123", "secret": "x", "ip": "9.9.9.9"}


@pytest.mark.asyncio
async def test_token_desde_body_json(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "x")

    async def fake(token, secret, ip):
        return {"success": True, "action": "contact", "hostname": "acten.app"}
    monkeypatch.setattr(ts, "_siteverify", fake)
    req = _FakeReq()
    await ts.verificar_turnstile(req, ts.ACTION_CONTACT, body={"turnstile_token": "bodytok"})


@pytest.mark.asyncio
async def test_siteverify_red_caida_rechaza(monkeypatch):
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "x")

    class _ClosedClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k):
            raise httpx.ConnectError("boom")
    monkeypatch.setattr(ts.httpx, "AsyncClient", _ClosedClient)
    req = _FakeReq(headers={"cf-turnstile-token": "tok"})
    with pytest.raises(HTTPException) as exc:
        await ts.verificar_turnstile(req, ts.ACTION_CONTACT)
    assert exc.value.detail["error_code"] == "turnstile_unavailable"
