"""Las personas de una sesión, alineadas con el proyecto al que pertenece.

El proyecto es la fuente de verdad de quién es quién: cargo, empresa y
correo de cada miembro llegan sincronizados desde Altum. Cuando una sesión
cambia de proyecto —o se piden de nuevo sus participantes— los asistentes y
los responsables de las tareas se vuelven a casar con esos miembros. Lo que
el proyecto no conoce se deja como estaba.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from sqlmodel import Session, select

from models import ActionItem, MeetingSession, ProjectContact
from services.alias_personas import cargar, canonizar, normalizar

logger = logging.getLogger(__name__)

SIN_DATO = ("", "—", None)
# Etiquetas del bot y de Fireflies para voces sin nombre: no son personas.
_ANONIMO = re.compile(r"^(hablante( sin identificar| \S+)?|speaker\s*\d*)$", re.I)


def _asistentes(s: MeetingSession) -> list[dict]:
    try:
        datos = json.loads(s.processed_attendees or "[]")
    except (TypeError, ValueError):
        return []
    return [a for a in datos if isinstance(a, dict)]


def _guardar(db: Session, s: MeetingSession, asistentes: list[dict]) -> list[dict]:
    s.processed_attendees = json.dumps(asistentes, ensure_ascii=False)
    db.add(s)
    db.commit()
    return asistentes


class _Miembros:
    """Los miembros de un proyecto, buscables por correo o por nombre (con alias)."""

    def __init__(self, db: Session, tenant_id: int, project_id: Optional[int]):
        self.tabla = cargar(db, tenant_id) if tenant_id else {}
        self.por_correo: dict[str, ProjectContact] = {}
        self.por_nombre: dict[str, ProjectContact] = {}
        if not project_id:
            return
        for c in db.exec(select(ProjectContact).where(ProjectContact.project_id == project_id)).all():
            if not (c.name or "").strip():
                continue
            if (c.email or "").strip():
                self.por_correo.setdefault(c.email.strip().lower(), c)
            self.por_nombre.setdefault(normalizar(c.name), c)
            nombre_bueno, _ = canonizar(self.tabla, c.name, c.email)
            self.por_nombre.setdefault(normalizar(nombre_bueno), c)

    def buscar(self, nombre: Optional[str], correo: Optional[str]) -> Optional[ProjectContact]:
        if (correo or "").strip().lower() in self.por_correo:
            return self.por_correo[correo.strip().lower()]
        nombre_bueno, _ = canonizar(self.tabla, nombre, correo)
        return self.por_nombre.get(normalizar(nombre_bueno)) or self.por_nombre.get(normalizar(nombre))


def realinear_con_proyecto(db: Session, s: MeetingSession) -> dict:
    """Cargo, empresa y correo de asistentes y responsables según el proyecto actual."""
    miembros = _Miembros(db, s.tenant_id, s.project_id)
    asistentes, tocados = _asistentes(s), 0
    for a in asistentes:
        c = miembros.buscar(a.get("name"), a.get("email"))
        if not c:
            continue
        nuevo = {**a, "name": c.name, "role": c.role or a.get("role") or "—",
                 "entity": c.entity or a.get("entity") or "—", "email": c.email or a.get("email") or ""}
        if nuevo != a:
            a.update(nuevo)
            tocados += 1
    _guardar(db, s, asistentes)
    tareas = 0
    for t in db.exec(select(ActionItem).where(ActionItem.session_id == s.id)).all():
        c = miembros.buscar(t.owner_name, t.owner_email)
        if c and (t.owner_name, t.owner_email or "") != (c.name, c.email or t.owner_email or ""):
            t.owner_name, t.owner_email = c.name, c.email or t.owner_email
            db.add(t)
            tareas += 1
    db.commit()
    logger.info("Sesión %s realineada con su proyecto: %s asistentes, %s tareas", s.id, tocados, tareas)
    return {"attendees_updated": tocados, "tasks_updated": tareas}


def regenerar_participantes(db: Session, s: MeetingSession) -> list[dict]:
    """Quién estuvo: los que hablan en la transcripción y los que el bot vio en la sala.

    Lo que ya estaba se conserva (salvo etiquetas de voz anónima), se suman
    los que falten, se funden los repetidos por alias y todo se alinea con
    los miembros del proyecto.
    """
    from services.transcript_pipeline import _hablantes_y_presentes

    tabla = cargar(db, s.tenant_id)
    resultado: list[dict] = []
    vistos: set[str] = set()

    def meter(ficha: dict) -> None:
        nombre, correo = canonizar(tabla, ficha.get("name"), ficha.get("email"))
        nombre = (nombre or ficha.get("name") or "").strip()
        if not nombre or _ANONIMO.match(nombre):
            return
        clave = normalizar(nombre)
        if clave in vistos:
            return
        vistos.add(clave)
        resultado.append({"name": nombre, "role": ficha.get("role") or "—",
                          "entity": ficha.get("entity") or "—", "email": correo or ficha.get("email") or ""})

    for a in _asistentes(s):
        meter(a)
    for nombre in _hablantes_y_presentes(db, s.id, s.raw_transcript or ""):
        meter({"name": nombre})
    _guardar(db, s, resultado)
    realinear_con_proyecto(db, s)
    return _asistentes(s)


def anadir_participante(db: Session, s: MeetingSession, *, name: str, role: str = "",
                        entity: str = "", email: str = "") -> list[dict]:
    """Añade a alguien, o completa su ficha si ya estaba. Idempotente."""
    tabla = cargar(db, s.tenant_id)
    nombre, correo = canonizar(tabla, name, email)
    nombre = (nombre or name).strip()
    asistentes = _asistentes(s)
    existente = next((a for a in asistentes if normalizar(a.get("name")) == normalizar(nombre)
                      or (correo and (a.get("email") or "").lower() == correo.lower())), None)
    ficha = existente if existente is not None else {"name": nombre, "role": "—", "entity": "—", "email": ""}
    if existente is None:
        asistentes.append(ficha)  # quien ya estaba conserva su nombre tal como se escribió
    for campo, valor in (("role", role), ("entity", entity), ("email", correo or email)):
        if (valor or "").strip():
            ficha[campo] = valor.strip()
    _guardar(db, s, asistentes)
    realinear_con_proyecto(db, s)
    return _asistentes(s)


def quitar_participante(db: Session, s: MeetingSession, name: str) -> list[dict]:
    tabla = cargar(db, s.tenant_id)
    nombre, _ = canonizar(tabla, name, None)
    clave = normalizar(nombre or name)
    quedan = [a for a in _asistentes(s) if normalizar(a.get("name")) != clave]
    return _guardar(db, s, quedan)
