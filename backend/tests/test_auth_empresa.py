"""Login y recuperación de contraseña con varias empresas."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlmodel import Session, select

from auth_utils import (
    create_password_reset_token,
    get_password_hash,
    password_reset_claims,
    verify_password_reset_token,
)
from models import Role, Tenant, User


@pytest.fixture()
def mundo(test_engine):
    """Dos empresas más la genérica `acten`; ana solo en la primera, beto en las dos."""
    suf = uuid.uuid4().hex[:6]
    with Session(test_engine) as db:
        generica = db.exec(select(Tenant).where(Tenant.slug == "acten")).first()
        if not generica:
            generica = Tenant(slug="acten", name="Acten"); db.add(generica); db.commit(); db.refresh(generica)
        a = Tenant(slug=f"alfa-{suf}", name="Alfa", domain=f"acten.alfa-{suf}.test")
        b = Tenant(slug=f"beta-{suf}", name="Beta")
        db.add(a); db.add(b); db.commit(); db.refresh(a); db.refresh(b)
        rol = db.exec(select(Role).where(Role.name == "admin")).first() or Role(name="admin")
        db.add(rol); db.commit(); db.refresh(rol)

        def usuario(correo, tenant, clave):
            u = User(email=correo, full_name=correo, hashed_password=get_password_hash(clave), role_id=rol.id,
                     tenant_id=tenant.id, is_active=True)
            db.add(u); db.commit(); db.refresh(u)
            return u
        ana = usuario(f"ana-{suf}@x.test", a, "clave-ana")
        usuario(f"beto-{suf}@x.test", a, "clave-beto-a")
        usuario(f"beto-{suf}@x.test", b, "clave-beto-b")
        return {"suf": suf, "a": a.id, "b": b.id, "slug_a": a.slug, "slug_b": b.slug, "ana": ana.email, "beto": f"beto-{suf}@x.test"}


def login(client, correo, clave, slug):
    return client.post("/auth/login", json={"username": correo, "password": clave, "tenant_slug": slug})


def test_entrar_por_la_puerta_generica_con_una_cuenta_de_una_sola_empresa(client, mundo):
    # Antes: 401, porque la web manda `acten` y ana no existe ahí.
    r = login(client, mundo["ana"], "clave-ana", "acten")
    assert r.status_code == 200, r.text
    assert r.json()["tenant"]["slug"] == mundo["slug_a"]
    assert login(client, mundo["ana"], "mala", "acten").status_code == 401


def test_con_el_correo_en_varias_empresas_no_se_adivina(client, mundo):
    assert login(client, mundo["beto"], "clave-beto-a", "acten").status_code == 401
    # Con la empresa explícita sigue funcionando, cada una con su clave.
    ok = login(client, mundo["beto"], "clave-beto-b", mundo["slug_b"])
    assert ok.status_code == 200 and ok.json()["tenant"]["slug"] == mundo["slug_b"]
    assert login(client, mundo["beto"], "clave-beto-a", mundo["slug_b"]).status_code == 401


def test_la_empresa_pedida_manda_sobre_la_puerta_generica(client, mundo):
    assert login(client, mundo["ana"], "clave-ana", mundo["slug_b"]).status_code == 401


def test_el_token_de_restablecimiento_lleva_la_empresa(client, mundo, test_engine):
    token = create_password_reset_token(mundo["beto"], mundo["b"])
    assert password_reset_claims(token) == (mundo["beto"], mundo["b"])
    assert verify_password_reset_token(token) == mundo["beto"]
    antiguo = create_password_reset_token(mundo["beto"])  # tokens ya enviados, sin empresa
    assert password_reset_claims(antiguo) == (mundo["beto"], None)
    assert password_reset_claims("basura") is None

    r = client.post("/auth/reset-password", json={"token": token, "new_password": "nueva-clave-b"})
    assert r.status_code == 200, r.text
    # Cambió la cuenta de beta, no la de alfa.
    assert login(client, mundo["beto"], "nueva-clave-b", mundo["slug_b"]).status_code == 200
    assert login(client, mundo["beto"], "clave-beto-a", mundo["slug_a"]).status_code == 200


def test_el_correo_de_recuperacion_apunta_a_la_empresa(test_engine, mundo, monkeypatch):
    from services.email_service import EmailService

    monkeypatch.setenv("FRONTEND_URL", "https://acten.app")
    with Session(test_engine) as db:
        con_dominio = EmailService(db=db, tenant_id=mundo["a"])._base_url_empresa()
        sin_dominio = EmailService(db=db, tenant_id=mundo["b"])._base_url_empresa()
        generica = EmailService(db=db, tenant_id=None)._base_url_empresa()
    assert con_dominio == f"https://acten.alfa-{mundo['suf']}.test"
    assert sin_dominio == f"https://acten.app/t/{mundo['slug_b']}"
    assert generica == "https://acten.app"

    enviado = {}

    async def falso(self, to_email, subject, html, attachments=None):
        enviado["html"] = html
        return True
    monkeypatch.setattr(EmailService, "_send_html_email", falso)
    with Session(test_engine) as db:
        asyncio.run(EmailService(db=db, tenant_id=mundo["b"]).send_forgot_password_email("x@y.test", "X", "TOK"))
    assert f"https://acten.app/t/{mundo['slug_b']}/reset-password?token=TOK" in enviado["html"]
