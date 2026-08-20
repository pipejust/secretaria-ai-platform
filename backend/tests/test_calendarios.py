"""El módulo de calendarios, ejercitado por donde de verdad se rompe.

No se prueba que los endpoints existan —eso lo dice el arranque—, sino
las decisiones que costaron: que el permiso filtre los datos y no solo
los botones, que un `.ics` con repetición salga las veces que toca, que
la copia de fuera no se duplique, y que un fallo del proveedor se vea en
vez de tragárselo.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlmodel import Session, select

from auth_utils import create_access_token, get_password_hash
from models import Role, Tenant, User


# ── Andamio ────────────────────────────────────────────────────────────

@pytest.fixture()
def mundo(test_engine):
    """Una empresa con una administradora y una persona normal."""
    with Session(test_engine) as db:
        t = db.exec(select(Tenant).where(Tenant.slug == "cal")).first()
        if not t:
            t = Tenant(slug="cal", name="Empresa de pruebas")
            db.add(t); db.commit(); db.refresh(t)

        def rol(nombre: str) -> Role:
            r = db.exec(select(Role).where(Role.name == nombre)).first()
            if not r:
                r = Role(name=nombre); db.add(r); db.commit(); db.refresh(r)
            return r

        def persona(correo: str, nombre: str, r: Role) -> User:
            u = db.exec(select(User).where(User.email == correo)).first()
            if not u:
                u = User(email=correo, full_name=nombre, tenant_id=t.id,
                         hashed_password=get_password_hash("x"), role_id=r.id,
                         is_active=True)
                db.add(u); db.commit(); db.refresh(u)
            return u

        admin = persona("ana@cal.test", "Ana Admin", rol("admin"))
        otra = persona("beto@cal.test", "Beto Equipo", rol("user"))
        return {"tenant": t.id, "admin": admin.email, "otra": otra.email}


def cab(correo: str, tenant_id: int) -> dict:
    return {"Authorization": "Bearer " + create_access_token(
        {"sub": correo, "tenant_id": tenant_id})}


@pytest.fixture()
def ana(mundo):
    return cab(mundo["admin"], mundo["tenant"])


@pytest.fixture()
def beto(mundo):
    return cab(mundo["otra"], mundo["tenant"])


# ── Los derivados ──────────────────────────────────────────────────────

def test_los_derivados_salen_sin_tener_tabla(client, ana):
    r = client.get("/api/v1/calendars", headers=ana)
    claves = {c["key"] for c in r.json()["calendars"]}
    assert {"sys:sesiones", "sys:tareas", "sys:festivos"} <= claves


def test_un_derivado_no_se_edita_pero_si_se_apaga(client, ana):
    assert client.patch("/api/v1/calendars/sys:festivos",
                        json={"name": "Otro"}, headers=ana).status_code == 400
    r = client.put("/api/v1/calendars/sys:festivos/preferencia",
                   json={"visible": False}, headers=ana)
    assert r.status_code == 200 and r.json()["visible"] is False


def test_apagar_es_de_quien_mira_no_de_todos(client, ana, beto):
    client.put("/api/v1/calendars/sys:festivos/preferencia",
               json={"visible": False}, headers=ana)
    vista = {c["key"]: c for c in client.get(
        "/api/v1/calendars", headers=beto).json()["calendars"]}
    assert vista["sys:festivos"]["visible"] is True


def test_los_festivos_se_calculan_con_la_ley_emiliani():
    from services import festivos_co
    f = dict((n, d) for d, n in festivos_co.del_anio(2026))
    # Los trasladables caen en lunes; los de fecha fija, donde estén.
    assert f["Batalla de Boyacá"] == date(2026, 8, 7)
    assert f["Asunción de la Virgen"] == date(2026, 8, 17)   # 15-ago es sábado
    assert f["Reyes Magos"] == date(2026, 1, 12)             # 6-ene es martes
    assert f["Viernes Santo"] == date(2026, 4, 3)
    assert len(festivos_co.del_anio(2026)) == 18


# ── Permisos: el punto donde deja de ser cosmético ─────────────────────

def test_ocupado_ensena_la_franja_pero_no_el_titulo(client, ana, beto):
    mio = next(c for c in client.get("/api/v1/calendars", headers=ana)
               .json()["calendars"] if c["origin"] == "propio")
    client.post("/api/v1/calendars/eventos", headers=ana, json={
        "calendar": mio["key"], "title": "Cita con el médico",
        "start_at": "2026-08-25T15:00:00+00:00"})
    client.post(f"/api/v1/calendars/{mio['key']}/compartido", headers=ana,
                json={"user_id": None, "permission": "ocupado"})

    vistos = client.get("/api/v1/calendars/eventos"
                        "?desde=2026-08-01T00:00:00Z&hasta=2026-09-01T00:00:00Z",
                        headers=beto).json()["events"]
    franjas = [e for e in vistos if e["start_at"].startswith("2026-08-25")]
    assert len(franjas) == 1, "la hora ocupada tiene que verse"
    assert franjas[0]["title"] == "Ocupado", "el título NO puede viajar"
    assert franjas[0]["editable"] is False


def test_elegir_calendario_se_comprueba_contra_el_permiso(client, ana, beto):
    mio = next(c for c in client.get("/api/v1/calendars", headers=ana)
               .json()["calendars"] if c["origin"] == "propio")
    client.post(f"/api/v1/calendars/{mio['key']}/compartido", headers=ana,
                json={"user_id": None, "permission": "ocupado"})
    r = client.post("/api/v1/calendars/eventos", headers=beto, json={
        "calendar": mio["key"], "title": "Colado",
        "start_at": "2026-08-26T10:00:00+00:00"})
    assert r.status_code == 403


def test_el_calendario_personal_lleva_el_nombre_de_su_dueno(client, ana, beto):
    mio = next(c for c in client.get("/api/v1/calendars", headers=ana)
               .json()["calendars"] if c["origin"] == "propio")
    client.post(f"/api/v1/calendars/{mio['key']}/compartido", headers=ana,
                json={"user_id": None, "permission": "ver"})
    ajeno = next(c for c in client.get("/api/v1/calendars", headers=beto)
                 .json()["calendars"] if c["key"] == mio["key"])
    assert "Ana Admin" in ajeno["name"], "«Mi calendario» no dice nada fuera de su dueño"


def test_solo_un_administrador_crea_calendarios_de_empresa(client, ana, beto):
    assert client.post("/api/v1/calendars", headers=ana,
                       json={"name": "Comités", "origin": "equipo"}).status_code == 201
    assert client.post("/api/v1/calendars", headers=beto,
                       json={"name": "Ajeno", "origin": "equipo"}).status_code == 403


# ── El `.ics` ──────────────────────────────────────────────────────────

ICS = """BEGIN:VCALENDAR
X-WR-CALNAME:Agenda del cliente
BEGIN:VEVENT
UID:semanal@cliente
SUMMARY:Seguimiento semanal
DTSTART:20260902T130000Z
DTEND:20260902T133000Z
RRULE:FREQ=WEEKLY;COUNT=4
END:VEVENT
END:VCALENDAR"""


def test_una_reunion_semanal_sale_las_cuatro_veces(client, ana, monkeypatch):
    from services import calendar_ics
    monkeypatch.setattr(calendar_ics, "descargar",
                        lambda url, etag="": (ICS, 'W/"1"'))
    r = client.post("/api/v1/calendars/suscribir", headers=ana,
                    json={"url": "https://cal.cliente.test/a.ics"})
    assert r.status_code == 201
    cal = r.json()["calendar"]
    assert cal["name"] == "Agenda del cliente"
    assert cal["read_only"] is True, "un .ics no se escribe"

    evs = client.get("/api/v1/calendars/eventos"
                     "?desde=2026-09-01T00:00:00Z&hasta=2026-10-15T00:00:00Z",
                     headers=ana).json()["events"]
    suyos = [e for e in evs if e["calendar"] == cal["key"]]
    assert len(suyos) == 4, "sin expandir la RRULE saldría una sola vez"
    assert all(e["editable"] is False for e in suyos)


def test_el_ics_no_deja_pedir_direcciones_internas():
    from services.calendar_ics import IcsError, descargar
    for url in ("http://localhost:8000/x.ics", "http://127.0.0.1/x.ics",
                "http://169.254.169.254/latest/meta-data/", "http://10.0.0.5/x.ics"):
        with pytest.raises(IcsError):
            descargar(url)


# ── Credenciales ───────────────────────────────────────────────────────

def test_el_secreto_se_guarda_cifrado_y_no_vuelve_en_claro(client, ana, test_engine):
    r = client.put("/api/v1/calendars/config/proveedores/google", headers=ana, json={
        "client_id": "123.apps.googleusercontent.com",
        "client_secret": "GOCSPX-secretisimo",
        "redirect_uri": "https://acten.app/api/v1/calendars/google/callback"})
    assert r.status_code == 200 and r.json()["configurado"] is True
    assert "secretisimo" not in r.text
    with Session(test_engine) as db:
        from models import IntegrationSetting
        fila = db.exec(select(IntegrationSetting).where(
            IntegrationSetting.provider_name == "google_calendar")).first()
        assert "GOCSPX-secretisimo" not in (fila.config_json or "")


def test_conectar_sin_credenciales_dice_donde_se_arregla(client, ana):
    r = client.get("/api/v1/calendars/zoho/conectar", headers=ana)
    assert r.status_code == 424
    assert "Configuración" in r.text


def test_la_url_de_google_pide_escritura_y_elegir_cuenta(client, ana):
    client.put("/api/v1/calendars/config/proveedores/google", headers=ana, json={
        "client_id": "cid", "client_secret": "sec",
        "redirect_uri": "https://acten.app/cb"})
    url = client.get("/api/v1/calendars/google/conectar", headers=ana).json()["url"]
    assert "calendar.events" in url, "sin escritura el evento no llega a la agenda"
    assert "auth%2Fcalendar&" not in url, "`calendar` a secas pide de más"
    assert "select_account" in url, "sin esto la segunda cuenta no se puede conectar"
    assert "consent" in url, "sin esto la reconexión no devuelve refresh_token"


def test_la_configuracion_es_solo_de_administradores(client, beto):
    assert client.get("/api/v1/calendars/config/proveedores",
                      headers=beto).status_code == 403


def test_el_veredicto_distingue_la_app_del_permiso():
    from services.calendar_check import _veredicto
    assert _veredicto('{"error":"invalid_scope"}')[0] is True
    assert _veredicto("AADSTS70011: scope is not valid")[0] is True
    assert _veredicto('{"error":"invalid_code"}')[0] is True      # Zoho
    assert _veredicto('{"error":"invalid_client"}')[0] is False
    assert _veredicto("AADSTS7000215: Invalid client secret")[0] is False
    assert _veredicto("una respuesta cualquiera")[0] is False


# ── Zoho: sus dos particularidades ─────────────────────────────────────

def test_zoho_trocea_el_ano_en_doce_consultas_de_31_dias(monkeypatch):
    import asyncio
    import json as _json

    import httpx

    from services import calendar_zoho as z

    rangos = []

    def handler(request):
        rangos.append(_json.loads(request.url.params.get("range")))
        return httpx.Response(200, json={"events": []})

    class Falso(httpx.AsyncClient):
        def __init__(self, *a, **k):
            super().__init__(*a, transport=httpx.MockTransport(handler), **k)

    monkeypatch.setattr(httpx, "AsyncClient", Falso)
    asyncio.run(z.listar_eventos("tok", "cal", datetime(2026, 1, 1, tzinfo=timezone.utc),
                                 datetime(2026, 12, 31, tzinfo=timezone.utc)))
    assert len(rangos) == 12
    for r in rangos:
        a = datetime.strptime(r["start"], "%Y%m%dT%H%M%SZ")
        b = datetime.strptime(r["end"], "%Y%m%dT%H%M%SZ")
        assert (b - a).days < 31, "Zoho rechaza el rango entero, no lo recorta"
    for fin, ini in zip(rangos, rangos[1:]):
        a = datetime.strptime(fin["end"], "%Y%m%dT%H%M%SZ")
        b = datetime.strptime(ini["start"], "%Y%m%dT%H%M%SZ")
        assert (b - a).total_seconds() == 1, "un hueco entre tramos pierde eventos"


def test_el_centro_de_datos_de_zoho_se_valida_por_lista_exacta():
    from services.calendar_providers import zoho_servidor_valido
    assert zoho_servidor_valido("accounts.zoho.eu") == "eu"
    assert zoho_servidor_valido("https://accounts.zoho.in/") == "in"
    # «empieza por accounts.zoho.» no vale: ahí se manda el secreto.
    assert zoho_servidor_valido("accounts.zoho.evil.com") is None
    assert zoho_servidor_valido("") is None


# ── Cifrado ────────────────────────────────────────────────────────────

def test_lo_guardado_antes_del_cifrado_se_sigue_leyendo():
    from services.calendar_crypto import cifrar, descifrar
    c = cifrar("refresh-token")
    assert c.startswith("fer1:") and descifrar(c) == "refresh-token"
    assert cifrar(c) == c, "no debe cifrar dos veces"
    assert descifrar("token-viejo-en-claro") == "token-viejo-en-claro"
