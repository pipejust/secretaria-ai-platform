"""Helpers para el API público de sesiones (GET /sessions, GET /sessions/{id}).

Separado de `routers/integration_v1.py` para no inflar un archivo que ya está
sobre el umbral del hook de tamaño (>1000 líneas). Aquí vive:

- Resolución de un filtro de participante (correo o `employee_external_id`
  resueltos a correos canónicos contra User.external_ref / ProjectContact.external_ref).
- Enriquecimiento de la lista de asistentes de una sesión con
  `employee_external_id` tomado de User o ProjectContact por correo.
- Serialización de la duración real cuando existe (started_at + ended_at),
  cayendo a la estimación por palabras cuando no.

Siempre lectura. No modifica el estado.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Iterable, Optional

from sqlmodel import Session, select

from models import MeetingSession, Project, ProjectContact, User


# ─────────────────────────────────────────────────────────────────────────
# Helpers de parseo tolerante.
# ─────────────────────────────────────────────────────────────────────────

def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        # Acepta "2026-11-16", "2026-11-16T10:30:00", "...+00:00", "...Z".
        s = value.strip().replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def duration_min_from(started_at: Optional[str], ended_at: Optional[str]) -> Optional[int]:
    """Minutos reales entre dos ISO; None si falta alguno o se parsean mal."""
    a = _parse_iso(started_at)
    b = _parse_iso(ended_at)
    if a is None or b is None:
        return None
    delta = (b - a).total_seconds() / 60.0
    if delta < 0:
        return None
    return max(1, round(delta))


# ─────────────────────────────────────────────────────────────────────────
# Resolución de participante para filtrar.
# ─────────────────────────────────────────────────────────────────────────

def resolver_correos_de_participante(
    db: Session,
    tenant_id: int,
    participant: str,
) -> list[str]:
    """Devuelve los correos asociados al identificador dado.

    `participant` puede ser:
    - Un correo directamente (contiene '@'): se devuelve tal cual, canonizado.
    - Un `employee_external_id` (= User.external_ref) o un
      ProjectContact.external_ref: se buscan los correos vinculados en la
      empresa.

    Devuelve lista vacía si no hay match: el endpoint traducirá eso a 0
    resultados. Nunca lanza.
    """
    valor = (participant or "").strip()
    if not valor:
        return []
    correos: set[str] = set()
    if "@" in valor:
        correos.add(valor.lower())
    # Buscar por external_ref en User (empleados) y ProjectContact (contactos).
    users = db.exec(
        select(User.email).where(
            User.tenant_id == tenant_id, User.external_ref == valor
        )
    ).all()
    for e in users:
        if e:
            correos.add(e.lower())
    # ProjectContact no tiene tenant_id — se filtra vía Project.
    contactos = db.exec(
        select(ProjectContact.email)
        .join(Project, Project.id == ProjectContact.project_id)
        .where(Project.tenant_id == tenant_id, ProjectContact.external_ref == valor)
    ).all()
    for e in contactos:
        if e:
            correos.add(e.lower())
    return sorted(correos)


# ─────────────────────────────────────────────────────────────────────────
# Enriquecimiento de asistentes con employee_external_id.
# ─────────────────────────────────────────────────────────────────────────

def enriquecer_asistentes(
    db: Session,
    tenant_id: int,
    attendees: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Añade `employee_external_id` a cada asistente, resolviendo por correo.

    Resuelve contra User.external_ref primero (empleado interno), y si no
    hay, contra ProjectContact.external_ref (contacto externo). Deja el
    campo como None cuando no hay match. Nunca modifica los dicts
    originales — devuelve copias.
    """
    attendees_list = list(attendees or [])
    correos_a_resolver: set[str] = set()
    for a in attendees_list:
        if not isinstance(a, dict):
            continue
        email = (a.get("email") or "").strip().lower()
        if email:
            correos_a_resolver.add(email)
    if not correos_a_resolver:
        return [
            dict(a, employee_external_id=None)
            for a in attendees_list
            if isinstance(a, dict)
        ]
    # Dos queries puntuales, no por asistente.
    mapa_user: dict[str, str] = {}
    for email, ref in db.exec(
        select(User.email, User.external_ref).where(
            User.tenant_id == tenant_id,
            User.email.in_(correos_a_resolver),
        )
    ).all():
        if email and ref:
            mapa_user[email.lower()] = ref
    mapa_contact: dict[str, str] = {}
    pendientes = correos_a_resolver - set(mapa_user.keys())
    if pendientes:
        for email, ref in db.exec(
            select(ProjectContact.email, ProjectContact.external_ref)
            .join(Project, Project.id == ProjectContact.project_id)
            .where(Project.tenant_id == tenant_id, ProjectContact.email.in_(pendientes))
        ).all():
            if email and ref:
                mapa_contact[email.lower()] = ref
    resultado: list[dict[str, Any]] = []
    for a in attendees_list:
        if not isinstance(a, dict):
            continue
        email = (a.get("email") or "").strip().lower()
        ref = mapa_user.get(email) or mapa_contact.get(email)
        resultado.append(dict(a, employee_external_id=ref))
    return resultado


def leer_asistentes_de_sesion(s: MeetingSession) -> list[dict[str, Any]]:
    """Parsea `processed_attendees` tolerante al formato (JSON list/dict/texto)."""
    raw = (s.processed_attendees or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if isinstance(data, list):
        return [a for a in data if isinstance(a, dict)]
    if isinstance(data, dict):
        return [data]
    return []


# ─────────────────────────────────────────────────────────────────────────
# Where clause reutilizable para el filtro de participante.
# ─────────────────────────────────────────────────────────────────────────

def ilike_pattern_para_correo(correo: str) -> str:
    """Patrón ILIKE para buscar un correo dentro del JSON de asistentes.

    Simple: `%"email": "<correo>"%`, insensitive. No tolera orden distinto
    de claves porque el serializer nuestro siempre pone email en la misma
    posición dentro del dict; el escape de JSON garantiza que el valor va
    entre comillas dobles.
    """
    # Escape mínimo para evitar wildcards en el correo mismo.
    sanitized = re.sub(r"[\\%_]", lambda m: "\\" + m.group(0), correo)
    return f'%"email": "{sanitized}"%'


# ponytail: no indexo la columna processed_attendees; ILIKE es O(n) pero el
# volumen de reuniones por tenant es bajo (centenas, no millones). Si crece,
# migrar a tabla SessionAttendee normalizada.
