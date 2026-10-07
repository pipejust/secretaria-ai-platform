"""Remitentes del bot = usuarios activos de la empresa; se sincroniza sin escribir de más."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlmodel import Session, select

from auth_utils import get_password_hash
from models import Role, Tenant, User
from routers import bot_control
from services import mail_policy_sync


@pytest.fixture()
def empresa(db_session: Session, monkeypatch):
    suf = uuid.uuid4().hex[:8]
    t = Tenant(slug=f"sync-{suf}", name="Sync", meeting_source="both")  # nueva: en prueba, todo incluido
    db_session.add(t); db_session.commit(); db_session.refresh(t)
    rol = db_session.exec(select(Role).where(Role.name == "admin")).first()
    if not rol:
        rol = Role(name="admin"); db_session.add(rol); db_session.commit(); db_session.refresh(rol)

    def usuario(correo, activo=True):
        u = User(email=correo, full_name=correo, hashed_password=get_password_hash("x"),
                 role_id=rol.id, tenant_id=t.id, is_active=activo)
        db_session.add(u); db_session.commit()
        return u

    usuario(f"ana-{suf}@s.test"); usuario(f"baja-{suf}@s.test", activo=False)
    bot = {"policy": None, "puts": []}

    async def bot_call(db, tenant_id, method, path, *, body=None, content=None):
        if method == "PUT":
            bot["puts"].append(body)
            bot["policy"] = dict(body)
        return {"policy": bot["policy"]}

    monkeypatch.setattr(bot_control, "bot_call", bot_call)
    return t, suf, bot, usuario


def sync(db, tenant_id, extra=None):
    return asyncio.run(mail_policy_sync.sincronizar(db, tenant_id, extra))


def test_usuario_nuevo_entra_en_la_siguiente_pasada_y_sin_cambios_no_se_escribe(empresa, db_session):
    t, suf, bot, usuario = empresa
    assert sync(db_session, t.id) is None and bot["puts"] == []  # sin política aún: nada que hacer

    sync(db_session, t.id, extra=["Calendario@S.test"])
    assert bot["puts"][-1]["allowed_senders"] == sorted([f"ana-{suf}@s.test", "calendario@s.test"])
    assert bot["puts"][-1]["extra_senders"] == ["calendario@s.test"]

    assert sync(db_session, t.id) is None and len(bot["puts"]) == 1  # nada cambió: no se escribe

    usuario(f"nuevo-{suf}@s.test")
    sync(db_session, t.id)
    assert f"nuevo-{suf}@s.test" in bot["puts"][-1]["allowed_senders"]
    assert bot["puts"][-1]["extra_senders"] == ["calendario@s.test"]  # lo manual se conserva
    assert f"baja-{suf}@s.test" not in bot["puts"][-1]["allowed_senders"]


def test_apagar_el_bot_sin_remitentes_extra_retira_la_autorizacion(empresa, db_session):
    t, suf, bot, _ = empresa
    bot["policy"] = {"allowed_senders": [f"ana-{suf}@s.test"], "extra_senders": [],
                     "recording_authorized": True, "timezone": "America/Bogota",
                     "bot_name": "Asistente Acten", "video": False}
    t.meeting_source = "fireflies"
    db_session.add(t); db_session.commit()
    sync(db_session, t.id)
    assert bot["puts"][-1]["recording_authorized"] is False
    assert bot["puts"][-1]["allowed_senders"] == [f"ana-{suf}@s.test"]  # el bot no admite lista vacía
    assert sync(db_session, t.id) is None and len(bot["puts"]) == 1


def test_las_cuentas_autenticadas_de_la_empresa_viajan_con_la_politica(empresa, db_session):
    """El superadmin fija cuentas de Skribby propias; la política las lleva al bot
    solo cuando el bot ya las entiende (la clave viene en su política)."""
    import json

    from models import IntegrationSetting

    t, _suf, bot, _usuario = empresa
    db_session.add(IntegrationSetting(
        tenant_id=t.id, provider_name="owned_bot", is_active=True,
        config_json=json.dumps({"service_url": "https://bot.test", "client_key": "", "bot_name": "Notas"})))
    db_session.commit()

    # Bot viejo: su política no conoce auth_accounts → no se manda la clave.
    bot["policy"] = {"allowed_senders": ["x@s.test"], "recording_authorized": True,
                     "timezone": "America/Bogota", "bot_name": "Notas", "video": False}
    asyncio.run(mail_policy_sync.sincronizar(db_session, t.id))
    assert "auth_accounts" not in bot["puts"][-1]

    # Bot nuevo con cuentas propias guardadas por el superadmin.
    bot_control.set_auth_accounts(db_session, t.id, {"gmeet": " cuenta-meet ", "teams": "", "webex": "no"})
    bot["policy"] = {**bot["policy"], "auth_accounts": {}}
    asyncio.run(mail_policy_sync.sincronizar(db_session, t.id))
    assert bot["puts"][-1]["auth_accounts"] == {"gmeet": "cuenta-meet"}
