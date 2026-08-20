"""Traer de fuera hacia dentro: cuentas, calendarios y eventos.

Lo de fuera vive en `ExternalEvent`, aparte de lo propio. Mezclarlo con
`CalendarEntry` haría que editar aquí pareciera posible, y al desconectar
la cuenta quedarían huérfanos eventos que ya no existen en ningún sitio.

El refresco es por sondeo, cada 30 minutos. Google y Graph ofrecen
webhooks que lo evitarían, pero requieren una URL pública verificada y
renovar la suscripción cada pocos días; con este volumen, sondear cuesta
menos que mantener eso.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from anyio import to_thread
from sqlmodel import Session, select

from models import Calendar, CalendarAccount, CalendarEntry, ExternalEvent, User
from services import calendar_config, calendar_google, calendar_ics
from services import calendar_microsoft, calendar_zoho
from services.calendar_crypto import cifrar, descifrar

logger = logging.getLogger(__name__)

# Cuánto se trae. Hacia atrás lo justo para que el mes en curso esté
# completo; hacia delante un año, que es donde la gente pone las cosas.
VENTANA_ATRAS = timedelta(days=60)
VENTANA_ADELANTE = timedelta(days=365)

MODULOS = {
    "google": calendar_google,
    "microsoft": calendar_microsoft,
    "zoho": calendar_zoho,
}


class SyncError(Exception):
    pass


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Tokens
# ─────────────────────────────────────────────────────────────────────────────

async def token_de(db: Session, cuenta: CalendarAccount) -> str:
    """Un access_token válido, renovándolo si hace falta.

    **Microsoft rota el `refresh_token` en cada renovación.** Si no se
    guarda el nuevo, la conexión se cae sola a los pocos días y sin
    motivo aparente; por eso el guardado es incondicional aquí y no una
    rama del `if` de cada proveedor.
    """
    mod = MODULOS.get(cuenta.provider)
    if not mod:
        raise SyncError(f"Proveedor desconocido: {cuenta.provider}")

    if cuenta.access_token and cuenta.token_expires_at:
        try:
            if datetime.fromisoformat(cuenta.token_expires_at) > datetime.now(timezone.utc) + timedelta(minutes=2):
                return descifrar(cuenta.access_token)
        except ValueError:
            pass

    refresco = descifrar(cuenta.refresh_token or "")
    if not refresco:
        _marcar(db, cuenta, "revoked",
                "La cuenta no tiene permiso de larga duración. Hay que volver a conectarla.")
        raise SyncError("La cuenta hay que volver a conectarla.")

    cfg = calendar_config.cargar(db, cuenta.tenant_id or 0, cuenta.provider)
    try:
        if cuenta.provider == "zoho":
            tok = await mod.refrescar(cfg, refresco, cuenta.data_center)
        else:
            tok = await mod.refrescar(cfg, refresco)
    except Exception as e:  # noqa: BLE001
        # Si la persona retiró el permiso desde su cuenta, se dice en
        # pantalla en vez de fallar en silencio cada media hora para siempre.
        _marcar(db, cuenta, "revoked", str(e)[:400])
        raise SyncError(str(e)) from e

    guardar_tokens(db, cuenta, tok)
    return tok.get("access_token", "")


def guardar_tokens(db: Session, cuenta: CalendarAccount, tok: dict) -> None:
    cuenta.access_token = cifrar(tok.get("access_token", ""))
    if tok.get("refresh_token"):
        cuenta.refresh_token = cifrar(tok["refresh_token"])
    if tok.get("expires_in"):
        try:
            cuenta.token_expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=int(tok["expires_in"]))
            ).isoformat()
        except (TypeError, ValueError):
            cuenta.token_expires_at = None
    if tok.get("scope"):
        cuenta.scopes = tok["scope"]
    cuenta.status = "ok"
    cuenta.last_error = ""
    db.add(cuenta); db.commit()


def _marcar(db: Session, cuenta: CalendarAccount, estado: str, motivo: str) -> None:
    cuenta.status = estado
    cuenta.last_error = motivo
    db.add(cuenta); db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Descubrir los calendarios de una cuenta
# ─────────────────────────────────────────────────────────────────────────────

async def descubrir_calendarios(db: Session, cuenta: CalendarAccount, user: User) -> int:
    """Crea o actualiza una fila `Calendar` por cada calendario de la cuenta."""
    mod = MODULOS[cuenta.provider]
    token = await token_de(db, cuenta)
    if cuenta.provider == "zoho":
        remotos = await mod.listar_calendarios(token, cuenta.data_center)
    else:
        remotos = await mod.listar_calendarios(token)

    nuevos = 0
    for r in remotos:
        cal = db.exec(
            select(Calendar).where(Calendar.account_id == cuenta.id,
                                   Calendar.external_id == r["external_id"])
        ).first()
        if not cal:
            cal = Calendar(
                key=uuid.uuid4().hex, tenant_id=user.tenant_id,
                name=r["name"], origin=cuenta.provider,
                owner_user_id=user.id, account_id=cuenta.id,
                external_id=r["external_id"],
                color=r.get("color") or "#0ea5e9",
            )
            nuevos += 1
        cal.name = r["name"]
        cal.read_only = bool(r.get("read_only"))
        if r.get("timezone"):
            cal.timezone = r["timezone"]
        db.add(cal)
    db.commit()
    return nuevos


# ─────────────────────────────────────────────────────────────────────────────
# Traer los eventos
# ─────────────────────────────────────────────────────────────────────────────

async def sincronizar_calendario(db: Session, cal: Calendar) -> int:
    """Relee un calendario. Devuelve cuántas entradas quedaron.

    Lo que nació aquí y se llevó allá vuelve en esta lectura: si no se
    descartara, saldría por duplicado y editar una de las dos copias
    dejaría las dos distintas.
    """
    desde = datetime.now(timezone.utc) - VENTANA_ATRAS
    hasta = datetime.now(timezone.utc) + VENTANA_ADELANTE

    try:
        if cal.origin == "suscrito":
            # `descargar` usa httpx síncrono y puede tardar hasta 20 s: en el
            # bucle de eventos dejaría la API entera parada mientras espera.
            eventos = await to_thread.run_sync(_leer_ics, cal, desde, hasta)
        else:
            eventos = await _leer_proveedor(db, cal, desde, hasta)
    except Exception as e:  # noqa: BLE001
        cal.sync_error = str(e)[:500]
        cal.last_synced_at = _ahora()
        db.add(cal); db.commit()
        logger.warning("Sync de «%s» falló: %s", cal.name, e)
        raise

    # Los uid que ya son nuestros: su copia de fuera se descarta.
    mios = {
        e.external_uid for e in db.exec(
            select(CalendarEntry).where(CalendarEntry.calendar_id == cal.id)
        ).all() if e.external_uid
    }

    vistos: set[str] = set()
    for ev in eventos:
        uid = ev.get("external_uid") or ""
        if not uid or uid in mios or ev.get("acten_id"):
            continue
        vistos.add(uid)
        fila = db.exec(
            select(ExternalEvent).where(ExternalEvent.calendar_id == cal.id,
                                        ExternalEvent.external_uid == uid)
        ).first()
        if not fila:
            fila = ExternalEvent(calendar_id=cal.id, external_uid=uid)
        fila.title = ev.get("title") or ""
        fila.description = ev.get("description") or ""
        fila.location = ev.get("location") or ""
        fila.start_at = ev.get("start_at") or ""
        fila.end_at = ev.get("end_at") or ""
        fila.all_day = bool(ev.get("all_day"))
        fila.url = ev.get("url") or ""
        fila.cancelled = bool(ev.get("cancelled"))
        fila.updated_at = _ahora()
        db.add(fila)

    # Lo que ya no está allá se va de aquí: si no, un evento cancelado
    # seguiría ocupando la hora en la pantalla de todo el mundo.
    if vistos:
        for viejo in db.exec(
            select(ExternalEvent).where(ExternalEvent.calendar_id == cal.id)
        ).all():
            if viejo.external_uid not in vistos:
                db.delete(viejo)

    cal.sync_error = ""
    cal.last_synced_at = _ahora()
    db.add(cal); db.commit()
    return len(vistos)


def _leer_ics(cal: Calendar, desde: datetime, hasta: datetime) -> list[dict]:
    texto, etag = calendar_ics.descargar(cal.ics_url, cal.sync_token)
    if texto is None:
        return []           # 304: no cambió
    cal.sync_token = etag or ""
    crudos = calendar_ics.parsear(texto)
    salida = []
    for ev in calendar_ics.expandir(crudos, desde, hasta):
        salida.append({
            "external_uid": calendar_ics.clave_instancia(ev),
            "title": ev.get("title") or "(sin título)",
            "description": ev.get("description") or "",
            "location": ev.get("location") or "",
            "start_at": ev["instancia"].isoformat(),
            "end_at": ev["fin"].isoformat(),
            "all_day": bool(ev.get("all_day")),
            "url": ev.get("url") or "",
            "cancelled": bool(ev.get("cancelled")),
        })
    return salida


async def _leer_proveedor(
    db: Session, cal: Calendar, desde: datetime, hasta: datetime) -> list[dict]:
    cuenta = db.get(CalendarAccount, cal.account_id) if cal.account_id else None
    if not cuenta:
        raise SyncError("El calendario no tiene cuenta conectada.")
    mod = MODULOS[cuenta.provider]
    token = await token_de(db, cuenta)
    if cuenta.provider == "zoho":
        eventos, _ = await mod.listar_eventos(
            token, cal.external_id, desde, hasta, cuenta.data_center)
    else:
        eventos, nuevo_token = await mod.listar_eventos(
            token, cal.external_id, desde, hasta, cal.sync_token)
        if nuevo_token:
            cal.sync_token = nuevo_token
    cuenta.last_synced_at = _ahora()
    db.add(cuenta)
    return eventos


async def sincronizar_todo_de(db: Session, user: User) -> dict:
    """Todos los calendarios de esta persona. Un fallo no para a los demás."""
    cals = db.exec(
        select(Calendar).where(Calendar.owner_user_id == user.id)
    ).all()
    hechos, fallos = 0, []
    for cal in cals:
        if cal.origin in ("propio", "equipo", "proyecto"):
            continue
        try:
            await sincronizar_calendario(db, cal)
            hechos += 1
        except Exception as e:  # noqa: BLE001
            fallos.append({"calendario": cal.name, "motivo": str(e)[:200]})
    return {"sincronizados": hechos, "fallos": fallos}
