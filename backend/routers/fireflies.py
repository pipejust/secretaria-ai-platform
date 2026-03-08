from fastapi import APIRouter, Depends, Request, BackgroundTasks, HTTPException
from sqlmodel import Session, select
from typing import Dict, Any

from database import get_session
from models import MeetingSession, Project
from services.fireflies_service import FirefliesService
from services.groq_service import GroqService

router = APIRouter(
    prefix="/api/webhook/fireflies",
    tags=["Webhooks Fireflies"]
)

# Simulamos Background Task para el procesamiento de IA
async def process_transcript_background(transcript_id: str, payload_data: dict, db: Session):
    try:
        # Extraer datos nativos enviados en el Webhook (como indica el usuario de que Fireflies envía esto)
        title = payload_data.get("title", "Reunión Sin Título")
        date_str = str(payload_data.get("date", payload_data.get("createdAt", "")))
        
        raw_transcript = str(payload_data.get("transcript", ""))
        raw_summary = str(payload_data.get("summary", ""))
        
        # Fallback: Si el payload viene solo con el ID sin los textos ricos (comportamiento por defecto de la API)
        if not raw_transcript or len(raw_transcript) < 10:
            service = FirefliesService()
            data = await service.get_transcript_data(transcript_id)
            title = data.get("title", title)
            date_str = str(data.get("date", date_str))
            sentences = [f"{s.get('speaker_name', 'Anon')}: {s.get('text', '')}" for s in data.get("sentences", [])]
            raw_transcript = "\n".join(sentences)
            raw_summary = str(data.get("summary", {}))
        
        # 1. Deducción Nivel 1: Match por Calendario (Título exacto)
        projects = db.exec(select(Project)).all()
        matched_project_id = None
        for p in projects:
            if p.name.lower() in title.lower():
                matched_project_id = p.id
                break
                
        # 2. Deducción Nivel 2: Match por Contexto via Groq IA
        if not matched_project_id and raw_summary:
            groq_svc = GroqService()
            proj_dict_list = [{"id": p.id, "name": p.name} for p in projects]
            deduced_id = await groq_svc.deduce_project(raw_summary, proj_dict_list)
            if deduced_id:
                matched_project_id = deduced_id
        
        new_session = MeetingSession(
            fireflies_id=transcript_id,
            title=title,
            date=date_str,
            project_id=matched_project_id,
            raw_transcript=raw_transcript,
            raw_summary=raw_summary,
            status="pending" # Pendiente de curación
        )
        
        db.add(new_session)
        db.commit()
        db.refresh(new_session)
        
        # TODO: Inyectar Groq Service aquí para extraer tareas estructuradas
        # 1. groq_service.extract_structured_data(raw_transcript)
        # 2. Generar ActionItems
        # 3. Guardar en DB.
        
    except Exception as e:
        # En producción se manejaría el log y reintento
        print(f"Error procesando el transcript en background: {e}")

@router.post("")
async def receive_fireflies_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session)
):
    """
    Endpoint para recibir el evento 'Transcription complete' desde Fireflies.
    """
    payload = await request.json()
    
    # Fireflies envía transcriptId en el payload
    transcript_id = payload.get("transcriptId")
    event_type = payload.get("event")
    
    if not transcript_id:
        # Algunos payloads de prueba en webhooks pueden tener otras estructuras
        transcript_id = payload.get("id") or payload.get("meeting_id")
        
    if not transcript_id:
        raise HTTPException(status_code=400, detail="Falta transcriptId en el payload")
        
    # Despachar procesamiento asíncrono pasándole el payload para ahorrar llamadas a API si es posible
    background_tasks.add_task(process_transcript_background, transcript_id, payload, db)
    
    return {"status": "accepted", "message": f"Transcript {transcript_id} programado para procesar en background."}
