from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from database import get_session
from sqlmodel import Session
from models import MeetingSession
import datetime
import uuid

router = APIRouter(
    prefix="/api/sessions",
    tags=["Sessions"]
)

@router.post("/upload")
async def upload_manual_session(
    title: str = Form(...),
    file: UploadFile = File(...),
    session: Session = Depends(get_session)
):
    try:
        # Extraer el contenido o guardar el archivo
        content = await file.read()
        file_size = len(content)
        
        # Crear una nueva sesión
        new_session_id = str(uuid.uuid4())
        new_session = MeetingSession(
            id=new_session_id,
            title=title,
            date=datetime.datetime.utcnow().isoformat() + "Z",
            video_url=f"manual_upload_{file.filename}",
            transcript_text=f"Uploaded {file.filename} manually.",  # O convertir si es texto/audio real
            status="pending",
            summary="",
            action_items="[]"
        )
        
        session.add(new_session)
        session.commit()
        session.refresh(new_session)
        
        return {"status": "success", "session_id": new_session.id, "message": "Archivo subido exitosamente."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
