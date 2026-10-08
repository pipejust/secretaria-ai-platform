"""Vocem (Element Call): Acten crea la sala y manda el bot; el cliente no configura nada."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from sqlmodel import select

from models import IntegrationSetting, Tenant, VocemInvite
from services import vocem
from services.cifrado import cifrar


@pytest.fixture()
def empresa(db_session):
    t = Tenant(slug=f"vo-{uuid.uuid4().hex[:8]}", name="Vocem", meeting_source="both")
    db_session.add(t); db_session.commit(); db_session.refresh(t)
    return t


def test_guardar_cifra_el_token_y_el_estado_no_lo_muestra(db_session, empresa):
    est = vocem.guardar(db_session, empresa.id, {
        "homeserver": "https://matrix.softnexus.co/", "user_id": "@integraciones:softnexus.co",
        "call_base_url": "https://call.vocem.softnexus.co", "access_token": "syt_secreto_123456"})
    assert est["configured"] is True and est["homeserver"] == "https://matrix.softnexus.co"
    assert "syt_secreto" not in json.dumps(est)
    fila = db_session.exec(select(IntegrationSetting).where(
        IntegrationSetting.tenant_id == empresa.id, IntegrationSetting.provider_name == "vocem")).first()
    assert "syt_secreto" not in fila.config_json and "fer1:" in fila.config_json
    # Volver a guardar sin token conserva el anterior.
    vocem.guardar(db_session, empresa.id, {"user_id": "@bot:softnexus.co"})
    assert vocem.cargar(db_session, empresa.id)["access_token"] == "syt_secreto_123456"
    vocem.guardar(db_session, empresa.id, {"clear": True})
    assert vocem.configurada(db_session, empresa.id) is False


def test_crear_sala_sin_cifrado_cerrada_al_servidor(db_session, empresa, monkeypatch):
    vocem.guardar(db_session, empresa.id, {
        "homeserver": "https://matrix.softnexus.co", "user_id": "@integraciones:softnexus.co",
        "call_base_url": "https://call.vocem.softnexus.co", "access_token": "tok"})
    from auth_utils import get_password_hash
    from models import Role, User
    rol = db_session.exec(select(Role).where(Role.name == "admin")).first() or Role(name="admin")
    db_session.add(rol); db_session.commit(); db_session.refresh(rol)
    for correo, nombre in (("felipe@softnexus.io", "Felipe"), ("nadie@softnexus.io", "Nadie Conocido"),
                           ("Ana@Softnexus.co", "Ana"), ("dvera@softnexus.io", "Danny Vera")):
        db_session.add(User(email=correo, full_name=nombre, hashed_password=get_password_hash("x"), role_id=rol.id,
                            tenant_id=empresa.id, is_active=True))
    db_session.add(User(email="baja@softnexus.io", full_name="b", hashed_password=get_password_hash("x"), role_id=rol.id,
                        tenant_id=empresa.id, is_active=False))
    db_session.commit()
    enviado = {}

    def falso(method, url, json=None, timeout=None, headers=None):
        if "/profile/" in url:
            existe = any(u in url for u in ("%40felipe%3A", "%40ana%3A"))
            return httpx.Response(200 if existe else 404, json={} if existe else {"errcode": "M_NOT_FOUND"}, request=httpx.Request(method, url))
        if url.endswith("/user_directory/search"):
            # dvera@ no existe como cuenta, pero «Danny Vera» sí está en el directorio.
            res = [{"user_id": "@danny:softnexus.co", "display_name": "Danny  Vera"}] if "danny" in json["search_term"].lower() else []
            return httpx.Response(200, json={"results": res, "limited": False}, request=httpx.Request(method, url))
        enviado.update(method=method, url=url, body=json, auth=headers["Authorization"])
        return httpx.Response(200, json={"room_id": "!abc"}, request=httpx.Request(method, url))
    monkeypatch.setattr(vocem.httpx, "request", falso)
    sala = vocem.crear_sala(db_session, empresa.id, "Comité", ["@ana:softnexus.co", "@integraciones:softnexus.co", "no-es-id"])
    enlace = "https://call.vocem.softnexus.co/room/#?roomId=%21abc&viaServers=softnexus.co"
    assert sala == {"room_id": "!abc", "meeting_url": enlace, "join_url": enlace,
                    "app_url": "https://matrix.to/#/%21abc?via=softnexus.co"}
    assert enviado["body"]["power_level_content_override"]["events"]["m.rtc.member"] == 0
    assert enviado["url"].endswith("/_matrix/client/v3/createRoom") and enviado["auth"] == "Bearer tok"
    # Solo invitados explícitos (sin el bot ni ids inválidos); a nadie más le aparece la llamada en el chat.
    assert enviado["body"]["invite"] == ["@ana:softnexus.co"]
    # La resolución de cuentas sigue disponible para quien la pida explícitamente.
    assert vocem.cuentas_de_usuarios(db_session, empresa.id, vocem.cargar(db_session, empresa.id)) == [
        "@ana:softnexus.co", "@danny:softnexus.co", "@felipe:softnexus.co"]
    assert enviado["body"]["preset"] == "public_chat" and enviado["body"]["visibility"] == "private"
    assert enviado["body"]["creation_content"] == {"m.federate": False}
    assert "initial_state" not in enviado["body"]  # ni cifrado ni invitados anónimos


def test_las_invitaciones_salen_a_la_hora_de_la_sesion(db_session, empresa, monkeypatch):
    from datetime import datetime, timedelta, timezone

    vocem.guardar(db_session, empresa.id, {
        "homeserver": "https://matrix.softnexus.co", "user_id": "@integraciones:softnexus.co",
        "call_base_url": "https://call.vocem.softnexus.co", "access_token": "tok"})
    llamadas = []
    monkeypatch.setattr(vocem, "cuentas_de_usuarios", lambda db, t, cfg: ["@felipe:softnexus.co"])

    def falso(method, url, json=None, timeout=None, headers=None):
        llamadas.append((method, url.split("/_matrix/client/v3", 1)[1], json))
        cuerpo = {"name": "Comité"} if url.endswith("/state/m.room.name/") else {}
        return httpx.Response(200, json=cuerpo, request=httpx.Request(method, url))
    monkeypatch.setattr(vocem.httpx, "request", falso)

    def invitadas():
        return [(p.split("/rooms/")[1].split("/")[0], j["user_id"]) for m, p, j in llamadas if p.endswith("/invite")]

    ahora = datetime.now(timezone.utc)
    # Sesión ahora (o sin hora): aviso inmediato a los usuarios de la empresa.
    assert vocem.programar_invitaciones(db_session, empresa.id, "!ya", None) == "ahora"
    assert vocem.programar_invitaciones(db_session, empresa.id, "!pronto", ahora + timedelta(minutes=1)) == "ahora"
    # Sesión en tres días con lista explícita: pendiente; el cron no la toca todavía.
    assert vocem.programar_invitaciones(db_session, empresa.id, "!luego", ahora + timedelta(days=3),
                                        ["@ana:softnexus.co", "@luis:softnexus.co"]) == "programada"
    assert invitadas() == [("%21ya", "@felipe:softnexus.co"), ("%21pronto", "@felipe:softnexus.co")]
    assert vocem.enviar_invitaciones_pendientes(db_session) == 0
    # Cuando falta menos de la antelación, el cron avisa a la lista explícita, no a todos.
    fila = db_session.exec(select(VocemInvite).where(VocemInvite.room_id == "!luego")).first()
    fila.invite_at = (ahora - timedelta(seconds=1)).isoformat(); db_session.add(fila); db_session.commit()
    assert vocem.enviar_invitaciones_pendientes(db_session) == 1
    assert invitadas()[-2:] == [("%21luego", "@ana:softnexus.co"), ("%21luego", "@luis:softnexus.co")]
    # Mensaje en la sala: menciona a todos y trae los dos enlaces (computador y app).
    mensajes = [j for m, p, j in llamadas if "/send/m.room.message/" in p]
    assert len(mensajes) == 3
    ultimo = mensajes[-1]
    assert ultimo["m.mentions"] == {"room": True} and ultimo["body"].startswith("@room «Comité»")
    assert "roomId=%21luego&viaServers=softnexus.co" in ultimo["body"] and "https://matrix.to/#/%21luego?via=softnexus.co" in ultimo["body"]
    assert vocem.enviar_invitaciones_pendientes(db_session) == 0
