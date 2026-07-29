"""Sprint 03 — Endpoints OAuth + sync de calendarios."""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from database import get_session
from models import CalendarAccount, CalendarEvent, IntegrationSetting, User
from routers.auth import get_current_user
from services.calendar_service import (
    google_exchange_code, google_list_events, google_oauth_url, google_refresh,
    microsoft_exchange_code, microsoft_list_events, microsoft_oauth_url, microsoft_refresh,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/calendar", tags=["Calendar"])


# Provider keys usados en IntegrationSetting.config_json
PROVIDER_KEY = {
    "google":    "google_calendar",
    "microsoft": "microsoft_calendar",
}


def _load_oauth_cfg(provider: str, tenant_id: int, db: Session) -> Optional[dict]:
    """Carga las credenciales OAuth almacenadas en IntegrationSetting para el tenant.

    Si no hay credenciales en DB, devuelve None y la capa de servicio cae en env vars.
    """
    key = PROVIDER_KEY.get(provider)
    if not key:
        return None
    row = db.exec(
        select(IntegrationSetting)
        .where(IntegrationSetting.tenant_id == tenant_id)
        .where(IntegrationSetting.provider_name == key)
    ).first()
    if not row or not row.is_active:
        return None
    try:
        return json.loads(row.config_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return None


@router.get("/oauth_config_status")
def oauth_config_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Indica si las credenciales OAuth de Google/Microsoft están configuradas
    para este tenant (DB) o globalmente (env vars). NO devuelve secretos.
    """
    import os as _os
    def has(g, m_db_key: str, env_id: str, env_redir: str) -> dict:
        from_db = bool(g and g.get("client_id") and g.get("redirect_uri"))
        from_env = bool(_os.getenv(env_id) and _os.getenv(env_redir))
        return {
            "ready": from_db or from_env,
            "source": "tenant" if from_db else ("env" if from_env else None),
        }
    g_cfg = _load_oauth_cfg("google", current_user.tenant_id, db)
    m_cfg = _load_oauth_cfg("microsoft", current_user.tenant_id, db)
    return {
        "google":    has(g_cfg,    "google_calendar",    "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_REDIRECT_URI"),
        "microsoft": has(m_cfg, "microsoft_calendar",    "MS_OAUTH_CLIENT_ID",     "MS_OAUTH_REDIRECT_URI"),
    }



def get_current_user_optional(
    request: Request, db: Session = Depends(get_session),
) -> Optional[User]:
    """Usuario de la sesión si lo hay, `None` si no.

    Los callbacks de OAuth los abre el navegador del empleado, que puede
    no tener sesión de Acten. Exigirla ahí rompería la conexión desde la
    plataforma de Servicios.
    """
    auth = request.headers.get("Authorization") or ""
    if not auth.lower().startswith("bearer "):
        return None
    try:
        return get_current_user(token=auth[7:], db=db)
    except HTTPException:
        return None


def _usuario_del_callback(
    state: Optional[str], current_user: Optional[User], provider: str,
    db: Session,
) -> User:
    """A quién pertenece la agenda que se acaba de autorizar.

    Dos caminos, y el segundo es el que importa: cuando el empleado
    conecta desde la plataforma de Servicios **no hay sesión de Acten**,
    porque el acuerdo es que esa gente nunca entra. En ese caso el
    `state` es un token firmado por nosotros (emitido por
    `POST /api/v1/calendar/connect`) que dice de quién es el permiso.

    Sin esto habría que pedirles que se registren en Acten solo para
    enganchar su calendario, que es exactamente lo que la integración
    viene a evitar.
    """
    if state:
        from routers.integration_v1_platform import leer_state
        datos = leer_state(state)
        if datos:
            if datos.get("provider") != provider:
                raise HTTPException(400, "El enlace de conexión no es de este proveedor.")
            u = db.get(User, datos.get("uid"))
            if not u or u.tenant_id != datos.get("tid"):
                raise HTTPException(400, "El enlace de conexión ya no es válido.")
            return u
    if current_user:
        return current_user
    raise HTTPException(
        401,
        "Falta la sesión o un enlace de conexión válido. Los enlaces de "
        "`/api/v1/calendar/connect` caducan a los 15 minutos.",
    )


@router.get("/google/auth_url")
def google_auth_url(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Devuelve la URL para iniciar el OAuth flow de Google."""
    state = secrets.token_urlsafe(16)
    cfg = _load_oauth_cfg("google", current_user.tenant_id, db)
    try:
        url = google_oauth_url(state, cfg=cfg)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    return {"url": url, "state": state}


@router.get("/microsoft/auth_url")
def microsoft_auth_url(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    state = secrets.token_urlsafe(16)
    cfg = _load_oauth_cfg("microsoft", current_user.tenant_id, db)
    try:
        url = microsoft_oauth_url(state, cfg=cfg)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    return {"url": url, "state": state}


@router.get("/google/callback")
async def google_callback(
    code: str = Query(...),
    state: Optional[str] = Query(None),
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: Session = Depends(get_session),
):
    current_user = _usuario_del_callback(state, current_user, "google", db)
    cfg = _load_oauth_cfg("google", current_user.tenant_id, db)
    try:
        tok = await google_exchange_code(code, cfg=cfg)
    except Exception as e:
        logger.exception("google_callback: exchange falló")
        raise HTTPException(400, f"OAuth exchange falló: {e}")

    existing = db.exec(
        select(CalendarAccount).where(
            CalendarAccount.user_id == current_user.id,
            CalendarAccount.provider == "google",
        )
    ).first()
    if existing:
        existing.access_token = tok["access_token"]
        if tok.get("refresh_token"):
            existing.refresh_token = tok["refresh_token"]
        existing.account_email = tok.get("email") or existing.account_email
        existing.scopes = tok.get("scope") or ""
        existing.is_active = True
        db.add(existing); db.commit()
        return {"status": "updated", "account_id": existing.id}
    acc = CalendarAccount(
        user_id=current_user.id, provider="google",
        account_email=tok.get("email") or "unknown",
        access_token=tok["access_token"],
        refresh_token=tok.get("refresh_token"),
        scopes=tok.get("scope") or "",
    )
    db.add(acc); db.commit(); db.refresh(acc)
    return {"status": "created", "account_id": acc.id, "email": acc.account_email}


@router.get("/microsoft/callback")
async def microsoft_callback(
    code: str = Query(...),
    state: Optional[str] = Query(None),
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: Session = Depends(get_session),
):
    current_user = _usuario_del_callback(state, current_user, "microsoft", db)
    cfg = _load_oauth_cfg("microsoft", current_user.tenant_id, db)
    try:
        tok = await microsoft_exchange_code(code, cfg=cfg)
    except Exception as e:
        logger.exception("microsoft_callback: exchange falló")
        raise HTTPException(400, f"OAuth exchange falló: {e}")

    existing = db.exec(
        select(CalendarAccount).where(
            CalendarAccount.user_id == current_user.id,
            CalendarAccount.provider == "microsoft",
        )
    ).first()
    if existing:
        existing.access_token = tok["access_token"]
        if tok.get("refresh_token"):
            existing.refresh_token = tok["refresh_token"]
        existing.account_email = tok.get("email") or existing.account_email
        existing.scopes = tok.get("scope") or ""
        existing.is_active = True
        db.add(existing); db.commit()
        return {"status": "updated", "account_id": existing.id}
    acc = CalendarAccount(
        user_id=current_user.id, provider="microsoft",
        account_email=tok.get("email") or "unknown",
        access_token=tok["access_token"],
        refresh_token=tok.get("refresh_token"),
        scopes=tok.get("scope") or "",
    )
    db.add(acc); db.commit(); db.refresh(acc)
    return {"status": "created", "account_id": acc.id, "email": acc.account_email}


@router.get("/accounts")
def my_accounts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    rows = db.exec(
        select(CalendarAccount).where(CalendarAccount.user_id == current_user.id)
    ).all()
    return [
        {"id": r.id, "provider": r.provider, "account_email": r.account_email,
         "is_active": r.is_active, "created_at": r.created_at}
        for r in rows
    ]


@router.delete("/accounts/{account_id}")
def disconnect(
    account_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    acc = db.get(CalendarAccount, account_id)
    if not acc or acc.user_id != current_user.id:
        raise HTTPException(404, "Cuenta no encontrada.")
    acc.is_active = False
    db.add(acc); db.commit()
    return {"status": "disconnected", "account_id": account_id}


@router.post("/sync")
async def sync_now(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Fuerza un sync ahora de todas las cuentas activas del usuario."""
    accounts = db.exec(
        select(CalendarAccount).where(
            CalendarAccount.user_id == current_user.id,
            CalendarAccount.is_active == True,
        )
    ).all()
    total_new = 0
    for acc in accounts:
        try:
            if acc.provider == "google":
                events = await google_list_events(acc.access_token, days=7)
            elif acc.provider == "microsoft":
                events = await microsoft_list_events(acc.access_token, days=7)
            else:
                continue
            for ev in events:
                existing = db.exec(
                    select(CalendarEvent).where(
                        CalendarEvent.calendar_account_id == acc.id,
                        CalendarEvent.external_id == ev["external_id"],
                    )
                ).first()
                if existing:
                    continue
                db.add(CalendarEvent(
                    calendar_account_id=acc.id,
                    external_id=ev["external_id"],
                    title=ev["title"],
                    start_at=ev["start_at"] or "",
                    end_at=ev["end_at"] or "",
                    attendees_json=json.dumps(ev["attendees"]),
                    meeting_url=ev["meeting_url"],
                ))
                total_new += 1
            db.commit()
        except Exception as e:
            # Token probablemente expirado; intentar refresh y reportar.
            logger.warning("sync %s falló: %s. Intentando refresh.", acc.provider, e)
            try:
                cfg_refresh = _load_oauth_cfg(acc.provider, current_user.tenant_id, db)
                if acc.provider == "google" and acc.refresh_token:
                    new_tok = await google_refresh(acc.refresh_token, cfg=cfg_refresh)
                elif acc.provider == "microsoft" and acc.refresh_token:
                    new_tok = await microsoft_refresh(acc.refresh_token, cfg=cfg_refresh)
                else:
                    raise
                acc.access_token = new_tok["access_token"]
                if new_tok.get("refresh_token"):
                    acc.refresh_token = new_tok["refresh_token"]
                db.add(acc); db.commit()
                logger.info("Token refresheado para account_id=%s. Reintentar /sync.", acc.id)
            except Exception:
                logger.exception("Refresh falló para account_id=%s; marcando inactiva.", acc.id)
                acc.is_active = False
                db.add(acc); db.commit()
    return {"status": "ok", "events_added": total_new}


@router.get("/upcoming")
def upcoming(
    days: int = Query(7, ge=1, le=180),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Próximos eventos del usuario, agnóstico de provider."""
    accounts = db.exec(
        select(CalendarAccount).where(
            CalendarAccount.user_id == current_user.id,
            CalendarAccount.is_active == True,
        )
    ).all()
    if not accounts:
        return {"events": [], "linked_accounts": 0}

    acc_ids = [a.id for a in accounts]
    rows = db.exec(
        select(CalendarEvent).where(CalendarEvent.calendar_account_id.in_(acc_ids))
        .order_by(CalendarEvent.start_at.asc())
    ).all()
    out = [
        {"id": r.id, "title": r.title, "start_at": r.start_at, "end_at": r.end_at,
         "attendees": json.loads(r.attendees_json or "[]"),
         "meeting_url": r.meeting_url, "session_id": r.session_id}
        for r in rows
    ]
    return {"events": out, "linked_accounts": len(accounts)}
