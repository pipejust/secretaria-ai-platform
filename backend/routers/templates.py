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
