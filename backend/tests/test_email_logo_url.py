"""El logo del correo apunta a la ruta pública real del API."""

from services.email_service import _resolve_tenant_logo_url


def test_logo_propio_usa_ruta_api(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://api.acten.app")
    url = _resolve_tenant_logo_url({"logo_data_url": "data:image/png;base64,AAAA"}, 6)
    assert url == "https://api.acten.app/api/branding/6/logo.png"


def test_sin_logo_cae_al_de_acten(monkeypatch):
    monkeypatch.delenv("EMAIL_ASSETS_BASE_URL", raising=False)
    monkeypatch.delenv("FRONTEND_URL", raising=False)
    assert _resolve_tenant_logo_url({}, 6) == "https://acten.app/email-logo.png"
