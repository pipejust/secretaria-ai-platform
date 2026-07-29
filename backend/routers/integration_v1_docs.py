"""API pública v1 — plantillas documentales (`.docx`).

Aclaración que pidió el equipo de Servicios, porque el nombre confunde:

* **`/output-templates`** son los artefactos que escribe un modelo — PRD,
  Deal Brief, informe de estado. Salen en markdown.
* **`/document-templates`** (esto) son los **archivos Word** con los que
  se genera el acta formal: nombre, categoría, versión, historial y el
  constructor visual de bloques.

Son dos cosas distintas con nombres parecidos, y dar más de la primera no
acerca la segunda.

Nada de esto necesitó columnas nuevas. Los metadatos ya vivían dentro de
`Template.style_config`, bajo la clave `__meta`, porque la interfaz de
Acten los guardaba ahí. Aquí se traducen a campos con nombre propio: que
quien consuma la API tenga que saber que hay un objeto escondido dentro
de una columna de estilos sería trasladarle una decisión que no es suya.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, select

from database import get_session
from models import Project, Template
from services.api_key_auth import IntegrationContext, require_scopes

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/document-templates", tags=["Plantillas documentales"])

# Los bloques que el generador sabe pintar. **Se sirven desde aquí** para
# que su pantalla no los tenga escritos a mano: el día que se añada uno,
# aparece solo en su selector en vez de faltar sin que nadie se entere.
BLOQUES = [
    {"id": "meta",         "nombre": "Cabecera / identificación"},
    {"id": "attendees",    "nombre": "Asistentes"},
    {"id": "summary",      "nombre": "Resumen ejecutivo"},
    {"id": "decisions",    "nombre": "Decisiones"},
    {"id": "risks",        "nombre": "Riesgos"},
    {"id": "agreements",   "nombre": "Acuerdos"},
    {"id": "action_items", "nombre": "Tabla de tareas"},
]
IDS_BLOQUES = {b["id"] for b in BLOQUES}

CATEGORIAS = [
    {"id": "meeting",  "nombre": "Reuniones"},
    {"id": "project",  "nombre": "Proyecto"},
    {"id": "reports",  "nombre": "Reportes"},
    {"id": "finance",  "nombre": "Financiero"},
    {"id": "comms",    "nombre": "Comunicaciones"},
    {"id": "strategy", "nombre": "Estrategia"},
]
IDS_CATEGORIAS = {c["id"] for c in CATEGORIAS}

UPLOAD_DIR = "uploads/templates"


def _meta(t: Template) -> dict[str, Any]:
    try:
        obj = json.loads(t.style_config or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    m = obj.get("__meta") if isinstance(obj, dict) else None
    return m if isinstance(m, dict) else {}


def _estilos(t: Template) -> dict[str, Any]:
    try:
        obj = json.loads(t.style_config or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(obj, dict):
        return {}
    return {k: v for k, v in obj.items() if k != "__meta"}


def _bloques(t: Template) -> list[str]:
    try:
        v = json.loads(t.mapping_config or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(x) for x in v] if isinstance(v, list) else []


def _categoria_por_nombre(nombre: str) -> str:
    """Categoría deducida del nombre, para las plantillas antiguas.

    Es la misma heurística que aplica la interfaz de Acten; se replica
    aquí para que las dos pantallas clasifiquen igual una plantilla que
    nunca eligió categoría.
    """
    n = (nombre or "").lower()
    reglas = (
        ("meeting",  r"reuni|sesi|acta|minut"),
        ("project",  r"proyecto|plan|riesg|matriz|acci[oó]n"),
        ("reports",  r"reporte|status|resumen|ejecutiv"),
        ("finance",  r"finan|presup|budget|cost"),
        ("comms",    r"correo|email|notific|comunic|mensaj"),
        ("strategy", r"estrat|kickoff|roadmap"),
    )
    for cat, patron in reglas:
        if re.search(patron, n):
            return cat
    return "meeting"


def _serializar(t: Template, proyectos: dict[int, Project]) -> dict[str, Any]:
    m = _meta(t)
    proj = proyectos.get(t.project_id)
    return {
        "id": t.id,
        "nombre": t.name or "",
        "tipo": m.get("type") or _categoria_por_nombre(t.name or ""),
        "version": m.get("version") or "1.0",
        "estado": m.get("status") or "active",
        "actualizada": m.get("updated_at"),
        "descripcion": m.get("description") or "",
        "casos_de_uso": m.get("use_cases") or [],
        "proyecto_destino": {
            "id": proj.external_ref if proj else None,
            "nombre": proj.name if proj else None,
        },
        "bloques": _bloques(t),
        "archivo": bool(t.file_path),
    }


def _mias(db: Session, ctx: IntegrationContext) -> list[Template]:
    return list(db.exec(
        select(Template)
        .join(Project, Project.id == Template.project_id)
        .where(Project.tenant_id == ctx.tenant.id)
    ).all())


def _una(db: Session, ctx: IntegrationContext, template_id: int) -> Template:
    t = db.get(Template, template_id)
    if not t:
        raise HTTPException(404, "Plantilla no encontrada.")
    proj = db.get(Project, t.project_id)
    if not proj or proj.tenant_id != ctx.tenant.id:
        raise HTTPException(404, "Plantilla no encontrada.")
    return t


def _proyecto(db: Session, ctx: IntegrationContext, ref: str) -> Project:
    p = db.exec(
        select(Project)
        .where(Project.tenant_id == ctx.tenant.id)
        .where(Project.external_ref == ref)
    ).first()
    if not p:
        raise HTTPException(422, f"No existe un proyecto sincronizado con id '{ref}'.")
    return p


def _guardar_meta(t: Template, cambios: dict[str, Any], accion: str) -> None:
    """Mete los metadatos y una entrada de historial dentro de style_config."""
    try:
        obj = json.loads(t.style_config or "{}")
        if not isinstance(obj, dict):
            obj = {}
    except (json.JSONDecodeError, TypeError):
        obj = {}
    m = obj.get("__meta") if isinstance(obj.get("__meta"), dict) else {}
    m.update({k: v for k, v in cambios.items() if v is not None})
    m["updated_at"] = datetime.now().isoformat()
    historial = m.get("history")
    historial = historial if isinstance(historial, list) else []
    historial.insert(0, {"at": m["updated_at"], "action": accion})
    m["history"] = historial[:50]
    obj["__meta"] = m
    t.style_config = json.dumps(obj, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════
# CATÁLOGOS
# ══════════════════════════════════════════════════════════════════════

@router.get("/layout/blocks")
def bloques_disponibles(
    ctx: IntegrationContext = Depends(require_scopes("outputs:read")),
):
    """Bloques que sabe pintar el generador. Para el constructor visual."""
    return {"items": BLOQUES, "total": len(BLOQUES)}


@router.get("/categories")
def categorias(
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("outputs:read")),
):
    """Categorías con cuántas plantillas hay en cada una."""
    plantillas = _mias(db, ctx)
    cuenta: dict[str, int] = {}
    for t in plantillas:
        m = _meta(t)
        cat = m.get("type") or _categoria_por_nombre(t.name or "")
        cuenta[cat] = cuenta.get(cat, 0) + 1
    return {
        "items": [{**c, "plantillas": cuenta.get(c["id"], 0)} for c in CATEGORIAS],
        "total_plantillas": len(plantillas),
    }


# ══════════════════════════════════════════════════════════════════════
# CRUD
# ══════════════════════════════════════════════════════════════════════

@router.get("")
def listar(
    tipo: Optional[str] = None,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("outputs:read")),
):
    plantillas = _mias(db, ctx)
    proyectos = {
        p.id: p for p in db.exec(
            select(Project).where(Project.tenant_id == ctx.tenant.id)
        ).all()
    }
    items = [_serializar(t, proyectos) for t in plantillas]
    if tipo:
        items = [i for i in items if i["tipo"] == tipo]
    return {"items": items, "total": len(items)}


@router.post("", status_code=201)
async def crear(
    file: UploadFile = File(..., description="El .docx"),
    nombre: str = Form(...),
    proyecto_destino: str = Form(..., description="external_id del proyecto"),
    tipo: Optional[str] = Form(None),
    descripcion: Optional[str] = Form(None),
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("outputs:write")),
):
    """Sube una plantilla `.docx`."""
    if not (file.filename or "").lower().endswith(".docx"):
        raise HTTPException(422, "Solo se admiten archivos .docx")
    if tipo and tipo not in IDS_CATEGORIAS:
        raise HTTPException(422, f"Categoría desconocida: '{tipo}'. Ver /categories.")

    proj = _proyecto(db, ctx, proyecto_destino)

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    local = os.path.join(UPLOAD_DIR, os.path.basename(file.filename))
    with open(local, "wb") as out:
        shutil.copyfileobj(file.file, out)

    destino = local
    try:
        from services.supabase_service import upload_file_to_bucket
        from routers.templates import sanitize_filename
        destino = upload_file_to_bucket(
            "templates", local, f"project_{proj.id}/{sanitize_filename(file.filename)}",
        )
    except Exception as exc:  # noqa: BLE001
        # Sin nube, la plantilla queda en disco y sigue sirviendo. Perder
        # el archivo por un fallo de almacenamiento sería peor.
        logger.warning("plantilla %r no subió a la nube: %s", file.filename, exc)

    t = Template(
        project_id=proj.id, name=nombre.strip(),
        file_path=destino, mapping_config="[]", style_config="{}",
    )
    _guardar_meta(t, {
        "type": tipo or _categoria_por_nombre(nombre),
        "description": descripcion, "status": "active", "version": "1.0",
    }, "created")
    db.add(t); db.commit(); db.refresh(t)

    proyectos = {proj.id: proj}
    return _serializar(t, proyectos)


class PlantillaPatch(BaseModel):
    nombre: Optional[str] = None
    tipo: Optional[str] = None
    descripcion: Optional[str] = None
    estado: Optional[str] = None
    version: Optional[str] = None
    casos_de_uso: Optional[list[str]] = None
    proyecto_destino: Optional[str] = None


@router.patch("/{template_id}")
def editar(
    template_id: int,
    payload: PlantillaPatch,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("outputs:write")),
):
    """Cambia los datos de la plantilla. **El archivo no se toca.**

    Para reemplazar el `.docx` está `PUT /{id}/file`: separarlos evita que
    un cambio de nombre pise el archivo por accidente.
    """
    t = _una(db, ctx, template_id)
    if payload.tipo and payload.tipo not in IDS_CATEGORIAS:
        raise HTTPException(422, f"Categoría desconocida: '{payload.tipo}'.")

    if payload.nombre is not None:
        t.name = payload.nombre.strip()
    if payload.proyecto_destino is not None:
        t.project_id = _proyecto(db, ctx, payload.proyecto_destino).id

    _guardar_meta(t, {
        "type": payload.tipo,
        "description": payload.descripcion,
        "status": payload.estado,
        "version": payload.version,
        "use_cases": payload.casos_de_uso,
    }, "updated")
    db.add(t); db.commit(); db.refresh(t)

    proyectos = {p.id: p for p in db.exec(
        select(Project).where(Project.tenant_id == ctx.tenant.id)
    ).all()}
    return _serializar(t, proyectos)


@router.put("/{template_id}/file")
async def reemplazar_archivo(
    template_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("outputs:write")),
):
    """Sustituye el `.docx` conservando bloques, estilos y metadatos."""
    if not (file.filename or "").lower().endswith(".docx"):
        raise HTTPException(422, "Solo se admiten archivos .docx")
    t = _una(db, ctx, template_id)

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    local = os.path.join(UPLOAD_DIR, os.path.basename(file.filename))
    with open(local, "wb") as out:
        shutil.copyfileobj(file.file, out)

    destino = local
    try:
        from services.supabase_service import upload_file_to_bucket
        from routers.templates import sanitize_filename
        destino = upload_file_to_bucket(
            "templates", local,
            f"project_{t.project_id}/{sanitize_filename(file.filename)}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("plantilla %s no subió a la nube: %s", template_id, exc)

    t.file_path = destino
    m = _meta(t)
    actual = str(m.get("version") or "1.0")
    try:
        mayor, menor = actual.split(".")[:2]
        siguiente = f"{mayor}.{int(menor) + 1}"
    except (ValueError, IndexError):
        siguiente = "1.1"
    _guardar_meta(t, {"version": siguiente}, "file_replaced")
    db.add(t); db.commit(); db.refresh(t)
    return {"id": t.id, "version": siguiente, "archivo": bool(t.file_path)}


@router.delete("/{template_id}")
def borrar(
    template_id: int,
    definitivo: bool = False,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("outputs:write")),
):
    """Desactiva la plantilla. Con `definitivo=true`, la borra.

    Por defecto solo se desactiva: un acta generada hace meses apunta a
    esta plantilla, y borrarla deja ese documento sin explicación de cómo
    se produjo.
    """
    t = _una(db, ctx, template_id)
    if definitivo:
        db.delete(t); db.commit()
        return {"status": "borrada", "id": template_id}
    _guardar_meta(t, {"status": "archived"}, "archived")
    db.add(t); db.commit()
    return {"status": "desactivada", "id": template_id}


# ══════════════════════════════════════════════════════════════════════
# CONSTRUCTOR VISUAL
# ══════════════════════════════════════════════════════════════════════

class LayoutIn(BaseModel):
    bloques: Optional[list[str]] = None
    estilos: Optional[dict[str, Any]] = None


@router.get("/{template_id}/layout")
def leer_layout(
    template_id: int,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("outputs:read")),
):
    """Lo que guarda el constructor: qué bloques y con qué estilos."""
    t = _una(db, ctx, template_id)
    return {
        "bloques": _bloques(t),
        "estilos": _estilos(t),
        "bloques_disponibles": BLOQUES,
    }


@router.put("/{template_id}/layout")
def guardar_layout(
    template_id: int,
    payload: LayoutIn,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("outputs:write")),
):
    """Guarda bloques y estilos. El orden de `bloques` es el del documento."""
    t = _una(db, ctx, template_id)

    if payload.bloques is not None:
        desconocidos = [b for b in payload.bloques if b not in IDS_BLOQUES]
        if desconocidos:
            raise HTTPException(
                422,
                f"Bloques que el generador no sabe pintar: {desconocidos}. "
                f"Ver /document-templates/layout/blocks.",
            )
        t.mapping_config = json.dumps(payload.bloques)

    if payload.estilos is not None:
        obj = _estilos(t)
        obj.update(payload.estilos)
        obj["__meta"] = _meta(t)
        t.style_config = json.dumps(obj, ensure_ascii=False)

    _guardar_meta(t, {}, "configured")
    db.add(t); db.commit(); db.refresh(t)
    return {"bloques": _bloques(t), "estilos": _estilos(t)}


@router.get("/{template_id}/versions")
def historial(
    template_id: int,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("outputs:read")),
):
    """Historial de cambios de la plantilla.

    ⚠️ Es un **registro de acciones**, no un archivo por versión: Acten
    guarda un solo `.docx` y lo sustituye. No se puede descargar una
    versión anterior, y decir lo contrario sería vender algo que no hay.
    """
    t = _una(db, ctx, template_id)
    m = _meta(t)
    hist = m.get("history")
    return {
        "version_actual": m.get("version") or "1.0",
        "items": hist if isinstance(hist, list) else [],
        "guarda_archivos_anteriores": False,
    }


@router.get("/{template_id}/preview")
def vista_previa(
    template_id: int,
    session_id: Optional[int] = None,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("outputs:read")),
):
    """Genera el acta con esta plantilla, para verla antes de usarla.

    Sin `session_id` toma la reunión más reciente del proyecto de la
    plantilla. Si el proyecto no tiene ninguna, responde `422` en vez de
    devolver un documento con huecos que parecería un fallo del diseño.
    """
    from fastapi.responses import Response
    from models import MeetingSession

    t = _una(db, ctx, template_id)

    if session_id is not None:
        s = db.get(MeetingSession, session_id)
        if not s or s.tenant_id != ctx.tenant.id:
            raise HTTPException(404, "Sesión no encontrada.")
    else:
        s = db.exec(
            select(MeetingSession)
            .where(MeetingSession.tenant_id == ctx.tenant.id)
            .where(MeetingSession.project_id == t.project_id)
            .order_by(MeetingSession.id.desc())
        ).first()
    if not s:
        raise HTTPException(
            422,
            "El proyecto de esta plantilla no tiene ninguna reunión todavía. "
            "Pasa `session_id` para previsualizar con otra.",
        )

    from models import ActionItem
    from routers.sessions_upload import generate_word_document_bytes

    items = db.exec(select(ActionItem).where(ActionItem.session_id == s.id)).all()
    docx = generate_word_document_bytes(s, items, db).getvalue()
    return Response(
        content=docx,
        media_type=(
            "application/vnd.openxmlformats-officedocument"
            ".wordprocessingml.document"
        ),
        headers={"Content-Disposition": 'inline; filename="vista-previa.docx"'},
    )
