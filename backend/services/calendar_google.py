"""Google Calendar: entrar con la cuenta, leer los calendarios y escribir en ellos."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional
from urllib.parse import quote, urlencode

import httpx

from services.calendar_providers import PROVEEDORES

logger = logging.getLogger(__name__)

P = PROVEEDORES["google"]
TIMEOUT = 30.0


def _id(external_id: str) -> str:
    """El id de un calendario de Google es un correo: va escapado en la ruta."""
    return quote(external_id or "primary", safe="")


class ProveedorError(Exception):
    """Lo que contestó el proveedor, en un texto que se puede enseñar."""


def _pedir(cfg: dict, campo: str) -> str:
    v = (cfg or {}).get(campo) or ""
    if not v:
        raise ProveedorError(
            "Google Calendar no está dado de alta: falta el "
            f"«{campo}» en Configuración → Calendarios."
        )
    return v


def url_autorizacion(cfg: dict, state: str) -> str:
    params = {
        "client_id": _pedir(cfg, "client_id"),
        "redirect_uri": _pedir(cfg, "redirect_uri"),
        "response_type": "code",
        "scope": P["scopes"],
        "state": state,
        "include_granted_scopes": "true",
        **P["extra_auth"],
    }
    return f"{P['auth_url']}?{urlencode(params)}"


async def canjear(cfg: dict, code: str) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        r = await cli.post(P["token_url"], data={
            "code": code,
            "client_id": _pedir(cfg, "client_id"),
            "client_secret": _pedir(cfg, "client_secret"),
            "redirect_uri": _pedir(cfg, "redirect_uri"),
            "grant_type": "authorization_code",
        })
        if r.status_code >= 400:
            raise ProveedorError(f"Google rechazó el código: {r.text[:300]}")
        tok = r.json()
        tok["email"] = await _correo(cli, tok.get("access_token", ""))
        return tok


async def refrescar(cfg: dict, refresh_token: str) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        r = await cli.post(P["token_url"], data={
            "refresh_token": refresh_token,
            "client_id": _pedir(cfg, "client_id"),
            "client_secret": _pedir(cfg, "client_secret"),
            "grant_type": "refresh_token",
        })
        if r.status_code >= 400:
            raise ProveedorError(f"Google no renovó el permiso: {r.text[:300]}")
        return r.json()


async def _correo(cli: httpx.AsyncClient, access_token: str) -> str:
    """Con qué cuenta entró.

    Llega en el `id_token` solo si se pidió `openid`; el endpoint de
    perfil sirve de red. Sin el correo, dos cuentas de la misma persona
    no se distinguen y la lista queda con dos «cuenta de Google».
    """
    if not access_token:
        return ""
    try:
        r = await cli.get(P["userinfo_url"],
                          headers={"Authorization": f"Bearer {access_token}"})
        if r.status_code < 400:
            return r.json().get("email", "") or ""
        logger.info("Google no dio el correo de la cuenta: %s", r.text[:200])
    except httpx.HTTPError as e:
        logger.info("Google no dio el correo de la cuenta: %s", e)
    return ""


async def revocar(cfg: dict, token: str) -> bool:
    if not token:
        return False
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
            r = await cli.post(P["revoca"], data={"token": token})
            return r.status_code < 400
    except httpx.HTTPError:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Leer
# ─────────────────────────────────────────────────────────────────────────────

async def listar_calendarios(access_token: str) -> list[dict]:
    """Los calendarios de la cuenta, con si esa persona puede escribir en ellos.

    Quién puede escribir lo dice el proveedor, no nosotros: `accessRole`
    owner/writer. Un calendario donde solo lee se marca de solo lectura
    aunque nuestro permiso diga otra cosa, porque la que no puede es ella.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        r = await cli.get(
            f"{P['api_base']}/users/me/calendarList",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"minAccessRole": "reader", "maxResults": 250},
        )
        if r.status_code >= 400:
            raise ProveedorError(f"Google no listó los calendarios: {r.text[:300]}")
        salida = []
        for c in r.json().get("items", []):
            salida.append({
                "external_id": c.get("id", ""),
                "name": c.get("summaryOverride") or c.get("summary") or "Calendario",
                "color": c.get("backgroundColor") or "",
                "timezone": c.get("timeZone") or "",
                "read_only": c.get("accessRole") not in ("owner", "writer"),
                "primary": bool(c.get("primary")),
            })
        return salida


