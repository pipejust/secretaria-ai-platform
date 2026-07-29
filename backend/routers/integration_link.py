"""Asistente de enlace bidireccional Acten ↔ plataforma de Servicios.

Resuelve el caso real: un cliente compra **una** de las dos plataformas y
la otra después. Uno de los dos lados tiene la gente y los proyectos, y el
otro está vacío. Pedirle que dé de alta a treinta empleados dos veces es
la forma más rápida de que abandone la integración.

Sirve para los dos sentidos:

  · **Ya tiene Servicios, conecta Acten** → Servicios lee `/directory/*`
    de Acten, ve qué falta y crea aquí con `POST /directory/*`.
  · **Ya tiene Acten, conecta Servicios** → Servicios lee `/directory/*`
    de Acten y da de alta allá lo que le falte.

La interfaz vive en la plataforma de Servicios (es donde se opera); Acten
aporta estos endpoints.

**Todo es idempotente.** Relanzar el asistente no duplica nada: la llave
es `external_ref`, y cuando aún no existe se cae a correo y a nombre. Un
asistente que solo se puede usar una vez es un asistente que nadie se
atreve a usar.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from database import get_session
from models import MeetingSession, Project, ProjectContact
from services.api_key_auth import IntegrationContext, require_scopes
from services.servicios_sync import norm_email, norm_project, name_tokens

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/link", tags=["Asistente de enlace"])


# ══════════════════════════════════════════════════════════════════════
# LECTURA — qué tiene Acten (para que el otro lado lo compare/importe)
# ══════════════════════════════════════════════════════════════════════

@router.get("/directory/people")
def acten_people(
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("sessions:read")),
):
    """Personas de Acten, **agrupadas por persona real**.

    Una misma persona puede tener varias fichas (una por proyecto) y
    varios correos: Felipe figura con `@nexura.com` en el proyecto de ese
    cliente y con `@softnexus.io` en el interno. Se devuelve **una
    entrada por persona** con todos sus correos, para que el otro lado no
    la dé de alta tres veces.
    """
    rows = db.exec(
        select(ProjectContact, Project)
        .join(Project, Project.id == ProjectContact.project_id)
        .where(Project.tenant_id == ctx.tenant.id)
    ).all()

    personas: dict[str, dict] = {}
    for c, p in rows:
        # Clave: el UUID si ya está enlazado; si no, el nombre normalizado.
        key = (c.external_ref or "").strip() or "n:" + " ".join(sorted(name_tokens(c.name)))
        e = personas.setdefault(key, {
            "external_ref": c.external_ref,
            "nombre": c.name or "",
            "correos": [],
            "proyectos": [],
            "cargos": [],
        })
        em = norm_email(c.email)
        if em and em not in e["correos"]:
            e["correos"].append(em)
        if p.name not in e["proyectos"]:
            e["proyectos"].append(p.name)
        if (c.role or "").strip() and c.role not in e["cargos"]:
            e["cargos"].append(c.role)

    items = list(personas.values())
    return {
        "items": items,
        "total": len(items),
        "enlazadas": sum(1 for x in items if x["external_ref"]),
        "sin_enlazar": sum(1 for x in items if not x["external_ref"]),
    }


@router.get("/directory/projects")
def acten_projects(
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("sessions:read")),
):
    """Proyectos de Acten, con su conteo de sesiones — sirve para que el
    otro lado sepa cuáles tienen historial real y merecen crearse allá."""
    out = []
    for p in db.exec(select(Project).where(Project.tenant_id == ctx.tenant.id)).all():
        n_ses = len(db.exec(
            select(MeetingSession).where(MeetingSession.project_id == p.id)
        ).all())
        out.append({
            "external_ref": p.external_ref,
            "nombre": p.name,
            "descripcion": p.description or "",
            "activo": p.is_active,
            "gestionado_externamente": p.managed_externally,
            "sesiones": n_ses,
            "integrantes": len(db.exec(
                select(ProjectContact).where(ProjectContact.project_id == p.id)
            ).all()),
        })
    return {"items": out, "total": len(out)}


# ══════════════════════════════════════════════════════════════════════
# PREVISUALIZACIÓN — el paso 3 del asistente: proponer sin escribir
# ══════════════════════════════════════════════════════════════════════

class RemotePerson(BaseModel):
    external_ref: str
    display_name: str = ""
    full_name: str = ""
    work_email: str = ""


class RemoteProject(BaseModel):
    external_ref: str
    name: str
    client_name: str = ""


class LinkPreviewIn(BaseModel):
    people: list[RemotePerson] = []
    projects: list[RemoteProject] = []


@router.post("/preview")
def link_preview(
    payload: LinkPreviewIn,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("sessions:read")),
):
    """Propone enlaces **sin escribir nada**.

    Devuelve tres listas que alimentan la pantalla del asistente:
      · `enlaces`   — coincidencia clara, se puede aplicar
      · `ambiguos`  — varias personas encajan: **decide un humano**, no se
                      adivina. Asignar mal es darle a alguien las actas de
                      otro, y eso no se descubre hasta que alguien lee un
                      acta que no le corresponde.
      · `faltantes` — están del otro lado y no aquí: hay que crearlos
    """
    contacts = list(db.exec(
        select(ProjectContact)
        .join(Project, Project.id == ProjectContact.project_id)
        .where(Project.tenant_id == ctx.tenant.id)
    ).all())
    projects = list(db.exec(
        select(Project).where(Project.tenant_id == ctx.tenant.id)
    ).all())

    enlaces: list[dict] = []
    ambiguos: list[dict] = []
    faltantes: list[dict] = []

    for rp in payload.people:
        ya = [c for c in contacts if (c.external_ref or "") == rp.external_ref]
        if ya:
            enlaces.append({
                "tipo": "persona", "external_ref": rp.external_ref,
                "remoto": rp.display_name or rp.full_name,
                "acten": [c.name for c in ya], "criterio": "ya_enlazado",
            })
            continue

        em = norm_email(rp.work_email)
        por_correo = [c for c in contacts if em and norm_email(c.email) == em]
        if por_correo:
            enlaces.append({
                "tipo": "persona", "external_ref": rp.external_ref,
                "remoto": rp.display_name or rp.full_name,
                "acten": [c.name for c in por_correo], "criterio": "correo",
            })
            continue

        toks = name_tokens(rp.display_name or rp.full_name)
        fuertes = [c for c in contacts if len(toks & name_tokens(c.name)) >= 2]
        debiles = [c for c in contacts if len(toks & name_tokens(c.name)) == 1]

        def distintas(cs: list) -> set[str]:
            return {" ".join(sorted(name_tokens(c.name))) for c in cs}

        if len(distintas(fuertes)) > 1:
            candidatos = fuertes
            motivo = "Varias personas encajan con ese nombre — elige tú."
        elif fuertes:
            enlaces.append({
                "tipo": "persona", "external_ref": rp.external_ref,
                "remoto": rp.display_name or rp.full_name,
                "acten": [c.name for c in fuertes], "criterio": "nombre",
            })
            continue
        elif debiles:
            # Coincidencia parcial (típicamente solo el apellido). NO se
            # crea a ciegas: «Cortés Burgos» con dos hermanos Cortés daría
            # de alta un tercero que no existe. Decide un humano.
            candidatos = debiles
            motivo = (
                "Solo coincide parcialmente (apellido). Confirma si es una "
                "de estas personas o alguien nuevo — crear a ciegas duplicaría."
            )
        else:
            faltantes.append({
                "tipo": "persona", "external_ref": rp.external_ref,
                "remoto": rp.display_name or rp.full_name,
                "correo": rp.work_email,
            })
            continue

        ambiguos.append({
            "tipo": "persona", "external_ref": rp.external_ref,
            "remoto": rp.display_name or rp.full_name,
            "candidatos": [
                {"nombre": c.name, "correo": c.email, "external_ref": c.external_ref}
                for c in candidatos
            ],
            "motivo": motivo,
        })

    for rpr in payload.projects:
        ya = [p for p in projects if (p.external_ref or "") == rpr.external_ref]
        if ya:
            enlaces.append({
                "tipo": "proyecto", "external_ref": rpr.external_ref,
                "remoto": rpr.name, "acten": [ya[0].name],
                "criterio": "ya_enlazado",
            })
            continue
        n = norm_project(rpr.name)
        cand = [p for p in projects if norm_project(p.name) == n]
        if cand:
            enlaces.append({
                "tipo": "proyecto", "external_ref": rpr.external_ref,
                "remoto": rpr.name, "acten": [cand[0].name], "criterio": "nombre",
            })
        else:
            faltantes.append({
                "tipo": "proyecto", "external_ref": rpr.external_ref,
                "remoto": rpr.name,
            })

    # ── El otro sentido ────────────────────────────────────────────────
    # Lo anterior dice qué falta *en Acten*. Falta la mitad simétrica: qué
    # existe aquí y no allá. Sin ella el asistente solo sirve a quien ya
    # tiene poblada la plataforma de Servicios, y una integración que solo
    # funciona en un sentido obliga a dar de alta a mano el otro.
    #
    # Se calcula en la misma pasada, con las mismas listas ya cargadas: no
    # hay carrera, porque una ejecución la orquesta un solo lado.
    refs_remotos = {p.external_ref for p in payload.people}
    correos_remotos = {norm_email(p.work_email) for p in payload.people if p.work_email}
    nombres_remotos = [name_tokens(p.display_name or p.full_name) for p in payload.people]

    def _emparejado_alla(c: ProjectContact) -> bool:
        if (c.external_ref or "") in refs_remotos:
            return True
        em = norm_email(c.email)
        if em and em in correos_remotos:
            return True
        toks = name_tokens(c.name)
        return any(len(toks & r) >= 2 for r in nombres_remotos)

    # Agrupadas por persona real: Felipe tiene tres fichas y un solo cuerpo.
    pendientes_alla: dict[str, dict] = {}
    for c in contacts:
        if _emparejado_alla(c):
            continue
        key = " ".join(sorted(name_tokens(c.name))) or (c.name or "").lower()
        e = pendientes_alla.setdefault(key, {
            "tipo": "persona", "acten": c.name or "",
            "correos": [], "external_ref_acten": c.external_ref,
        })
        em = norm_email(c.email)
        if em and em not in e["correos"]:
            e["correos"].append(em)

    refs_proy_remotos = {p.external_ref for p in payload.projects}
    nombres_proy_remotos = {norm_project(p.name) for p in payload.projects}
    faltantes_en_servicios: list[dict] = [
        {
            "tipo": "proyecto", "acten": p.name,
            "external_ref_acten": p.external_ref,
            "sesiones": len(db.exec(
                select(MeetingSession).where(MeetingSession.project_id == p.id)
            ).all()),
        }
        for p in projects
        if (p.external_ref or "") not in refs_proy_remotos
        and norm_project(p.name) not in nombres_proy_remotos
    ]
    faltantes_en_servicios.extend(pendientes_alla.values())

    return {
        "enlaces": enlaces,
        "ambiguos": ambiguos,
        "faltantes": faltantes,
        "faltantes_en_servicios": faltantes_en_servicios,
        "resumen": {
            "enlazables": len(enlaces),
            "requieren_decision": len(ambiguos),
            "a_crear_en_acten": len(faltantes),
            "a_crear_en_servicios": len(faltantes_en_servicios),
        },
    }


# ══════════════════════════════════════════════════════════════════════
# ESCRITURA — crear/enlazar en Acten (idempotente)
# ══════════════════════════════════════════════════════════════════════

class UpsertProjectIn(BaseModel):
    external_ref: str = Field(min_length=1)
    name: str = Field(min_length=1)
    client_name: str = ""
    active: bool = True


@router.post("/directory/projects")
def upsert_project(
    payload: UpsertProjectIn,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("sync:write")),
):
    """Crea o enlaza un proyecto. **Idempotente**: llamarlo dos veces con
    el mismo `external_ref` devuelve el existente en vez de duplicar."""
    existing = db.exec(
        select(Project)
        .where(Project.tenant_id == ctx.tenant.id)
        .where(Project.external_ref == payload.external_ref)
    ).first()
    creado = False

    if existing is None:
        # Aún sin enlazar: reusar el que ya tenga ese nombre antes de crear.
        n = norm_project(payload.name)
        for p in db.exec(select(Project).where(Project.tenant_id == ctx.tenant.id)).all():
            if norm_project(p.name) == n:
                existing = p
                break

    if existing is None:
        existing = Project(
            tenant_id=ctx.tenant.id,
            name=payload.name,
            description=payload.client_name or "",
            is_active=payload.active,
        )
        creado = True

    existing.external_ref = payload.external_ref
    existing.managed_externally = True
    db.add(existing)
    db.commit()
    db.refresh(existing)
    return {
        "creado": creado, "enlazado": True,
        "id": existing.id, "nombre": existing.name,
        "external_ref": existing.external_ref,
    }


class UpsertPersonIn(BaseModel):
    external_ref: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    email: str = ""
    position: str = ""
    project_external_ref: Optional[str] = None


@router.post("/directory/people")
def upsert_person(
    payload: UpsertPersonIn,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("sync:write")),
):
    """Crea o enlaza una persona en un proyecto. **Idempotente.**

    Si ya existe una ficha con ese `external_ref` en ese proyecto, se
    actualiza en vez de crear otra. Una persona puede tener varias fichas
    (una por proyecto) y varios correos: eso es correcto y deliberado.
    """
    proyecto: Optional[Project] = None
    if payload.project_external_ref:
        proyecto = db.exec(
            select(Project)
            .where(Project.tenant_id == ctx.tenant.id)
            .where(Project.external_ref == payload.project_external_ref)
        ).first()
    if proyecto is None:
        return {
            "creado": False, "enlazado": False,
            "error": "Primero hay que crear/enlazar el proyecto "
                     f"'{payload.project_external_ref}'.",
        }

    ficha = db.exec(
        select(ProjectContact)
        .where(ProjectContact.project_id == proyecto.id)
        .where(ProjectContact.external_ref == payload.external_ref)
    ).first()
    creado = False

    if ficha is None and payload.email:
        em = norm_email(payload.email)
        for c in db.exec(
            select(ProjectContact).where(ProjectContact.project_id == proyecto.id)
        ).all():
            if norm_email(c.email) == em:
                ficha = c
                break

    if ficha is None:
        ficha = ProjectContact(
            project_id=proyecto.id,
            name=payload.display_name,
            email=payload.email or "",
            role=payload.position or "",
        )
        creado = True

    ficha.external_ref = payload.external_ref
    ficha.name = payload.display_name
    if payload.position:
        ficha.role = payload.position
    # El correo NO se pisa si ya hay uno: en cada proyecto la persona
    # puede figurar con el correo del cliente, y eso es deliberado.
    if payload.email and not (ficha.email or "").strip():
        ficha.email = payload.email

    db.add(ficha)
    db.commit()
    db.refresh(ficha)
    return {
        "creado": creado, "enlazado": True,
        "id": ficha.id, "nombre": ficha.name,
        "proyecto": proyecto.name, "external_ref": ficha.external_ref,
    }
