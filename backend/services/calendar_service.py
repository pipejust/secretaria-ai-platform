"""Calendar integration — Google Calendar + Microsoft Graph.

Implementación real con httpx (sin SDK pesado tipo google-api-client) para
mantener la imagen Docker delgada. Soporta refresh automático de token.

Variables de entorno necesarias:
- GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET / GOOGLE_OAUTH_REDIRECT_URI
- MS_OAUTH_CLIENT_ID    / MS_OAUTH_CLIENT_SECRET    / MS_OAUTH_REDIRECT_URI
- MS_OAUTH_TENANT (default 'common')

Si no están configuradas, los endpoints OAuth devuelven 503 ordenado
sugiriendo qué configurar.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

# === Google ===
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GOOGLE_CALENDAR_API = "https://www.googleapis.com/calendar/v3"
GOOGLE_SCOPES = "openid email https://www.googleapis.com/auth/calendar.readonly"

# === Microsoft ===
MS_TENANT = os.getenv("MS_OAUTH_TENANT", "common")
MS_AUTH_URL = f"https://login.microsoftonline.com/{MS_TENANT}/oauth2/v2.0/authorize"
MS_TOKEN_URL = f"https://login.microsoftonline.com/{MS_TENANT}/oauth2/v2.0/token"
MS_GRAPH = "https://graph.microsoft.com/v1.0"
MS_SCOPES = "openid email profile offline_access Calendars.Read User.Read"


# ---------------------------------------------------------------------------
# Google
# ---------------------------------------------------------------------------

def _g(cfg: Optional[dict], key: str, env: str) -> str:
    """Resuelve un valor del cfg dict (db por tenant) o del env var. Vacío si no hay."""
    if cfg and cfg.get(key):
        return str(cfg.get(key)).strip()
    return os.getenv(env, "").strip()


def google_oauth_url(state: str, cfg: Optional[dict] = None) -> str:
    cid = _g(cfg, "client_id", "GOOGLE_OAUTH_CLIENT_ID")
    redirect = _g(cfg, "redirect_uri", "GOOGLE_OAUTH_REDIRECT_URI")
    if not cid or not redirect:
        raise RuntimeError("GOOGLE_OAUTH_CLIENT_ID / REDIRECT_URI no configurados.")
    params = {
        "client_id": cid, "redirect_uri": redirect,
        "response_type": "code", "scope": GOOGLE_SCOPES,
        "access_type": "offline", "prompt": "consent",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def google_exchange_code(code: str, cfg: Optional[dict] = None) -> dict:
    cid = _g(cfg, "client_id", "GOOGLE_OAUTH_CLIENT_ID")
    cs = _g(cfg, "client_secret", "GOOGLE_OAUTH_CLIENT_SECRET")
    redirect = _g(cfg, "redirect_uri", "GOOGLE_OAUTH_REDIRECT_URI")
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(GOOGLE_TOKEN_URL, data={
            "code": code, "client_id": cid, "client_secret": cs,
            "redirect_uri": redirect, "grant_type": "authorization_code",
        })
        r.raise_for_status()
        tok = r.json()
        ui = await client.get(GOOGLE_USERINFO_URL,
                              headers={"Authorization": f"Bearer {tok['access_token']}"})
        ui.raise_for_status()
        info = ui.json()
        return {
            "access_token": tok["access_token"],
            "refresh_token": tok.get("refresh_token"),
            "expires_in": tok.get("expires_in"),
            "scope": tok.get("scope"),
            "email": info.get("email"),
        }


async def google_refresh(refresh_token: str, cfg: Optional[dict] = None) -> dict:
    cid = _g(cfg, "client_id", "GOOGLE_OAUTH_CLIENT_ID")
    cs = _g(cfg, "client_secret", "GOOGLE_OAUTH_CLIENT_SECRET")
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(GOOGLE_TOKEN_URL, data={
            "refresh_token": refresh_token, "client_id": cid,
            "client_secret": cs, "grant_type": "refresh_token",
        })
        r.raise_for_status()
        return r.json()


async def google_list_events(access_token: str, days: int = 7) -> list[dict]:
    now = datetime.now(timezone.utc)
    until = now + timedelta(days=days)
    params = {
        "timeMin": now.isoformat(),
        "timeMax": until.isoformat(),
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": 100,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            f"{GOOGLE_CALENDAR_API}/calendars/primary/events",
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
        )
        r.raise_for_status()
        items = r.json().get("items", []) or []
        out = []
        for it in items:
            out.append({
                "external_id": it.get("id"),
                "title": it.get("summary") or "(sin título)",
                "start_at": (it.get("start") or {}).get("dateTime") or (it.get("start") or {}).get("date"),
                "end_at":   (it.get("end") or {}).get("dateTime")   or (it.get("end") or {}).get("date"),
                "attendees": [a.get("email") for a in (it.get("attendees") or []) if a.get("email")],
                "meeting_url": it.get("hangoutLink") or it.get("htmlLink"),
            })
        return out


# ---------------------------------------------------------------------------
# Microsoft
# ---------------------------------------------------------------------------

def _ms_auth_url(cfg: Optional[dict]) -> str:
    """Microsoft permite especificar tenant ('common', 'organizations', un GUID, …)."""
    tenant_seg = (cfg or {}).get("tenant") or os.getenv("MS_OAUTH_TENANT", "common")
    return f"https://login.microsoftonline.com/{tenant_seg}/oauth2/v2.0/authorize"


def _ms_token_url(cfg: Optional[dict]) -> str:
    tenant_seg = (cfg or {}).get("tenant") or os.getenv("MS_OAUTH_TENANT", "common")
    return f"https://login.microsoftonline.com/{tenant_seg}/oauth2/v2.0/token"


def microsoft_oauth_url(state: str, cfg: Optional[dict] = None) -> str:
    cid = _g(cfg, "client_id", "MS_OAUTH_CLIENT_ID")
    redirect = _g(cfg, "redirect_uri", "MS_OAUTH_REDIRECT_URI")
    if not cid or not redirect:
        raise RuntimeError("MS_OAUTH_CLIENT_ID / REDIRECT_URI no configurados.")
    params = {
        "client_id": cid, "redirect_uri": redirect,
        "response_type": "code", "response_mode": "query",
        "scope": MS_SCOPES, "state": state,
    }
    return f"{_ms_auth_url(cfg)}?{urlencode(params)}"


async def microsoft_exchange_code(code: str, cfg: Optional[dict] = None) -> dict:
    cid = _g(cfg, "client_id", "MS_OAUTH_CLIENT_ID")
    cs = _g(cfg, "client_secret", "MS_OAUTH_CLIENT_SECRET")
    redirect = _g(cfg, "redirect_uri", "MS_OAUTH_REDIRECT_URI")
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(_ms_token_url(cfg), data={
            "code": code, "client_id": cid, "client_secret": cs,
            "redirect_uri": redirect, "grant_type": "authorization_code",
            "scope": MS_SCOPES,
        })
        r.raise_for_status()
        tok = r.json()
        me = await client.get(f"{MS_GRAPH}/me",
                              headers={"Authorization": f"Bearer {tok['access_token']}"})
        me.raise_for_status()
        info = me.json()
        return {
            "access_token": tok["access_token"],
            "refresh_token": tok.get("refresh_token"),
            "expires_in": tok.get("expires_in"),
            "scope": tok.get("scope"),
            "email": info.get("mail") or info.get("userPrincipalName"),
        }


async def microsoft_refresh(refresh_token: str, cfg: Optional[dict] = None) -> dict:
    cid = _g(cfg, "client_id", "MS_OAUTH_CLIENT_ID")
    cs = _g(cfg, "client_secret", "MS_OAUTH_CLIENT_SECRET")
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(_ms_token_url(cfg), data={
            "refresh_token": refresh_token, "client_id": cid,
            "client_secret": cs, "grant_type": "refresh_token",
            "scope": MS_SCOPES,
        })
        r.raise_for_status()
        return r.json()


async def microsoft_list_events(access_token: str, days: int = 7) -> list[dict]:
    now = datetime.now(timezone.utc)
    until = now + timedelta(days=days)
    params = {
        "startDateTime": now.isoformat(), "endDateTime": until.isoformat(),
        "$top": 100, "$orderby": "start/dateTime",
    }
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            f"{MS_GRAPH}/me/calendarView",
            headers={"Authorization": f"Bearer {access_token}",
                     "Prefer": 'outlook.timezone="UTC"'},
            params=params,
        )
        r.raise_for_status()
        items = r.json().get("value", []) or []
        out = []
        for it in items:
            attendees = [
                a.get("emailAddress", {}).get("address")
                for a in (it.get("attendees") or [])
                if a.get("emailAddress", {}).get("address")
            ]
            out.append({
                "external_id": it.get("id"),
                "title": it.get("subject") or "(sin título)",
                "start_at": (it.get("start") or {}).get("dateTime"),
                "end_at":   (it.get("end") or {}).get("dateTime"),
                "attendees": attendees,
                "meeting_url": (it.get("onlineMeeting") or {}).get("joinUrl"),
            })
        return out
