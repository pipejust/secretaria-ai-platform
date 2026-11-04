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
        if url.endswith("/createRoom"):
            enviado.update(method=method, url=url, body=json, auth=headers["Authorization"])
        return httpx.Response(200, json={"room_id": "!abc"}, request=httpx.Request(method, url))
    monkeypatch.setattr(vocem.httpx, "request", falso)
    sala = vocem.crear_sala(db_session, empresa.id, "Comité", ["@ana:softnexus.co", "@integraciones:softnexus.co", "no-es-id"], organizador="@felipe:softnexus.co")
    enlace = "https://call.vocem.softnexus.co/room/#?roomId=%21abc&viaServers=softnexus.co"
    assert sala == {"room_id": "!abc", "meeting_url": enlace, "join_url": enlace,
                    "app_url": "https://matrix.to/#/%21abc?via=softnexus.co"}
    assert enviado["body"]["power_level_content_override"]["events"]["m.rtc.member"] == 0
    assert enviado["url"].endswith("/_matrix/client/v3/createRoom") and enviado["auth"] == "Bearer tok"
    # Solo invitados explícitos (sin el bot ni ids inválidos); a nadie más le aparece la llamada en el chat.
    assert enviado["body"]["invite"] == ["@ana:softnexus.co", "@felipe:softnexus.co"]
    assert enviado["body"]["power_level_content_override"]["users"]["@felipe:softnexus.co"] == 50
    # La resolución de cuentas sigue disponible para quien la pida explícitamente.
    assert vocem.cuentas_de_usuarios(db_session, empresa.id, vocem.cargar(db_session, empresa.id)) == [
        "@ana:softnexus.co", "@danny:softnexus.co", "@felipe:softnexus.co"]
    # El mapa correo → cuenta gana: «Nadie Conocido» entra con otro usuario de Vocem.
    vocem.guardar(db_session, empresa.id, {"cuentas": {"Nadie@Softnexus.io": "@nadie.real:softnexus.co"}})
    assert "@nadie.real:softnexus.co" in vocem.cuentas_de_usuarios(db_session, empresa.id, vocem.cargar(db_session, empresa.id))
    assert vocem.estado(db_session, empresa.id)["cuentas"] == {"nadie@softnexus.io": "@nadie.real:softnexus.co"}
    assert enviado["body"]["preset"] == "public_chat" and enviado["body"]["visibility"] == "private"
    assert enviado["body"]["creation_content"] == {"m.federate": False}
    assert enviado["body"]["initial_state"] == [{"type": "co.vocem.session", "state_key": "", "content": {"organizer": "@felipe:softnexus.co"}}]


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


def test_crear_sala_rechaza_organizador_tecnico_o_ausente(db_session, empresa, monkeypatch):
    vocem.guardar(db_session, empresa.id, {"homeserver": "https://matrix.softnexus.co",
        "user_id": "@integraciones:softnexus.co", "call_base_url": "https://call.vocem.softnexus.co", "access_token": "tok"})
    llamadas = []
    monkeypatch.setattr(vocem, "_llamar", lambda *args: llamadas.append(args))
    for organizador in (None, "@integraciones:softnexus.co", "@alguien:otro.co"):
        with pytest.raises(vocem.VocemError):
            vocem.crear_sala(db_session, empresa.id, "Sesión", organizador=organizador)
    assert llamadas == []


def test_organizador_exige_vinculo_administrado_y_tenant_correcto(db_session, empresa, monkeypatch):
    from models import User
    actor = User(email=f"organizador-{uuid.uuid4().hex}@softnexus.co", full_name="Organizador", hashed_password="unused", tenant_id=empresa.id)
    db_session.add(actor); db_session.commit(); db_session.refresh(actor)
    vocem.guardar(db_session, empresa.id, {"homeserver": "https://matrix.softnexus.co", "user_id": "@integraciones:softnexus.co", "call_base_url": "https://call.vocem.softnexus.co", "access_token": "tok"})
    monkeypatch.setattr(vocem, "_existe", lambda *args: True)
    with pytest.raises(vocem.VocemError):
        vocem.cuenta_del_organizador(db_session, empresa.id, actor.id)
    vocem.guardar(db_session, empresa.id, {"cuentas": {actor.email: "@felipe:softnexus.co"}})
    assert vocem.cuenta_del_organizador(db_session, empresa.id, actor.id) == "@felipe:softnexus.co"
    with pytest.raises(vocem.VocemError):
        vocem.cuenta_del_organizador(db_session, empresa.id + 1000, actor.id)
    actor.is_active = False; db_session.add(actor); db_session.commit()
    with pytest.raises(vocem.VocemError):
        vocem.cuenta_del_organizador(db_session, empresa.id, actor.id)


# ──────────────────────────────────────────────────────────────────────────
# Detector de enlaces de reunión
# ──────────────────────────────────────────────────────────────────────────

ROOM = "!5fFLP-0eq0iIaqWaxagXPAwrSVM-veo-0REnCch_oIs"
QID = "%215fFLP-0eq0iIaqWaxagXPAwrSVM-veo-0REnCch_oIs"


@pytest.mark.parametrize("enlace,esperado", [
    (f"https://matrix.to/#/{ROOM}?via=softnexus.co", "element"),
    (f"https://matrix.to/#/{QID}?via=softnexus.co", "element"),
    (f"https://call.vocem.softnexus.co/room/#?roomId={QID}&viaServers=softnexus.co", "element"),
    (f"https://call.vocem.softnexus.co/room/#/{ROOM}", "element"),
    (f"https://call.vocem.softnexus.co/room/#/reunion-acme?roomId={QID}", "element"),
    ("https://matrix.to/#/@felipe:softnexus.co", None),
    ("https://meet.google.com/abc-defg-hij", "gmeet"),
    ("https://softnexus.zoom.us/j/123", "zoom"),
    ("https://teams.microsoft.com/l/meetup-join/x", "teams"),
    ("https://teams.live.com/meet/999", "teams"),
    ("https://example.com/loquesea", None),
    ("no es una url", None),
    ("", None),
])
def test_clasificar_enlace_cubre_los_tres_formatos_de_element_y_rechaza_otros(enlace, esperado):
    assert vocem.clasificar_enlace(enlace) == esperado


