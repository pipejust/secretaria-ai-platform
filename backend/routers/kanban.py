"""Tablero Kanban de tareas dentro de Acten.

Los campos `kanban_column` y `kanban_order` existían desde el sprint de
integración, pero solo los usaba la API pública: dentro de Acten no había
tablero, solo la lista de pendientes.

**Las columnas del tablero son los estados de la tarea.** No se inventa un
eje aparte: si la columna y el estado fueran cosas distintas, un mismo
pendiente podría estar «hecho» en la lista y «en curso» en el tablero, y
nadie sabría cuál creer. Arrastrar una tarjeta cambia el estado, con las
mismas reglas de transición que aplica la API — `cancelada` es terminal y
se rechaza con un mensaje claro en vez de moverse a medias.

`kanban_order` guarda la posición dentro de la columna. `kanban_column`
se sigue respetando para la plataforma externa, que sí puede tener
columnas propias; el tablero de Acten no lo usa para colocar.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from database import get_session
from models import ActionItem, MeetingSession, Project, Tenant, User
from routers.auth import get_current_tenant, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/kanban", tags=["Kanban"])

# Orden de izquierda a derecha. `cancelled` va al final porque es donde
# muere una tarea, no donde se trabaja.
COLUMNAS = [
    {"key": "pending",   "titulo": "Por hacer"},
    {"key": "blocked",   "titulo": "Bloqueada"},
    {"key": "done",      "titulo": "Hecha"},
    {"key": "cancelled", "titulo": "Cancelada"},
]
CLAVES = [c["key"] for c in COLUMNAS]

# Mismas reglas que la API pública (`integration_v1.VALID_TRANSITIONS`).
TRANSICIONES: dict[str, set[str]] = {
    "pending":   {"blocked", "done", "cancelled"},
    "blocked":   {"pending", "done", "cancelled"},
    "done":      {"pending"},
    "cancelled": set(),
}


def _persona(item: ActionItem) -> dict[str, Any]:
    """Quién carga con la tarea. `None` cuando no la ha cogido nadie."""
    from services.owners import limpiar, tiene_responsable

    if not tiene_responsable(item.owner_name, item.owner_email):
        return {"clave": "__sin_dueno__", "nombre": None, "correo": None}
    correo = limpiar(item.owner_email)
    nombre = limpiar(item.owner_name)
    # La clave agrupa: el correo manda porque el nombre viene de la
    # transcripción y cambia de una reunión a otra («William» / «William
    # Aragón»). Sin correo, el nombre normalizado.
    clave = (correo or "").lower() or (nombre or "").strip().lower()
    return {"clave": clave, "nombre": nombre, "correo": correo}


@router.get("")
def tablero(
    project_id: Optional[int] = Query(None),
    solo_mias: bool = Query(False),
    incluir_cerradas: bool = Query(
        False, description="Traer también hechas y canceladas.",
    ),
    db: Session = Depends(get_session),
    tenant: Tenant = Depends(get_current_tenant),
    user: User = Depends(get_current_user),
):
    """Tarjetas agrupadas por columna, con su persona para las calles."""
    from services.owners import tiene_responsable

    q = select(ActionItem).where(ActionItem.tenant_id == tenant.id)
    if project_id is not None:
        q = q.join(
            MeetingSession, MeetingSession.id == ActionItem.session_id,
        ).where(MeetingSession.project_id == project_id)
    items = list(db.exec(q).all())

    proyectos = {
        p.id: p.name
        for p in db.exec(select(Project).where(Project.tenant_id == tenant.id)).all()
    }
    ses_proy = {
        s.id: s.project_id
        for s in db.exec(
            select(MeetingSession).where(MeetingSession.tenant_id == tenant.id)
        ).all()
    }

    mio = (user.email or "").strip().lower()
    gente: dict[str, dict] = {}
    por_columna: dict[str, list] = {k: [] for k in CLAVES}

    for it in items:
        estado = it.status if it.status in CLAVES else "pending"
        if not incluir_cerradas and estado in ("done", "cancelled"):
            continue
        p = _persona(it)
        if solo_mias and (p["correo"] or "").lower() != mio:
            continue

        if p["clave"] not in gente:
            gente[p["clave"]] = {
                "clave": p["clave"], "nombre": p["nombre"],
                "correo": p["correo"], "tarjetas": 0,
            }
        gente[p["clave"]]["tarjetas"] += 1

        pid = ses_proy.get(it.session_id)
        por_columna[estado].append({
            "id": it.id,
            "titulo": it.title or "",
            "descripcion": it.description or "",
            "prioridad": (it.priority or "media").lower(),
            "vence": it.due_date,
            "hora": it.due_time,
            "session_id": it.session_id,
            "proyecto": proyectos.get(pid) or "General",
            "project_id": pid,
            "persona": p,
            "sin_dueno": not tiene_responsable(it.owner_name, it.owner_email),
            "orden": it.kanban_order if it.kanban_order is not None else 10_000,
            "columna_externa": it.kanban_column,
        })

    for k in CLAVES:
        # Sin posición guardada, lo más urgente arriba: primero lo que
        # tiene fecha y antes vence.
        por_columna[k].sort(
            key=lambda c: (c["orden"], c["vence"] or "9999-12-31", c["id"])
        )

    personas = sorted(
        gente.values(),
        key=lambda g: (g["clave"] == "__sin_dueno__", (g["nombre"] or "~").lower()),
    )
    return {
        "columnas": [
            {**c, "tarjetas": por_columna[c["key"]], "total": len(por_columna[c["key"]])}
            for c in COLUMNAS
        ],
        "personas": personas,
        "total": sum(len(v) for v in por_columna.values()),
    }


class MoverIn(BaseModel):
    columna: str
    orden: Optional[int] = None


@router.patch("/{item_id}")
def mover(
    item_id: int,
    payload: MoverIn,
    db: Session = Depends(get_session),
    tenant: Tenant = Depends(get_current_tenant),
    user: User = Depends(get_current_user),
):
    """Mueve una tarjeta de columna y/o de posición."""
    destino = (payload.columna or "").strip()
    if destino not in CLAVES:
        raise HTTPException(422, f"Columna desconocida: '{destino}'.")

    item = db.get(ActionItem, item_id)
    # 404 y no 403: confirmar que existe una tarea de otra empresa ya es
    # decir de más.
    if not item or item.tenant_id != tenant.id:
        raise HTTPException(404, "Tarea no encontrada.")

    origen = item.status if item.status in CLAVES else "pending"
    if destino != origen and destino not in TRANSICIONES.get(origen, set()):
        raise HTTPException(
            409,
            f"No se puede pasar de «{origen}» a «{destino}». "
            + ("Una tarea cancelada no vuelve; crea otra."
               if origen == "cancelled" else "Transición no permitida."),
        )

    item.status = destino
    item.completed_at = datetime.now().isoformat() if destino == "done" else None
    if payload.orden is not None:
        item.kanban_order = payload.orden
    item.updated_at = datetime.now().isoformat()
    db.add(item)
    db.commit()
    db.refresh(item)

    # La plataforma conectada tiene que enterarse: si no, su tablero
    # muestra el estado viejo hasta que alguien recargue.
    try:
        from services.webhook_sender import send_event_bg
        send_event_bg("task.updated", {
            "task_id": item.id, "status": item.status, "source": "acten",
        }, tenant_id=item.tenant_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("webhook task.updated (%s) no enviado: %s", item.id, exc)

    return {"id": item.id, "columna": item.status, "orden": item.kanban_order}


class ReordenarIn(BaseModel):
    columna: str
    ids: list[int]


@router.post("/reordenar")
def reordenar(
    payload: ReordenarIn,
    db: Session = Depends(get_session),
    tenant: Tenant = Depends(get_current_tenant),
    user: User = Depends(get_current_user),
):
    """Fija el orden de una columna entera tras soltar una tarjeta.

    Se manda la columna completa y no solo la tarjeta movida: calcular
    huecos entre posiciones acaba con dos tarjetas empatadas y un orden
    que depende de cómo desempate la base.
    """
    if (payload.columna or "").strip() not in CLAVES:
        raise HTTPException(422, "Columna desconocida.")
    for pos, tid in enumerate(payload.ids):
        it = db.get(ActionItem, tid)
        if not it or it.tenant_id != tenant.id:
            continue
        it.kanban_order = pos
        db.add(it)
    db.commit()
    return {"columna": payload.columna, "tarjetas": len(payload.ids)}
