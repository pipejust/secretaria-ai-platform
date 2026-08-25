"""El historial de una tarea: quién la movió, de qué valor a cuál.

Antes, `task.updated` decía «la tarea 1664 quedó en pending» y nada más.
Con eso, quien lo recibe no puede pintar un historial sin inventarse la
mitad: falta **quién** lo hizo —el único dato que no se deduce por ningún
camino—, **desde qué valor** venía, y si `status` es lo que cambió o solo
el estado actual viajando de acompañamiento.

Este módulo hace tres cosas en un solo sitio, para que las cinco puertas
por las que se toca una tarea cuenten lo mismo:

1. Saca una foto de la tarea antes de tocarla (`instantanea`).
2. Compara con el después y calcula el diff campo a campo.
3. Guarda el evento y lo manda por webhook con el actor dentro.

Lo que **no** hace: inventar. Si no hay actor identificable, se dice
`system` en vez de atribuirle el cambio a alguien.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlmodel import Session, select

from models import ActionItem, TaskEvent, User

logger = logging.getLogger(__name__)

# Los campos cuyo cambio merece una línea en el historial. El orden es el
# que se usa al enseñarlos.
# Desde cuándo hay historial. Antes de esta fecha el autor no se guardaba
# en ningún sitio, así que no hay nada que devolver — y es mejor decirlo
# que dejar que se lea como «esta tarea no se tocó nunca».
DESDE_CUANDO = "2026-08-21"

CAMPOS = (
    "status", "title", "description", "due_date", "due_time",
    "priority", "owner_name", "owner_email", "is_approved",
)


def instantanea(item: ActionItem) -> dict:
    """La foto de la tarea, para comparar después."""
    return {c: getattr(item, c, None) for c in CAMPOS}


def _diff(antes: dict, despues: dict) -> dict:
    """`{"status": {"from": "done", "to": "pending"}}` — solo lo que cambió."""
    cambios: dict[str, dict] = {}
    for campo in CAMPOS:
        a, d = antes.get(campo), despues.get(campo)
        if a != d:
            cambios[campo] = {"from": a, "to": d}
    return cambios


# ─────────────────────────────────────────────────────────────────────────────
# Quién
# ─────────────────────────────────────────────────────────────────────────────

def actor_de_usuario(db: Session, user: Optional[User]) -> dict:
    """El actor cuando el cambio entra por la sesión de una persona."""
    if not user:
        return actor_sistema()
    return {
        "kind": "user",
        "id": user.id,
        "name": user.full_name or user.email,
        "employee_external_id": _external_id(db, user),
    }


def actor_de_integracion(db: Session, ctx: Any) -> dict:
    """El actor cuando el cambio entra por una clave de integración.

    Si viene `X-On-Behalf-Of`, el empleado en cuyo nombre se actúa **es** el
    actor: es una persona apretando un botón en la otra plataforma, y
    atribuirlo a «la integración» perdería justo lo que se pregunta.
    Sin él, el autor es la clave, y se marca como `integration` para que
    nadie lo lea como una persona.
    """
    quien = getattr(ctx, "acting_user", None)
    if quien:
        datos = actor_de_usuario(db, quien)
        # Llegó por una clave, pero lo hizo una persona identificada.
        datos["via"] = "integration"
        return datos
    nombre = getattr(getattr(ctx, "api_key", None), "name", "") or "Integración"
    return {"kind": "integration", "id": None, "name": nombre,
            "employee_external_id": ""}


def actor_sistema(motivo: str = "") -> dict:
    """El cron, el pipeline de IA, una migración. Nunca una persona."""
    return {"kind": "system", "id": None,
            "name": motivo or "Acten (automático)", "employee_external_id": ""}


def _external_id(db: Session, user: User) -> str:
    """El id de esa persona en el directorio de la otra plataforma.

    Se resuelve por el mismo camino que ya usa el resto del API —el
    `external_ref` del contacto con ese correo—, y no por uno nuevo: dos
    formas de resolver la misma pregunta acaban dando respuestas distintas
    el día que una de las dos se cambie.
    """
    # `User.external_ref` es la fuente directa: es el mismo campo por el que
    # se resuelve `X-On-Behalf-Of`, así que si la persona pudo actuar en
    # nombre de alguien, este id existe. El contacto queda de reserva para
    # quien no esté enlazado todavía.
    directo = (getattr(user, "external_ref", "") or "").strip()
    if directo:
        return directo

    correo = (user.email or "").strip().lower()
    if not correo:
        return ""
    try:
        from models import ProjectContact  # noqa: PLC0415

        fila = db.exec(
            select(ProjectContact.external_ref)
            .where(ProjectContact.email == correo)
            .where(ProjectContact.external_ref.is_not(None))
        ).first()
        valor = fila[0] if isinstance(fila, tuple) else fila
        return valor or ""
    except Exception:  # noqa: BLE001 — el enlace es opcional
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Registrar
# ─────────────────────────────────────────────────────────────────────────────

def registrar(
    db: Session,
    item: ActionItem,
    antes: Optional[dict],
    actor: dict,
    *,
    kind: str = "updated",
    body: str = "",
    enviar: bool = True,
    extra: Optional[dict] = None,
) -> Optional[TaskEvent]:
    """Guarda el evento y lo manda. Devuelve `None` si no cambió nada.

    Un `updated` sin cambios no se registra: llenaría el historial de
    líneas que no dicen nada, y quien lo lee dejaría de mirarlo.
    """
    cambios = _diff(antes, instantanea(item)) if antes is not None else {}
    if kind == "updated" and not cambios:
        return None

    evento = TaskEvent(
        tenant_id=item.tenant_id,
        task_id=item.id,
        event_id=str(uuid.uuid4()),
        kind=kind,
        occurred_at=datetime.now(timezone.utc).isoformat(),
        actor_kind=actor.get("kind", "system"),
        actor_user_id=actor.get("id"),
        actor_name=actor.get("name", ""),
        actor_external_id=actor.get("employee_external_id", "") or "",
        changes_json=json.dumps(cambios, ensure_ascii=False, default=str),
        body=body[:4000],
    )
    try:
        db.add(evento)
        db.commit()
        db.refresh(evento)
    except Exception:  # noqa: BLE001
        # El historial no puede tumbar el cambio que lo originó.
        logger.exception("No se pudo guardar el historial de la tarea %s", item.id)
        db.rollback()
        return None

    if enviar:
        _mandar(db, item, evento, cambios, actor, extra or {})
    return evento


def _mandar(db: Session, item: ActionItem, evento: TaskEvent,
            cambios: dict, actor: dict, extra: dict) -> None:
    from services.webhook_sender import send_event_bg

    tipo = {"created": "task.created", "deleted": "task.deleted",
            "comment": "comment.created"}.get(evento.kind, "task.updated")
    cuerpo = {
        "event_id": evento.event_id,
        "occurred_at": evento.occurred_at,
        "task_id": item.id,
        "status": item.status,          # se mantiene: ya lo consumen
        "source": "acten",
        "actor": actor,
        "changes": cambios,
        **extra,
    }
    if evento.body:
        cuerpo["comment"] = {"body": evento.body}
    try:
        send_event_bg(tipo, cuerpo, tenant_id=item.tenant_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("webhook %s (tarea %s) no enviado: %s", tipo, item.id, exc)


# ─────────────────────────────────────────────────────────────────────────────
# Leer
# ─────────────────────────────────────────────────────────────────────────────

def historial(db: Session, task_id: int, tenant_id: int, limite: int = 200) -> list[dict]:
    filas = db.exec(
        select(TaskEvent)
        .where(TaskEvent.task_id == task_id)
        .where(TaskEvent.tenant_id == tenant_id)
        .order_by(TaskEvent.occurred_at.desc())
        .limit(limite)
    ).all()
    salida = []
    for f in filas:
        try:
            cambios = json.loads(f.changes_json or "{}")
        except (json.JSONDecodeError, TypeError):
            cambios = {}
        salida.append({
            "event_id": f.event_id,
            "kind": f.kind,
            "occurred_at": f.occurred_at,
            "actor": {
                "kind": f.actor_kind,
                "id": f.actor_user_id,
                "name": f.actor_name,
                "employee_external_id": f.actor_external_id or None,
            },
            "changes": cambios,
            **({"comment": {"body": f.body}} if f.body else {}),
        })
    return salida
