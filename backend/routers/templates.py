import os
import shutil
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlmodel import Session, select
from typing import List

from database import get_session
from models import User, Template
from routers.auth import require_admin
import crud

router = APIRouter(prefix="/templates", tags=["Gestión de Plantillas"])
UPLOAD_DIR = "uploads/templates"

@router.get("")
def list_templates(db: Session = Depends(get_session), admin_user: User = Depends(require_admin)):
    """Lista las plantillas disponibles en Base de Datos."""
    return crud.template.get_multi(db)

@router.post("/upload")
async def upload_template(
    project_id: int, 
    file: UploadFile = File(...), 
    db: Session = Depends(get_session), 
    admin_user: User = Depends(require_admin)
):
    """Sube un archivo Word .docx como plantilla de un Proyecto"""
    if not file.filename.endswith('.docx'):
        raise HTTPException(status_code=400, detail="Solo se permiten archivos .docx")
        
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    file_path = os.path.join(UPLOAD_DIR, file.filename)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    try:
        from services.supabase_service import upload_file_to_bucket
        # Subir a Supabase bucket 'templates'
        supabase_path = f"project_{project_id}/{file.filename}"
        public_url = upload_file_to_bucket("templates", file_path, supabase_path)
        final_file_path = public_url
    except Exception as e:
        print(f"Advertencia: No se pudo subir a Supabase. Se usará ruta local. Error: {e}")
        final_file_path = file_path
        
    template_record = Template(
        project_id=project_id,
        name=file.filename,
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
    admin_user: User = Depends(require_admin)
):
    """Actualiza una plantilla existente (nombre, proyecto o archivo .docx)"""
    template = crud.template.get(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")

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
            supabase_path = f"project_{project_id}/{file.filename}"
            public_url = upload_file_to_bucket("templates", file_path, supabase_path)
            update_data["file_path"] = public_url
        except Exception as e:
            print(f"Advertencia: No se pudo subir a Supabase. Se usará ruta local. Error: {e}")
            update_data["file_path"] = file_path

    crud.template.update(db, db_obj=template, obj_in=update_data)
    
    return {"msg": "Plantilla actualizada con éxito"}

from pydantic import BaseModel

class MappingUpdate(BaseModel):
    mapping_config: str

@router.put("/{template_id}/mapping")
def update_template_mapping(
    template_id: int, 
    mapping: MappingUpdate,
    db: Session = Depends(get_session), 
    admin_user: User = Depends(require_admin)
):
    """Actualiza la configuración de mapeo de una plantilla (drag & drop)"""
    template = crud.template.get(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")
        
    crud.template.update(db, db_obj=template, obj_in={"mapping_config": mapping.mapping_config})
    return {"msg": "Mapeo guardado exitosamente"}

@router.delete("/{template_id}", status_code=204)
def delete_template(
    template_id: int,
    db: Session = Depends(get_session),
    admin_user: User = Depends(require_admin)
):
    """Elimina una plantilla de la Base de Datos"""
    template = crud.template.get(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")
        
    crud.template.remove(db, id=template_id)
    return

from models import MeetingSession
from services.word_generator import WordGeneratorService

@router.post("/{template_id}/generate/{session_id}")
def generate_document_from_template(
    template_id: int,
    session_id: int,
    db: Session = Depends(get_session),
    current_user: User = Depends(require_admin)
):
    """Genera un documento Word basado en la plantilla y la sesión."""
    template = crud.template.get(db, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")
        
    session = crud.meeting_session.get(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
        
    # Reconstruir meeting_data a partir del MeetingSession para el WordGenerator
    meeting_data = {
        "title": session.title,
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
            template_name=template.name, 
            meeting_data=meeting_data, 
            output_path=output_path
        )
        return {"msg": "Documento generado exitosamente", "download_url": f"/files/{output_filename}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al generar documento: {str(e)}")
