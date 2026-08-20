"""Quién ve qué calendarios, con qué permiso, y qué se pinta en ellos.

Un calendario no es una vista, es una pertenencia: cada cosa que se
pinta pertenece a exactamente uno, y cada uno tiene nombre, color y una
casilla que lo enciende y lo apaga. Eso es lo que permite decir «hoy no
quiero ver las tareas» sin perder nada.

Hay dos familias. Los **guardados** tienen fila en `calendar`. Los
**derivados** —sesiones, tareas, festivos— se calculan de otros datos y
no tienen fila a propósito; de ellos solo se guarda la preferencia de
cada quien, colgada de su clave de texto.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional

from sqlmodel import Session, select

from models import (
    ActionItem, Calendar, CalendarAccount, CalendarEntry, CalendarPref,
    CalendarShare, ExternalEvent, MeetingSession, Project, User,
)
from services import festivos_co

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Permisos
# ─────────────────────────────────────────────────────────────────────────────
# Cuatro niveles, cada uno incluye al anterior. `ocupado` es el «ver solo
# libre/ocupado» de Google, y existe por un motivo concreto: permite
# cuadrar una reunión con alguien sin enterarse de a qué médico va.
ORDEN = {"ocupado": 1, "ver": 2, "editar": 3, "gestionar": 4}
PERMISOS = tuple(ORDEN)


def puede(permiso: Optional[str], minimo: str) -> bool:
    return ORDEN.get(permiso or "", 0) >= ORDEN.get(minimo, 99)


def _es_admin(user: User) -> bool:
    return bool(getattr(user, "role", None) and user.role.name == "admin")


# ─────────────────────────────────────────────────────────────────────────────
# Los derivados
# ─────────────────────────────────────────────────────────────────────────────
DERIVADOS: dict[str, dict] = {
    "sys:sesiones": {
        "name": "Sesiones", "color": "#6366f1",
        "descripcion": "Las reuniones que Acten procesó.",
    },
    "sys:tareas": {
        "name": "Tareas", "color": "#f59e0b",
        "descripcion": "Los compromisos con fecha de entrega.",
    },
    "sys:festivos": {
        "name": "Festivos de Colombia", "color": "#10b981",
        "descripcion": "Calculados con la Ley Emiliani; no se editan.",
    },
}


def _bloque_derivado(clave: str, prefs: dict) -> dict:
    d = DERIVADOS[clave]
    p = prefs.get(clave, {})
    return {
        "key": clave,
        "name": d["name"],
        "description": d["descripcion"],
        "color": p.get("color") or d["color"],
        "origin": "derivado",
        "permission": "ver",     # nunca se escribe en un derivado
        "read_only": True,
        "visible": p.get("visible", True),
        "position": p.get("position", 0),
        "account_email": "",
        "status": "ok",
        "sync_error": "",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Preferencias
# ─────────────────────────────────────────────────────────────────────────────

def prefs_de(db: Session, user: User) -> dict:
    filas = db.exec(select(CalendarPref).where(CalendarPref.user_id == user.id)).all()
    return {f.calendar_key: {"visible": f.visible, "color": f.color,
                             "position": f.position} for f in filas}


def guardar_pref(db: Session, user: User, clave: str,
                 visible: Optional[bool] = None, color: Optional[str] = None,
                 position: Optional[int] = None) -> CalendarPref:
    fila = db.exec(
        select(CalendarPref).where(CalendarPref.user_id == user.id,
                                   CalendarPref.calendar_key == clave)
    ).first()
    if not fila:
        fila = CalendarPref(user_id=user.id, calendar_key=clave)
    if visible is not None:
        fila.visible = visible
    if color is not None:
        fila.color = color
    if position is not None:
        fila.position = position
    db.add(fila); db.commit(); db.refresh(fila)
    return fila


# ─────────────────────────────────────────────────────────────────────────────
# Permiso sobre un calendario guardado
# ─────────────────────────────────────────────────────────────────────────────

def permiso_de(db: Session, user: User, cal: Calendar) -> Optional[str]:
    """El permiso de esta persona sobre este calendario, o `None` si ni lo ve.

    Se resuelve en el servidor y viaja con la lista. Si la pantalla
    decidiera qué se puede editar repitiendo la regla, esa copia se
    quedaría vieja el día que la regla cambie.
    """
    if cal.tenant_id != user.tenant_id:
        return None
    # Quien administra la empresa gestiona todos. Partir esto dejaría
    # calendarios que nadie puede arreglar cuando quien los hizo se va.
    if _es_admin(user):
        return "gestionar"
    if cal.owner_user_id == user.id:
        return "gestionar"

    mejor: Optional[str] = None
    compartidos = db.exec(
        select(CalendarShare).where(CalendarShare.calendar_id == cal.id)
    ).all()
    for s in compartidos:
        if s.user_id in (None, user.id):
            if ORDEN.get(s.permission, 0) > ORDEN.get(mejor or "", 0):
                mejor = s.permission
    return mejor


def _nombre_para(cal: Calendar, user: User, duenios: dict[int, str]) -> str:
    """El nombre de un calendario personal no significa nada fuera de su dueño.

    «Mi calendario» hay que sustituirlo por el nombre de la persona al
    enseñárselo a otra, o la barra lateral acaba con once entradas
    idénticas y ninguna se sabe de quién es.
    """
    if cal.owner_user_id and cal.owner_user_id != user.id:
        quien = duenios.get(cal.owner_user_id)
        if quien:
            return f"{cal.name} · {quien}"
    return cal.name


def lista_para(db: Session, user: User) -> list[dict]:
    """Los calendarios de quien pregunta, con permiso, color y casilla resueltos.

    **La lista es de quien mira; la potestad es otra cosa.** Dársela al
    administrador según lo que puede tocar le llenaba la barra lateral
    con la agenda personal de todo el equipo: once entradas llamadas
    «Mi calendario» que no había pedido ver.
    """
    prefs = prefs_de(db, user)
    salida = [_bloque_derivado(k, prefs) for k in DERIVADOS]

    guardados = db.exec(
        select(Calendar).where(Calendar.tenant_id == user.tenant_id)
    ).all()
    compartidos_conmigo = {
        s.calendar_id: s.permission
        for s in db.exec(select(CalendarShare)).all()
        if s.user_id in (None, user.id)
    }
    duenios = {u.id: u.full_name or u.email
               for u in db.exec(select(User).where(User.tenant_id == user.tenant_id)).all()}
    cuentas = {c.id: c for c in db.exec(
        select(CalendarAccount).where(CalendarAccount.user_id == user.id)).all()}

    for cal in guardados:
        mio = cal.owner_user_id == user.id
        compartido = cal.id in compartidos_conmigo
        de_la_empresa = cal.origin in ("equipo", "proyecto") and not cal.owner_user_id
        if not (mio or compartido or de_la_empresa):
            continue

        permiso = permiso_de(db, user, cal) or "ver"
        p = prefs.get(cal.key, {})
        cuenta = cuentas.get(cal.account_id) if cal.account_id else None
        salida.append({
            "key": cal.key,
            "id": cal.id,
            "name": _nombre_para(cal, user, duenios),
            "description": cal.description,
            "color": p.get("color") or cal.color,
            "origin": cal.origin,
            "permission": permiso,
            "read_only": cal.read_only or not puede(permiso, "editar"),
            "visible": p.get("visible", True),
            "position": p.get("position", 0),
            "project_id": cal.project_id,
            "is_default": cal.is_default,
            "ics_url": cal.ics_url,
            # Dos cuentas suelen dar dos calendarios llamados igual: el
            # correo debajo es lo que los distingue.
            "account_email": cuenta.account_email if cuenta else "",
            "account_id": cal.account_id,
            "status": cuenta.status if cuenta else "ok",
            "sync_error": cal.sync_error,
            "last_synced_at": cal.last_synced_at,
        })

    salida.sort(key=lambda c: (c.get("position", 0), c["name"].lower()))
    return salida


def por_clave(db: Session, user: User, clave: str) -> tuple[Optional[Calendar], Optional[str]]:
    """(calendario, permiso). Para los derivados devuelve `(None, 'ver')`."""
    if clave in DERIVADOS:
        return None, "ver"
    cal = db.exec(select(Calendar).where(Calendar.key == clave)).first()
    if not cal:
        return None, None
    return cal, permiso_de(db, user, cal)


def asegurar_propio(db: Session, user: User) -> Calendar:
    """El calendario personal, creado la primera vez que hace falta.

    Se crea al vuelo y no en el alta del usuario: así los que ya existían
    antes de este módulo también lo tienen, sin migración que recorra la
    tabla entera.
    """
    cal = db.exec(
        select(Calendar).where(Calendar.owner_user_id == user.id,
                               Calendar.origin == "propio")
    ).first()
    if cal:
        return cal
    cal = Calendar(
        key=uuid.uuid4().hex, tenant_id=user.tenant_id, name="Mi calendario",
        color="#6366f1", origin="propio", owner_user_id=user.id, is_default=True,
    )
    db.add(cal); db.commit(); db.refresh(cal)
    return cal


def destino_por_defecto(db: Session, user: User) -> Calendar:
    """Dónde caen los eventos si nadie eligió."""
    cal = db.exec(
        select(Calendar).where(Calendar.owner_user_id == user.id,
                               Calendar.is_default == True)  # noqa: E712
    ).first()
    return cal or asegurar_propio(db, user)


# ─────────────────────────────────────────────────────────────────────────────
# Lo que se pinta
# ─────────────────────────────────────────────────────────────────────────────

def _iso(v: str) -> str:
    return (v or "").strip()


def _dentro(valor: str, desde: datetime, hasta: datetime) -> bool:
    if not valor:
        return False
    try:
        d = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        try:
            d = datetime.fromisoformat(valor[:10])
        except ValueError:
            return False
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return desde <= d <= hasta


def eventos(
    db: Session, user: User, desde: datetime, hasta: datetime,
    claves: Optional[Iterable[str]] = None,
) -> list[dict]:
    """Todo lo que cae en la ventana, con el calendario al que pertenece.

    Cada entrada lleva `calendar`, que es la clave de a cuál pertenece. La
    pantalla apaga y enciende con eso, sin volver a preguntar al
    servidor: pedir la agenda otra vez por cada clic haría que el
    calendario parpadeara.

    **El permiso filtra los datos, no solo los botones.** Un calendario
    en `ocupado` devuelve la franja sin el título; es el punto donde
    esto deja de ser cosmético.
    """
    visibles = {c["key"]: c for c in lista_para(db, user)}
    pedidas = set(claves) if claves else None
    def activo(clave: str) -> bool:
        if clave not in visibles:
            return False
        return clave in pedidas if pedidas is not None else True

    salida: list[dict] = []

    # ── Derivados ────────────────────────────────────────────────────────
    if activo("sys:sesiones"):
        for s in db.exec(
            select(MeetingSession).where(MeetingSession.tenant_id == user.tenant_id)
        ).all():
            if not _dentro(s.date, desde, hasta):
                continue
            salida.append({
                "id": f"sesion-{s.id}", "calendar": "sys:sesiones",
                "title": s.title, "start_at": _iso(s.date), "end_at": "",
                "all_day": False, "kind": "sesion", "session_id": s.id,
                "project_id": s.project_id, "editable": False,
            })

    if activo("sys:tareas"):
        for t in db.exec(
            select(ActionItem).where(ActionItem.tenant_id == user.tenant_id)
        ).all():
            if not t.due_date or not _dentro(t.due_date, desde, hasta):
                continue
            hora = (t.due_time or "").strip()
            inicio = f"{t.due_date[:10]}T{hora}:00+00:00" if hora else f"{t.due_date[:10]}T00:00:00+00:00"
            salida.append({
                "id": f"tarea-{t.id}", "calendar": "sys:tareas",
                "title": t.title, "start_at": inicio, "end_at": "",
                "all_day": not hora, "kind": "tarea",
                "owner": t.owner_name or t.owner_email, "status": t.status,
                "priority": t.priority, "session_id": t.session_id,
                "editable": False,
            })

    if activo("sys:festivos"):
        for d, nombre in festivos_co.en_rango(desde.date(), hasta.date()):
            salida.append({
                "id": f"festivo-{d.isoformat()}", "calendar": "sys:festivos",
                "title": nombre, "start_at": f"{d.isoformat()}T00:00:00+00:00",
                "end_at": "", "all_day": True, "kind": "festivo", "editable": False,
            })

    # ── Guardados: lo propio y lo traído de fuera ────────────────────────
    ids = {c["id"]: c for c in visibles.values() if c.get("id") and activo(c["key"])}
    if ids:
        for e in db.exec(
            select(CalendarEntry).where(CalendarEntry.calendar_id.in_(list(ids)))
        ).all():
            if not _dentro(e.start_at, desde, hasta):
                continue
            cal = ids[e.calendar_id]
            reservado = cal["permission"] == "ocupado"
            salida.append({
                "id": f"evento-{e.id}", "calendar": cal["key"],
                "title": "Ocupado" if reservado else e.title,
                "description": "" if reservado else e.description,
                "location": "" if reservado else e.location,
                "start_at": e.start_at, "end_at": e.end_at, "all_day": e.all_day,
                "kind": "evento", "meeting_url": "" if reservado else e.meeting_url,
                "project_id": e.project_id, "session_id": e.session_id,
                "external_uid": e.external_uid, "external_error": e.external_error,
                "editable": puede(cal["permission"], "editar") and not cal["read_only"],
            })

        for x in db.exec(
            select(ExternalEvent).where(ExternalEvent.calendar_id.in_(list(ids)))
        ).all():
            if x.cancelled or not _dentro(x.start_at, desde, hasta):
                continue
            cal = ids[x.calendar_id]
            reservado = cal["permission"] == "ocupado"
            salida.append({
                "id": f"externo-{x.id}", "calendar": cal["key"],
                "title": "Ocupado" if reservado else x.title,
                "description": "" if reservado else x.description,
                "location": "" if reservado else x.location,
                "start_at": x.start_at, "end_at": x.end_at, "all_day": x.all_day,
                "kind": "externo", "url": "" if reservado else x.url,
                # Lo de fuera no es nuestro: aquí no se edita.
                "editable": False,
            })

    salida.sort(key=lambda e: e.get("start_at") or "")
    return salida
