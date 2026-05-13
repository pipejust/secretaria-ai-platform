import os
import shutil
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlmodel import Session, select
from typing import List

from database import get_session
from models import User, Template, Project, Tenant
from routers.auth import require_admin, get_current_tenant
import crud
import unicodedata
import re

def sanitize_filename(filename: str) -> str:
    """Removes special characters and spaces for Supabase Storage compatibility."""
    # Remove accents
    normalized = unicodedata.normalize('NFKD', filename).encode('ASCII', 'ignore').decode('utf-8')
    # Replace non-alphanumeric (except dots and dashes) with underscores
    return re.sub(r'[^a-zA-Z0-9_.-]', '_', normalized)


def _project_belongs_to_tenant(db: Session, project_id: int, tenant_id: int) -> Project:
    """Garantiza que el proyecto existe Y pertenece al tenant del usuario.
    Sin este check un admin de la empresa A podía crear/editar plantillas
    de la empresa B pasando un project_id arbitrario."""
    proj = db.get(Project, project_id)
    if not proj or proj.tenant_id != tenant_id:
        # 404 (no 403) para no filtrar existencia entre empresas.
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
    return proj


def _template_in_tenant(db: Session, template_id: int, tenant_id: int) -> Template:
    """Garantiza que la plantilla existe Y pertenece (vía Project) al tenant.
    Las plantillas no son globales — cada empresa ve SOLO las suyas."""
    tpl = crud.template.get(db, template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")
    proj = db.get(Project, tpl.project_id)
    if not proj or proj.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")
    return tpl


router = APIRouter(prefix="/templates", tags=["Gestión de Plantillas"])
UPLOAD_DIR = "uploads/templates"

@router.get("")
def list_templates(
    db: Session = Depends(get_session),
    admin_user: User = Depends(require_admin),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Lista las plantillas DEL TENANT del usuario.

    Multi-tenant: las plantillas viven asociadas a Projects, y cada Project
    es de UN tenant. Aquí hacemos JOIN para traer solo las del tenant
    correcto. Antes esto devolvía TODAS las plantillas de la plataforma
    (bug de aislamiento entre empresas).
    """
    stmt = (
        select(Template)
        .join(Project, Project.id == Template.project_id)
        .where(Project.tenant_id == tenant.id)
    )
    return list(db.exec(stmt).all())

@router.post("/upload")
async def upload_template(
    project_id: int = Form(...),
    name: str = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
    admin_user: User = Depends(require_admin),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Sube un archivo Word .docx como plantilla de un Proyecto.

    Multi-tenant: el `project_id` debe pertenecer al tenant del usuario.
    """
    if not file.filename.endswith('.docx'):
        raise HTTPException(status_code=400, detail="Solo se permiten archivos .docx")

    # Aislamiento de tenant: rechazamos si el project_id es de otra empresa.
    _project_belongs_to_tenant(db, project_id, tenant.id)

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    file_path = os.path.join(UPLOAD_DIR, file.filename)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        from services.supabase_service import upload_file_to_bucket
        # Subir a Supabase bucket 'templates'
        safe_filename = sanitize_filename(file.filename)
        supabase_path = f"project_{project_id}/{safe_filename}"
        public_url = upload_file_to_bucket("templates", file_path, supabase_path)
        final_file_path = public_url
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"No se pudo guardar la plantilla en la nube: {e}")

    actual_name = name if name else file.filename
    template_record = Template(
        project_id=project_id,
        name=actual_name,
        file_path=final_file_path
    )
    db_template = crud.template.create(db, obj_in=template_record)

    return {"msg": "Plantilla subida con éxito", "template_id": db_template.id, "file_url": final_file_path}

@router.put("/{template_id}")
async def update_template(
    template_id: int,
    project_id: int = Form(...),
    name: str = Form(...),
    file: UploadFile = File(None),
    db: Session = Depends(get_session),
    admin_user: User = Depends(require_admin),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Actualiza una plantilla existente (nombre, proyecto o archivo .docx).

    Multi-tenant: la plantilla DEBE pertenecer al tenant + el nuevo project_id
    también. Sin esto un admin podía mover una plantilla de su empresa al
    proyecto de otra empresa.
    """
    template = _template_in_tenant(db, template_id, tenant.id)
    _project_belongs_to_tenant(db, project_id, tenant.id)

    update_data = {
        "project_id": project_id,
        "name": name
    }

    if file:
        if not file.filename.endswith('.docx'):
            raise HTTPException(status_code=400, detail="Solo se permiten archivos .docx")

        os.makedirs(UPLOAD_DIR, exist_ok=True)
        file_path = os.path.join(UPLOAD_DIR, file.filename)

        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        try:
            from services.supabase_service import upload_file_to_bucket
            safe_filename = sanitize_filename(file.filename)
            supabase_path = f"project_{project_id}/{safe_filename}"
            public_url = upload_file_to_bucket("templates", file_path, supabase_path)
            update_data["file_path"] = public_url
        except Exception as e:
            import traceback
            print(traceback.format_exc())
            raise HTTPException(status_code=500, detail=f"No se pudo reemplazar la plantilla en la nube publicamente: {e}")

    crud.template.update(db, db_obj=template, obj_in=update_data)

    return {"msg": "Plantilla actualizada con éxito"}

from pydantic import BaseModel

class MappingUpdate(BaseModel):
    mapping_config: str
    style_config: str = "{}"

@router.put("/{template_id}/mapping")
def update_template_mapping(
    template_id: int,
    mapping: MappingUpdate,
    db: Session = Depends(get_session),
    admin_user: User = Depends(require_admin),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Actualiza la configuración de mapeo de una plantilla (drag & drop) y sus estilos.
    Multi-tenant: la plantilla debe ser del tenant del usuario."""
    template = _template_in_tenant(db, template_id, tenant.id)

    crud.template.update(db, db_obj=template, obj_in={
        "mapping_config": mapping.mapping_config,
        "style_config": mapping.style_config
    })
    return {"msg": "Configuración guardada exitosamente"}

@router.delete("/{template_id}", status_code=204)
def delete_template(
    template_id: int,
    db: Session = Depends(get_session),
    admin_user: User = Depends(require_admin),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Elimina una plantilla. Multi-tenant: solo se borran plantillas del
    propio tenant; intentar borrar la de otra empresa devuelve 404."""
    _template_in_tenant(db, template_id, tenant.id)

    crud.template.remove(db, id=template_id)
    return

from models import MeetingSession
from services.word_generator import WordGeneratorService

@router.post("/{template_id}/generate/{session_id}")
def generate_document_from_template(
    template_id: int,
    session_id: int,
    db: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Genera un documento Word basado en la plantilla y la sesión.
    Multi-tenant: tanto la plantilla como la sesión deben ser del tenant
    del usuario. Sin esto se podía generar el acta de una empresa con
    la plantilla de otra (mezcla de marca + datos sensibles cruzados).
    """
    template = _template_in_tenant(db, template_id, tenant.id)

    session = crud.meeting_session.get(db, session_id)
    if not session or session.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")

    # Reconstruir meeting_data a partir del MeetingSession para el WordGenerator
    meeting_data = {
        "title": session.title,
        "date": session.date.strftime("%d/%m/%Y") if session.date else "",
        "summary": session.raw_summary,
        "decisions": session.processed_decisions,
        "risks": session.processed_risks,
        "agreements": session.processed_agreements,
        "action_items": []
    }

    for act in session.action_items:
        meeting_data["action_items"].append({
            "title": act.title,
            "owner_name": act.owner_name,
            "description": act.description,
            "due_date": act.due_date
        })

    generator = WordGeneratorService()
    output_filename = f"Acta_{session.id}_{template.name}"
    output_path = os.path.join("uploads", output_filename) # TODO: Better storage path

    try:
        generated_path = generator.generate_document(
            template_path=template.file_path,
            meeting_data=meeting_data,
            output_path=output_path
        )
        return {"msg": "Documento generado exitosamente", "download_url": f"/files/{output_filename}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al generar documento: {str(e)}")
