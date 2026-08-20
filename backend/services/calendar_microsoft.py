"""Microsoft 365 / Outlook, contra Microsoft Graph.

Dos cosas suyas que no tienen los otros y que si se ignoran fallan tarde
y sin síntoma:

1. **Rota el `refresh_token` en cada renovación.** Si no se guarda el
   nuevo, la conexión se cae sola a los pocos días y sin motivo aparente.
   `canjear()` y `refrescar()` devuelven siempre el que toca guardar.
2. **Se lee con `calendarView`, no con `events`.** `calendarView`
   despliega las repeticiones, que es lo que hace falta para pintar. Con
   `events`, una reunión semanal saldría una sola vez.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional
from urllib.parse import quote, urlencode

import httpx

from services.calendar_providers import PROVEEDORES

logger = logging.getLogger(__name__)

P = PROVEEDORES["microsoft"]
TIMEOUT = 30.0


class ProveedorError(Exception):
    pass


def _pedir(cfg: dict, campo: str) -> str:
    v = (cfg or {}).get(campo) or ""
    if not v:
        raise ProveedorError(
            "Microsoft no está dado de alta: falta el "
            f"«{campo}» en Configuración → Calendarios."
        )
    return v


def _directorio(cfg: dict) -> str:
    """El tenant de Entra. En blanco = cuentas de cualquier sitio."""
    return (cfg or {}).get("tenant") or "common"


def url_autorizacion(cfg: dict, state: str) -> str:
    params = {
        "client_id": _pedir(cfg, "client_id"),
        "redirect_uri": _pedir(cfg, "redirect_uri"),
        "response_type": "code",
        "scope": P["scopes"],
        "state": state,
        "response_mode": "query",
        **P["extra_auth"],
    }
    base = P["auth_tpl"].format(tenant=_directorio(cfg))
    return f"{base}?{urlencode(params)}"


async def canjear(cfg: dict, code: str) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        r = await cli.post(P["token_tpl"].format(tenant=_directorio(cfg)), data={
            "code": code,
            "client_id": _pedir(cfg, "client_id"),
            "client_secret": _pedir(cfg, "client_secret"),
            "redirect_uri": _pedir(cfg, "redirect_uri"),
            "grant_type": "authorization_code",
            "scope": P["scopes"],
        })
        if r.status_code >= 400:
            raise ProveedorError(f"Microsoft rechazó el código: {r.text[:300]}")
        tok = r.json()
        tok["email"] = await _correo(cli, tok.get("access_token", ""))
        return tok


async def refrescar(cfg: dict, refresh_token: str) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        r = await cli.post(P["token_tpl"].format(tenant=_directorio(cfg)), data={
            "refresh_token": refresh_token,
            "client_id": _pedir(cfg, "client_id"),
            "client_secret": _pedir(cfg, "client_secret"),
            "grant_type": "refresh_token",
            "scope": P["scopes"],
        })
        if r.status_code >= 400:
            raise ProveedorError(f"Microsoft no renovó el permiso: {r.text[:300]}")
        return r.json()


async def _correo(cli: httpx.AsyncClient, access_token: str) -> str:
    if not access_token:
        return ""
    try:
        r = await cli.get(P["userinfo_url"],
                          headers={"Authorization": f"Bearer {access_token}"})
        if r.status_code < 400:
            d = r.json()
            # Las cuentas personales (Outlook.com) no traen `mail`.
            return d.get("mail") or d.get("userPrincipalName") or ""
        logger.info("Graph no dio el correo: %s", r.text[:200])
    except httpx.HTTPError as e:
        logger.info("Graph no dio el correo: %s", e)
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Leer
# ─────────────────────────────────────────────────────────────────────────────

async def listar_calendarios(access_token: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        r = await cli.get(f"{P['api_base']}/me/calendars",
                          headers={"Authorization": f"Bearer {access_token}"},
                          params={"$top": 100})
        if r.status_code >= 400:
            raise ProveedorError(f"Graph no listó los calendarios: {r.text[:300]}")
        return [{
            "external_id": c.get("id", ""),
            "name": c.get("name") or "Calendario",
            "color": "",
            "timezone": "",
            # `canEdit` es de la persona, no del calendario: manda él.
            "read_only": not c.get("canEdit", False),
            "primary": bool(c.get("isDefaultCalendar")),
        } for c in r.json().get("value", [])]


async def listar_eventos(
    access_token: str, external_id: str,
    desde: datetime, hasta: datetime, sync_token: str = "",
) -> tuple[list[dict], str]:
    url = (f"{P['api_base']}/me/calendars/{quote(external_id, safe='')}/calendarView"
           if external_id else f"{P['api_base']}/me/calendarView")
    params = {
        "startDateTime": desde.isoformat(),
        "endDateTime": hasta.isoformat(),
        "$top": 500,
        "$select": "id,subject,bodyPreview,location,start,end,isAllDay,webLink,isCancelled,singleValueExtendedProperties",
    }
    salida: list[dict] = []
    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        siguiente: Optional[str] = None
        while True:
            r = (await cli.get(siguiente, headers={"Authorization": f"Bearer {access_token}"})
                 if siguiente else
                 await cli.get(url, headers={"Authorization": f"Bearer {access_token}",
                                             # Sin esto las horas vuelven en UTC
                                             # pero sin decirlo, y se pintan mal.
                                             "Prefer": 'outlook.timezone="UTC"'},
                               params=params))
            if r.status_code >= 400:
                raise ProveedorError(f"Graph no dio los eventos: {r.text[:300]}")
            datos = r.json()
            salida.extend(_evento(e) for e in datos.get("value", []))
            siguiente = datos.get("@odata.nextLink")
            if not siguiente:
                break
    return salida, ""


def _evento(e: dict) -> dict:
    entero = bool(e.get("isAllDay"))
    return {
        "external_uid": e.get("id", ""),
        # Graph llama `subject` al título; unificar esto a base de `if`
        # es lo que produce el módulo donde nadie encuentra nada.
        "title": e.get("subject") or "(sin título)",
        "description": e.get("bodyPreview") or "",
        "location": ((e.get("location") or {}).get("displayName") or ""),
        "start_at": _momento(e.get("start") or {}),
        "end_at": _momento(e.get("end") or {}),
        "all_day": entero,
        "url": e.get("webLink") or "",
        "cancelled": bool(e.get("isCancelled")),
        "acten_id": _acten_id(e),
    }


def _momento(bloque: dict) -> str:
    """Graph mete la fecha y la zona en campos aparte."""
    v = bloque.get("dateTime") or ""
    if not v:
        return ""
    if v.endswith("Z") or "+" in v[10:]:
        return v
    zona = bloque.get("timeZone") or "UTC"
    return v + ("Z" if zona.upper() == "UTC" else "")


_PROP_ACTEN = "String {66f5a359-4659-4830-9070-00047ec6ac6e} Name acten_id"


def _acten_id(e: dict) -> str:
    for p in e.get("singleValueExtendedProperties", []) or []:
        if p.get("id") == _PROP_ACTEN:
            return p.get("value", "") or ""
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Escribir
# ─────────────────────────────────────────────────────────────────────────────

def _cuerpo(ev: dict) -> dict:
    cuerpo = {
        "subject": ev.get("title") or "(sin título)",
        "body": {"contentType": "text", "content": ev.get("description") or ""},
        "location": {"displayName": ev.get("location") or ""},
        "start": {"dateTime": (ev.get("start_at") or "").replace("Z", ""), "timeZone": "UTC"},
        "end": {"dateTime": (ev.get("end_at") or ev.get("start_at") or "").replace("Z", ""),
                "timeZone": "UTC"},
        "isAllDay": bool(ev.get("all_day")),
    }
    if ev.get("acten_id"):
        cuerpo["singleValueExtendedProperties"] = [
            {"id": _PROP_ACTEN, "value": str(ev["acten_id"])}]
    return cuerpo


async def crear_evento(access_token: str, external_id: str, ev: dict) -> str:
    url = (f"{P['api_base']}/me/calendars/{quote(external_id, safe='')}/events"
           if external_id else f"{P['api_base']}/me/events")
    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        r = await cli.post(url, headers={"Authorization": f"Bearer {access_token}"},
                           json=_cuerpo(ev))
        if r.status_code >= 400:
            raise ProveedorError(f"Graph no creó el evento: {r.text[:300]}")
        return r.json().get("id", "")


async def actualizar_evento(
    access_token: str, external_id: str, uid: str, ev: dict) -> None:
    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        r = await cli.patch(
            f"{P['api_base']}/me/events/{quote(uid, safe='')}",
            headers={"Authorization": f"Bearer {access_token}"}, json=_cuerpo(ev))
        if r.status_code >= 400:
            raise ProveedorError(f"Graph no actualizó el evento: {r.text[:300]}")


async def borrar_evento(access_token: str, external_id: str, uid: str) -> None:
    async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
        r = await cli.delete(
            f"{P['api_base']}/me/events/{quote(uid, safe='')}",
            headers={"Authorization": f"Bearer {access_token}"})
        if r.status_code >= 400 and r.status_code not in (404, 410):
            raise ProveedorError(f"Graph no borró el evento: {r.text[:300]}")