async def listar_eventos(
    access_token: str, external_id: str,
    desde: datetime, hasta: datetime, sync_token: str = "",
) -> tuple[list[dict], str]:
    """Los eventos del calendario en la ventana, ya desplegadas las repeticiones.

    `singleEvents=true` es lo que despliega las series: sin él una
    reunión semanal saldría una sola vez.
    """
    params: dict = {"maxResults": 2500, "singleEvents": "true", "orderBy": "startTime"}
    if sync_token:
        params = {"maxResults": 2500, "singleEvents": "true", "syncToken": sync_token}
    else:
        params["timeMin"] = desde.isoformat()
        params["timeMax"] = hasta.isoformat()

    salida: list[dict] = []
    nuevo_token = ""
    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        pagina = None
        while True:
            if pagina:
                params["pageToken"] = pagina
            r = await cli.get(
                f"{P['api_base']}/calendars/{_id(external_id)}/events",
                headers={"Authorization": f"Bearer {access_token}"}, params=params,
            )
            if r.status_code == 410:
                # El syncToken caducó: Google pide releer entero. Reintentar
                # sin él aquí evita que el calendario se quede congelado.
                return await listar_eventos(access_token, external_id, desde, hasta, "")
            if r.status_code >= 400:
                raise ProveedorError(f"Google no dio los eventos: {r.text[:300]}")
            datos = r.json()
            for e in datos.get("items", []):
                salida.append(_evento(e))
            nuevo_token = datos.get("nextSyncToken", "") or nuevo_token
            pagina = datos.get("nextPageToken")
            if not pagina:
                break
    return salida, nuevo_token


def _evento(e: dict) -> dict:
    ini, entero = _momento(e.get("start") or {})
    fin, _ = _momento(e.get("end") or {})
    return {
        "external_uid": e.get("id", ""),
        "title": e.get("summary") or "(sin título)",
        "description": e.get("description") or "",
        "location": e.get("location") or "",
        "start_at": ini,
        "end_at": fin,
        "all_day": entero,
        "url": e.get("htmlLink") or "",
        "cancelled": e.get("status") == "cancelled",
        # De aquí sale la parte que evita el duplicado: si el evento nació
        # en Acten, viene con nuestra marca y se descarta al releer.
        "acten_id": (e.get("extendedProperties", {}).get("private", {}) or {}).get("acten_id", ""),
    }


def _momento(bloque: dict) -> tuple[str, bool]:
    if bloque.get("date"):
        return f"{bloque['date']}T00:00:00+00:00", True
    return bloque.get("dateTime", "") or "", False


# ─────────────────────────────────────────────────────────────────────────────
# Escribir
# ─────────────────────────────────────────────────────────────────────────────

def _cuerpo(ev: dict) -> dict:
    if ev.get("all_day"):
        ini = {"date": (ev["start_at"] or "")[:10]}
        fin = {"date": (ev.get("end_at") or ev["start_at"] or "")[:10]}
    else:
        ini = {"dateTime": ev["start_at"]}
        fin = {"dateTime": ev.get("end_at") or ev["start_at"]}
    cuerpo = {
        "summary": ev.get("title") or "(sin título)",
        "description": ev.get("description") or "",
        "location": ev.get("location") or "",
        "start": ini, "end": fin,
    }
    if ev.get("acten_id"):
        cuerpo["extendedProperties"] = {"private": {"acten_id": str(ev["acten_id"])}}
    return cuerpo


async def crear_evento(access_token: str, external_id: str, ev: dict) -> str:
    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        r = await cli.post(
            f"{P['api_base']}/calendars/{_id(external_id)}/events",
            headers={"Authorization": f"Bearer {access_token}"}, json=_cuerpo(ev),
        )
        if r.status_code >= 400:
            raise ProveedorError(f"Google no creó el evento: {r.text[:300]}")
        return r.json().get("id", "")


async def actualizar_evento(
    access_token: str, external_id: str, uid: str, ev: dict) -> None:
    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        r = await cli.patch(
            f"{P['api_base']}/calendars/{_id(external_id)}/events/{quote(uid, safe='')}",
            headers={"Authorization": f"Bearer {access_token}"}, json=_cuerpo(ev),
        )
        if r.status_code >= 400:
            raise ProveedorError(f"Google no actualizó el evento: {r.text[:300]}")


async def borrar_evento(access_token: str, external_id: str, uid: str) -> None:
    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        r = await cli.delete(
            f"{P['api_base']}/calendars/{_id(external_id)}/events/{quote(uid, safe='')}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        # 404/410 = ya no estaba. Es el resultado que se buscaba.
        if r.status_code >= 400 and r.status_code not in (404, 410):
            raise ProveedorError(f"Google no borró el evento: {r.text[:300]}")
