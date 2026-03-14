from fastapi import APIRouter, Depends, Request, BackgroundTasks, HTTPException
from sqlmodel import Session, select
from typing import Dict, Any

from database import get_session
from models import MeetingSession, Project, ActionItem, Routing, ProjectContact
from services.fireflies_service import FirefliesService
from services.groq_service import GroqService

router = APIRouter(
    prefix="/api/webhook/fireflies",
    tags=["Webhooks Fireflies"]
)

# Simulamos Background Task para el procesamiento de IA
async def process_transcript_background(session_id: int, transcript_id: str, payload_data: dict):
    from database import engine
    with Session(engine) as db:
        try:
            # Recuperar la sesión recién creada
            new_session = db.get(MeetingSession, session_id)
            if not new_session:
                print(f"Error: No se encontró la sesión {session_id} para actualizar.")
                return

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
                
                # Extraer overview del JSON de Fireflies de su objeto summary
                summary_obj = data.get("summary")
                if isinstance(summary_obj, dict):
                    raw_summary = summary_obj.get("overview", "")
                else:
                    raw_summary = str(summary_obj or "")
            
            # 1. Deducción Nivel 1: Match por Calendario (Título exacto) o Mención Explícita
            projects = db.exec(select(Project)).all()
            matched_project_id = None
            
            # A) Match por Título
            for p in projects:
                if p.name.lower() in title.lower():
                    matched_project_id = p.id
                    break
                    
            # B) Match por mención explícita en la transcripción (lo que hablaron las personas)
            if not matched_project_id and raw_transcript:
                for p in projects:
                    if p.name.lower() in raw_transcript.lower():
                        matched_project_id = p.id
                        break
                    
            # 2. Deducción Nivel 2: Intuición por Contexto via Groq IA
            if not matched_project_id and raw_summary:
                groq_svc = GroqService()
                # Pasamos tanto el nombre como la descripción del proyecto para que deduzca mejor
                proj_dict_list = [{"id": p.id, "name": p.name, "description": p.description} for p in projects]
                deduced_id = await groq_svc.deduce_project(raw_summary, proj_dict_list)
                if deduced_id:
                    matched_project_id = deduced_id
            
            new_session.title = title
            new_session.date = date_str
            new_session.project_id = matched_project_id
            new_session.raw_transcript = raw_transcript
            new_session.raw_summary = raw_summary
            new_session.status = "pending" # Pasa a pendiente de curación
            
            db.add(new_session)
            db.commit()
            db.refresh(new_session)
        
            # 3. Extraer tareas estructuradas con Groq
            if raw_transcript:
                groq_svc = GroqService()
                
                project_contacts = []
                if matched_project_id:
                    db_contacts = db.exec(select(ProjectContact).where(ProjectContact.project_id == matched_project_id)).all()
                    project_contacts = [{"name": c.name, "email": c.email, "role": c.role} for c in db_contacts]

                structured_data = await groq_svc.process_transcript(raw_transcript, project_contacts)
                
                # Guardar datos enriquecidos en session
                new_session.processed_decisions = structured_data.get("decisions", "")
                new_session.processed_risks = structured_data.get("risks", "")
                new_session.processed_agreements = structured_data.get("agreements", "")
                import json
                new_session.processed_attendees = json.dumps(structured_data.get("attendees", []), ensure_ascii=False)
                new_session.processed_themes = json.dumps(structured_data.get("themes", []), ensure_ascii=False)
                db.add(new_session)
                db.commit()

                action_items_data = structured_data.get("action_items", [])
                for item_data in action_items_data:
                    action_item = ActionItem(
                        session_id=new_session.id,
                        owner_name=item_data.get("owner_name", "Unknown"),
                        owner_email=item_data.get("owner_email", ""),
                        title=item_data.get("title", "Tarea sin título"),
                        description=item_data.get("description", ""),
                        due_date=item_data.get("due_date"),
                        is_approved=False
                    )
                    db.add(action_item)
                db.commit()
                
                # 4. Enviar a Task Managers externos basados en Project Routing si el proyecto existe
                if matched_project_id:
                    import json
                    from services.integrations.trello import TrelloIntegrationService
                    from services.integrations.jira import JiraIntegrationService
                    from services.integrations.clickup import ClickUpIntegrationService
                    
                    routings = db.exec(select(Routing).where(Routing.project_id == matched_project_id)).all()
                    for routing in routings:
                        config = json.loads(routing.destination_config or '{}')
                        dest_type = routing.destination_type.lower()
                        
                        try:
                            if "trello" in dest_type:
                                trello_service = TrelloIntegrationService("mock_key", "mock_token") # TODO use IntegrationSettings
                                for act in db.exec(select(ActionItem).where(ActionItem.session_id == new_session.id)).all():
                                    await trello_service.create_card(config.get("board_id"), config.get("list_id"), act.title, act.description, act.due_date)
                            elif "jira" in dest_type:
                                jira_service = JiraIntegrationService("mock_domain", "mock@email.com", "mock_token") 
                                for act in db.exec(select(ActionItem).where(ActionItem.session_id == new_session.id)).all():
                                    await jira_service.create_issue(config.get("project_key"), act.title, act.description)
                            elif "clickup" in dest_type:
                                clickup_service = ClickUpIntegrationService("mock_token") 
                                for act in db.exec(select(ActionItem).where(ActionItem.session_id == new_session.id)).all():
                                    await clickup_service.create_task(config.get("list_id"), act.title, act.description)
                        except Exception as route_err:
                            print(f"Error routeando a {routing.destination_type}: {route_err}")
        
        except Exception as e:
            import traceback
            print(f"====== ERROR EN PROCESAMIENTO BACKGROUND FIREFLIES ======")
            print(f"Error procesando el transcript en background: {e}")
            print(traceback.format_exc())
            print("=========================================================")