def test_id_de_sala_devuelve_el_mismo_id_sin_dominio_en_los_tres_formatos():
    for enlace in (
        f"https://matrix.to/#/{ROOM}",
        f"https://matrix.to/#/{QID}",
        f"https://call.vocem.softnexus.co/room/#?roomId={QID}&viaServers=softnexus.co",
        f"https://call.vocem.softnexus.co/room/#/{ROOM}",
    ):
        assert vocem.id_de_sala(enlace) == ROOM


def test_es_enlace_de_persona_distingue_arroba_de_bang():
    assert vocem.es_enlace_de_persona("https://matrix.to/#/@felipe:softnexus.co") is True
    assert vocem.es_enlace_de_persona(f"https://matrix.to/#/{ROOM}") is False
    assert vocem.es_enlace_de_persona(f"https://example.com/#/@felipe") is False


def test_enlace_canonico_normaliza_tres_variantes_al_mismo():
    cfg = {"call_base_url": "https://call.vocem.softnexus.co",
           "user_id": "@integraciones:softnexus.co"}
    canonico = f"https://call.vocem.softnexus.co/room/#?roomId={QID}&viaServers=softnexus.co"
    for enlace in (
        f"https://matrix.to/#/{ROOM}?via=softnexus.co",
        f"https://call.vocem.softnexus.co/room/#/{ROOM}",
        canonico,
    ):
        assert vocem.enlace_canonico_de(cfg, enlace) == canonico
    # URL no-Element: se devuelve como está (trimmed).
    assert vocem.enlace_canonico_de(cfg, "https://meet.google.com/abc") == "https://meet.google.com/abc"


def test_preparar_enlace_reunion_normaliza_element_tras_join_ok(db_session, empresa, monkeypatch):
    vocem.guardar(db_session, empresa.id, {
        "homeserver": "https://matrix.softnexus.co", "user_id": "@integraciones:softnexus.co",
        "call_base_url": "https://call.vocem.softnexus.co", "access_token": "tok"})
    hecho = {}

    def falso_post(url, headers=None, json=None, timeout=None):
        hecho["join_url"] = url
        return httpx.Response(200, json={"room_id": ROOM}, request=httpx.Request("POST", url))

    def falso_llamar(cfg, method, path, body=None):
        # m.room.encryption: 404 = sala sin cifrado (el caso bueno).
        raise vocem.VocemError("Vocem respondió 404: {}")

    monkeypatch.setattr(vocem.httpx, "post", falso_post)
    monkeypatch.setattr(vocem, "_llamar", falso_llamar)
    canonico = vocem.preparar_enlace_reunion(db_session, empresa.id, f"https://matrix.to/#/{ROOM}?via=softnexus.co")
    assert canonico == f"https://call.vocem.softnexus.co/room/#?roomId={QID}&viaServers=softnexus.co"
    assert f"/join/{QID}" in hecho["join_url"] and "server_name=softnexus.co" in hecho["join_url"]


def test_preparar_enlace_reunion_lanza_error_code_por_cada_caso(db_session, empresa, monkeypatch):
    vocem.guardar(db_session, empresa.id, {
        "homeserver": "https://matrix.softnexus.co", "user_id": "@integraciones:softnexus.co",
        "call_base_url": "https://call.vocem.softnexus.co", "access_token": "tok"})

    def code_of(enlace, post_status=None, encryption_200=False):
        def post(url, headers=None, json=None, timeout=None):
            return httpx.Response(post_status, json={}, request=httpx.Request("POST", url))

        def llamar(cfg, method, path, body=None):
            if "encryption" in path and encryption_200:
                return {"algorithm": "m.megolm.v1.aes-sha2"}
            raise vocem.VocemError("Vocem respondió 404: {}")
        if post_status is not None:
            monkeypatch.setattr(vocem.httpx, "post", post)
        monkeypatch.setattr(vocem, "_llamar", llamar)
        try:
            vocem.preparar_enlace_reunion(db_session, empresa.id, enlace)
        except vocem.EnlaceReunionError as e:
            return e.code
        return None

    # URL vacía
    assert code_of("") == "enlace_vacio"
    # Enlace de persona (@)
    assert code_of("https://matrix.to/#/@felipe:softnexus.co") == "enlace_es_usuario"
    # URL de otra cosa
    assert code_of("https://example.com/loquesea") == "enlace_no_reconocido"
    # Sala de solo invitación → 403 = falta_invitar
    assert code_of(f"https://matrix.to/#/{ROOM}", post_status=403) == "sala_falta_invitar"
    # Sala inexistente → 404 = no_existe
    assert code_of(f"https://matrix.to/#/{ROOM}", post_status=404) == "sala_no_existe"
    # Sala cifrada → encryption devuelve 200 con algorithm
    assert code_of(f"https://matrix.to/#/{ROOM}", post_status=200, encryption_200=True) == "sala_cifrada"


def test_enlace_reunion_error_viaja_como_http_422_con_error_code(db_session, empresa):
    err = vocem.EnlaceReunionError("sala_cifrada")
    assert err.status_code == 422
    assert err.detail == {"error_code": "sala_cifrada", "message": vocem.MENSAJES_ERROR["sala_cifrada"]}
