"""Zoho Calendar, contra su API v1.

Dos cosas suyas que no tienen Google ni Microsoft:

1. **El centro de datos es parte de la dirección.** Una cuenta creada en
   Europa vive en `accounts.zoho.eu`, la de India en `.in`, y los tokens
   de un centro no valen en otro. Zoho además dice en el retorno del
   OAuth dónde vive esa persona (`accounts-server=`) y exige canjear el
   código ahí; ignorarlo hace fallar en silencio a quien tenga la cuenta
   en otro sitio.
2. **No se pueden pedir más de 31 días por consulta.** Su API rechaza un
   rango mayor: devuelve un error, no una lista recortada. Traer un año
   son doce vueltas, y `listar_eventos` las hace.

**Salvedad honesta:** esto está escrito contra la documentación de Zoho y
probado con sus formatos de fecha, pero no se ha ejercitado contra una
cuenta real. Los nombres de los campos (`uid`, `title`, `start`, `end`,
`isallday`) salen de su documentación. Mientras no se confirme, el camino
sin riesgo para Zoho es suscribirse a su dirección `.ics`, que ya
funciona.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote, urlencode

import httpx

from services.calendar_providers import PROVEEDORES, zoho_dominios

logger = logging.getLogger(__name__)

P = PROVEEDORES["zoho"]
TIMEOUT = 30.0
MAX_DIAS = P["max_dias_por_consulta"]


class ProveedorError(Exception):
    pass


def _pedir(cfg: dict, campo: str) -> str:
    v = (cfg or {}).get(campo) or ""
    if not v:
        raise ProveedorError(
            "Zoho Calendar no está dado de alta: falta el "
            f"«{campo}» en Configuración → Calendarios."
        )
    return v


def url_autorizacion(cfg: dict, state: str) -> str:
    cuentas, _ = zoho_dominios((cfg or {}).get("data_center", "com"))
    params = {
        "client_id": _pedir(cfg, "client_id"),
        "redirect_uri": _pedir(cfg, "redirect_uri"),
        "response_type": "code",
        "scope": P["scopes"],
        # Zoho no manda `state` de vuelta si no se lo mandas tú, y sin él
        # el retorno se descarta entero: la persona vuelve de aceptar y no
        # encuentra nada montado, sin ningún error a la vista.
        "state": state,
        **P["extra_auth"],
    }
    return f"{P['auth_tpl'].format(cuentas=cuentas)}?{urlencode(params)}"


async def canjear(cfg: dict, code: str, data_center: str = "") -> dict:
    """`data_center` es el que dijo Zoho en el retorno, ya validado."""
    centro = data_center or (cfg or {}).get("data_center", "com")
    cuentas, _ = zoho_dominios(centro)
    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        r = await cli.post(P["token_tpl"].format(cuentas=cuentas), data={
            "code": code,
            "client_id": _pedir(cfg, "client_id"),
            "client_secret": _pedir(cfg, "client_secret"),
            "redirect_uri": _pedir(cfg, "redirect_uri"),
            "grant_type": "authorization_code",
        })
        if r.status_code >= 400:
            raise ProveedorError(f"Zoho rechazó el código: {r.text[:300]}")
        tok = r.json()
        if tok.get("error"):
            # Zoho contesta 200 con `{"error": "..."}`; tratarlo como éxito
            # deja una cuenta conectada que no sirve para nada.
            raise ProveedorError(f"Zoho rechazó el código: {tok['error']}")
        tok["data_center"] = centro
        tok["email"] = await _correo(cli, tok.get("access_token", ""), cuentas)
        return tok


async def refrescar(cfg: dict, refresh_token: str, data_center: str = "") -> dict:
    cuentas, _ = zoho_dominios(data_center or (cfg or {}).get("data_center", "com"))
    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        r = await cli.post(P["token_tpl"].format(cuentas=cuentas), data={
            "refresh_token": refresh_token,
            "client_id": _pedir(cfg, "client_id"),
            "client_secret": _pedir(cfg, "client_secret"),
            "grant_type": "refresh_token",
        })
        if r.status_code >= 400 or r.json().get("error"):
            raise ProveedorError(f"Zoho no renovó el permiso: {r.text[:300]}")
        return r.json()


async def _correo(cli: httpx.AsyncClient, access_token: str, cuentas: str) -> str:
    if not access_token:
        return ""
    try:
        r = await cli.get(P["userinfo_tpl"].format(cuentas=cuentas),
                          headers={"Authorization": f"Zoho-oauthtoken {access_token}"})
        if r.status_code < 400:
            d = r.json()
            return d.get("Email") or d.get("email") or ""
        if "INVALID_OAUTHSCOPE" in r.text:
            # Se conectó sin el permiso `email`. No se arregla solo: hay que
            # reconectar, y eso se dice en pantalla en vez de dejar una
            # cuenta anónima que parece un fallo.
            logger.info("Zoho: la cuenta se conectó sin el permiso de correo.")
    except httpx.HTTPError as e:
        logger.info("Zoho no dio el correo: %s", e)
    return ""


async def revocar(cfg: dict, refresh_token: str, data_center: str = "") -> bool:
    if not refresh_token:
        return False
    cuentas, _ = zoho_dominios(data_center or (cfg or {}).get("data_center", "com"))
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
            r = await cli.post(P["revoca_tpl"].format(cuentas=cuentas),
                               params={"token": refresh_token})
            return r.status_code < 400
    except httpx.HTTPError:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Leer
# ─────────────────────────────────────────────────────────────────────────────

def _api(data_center: str) -> str:
    _, api = zoho_dominios(data_center)
    return P["api_tpl"].format(api=api)


def _cab(access_token: str) -> dict:
    return {"Authorization": f"Zoho-oauthtoken {access_token}"}


async def listar_calendarios(access_token: str, data_center: str = "com") -> list[dict]:
    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        r = await cli.get(f"{_api(data_center)}/calendars", headers=_cab(access_token))
        if r.status_code >= 400:
            raise ProveedorError(f"Zoho no listó los calendarios: {r.text[:300]}")
        salida = []
        for c in r.json().get("calendars", []):
            salida.append({
                "external_id": c.get("uid", "") or c.get("id", ""),
                "name": c.get("name") or "Calendario",
                "color": c.get("color") or "",
                "timezone": c.get("timezone") or "",
                "read_only": (c.get("privilege") or "").lower() == "read",
                "primary": bool(c.get("isdefault")),
            })
        return salida


def _zfecha(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _leer_zfecha(v: str) -> tuple[str, bool]:
    """Sus fechas vienen en dos formatos y confundirlos mueve el evento de día."""
    v = (v or "").strip()
    if not v:
        return "", False
    try:
        if v.endswith("Z") and "T" in v:
            d = datetime.strptime(v, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            return d.isoformat(), False
        d = datetime.strptime(v[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
        return d.isoformat(), True
    except ValueError:
        return v, False


async def listar_eventos(
    access_token: str, external_id: str,
    desde: datetime, hasta: datetime, data_center: str = "com",
) -> tuple[list[dict], str]:
    """Trocea la ventana en tramos de 31 días porque Zoho no acepta más."""
    salida: list[dict] = []
    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        tramo_ini = desde
        while tramo_ini < hasta:
            # Menos un segundo: el tramo mide 31 días justos contando los dos
            # extremos, que es el máximo que acepta. Con `days=MAX_DIAS` pelado
            # el rango mide 32 y lo rechaza; con `MAX_DIAS - 1` se pierde un día
            # por vuelta y un año pasa de doce consultas a trece.
            tramo_fin = min(tramo_ini + timedelta(days=MAX_DIAS) - timedelta(seconds=1), hasta)
            r = await cli.get(
                f"{_api(data_center)}/calendars/{quote(external_id, safe='')}/events",
                headers=_cab(access_token),
                params={"range": f'{{"start":"{_zfecha(tramo_ini)}",'
                                 f'"end":"{_zfecha(tramo_fin)}"}}'},
            )
            if r.status_code >= 400:
                raise ProveedorError(f"Zoho no dio los eventos: {r.text[:300]}")
            for e in r.json().get("events", []):
                salida.append(_evento(e))
            tramo_ini = tramo_fin + timedelta(seconds=1)
    return salida, ""


def _evento(e: dict) -> dict:
    ini, entero_i = _leer_zfecha((e.get("dateandtime") or {}).get("start") or e.get("start", ""))
    fin, _ = _leer_zfecha((e.get("dateandtime") or {}).get("end") or e.get("end", ""))
    return {
        "external_uid": e.get("uid", "") or e.get("id", ""),
        "title": e.get("title") or "(sin título)",
        "description": e.get("description") or "",
        "location": e.get("location") or "",
        "start_at": ini,
        "end_at": fin,
        "all_day": bool(e.get("isallday")) or entero_i,
        "url": e.get("viewurl") or "",
        "cancelled": False,
        "acten_id": "",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Escribir
# ─────────────────────────────────────────────────────────────────────────────

def _cuerpo(ev: dict) -> dict:
    def z(v: str) -> str:
        try:
            return _zfecha(datetime.fromisoformat((v or "").replace("Z", "+00:00")))
        except ValueError:
            return ""
    return {
        "title": ev.get("title") or "(sin título)",
        "description": ev.get("description") or "",
        "location": ev.get("location") or "",
        "dateandtime": {
            "start": z(ev.get("start_at", "")),
            "end": z(ev.get("end_at") or ev.get("start_at", "")),
            "timezone": "UTC",
        },
        "isallday": bool(ev.get("all_day")),
    }


async def crear_evento(
    access_token: str, external_id: str, ev: dict, data_center: str = "com") -> str:
    import json as _json
    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        r = await cli.post(
            f"{_api(data_center)}/calendars/{quote(external_id, safe='')}/events",
            headers=_cab(access_token),
            params={"eventdata": _json.dumps(_cuerpo(ev))})
        if r.status_code >= 400:
            raise ProveedorError(f"Zoho no creó el evento: {r.text[:300]}")
        datos = r.json().get("events", [{}])
        return (datos[0] if datos else {}).get("uid", "")


async def actualizar_evento(
    access_token: str, external_id: str, uid: str, ev: dict,
    data_center: str = "com") -> None:
    import json as _json
    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        r = await cli.put(
            f"{_api(data_center)}/calendars/{quote(external_id, safe='')}"
            f"/events/{quote(uid, safe='')}",
            headers=_cab(access_token),
            params={"eventdata": _json.dumps(_cuerpo(ev))})
        if r.status_code >= 400:
            raise ProveedorError(f"Zoho no actualizó el evento: {r.text[:300]}")


async def borrar_evento(
    access_token: str, external_id: str, uid: str, data_center: str = "com") -> None:
    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        r = await cli.delete(
            f"{_api(data_center)}/calendars/{quote(external_id, safe='')}"
            f"/events/{quote(uid, safe='')}",
            headers=_cab(access_token))
        if r.status_code >= 400 and r.status_code not in (404, 410):
            raise ProveedorError(f"Zoho no borró el evento: {r.text[:300]}")
