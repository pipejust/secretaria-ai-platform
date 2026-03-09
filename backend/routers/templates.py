import os
import shutil
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlmodel import Session, select
from typing import List

from database import get_session
from models import User, Template
from routers.auth import require_admin

router = APIRouter(prefix="/templates", tags=["Gestión de Plantillas"])
UPLOAD_DIR = "uploads/templates"

@router.get("")
def list_templates(db: Session = Depends(get_session), admin_user: User = Depends(require_admin)):
    """Lista las plantillas disponibles en Base de Datos."""
    return db.exec(select(Template)).all()

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
        
    template_record = Template(
        project_id=project_id,
        name=file.filename,
        file_path=file_path
    )
    db.add(template_record)
    db.commit()
    db.refresh(template_record)
    
    return {"msg": "Plantilla subida con éxito", "template_id": template_record.id}

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
    template = db.get(Template, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")
        
    template.mapping_config = mapping.mapping_config
    db.add(template)
    db.commit()
    return {"msg": "Mapeo guardado exitosamente"}

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
    template = db.get(Template, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")
        
    session = db.get(MeetingSession, session_id)
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