import json

@router.post("")
async def receive_fireflies_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session)
):
    """
    Endpoint para recibir el evento 'Transcription complete' desde Fireflies.
    """
    try:
        payload = await request.json()
        print("====== FIREFLIES WEBHOOK INCOMING ======")
        print(f"Payload received: {json.dumps(payload, ensure_ascii=False)[:500]}...") # Loguear una porción segura del payload
    except Exception as e:
        print(f"Error parsing webhook JSON: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    
    # Verificamos qué estructura envía Fireflies realmente
    # A veces puede venir envuelto en {"data": {...}} o directo
    
    transcript_id = payload.get("transcriptId") or payload.get("meetingId")
    event_type = payload.get("eventType") or payload.get("event")
    
    print(f"Event type: {event_type}, Transcript ID extracted so far: {transcript_id}")
    
    if not transcript_id:
        # Fallbacks for different possible webhook schemas
        transcript_id = payload.get("id") or payload.get("meeting_id")
        # Check if it's nested under data
        if not transcript_id and "data" in payload and isinstance(payload["data"], dict):
            data_obj = payload["data"]
            transcript_id = data_obj.get("transcriptId") or data_obj.get("id") or data_obj.get("meetingId")
            print(f"Extracted from nested data: {transcript_id}")
            
    if not transcript_id:
        print("ERROR: No transcript ID found in payload.")
        # We return a 200 instead of 400 because some webhooks (like validation webhooks) 
        # might just be pings that don't have a transcript ID and we don't want Fireflies to disable the hook.
        return {"status": "ignored", "message": "Falta transcriptId o meetingId en el payload, ignorando."}
        
    print(f"Dispatching background task for transcript: {transcript_id}")
    
    # Creamos la sesión inmediatamente con estado processing para que el UI la muestre en tiempo real
    title = payload.get("title", "Reunión Procesando...")
    date_str = str(payload.get("date", payload.get("createdAt", "")))
    
    new_session = MeetingSession(
        fireflies_id=transcript_id,
        title=title,
        date=date_str,
        raw_transcript="",
        raw_summary="",
        status="processing"
    )
    db.add(new_session)
    db.commit()
    db.refresh(new_session)
    
    # Despachar procesamiento asíncrono pasándole el ID de la sesión recién creada
    background_tasks.add_task(process_transcript_background, new_session.id, transcript_id, payload)
    
    return {"status": "accepted", "message": f"Transcript/Meeting {transcript_id} programado para procesar en background."}
