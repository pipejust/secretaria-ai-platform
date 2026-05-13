from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException, BackgroundTasks, Query
from fastapi.responses import Response
from sqlmodel import Session, select
from models import MeetingSession, ActionItem, IntegrationSetting, Routing, Tenant, User
from database import get_session
from routers.auth import get_current_tenant, get_current_user, require_admin, require_session_writer
import uuid
import os
import io
import json
import base64
import datetime
from typing import List, Optional
from pydantic import BaseModel
from sqlalchemy.orm import selectinload
from fpdf import FPDF
import docx

router = APIRouter(
    prefix="/api/sessions",
    tags=["Sessions"]
)


def _get_session_or_404(db: Session, session_id: int, tenant: Tenant) -> MeetingSession:
    """Helper: trae la sesión y valida que pertenezca al tenant del caller.

    Cualquier intento de acceder a una sesión de otra empresa devuelve 404
    (no 403) para no filtrar siquiera la existencia.
    """
    obj = db.get(MeetingSession, session_id)
    if not obj or obj.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Session not found")
    return obj

@router.get("/")
def get_sessions(
    project_id: int = Query(None, description="Filter by project ID"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    search: str = Query(None, description="Search by title or id"),
    status: str = Query(None, description="Filter by status"),
    include_archived: bool = Query(False, description="Include archived sessions"),
    db: Session = Depends(get_session),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Fetch paginated meeting sessions (Actas) del tenant actual.

    AISLAMIENTO: siempre filtra por `tenant_id` — un usuario nunca ve sesiones
    de otra empresa. Por defecto excluye `status='archived'` también.
    """
    from sqlmodel import select, func, or_
    import math

    query = select(MeetingSession).where(MeetingSession.tenant_id == tenant.id)

    if not include_archived and status != "archived":
        query = query.where(MeetingSession.status != "archived")

    if project_id is not None:
        query = query.where(MeetingSession.project_id == project_id)

    if status:
        query = query.where(MeetingSession.status == status)

    if search:
        search_filter = f"%{search}%"
        conditions = [MeetingSession.title.ilike(search_filter)]
        if search.isdigit():
            conditions.append(MeetingSession.id == int(search))
        query = query.where(or_(*conditions))
        
    # Count total items for this filter
    total_query = select(func.count()).select_from(query.subquery())
    total_items = db.exec(total_query).one()
    
    # Apply pagination
    sessions = db.exec(
        query.order_by(MeetingSession.id.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    ).all()
    
    return {
        "items": sessions,
        "total": total_items,
        "page": page,
        "limit": limit,
        "pages": math.ceil(total_items / limit) if limit > 0 else 1
    }

@router.get("/{session_id}")
def get_session_details(
    session_id: int,
    db: Session = Depends(get_session),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Detail de una sesión — sólo si pertenece al tenant del usuario."""
    from sqlmodel import select
    from models import ActionItem

    session_obj = db.get(MeetingSession, session_id)
    if not session_obj or session_obj.tenant_id != tenant.id:
        # 404 (no 403) para no filtrar la existencia entre empresas.
        raise HTTPException(status_code=404, detail="Session not found")
        
    action_items = db.exec(select(ActionItem).where(ActionItem.session_id == session_id)).all()
    # Podríamos crear un Pydantic model response, pero dict/jsonable encoder lo maneja bien
    return {
        "session": session_obj,
        "action_items": action_items
    }

@router.post("/{session_id}/fetch_summary")
async def fetch_summary(
    session_id: int,
    db: Session = Depends(get_session),
    tenant: Tenant = Depends(get_current_tenant),
    _writer: User = Depends(require_session_writer),
):
    session_obj = _get_session_or_404(db, session_id, tenant)
        
    def _fallback_to_groq():
        if not session_obj.raw_transcript:
            raise HTTPException(status_code=400, detail="El ID de Fireflies es inválido o no existe, y no hay transcripción local para analizar con IA.")
        return True

    from services.fireflies_service import FirefliesService
    svc = FirefliesService()
    force_groq = False
    
    if session_obj.fireflies_id and session_obj.fireflies_id.startswith("MANUAL-"):
        force_groq = True
    else:
        try:
            data = await svc.get_transcript_data(session_obj.fireflies_id)
            
            summary_obj = data.get("summary", {})
            apps_layer = data.get("apps_layer", {})
            sentences = data.get("sentences") or []
            
            transcript_text = ""
            if sentences:
                transcript_text = "\n".join([f"[{s.get('speaker_name', 'Speaker')}] {s.get('text', '')}" for s in sentences])
            
            if transcript_text:
                session_obj.raw_transcript = transcript_text
                db.add(session_obj)
                db.commit()
                db.refresh(session_obj)
            
            mega_summary = ""
            
            # 1. Apps outputs (ej. Daily Digest, Executive Summary custom)
            app_outputs = apps_layer.get("outputs", [])
            for out in app_outputs:
                if out.get("title") and out.get("response"):
                    mega_summary += f"### {out['title']}\n{out['response']}\n\n"
                    
            # 2. Summary estándar
            if isinstance(summary_obj, dict):
                overview = summary_obj.get("overview", "")
                if overview and overview not in mega_summary:
                    mega_summary += f"### Resumen General\n{overview}\n\n"
                    
                bullet_gist = summary_obj.get("bullet_gist", "")
                if bullet_gist:
                    mega_summary += f"### Puntos Clave\n{bullet_gist}\n\n"
                    
                notes = summary_obj.get("notes", "")
                if notes:
                    mega_summary += f"### Notas Entendidas\n{notes}\n\n"
            
            mega_summary = mega_summary.strip()
            if not mega_summary:
                overview = str(summary_obj or "")
                mega_summary = overview
                
            if mega_summary:
                try:
                    from services.groq_service import GroqService
                    groq_svc = GroqService()
                    mega_summary = await groq_svc.translate_and_clean_summary(mega_summary)
                except Exception as e:
                    print(f"Error limpiando formato con Groq en capa de fetching: {e}")
                    
                session_obj.raw_summary = mega_summary
                db.add(session_obj)
                db.commit()
                db.refresh(session_obj)
                return {"summary": mega_summary, "transcript": session_obj.raw_transcript}
            else:
                force_groq = True
                
        except Exception as e:
            # Si lanza error (ej. Transcript not found u object_not_found), aplicamos fallback a Groq
            print(f"Fireflies API falló (posiblemente borrado en su nube). Fallback a IA. Detalle: {str(e)}")
            force_groq = True

    if force_groq:
        _fallback_to_groq()
        from services.groq_service import GroqService
        groq_svc = GroqService()
        
        project_contacts = []
        if session_obj.project_id:
            from models import ProjectContact
            db_contacts = db.exec(select(ProjectContact).where(ProjectContact.project_id == session_obj.project_id)).all()
            project_contacts = [{"name": c.name, "email": c.email, "role": c.role} for c in db_contacts]
            
        summary = ""
        try:
            structured_data = await groq_svc.process_transcript(session_obj.raw_transcript, project_contacts)
            summary = structured_data.get("summary", "")
        except Exception as e:
            print(f"Groq API Error en fallback fetch_summary: {e}")
            # Even if Groq fails (e.g. rate limit), we return the transcript instead of 500 error
            pass
        
        if summary:
            session_obj.raw_summary = summary
            db.add(session_obj)
            db.commit()
            db.refresh(session_obj)
            
        return {"summary": summary, "transcript": session_obj.raw_transcript}

@router.delete("/{session_id}")
def delete_session(
    session_id: int,
    db: Session = Depends(get_session),
    tenant: Tenant = Depends(get_current_tenant),
    _admin: User = Depends(require_admin),
):
    """Eliminar una sesión: SOLO admins."""
    from sqlmodel import select
    from models import ActionItem
    session_obj = db.get(MeetingSession, session_id)
    if not session_obj or session_obj.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Session not found")
        
    action_items = db.exec(select(ActionItem).where(ActionItem.session_id == session_id)).all()
    for item in action_items:
        db.delete(item)
        
    db.delete(session_obj)
    db.commit()
    return {"status": "success", "message": "Sesión eliminada"}

class SessionUpdate(BaseModel):
    title: Optional[str] = None
    raw_summary: Optional[str] = None
    raw_transcript: Optional[str] = None
    processed_decisions: Optional[str] = None
    processed_risks: Optional[str] = None
    processed_agreements: Optional[str] = None
    status: Optional[str] = None
    project_id: Optional[int] = None

class RegeneratePayload(BaseModel):
    raw_transcript: Optional[str] = None

@router.post("/{session_id}/regenerate_tasks")
async def regenerate_tasks_from_transcript(
    session_id: int,
    payload: Optional[RegeneratePayload] = None,
    db: Session = Depends(get_session),
    tenant: Tenant = Depends(get_current_tenant),
    _writer: User = Depends(require_session_writer),
):
    from models import ActionItem, ProjectContact
    from services.groq_service import OpenAIService
    from sqlmodel import delete

    session_obj = _get_session_or_404(db, session_id, tenant)

    # Solo se permite UN uso del botón "Regenerar Tareas".
    if session_obj.ai_tasks_regenerated:
        raise HTTPException(
            status_code=409,
            detail="Las tareas ya fueron regeneradas con IA una vez para esta sesión.",
        )

    if payload and payload.raw_transcript:
        session_obj.raw_transcript = payload.raw_transcript

    if not session_obj.raw_transcript:
        raise HTTPException(status_code=400, detail="No transcript available to regenerate tasks from.")

    # 1. Obtenemos Contactos
    project_contacts = []
    if session_obj.project_id:
        from sqlmodel import select
        db_contacts = db.exec(select(ProjectContact).where(ProjectContact.project_id == session_obj.project_id)).all()
        project_contacts = [{"name": c.name, "email": c.email, "role": c.role, "entity": c.entity} for c in db_contacts]

    # 2. Llamamos a OpenAI (gpt-4o), que es el LLM dedicado para tareas.
    # Le pasamos también las secciones ya procesadas (decisions, agreements,
    # summary) para mejorar cobertura: el LLM verifica que cada compromiso
    # listado en Acuerdos/Decisiones tenga su tarea correspondiente.
    openai_svc = OpenAIService()
    try:
        structured_data = await openai_svc.process_transcript_for_tasks_only(
            session_obj.raw_transcript,
            project_contacts,
            decisions=session_obj.processed_decisions or "",
            agreements=session_obj.processed_agreements or "",
            summary=session_obj.raw_summary or "",
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error conectando con la IA (OpenAI): {str(e)}")


    # 3. Insertamos Nuevas
    action_items_data = structured_data.get("action_items", [])
    if action_items_data is None:
        action_items_data = []

    if action_items_data:
        # Solo borramos las anteriores si realmente vienen nuevas
        db.exec(delete(ActionItem).where(ActionItem.session_id == session_id))
        db.commit()

    new_items_output = []
    
    # Defensive programming: If groq returned a single dict instead of a list of dicts
    if isinstance(action_items_data, dict):
        action_items_data = [action_items_data]
    elif not isinstance(action_items_data, list):
        action_items_data = []

    for item_data in action_items_data:
        # tenant_id se pasa explícito porque ActionItem ahora lo requiere.
        if isinstance(item_data, str):
            # Fallback for LLM hallucinations where it returns a list of strings
            title = "Tarea Detectada"
            description = item_data.strip()
            owner_name = "Unknown"
            owner_email = ""
            due_date = None
        elif isinstance(item_data, dict):
            title = str(item_data.get("title") or "").strip()
            description = str(item_data.get("description") or "").strip()
            owner_name = str(item_data.get("owner_name") or "Unknown")
            owner_email = str(item_data.get("owner_email") or "")
            due_date = item_data.get("due_date")
        else:
            continue
            
        # Filtro estricto contra tareas vacías alucinadas
        if not title and not description:
            continue
            
        action_item = ActionItem(
            tenant_id=tenant.id,
            session_id=session_id,
            owner_name=owner_name,
            owner_email=owner_email,
            title=title or "Tarea sin título",
            description=description,
            due_date=due_date,
            is_approved=False
        )
        db.add(action_item)
        db.commit()
        db.refresh(action_item)
        # Convertimos para el output JSON dict
        new_items_output.append({
            "id": action_item.id,
            "session_id": action_item.session_id,
            "owner_name": action_item.owner_name,
            "owner_email": action_item.owner_email,
            "title": action_item.title,
            "description": action_item.description,
            "due_date": str(action_item.due_date) if action_item.due_date else None,
            "is_approved": action_item.is_approved,
            "selected": False
        })

    # Marca el flag de uso único (el botón ya no se podrá presionar otra vez).
    session_obj.ai_tasks_regenerated = True
    db.add(session_obj)
    db.commit()

    return {
        "status": "success",
        "action_items": new_items_output,
        "ai_tasks_regenerated": True,
    }

@router.post("/{session_id}/regenerate_fields")
async def regenerate_fields_from_transcript(
    session_id: int,
    payload: Optional[RegeneratePayload] = None,
    db: Session = Depends(get_session),
    tenant: Tenant = Depends(get_current_tenant),
    _writer: User = Depends(require_session_writer),
):
    """Sugiere campos con IA (OpenAI gpt-4o). Solo se puede usar UNA vez por sesión.

    NO toca el resumen ejecutivo (ese viene nativo de Fireflies y se edita
    manualmente). Solo regenera: language, decisions, risks, agreements,
    attendees, themes.
    """
    from services.groq_service import OpenAIService

    session_obj = _get_session_or_404(db, session_id, tenant)

    # Validación de uso único
    if session_obj.ai_fields_regenerated:
        raise HTTPException(
            status_code=409,
            detail="Los campos ya fueron sugeridos con IA una vez para esta sesión.",
        )

    if payload and payload.raw_transcript:
        session_obj.raw_transcript = payload.raw_transcript

    if not session_obj.raw_transcript:
        raise HTTPException(status_code=400, detail="No transcript available to regenerate fields from.")

    openai_svc = OpenAIService()
    project_contacts = []
    if session_obj.project_id:
        from sqlmodel import select
        from models import ProjectContact
        db_contacts = db.exec(select(ProjectContact).where(ProjectContact.project_id == session_obj.project_id)).all()
        project_contacts = [{"name": c.name, "email": c.email, "role": c.role, "entity": c.entity} for c in db_contacts]

    structured_data = {}
    try:
        structured_data = await openai_svc.process_transcript(session_obj.raw_transcript, project_contacts)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error conectando con la IA (OpenAI): {str(e)}")

    def _unwrap_ai_field(val):
        if isinstance(val, dict):
            if "value" in val: return val["value"]
            if "items" in val: return val["items"]
        return val

    # NO sobrescribimos raw_summary: ese viene de Fireflies y es editable manualmente.

    language = _unwrap_ai_field(structured_data.get("language"))
    if language is not None:
        session_obj.language = str(language)

    decisions = _unwrap_ai_field(structured_data.get("decisions"))
    if decisions is not None:
        session_obj.processed_decisions = str(decisions)

    risks = _unwrap_ai_field(structured_data.get("risks"))
    if risks is not None:
        session_obj.processed_risks = str(risks)

    agreements = _unwrap_ai_field(structured_data.get("agreements"))
    if agreements is not None:
        session_obj.processed_agreements = str(agreements)

    import json
    attendees = _unwrap_ai_field(structured_data.get("attendees"))
    if attendees is not None:
        session_obj.processed_attendees = json.dumps(attendees, ensure_ascii=False)

    themes = _unwrap_ai_field(structured_data.get("themes"))
    if themes is not None:
        session_obj.processed_themes = json.dumps(themes, ensure_ascii=False)

    # Marca el flag de uso único.
    session_obj.ai_fields_regenerated = True

    db.add(session_obj)
    db.commit()
    db.refresh(session_obj)

    return {
        "status": "success",
        "ai_fields_regenerated": True,
        "fields": {
            "language": session_obj.language,
            "raw_summary": session_obj.raw_summary,
            "processed_decisions": session_obj.processed_decisions,
            "processed_risks": session_obj.processed_risks,
            "processed_agreements": session_obj.processed_agreements,
            "processed_attendees": session_obj.processed_attendees,
            "processed_themes": session_obj.processed_themes,
        },
    }

@router.put("/{session_id}")
def update_session_content(
    session_id: int,
    payload: SessionUpdate,
    db: Session = Depends(get_session),
    tenant: Tenant = Depends(get_current_tenant),
    _writer: User = Depends(require_session_writer),
):
    """Manually update the text content of a curated session (admin/validator)."""
    session_obj = _get_session_or_404(db, session_id, tenant)
        
    if payload.title is not None:
        session_obj.title = payload.title
    if payload.raw_summary is not None:
        session_obj.raw_summary = payload.raw_summary
    if payload.raw_transcript is not None:
        session_obj.raw_transcript = payload.raw_transcript
    if payload.processed_decisions is not None:
        session_obj.processed_decisions = payload.processed_decisions
    if payload.processed_risks is not None:
        session_obj.processed_risks = payload.processed_risks
    if payload.processed_agreements is not None:
        session_obj.processed_agreements = payload.processed_agreements
    if payload.status is not None:
        session_obj.status = payload.status
    if hasattr(payload, 'project_id') and payload.project_id is not None:
        session_obj.project_id = payload.project_id
        
    db.add(session_obj)
    db.commit()
    db.refresh(session_obj)
    return {"status": "success", "message": "Manual edits saved successfully"}

@router.put("/action_items/{item_id}")
def update_action_item_manual(
    item_id: int,
    title: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    owner_name: Optional[str] = Form(None),
    owner_email: Optional[str] = Form(None),
    due_date: Optional[str] = Form(None),
    db: Session = Depends(get_session),
    tenant: Tenant = Depends(get_current_tenant),
    _writer: User = Depends(require_session_writer),
):
    """Update details of an action item manually (admin/validator, tenant-scoped)."""
    from models import ActionItem
    item = db.get(ActionItem, item_id)
    if not item or item.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Action Item not found")
    
    if title is not None:
        item.title = title
    if description is not None:
        item.description = description
    if owner_name is not None:
        item.owner_name = owner_name
    if owner_email is not None:
        item.owner_email = owner_email
    if due_date is not None:
        item.due_date = due_date

    db.add(item)
    db.commit()
    db.refresh(item)
    return {"status": "success", "message": "Tarea actualizada", "item": item}

@router.post("/{session_id}/action_items")
def create_manual_action_item(
    session_id: int,
    title: str = Form(...),
    owner_name: str = Form(""),
    owner_email: str = Form(""),
    due_date: str = Form(""),
    description: str = Form(""),
    db: Session = Depends(get_session),
    tenant: Tenant = Depends(get_current_tenant),
    _writer: User = Depends(require_session_writer),
):
    """Crear una nueva tarea manual (admin/validator, tenant-scoped)."""
    from models import ActionItem
    session_obj = _get_session_or_404(db, session_id, tenant)

    new_item = ActionItem(
        tenant_id=tenant.id,
        session_id=session_id,
        title=title,
        owner_name=owner_name,
        owner_email=owner_email,
        due_date=due_date,
        description=description,
        is_approved=True
    )
    db.add(new_item)
    db.commit()
    db.refresh(new_item)
    
    return {"status": "success", "message": "Tarea agregada correctamente", "item": new_item}


async def _process_uploaded_session_background(session_id: int) -> None:
    """Background task que invoca el pipeline IA sobre una sesión recién subida.

    Vive en una Session DB nueva porque la del request HTTP ya cerró.
    """
    from database import engine
    from services.transcript_pipeline import process_session_with_ai

    with Session(engine) as db:
        try:
            await process_session_with_ai(db, session_id)
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "Pipeline IA falló para sesión subida manualmente %s", session_id
            )


@router.post("/upload")
async def upload_manual_session(
    title: str = Form(...),
    date: Optional[str] = Form(None),
    language: Optional[str] = Form(None),
    project_id: Optional[int] = Form(None),
    text_content: Optional[str] = Form(None),
    youtube_url: Optional[str] = Form(None),
    llm_provider: Optional[str] = Form("auto"),
    file: Optional[UploadFile] = File(None),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: Session = Depends(get_session),
    tenant: Tenant = Depends(get_current_tenant),
    _writer: User = Depends(require_session_writer),
):
    """Crea una sesión a partir de audio, texto o URL de YouTube.

    Pipeline post-creación (idéntico al del webhook de Fireflies):
      - Audio  → Groq Whisper (whisper-large-v3-turbo)
      - YouTube → yt-dlp → audio → Whisper
      - Texto  → se usa tal cual
      - Luego  → Groq fundamentals+insights + OpenAI tareas, en background.

    Args nuevos (Sprint 01):
      - youtube_url: URL de YouTube (alternativa a file/text_content)
      - llm_provider: 'auto' | 'openai' | 'groq' (override por sesión)
    """
    try:
        import time
        import uuid

        session_date = date if date else str(int(time.time() * 1000))
        session_language = language if language else "Desconocido"

        raw_transcript = ""

        if youtube_url:
            from services.youtube_ingest import fetch_audio_bytes, is_youtube_url
            if not is_youtube_url(youtube_url):
                raise HTTPException(status_code=400, detail="youtube_url no parece una URL válida de YouTube.")
            try:
                audio_bytes, fname = await fetch_audio_bytes(youtube_url)
            except RuntimeError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            from services.llm_groq import GroqLLMService
            raw_transcript = await GroqLLMService().transcribe_audio(audio_bytes, fname)
        elif file and file.filename:
            content = await file.read()
            if file.filename.lower().endswith(
                ('.mp3', '.wav', '.m4a', '.mp4', '.mpeg', '.mpga', '.webm', '.flac', '.ogg')
            ):
                from services.llm_groq import GroqLLMService
                groq = GroqLLMService()
                raw_transcript = await groq.transcribe_audio(content, file.filename)
            else:
                raw_transcript = content.decode('utf-8', errors='ignore')
        elif text_content:
            raw_transcript = text_content

        if not raw_transcript or len(raw_transcript.strip()) < 5:
            raise HTTPException(
                status_code=400,
                detail="No se pudo extraer texto. Sube audio, pega texto o pasa una URL de YouTube válida.",
            )

        # Validar que el project_id (si vino) sea de ESTE tenant — evita
        # subir un acta a un proyecto de otra empresa.
        if project_id:
            from models import Project
            proj = db.get(Project, project_id)
            if not proj or proj.tenant_id != tenant.id:
                raise HTTPException(status_code=400, detail="Proyecto inválido para esta empresa.")

        new_session = MeetingSession(
            tenant_id=tenant.id,
            fireflies_id=f"manual_{uuid.uuid4()}",
            title=title,
            date=session_date,
            language=session_language,
            project_id=project_id,
            raw_transcript=raw_transcript,
            status="processing",   # mientras corre el pipeline IA en background
            raw_summary="",
            processed_decisions="",
            processed_risks="",
            processed_agreements="",
            processed_attendees="[]",
            processed_themes="[]",
            llm_provider=(llm_provider or "auto"),  # Sprint 01
        )

        db.add(new_session)
        db.commit()
        db.refresh(new_session)

        # Mismo pipeline IA que se usa para webhooks de Fireflies.
        background_tasks.add_task(
            _process_uploaded_session_background, new_session.id
        )

        return {
            "status": "success",
            "session_id": new_session.id,
            "message": (
                "Sesión creada. La IA está procesando idioma, asistentes, temas, "
                "decisiones, riesgos, acuerdos y tareas en segundo plano."
            ),
        }
    except HTTPException:
        raise
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Error en upload manual de sesión")
        raise HTTPException(status_code=500, detail="Error procesando la subida.")

class DispatchEmailsRequest(BaseModel):
    action_item_ids: list[int]
    custom_pdf_b64: str = None
    attach_document: bool = False

def __build_corporate_data(session_obj, action_items, db=None) -> dict:
    import json
    import datetime
    from models import Template
    from sqlmodel import select
    
    formatted_date = session_obj.date
    if session_obj.date and str(session_obj.date).isdigit():
        dt = datetime.datetime.fromtimestamp(int(session_obj.date) / 1000)
        formatted_date = dt.strftime("%d/%m/%Y %H:%M")
    elif session_obj.date and "T" in str(session_obj.date):
        formatted_date = str(session_obj.date).split("T")[0]

    try:
        attendees = json.loads(session_obj.processed_attendees) if session_obj.processed_attendees else []
    except Exception:
        attendees = []

    clean_summary = ""
    if session_obj.raw_summary:
        clean_summary = session_obj.raw_summary.replace("Notes", "Notas de la Sesión").replace("Action items", "Elementos de Acción")

    formatted_items = []
    if action_items:
        for item in action_items:
            formatted_items.append({
                "title": item.title,
                "owner_email": f"{item.owner_name} ({item.owner_email})" if item.owner_name else (item.owner_email or "Asignado"),
                "due_date": item.due_date or "Sin fecha",
                "status": "Pendiente"
            })

    theme = None
    mapping_config = []
    project_name = "General"
    if db and hasattr(session_obj, "project_id") and session_obj.project_id:
        from models import Project
        proj = db.exec(select(Project).where(Project.id == session_obj.project_id)).first()
        if proj:
            project_name = proj.name
            
        template_obj = db.exec(select(Template).where(Template.project_id == session_obj.project_id)).first()
        if template_obj:
            if template_obj.style_config:
                try:
                    theme = json.loads(template_obj.style_config)
                except Exception:
                    pass
            if getattr(template_obj, "mapping_config", None):
                try:
                    mapping_config = json.loads(template_obj.mapping_config)
                except Exception:
                    pass
            template_path = getattr(template_obj, "file_path", None)
                    
    return {
        "entidad_principal": "Notiva",
        "entidad_secundaria": "Gestión Integral de Sesiones",
        "titulo_documento": "ACTA DE REUNIÓN",
        "subtitulo_documento": session_obj.title or "Sesión General",
        "version_documento": "1.0",
        "clasificacion": "Uso Corporativo",
        "no_acta": f"ACT-{session_obj.id:04d}",
        "fecha_documento": formatted_date,
        "idioma": getattr(session_obj, "language", "Español"),
        "proyecto": project_name,
        "asistentes": attendees,
        "contexto_antecedentes": clean_summary,
        "decisiones": session_obj.processed_decisions or "",
        "riesgos": session_obj.processed_risks or "",
        "agreements": session_obj.processed_agreements or "",
        "compromisos": formatted_items,
        "theme": theme,
        "mapping_config": mapping_config,
        "template_path": template_path if 'template_path' in locals() else None
    }

def generate_word_document_bytes(session_obj, action_items, db: Session) -> io.BytesIO:
    from services.docx_generator import CorporateDocxGenerator
    import io
    
    data = __build_corporate_data(session_obj, action_items, db)
    generator = CorporateDocxGenerator(data)
    return generator.generar_buffer()

@router.post("/{session_id}/dispatch_emails")
async def dispatch_emails(
    session_id: int,
    request: DispatchEmailsRequest,
    db: Session = Depends(get_session),
    tenant: Tenant = Depends(get_current_tenant),
    _writer: User = Depends(require_session_writer),
):
    """Dispatch emails for the selected action items (admin/validator, tenant-scoped)."""
    from models import ActionItem
    from services.email_service import EmailService
    import asyncio
    from fpdf import FPDF
    from sqlmodel import select

    session_obj = _get_session_or_404(db, session_id, tenant)

    email_service = EmailService(db=db, tenant_id=tenant.id)
    results = []
    
    action_items_all = db.exec(select(ActionItem).where(ActionItem.session_id == session_id)).all()
    
    import datetime
    import base64
    
    pdf_b64_global = None
    docx_b64_global = None
    
    if request.custom_pdf_b64:
        # Usar el PDF provisto por el frontend en base64
        pdf_b64_global = request.custom_pdf_b64
        if "base64," in pdf_b64_global:
            pdf_b64_global = pdf_b64_global.split("base64,")[1]
    else:
        # Generar siempre el PDF usando la plantilla + Gotenberg, igual que en export_document
        try:
            from models import Template
            import requests
            import json
            
            template = None
            if session_obj.project_id:
                template = db.exec(select(Template).where(Template.project_id == session_obj.project_id)).first()
            
            docx_bytes = None
            
            if template and template.file_path:
                from services.word_generator import WordGeneratorService
                generator = WordGeneratorService()
                
                formatted_date = ""
                if session_obj.date:
                    try:
                        import datetime as dt_lib
                        if str(session_obj.date).isdigit():
                            dt = dt_lib.datetime.fromtimestamp(int(session_obj.date) / 1000)
                            formatted_date = dt.strftime("%d/%m/%Y")
                        elif "T" in str(session_obj.date):
                            formatted_date = str(session_obj.date).split("T")[0]
                        else:
                            formatted_date = str(session_obj.date)
                    except Exception:
                        formatted_date = str(session_obj.date)
                
                # We need __build_corporate_data which is available in the current scope
                meeting_data = {
                    "title": session_obj.title,
                    "date": formatted_date,
                    "summary": session_obj.raw_summary,
                    "decisions": session_obj.processed_decisions,
                    "risks": session_obj.processed_risks,
                    "agreements": session_obj.processed_agreements,
                    "action_items": [],
                    "contexto_antecedentes": session_obj.raw_summary,
                    "decisiones": session_obj.processed_decisions,
                    "riesgos": session_obj.processed_risks,
                    "compromisos": __build_corporate_data(session_obj, action_items_all, db).get("compromisos", []),
                    "mapping_config": __build_corporate_data(session_obj, action_items_all, db).get("mapping_config", []),
                    "theme": __build_corporate_data(session_obj, action_items_all, db).get("theme", {}),
                    "asistentes": __build_corporate_data(session_obj, action_items_all, db).get("asistentes", []),
                    "no_acta": __build_corporate_data(session_obj, action_items_all, db).get("no_acta", ""),
                    "fecha_documento": __build_corporate_data(session_obj, action_items_all, db).get("fecha_documento", ""),
                    "idioma": __build_corporate_data(session_obj, action_items_all, db).get("idioma", "Español"),
                    "proyecto": __build_corporate_data(session_obj, action_items_all, db).get("proyecto", "General"),
                    "subtitulo_documento": __build_corporate_data(session_obj, action_items_all, db).get("subtitulo_documento", session_obj.title)
                }
                for act in action_items_all:
                    meeting_data["action_items"].append({
                        "title": act.title,
                        "owner_name": act.owner_name,
                        "description": act.description,
                        "due_date": act.due_date
                    })
                    
                try:
                    out_path = f"/tmp/Gen_{session_obj.id}_email.docx"
                    generator.generate_document(template.file_path, meeting_data, out_path)
                    with open(out_path, "rb") as f:
                        docx_bytes = f.read()
                except Exception as e:
                    import traceback
                    print(f"Template DOCX generation failed for email: {e}\n{traceback.format_exc()}")
            
            # Fallback if generation failed or no template exists
            if docx_bytes is None:
                docx_buffer = generate_word_document_bytes(session_obj, action_items_all, db)
                docx_bytes = docx_buffer.getvalue()
                
            try:
                r = requests.post(
                    "https://demo.gotenberg.dev/forms/libreoffice/convert",
                    files={"files": ("acta.docx", docx_bytes)},
                    timeout=60
                )
                if r.ok:
                    pdf_b64_global = base64.b64encode(r.content).decode('utf-8')
                else:
                    print(f"Gotenberg API Error in dispatch: {r.status_code} {r.text}")
            except Exception as e:
                print(f"Gotenberg error in dispatch email: {e}")
                
            if not pdf_b64_global:
                # Fallback to local FPDF generator
                from services.pdf_generator import CorporatePDFGenerator
                data = __build_corporate_data(session_obj, action_items_all, db)
                if template and template.style_config:
                    try:
                        data["theme"] = json.loads(template.style_config)
                    except (json.JSONDecodeError, TypeError):
                        pass
                if template and template.file_path:
                    data["template_path"] = template.file_path
                pdf_gen = CorporatePDFGenerator(data)
                pdf_buffer = pdf_gen.generar_buffer()
                pdf_b64_global = base64.b64encode(pdf_buffer.getvalue()).decode('utf-8')
                
        except Exception as e:
            import traceback
            print(f"Error generating PDF attachment for email: {e}\n{traceback.format_exc()}")
    
    # 1. Agrupar items por owner_email
    tasks_by_email = {}
    for item_id in request.action_item_ids:
        item = db.get(ActionItem, item_id)
        if not item or item.session_id != session_id:
            continue
            
        if not item.owner_email:
            results.append({"id": item_id, "status": "failed", "reason": "No email provided"})
            continue
            
        email = item.owner_email.lower().strip()
        if email not in tasks_by_email:
            tasks_by_email[email] = {
                "owner_name": item.owner_name if item.owner_name else email.split('@')[0],
                "items": []
            }
        tasks_by_email[email]["items"].append(item)
        
    # 2. Iterar por cada persona
    for email, data in tasks_by_email.items():
        attachments = []
        if docx_b64_global:
            safe_title = session_obj.title[:20].replace(' ', '_')
            attachments.append({
                "filename": f"Acta_{session_obj.id}_{safe_title}.docx",
                "content": docx_b64_global,
                "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            })
        elif pdf_b64_global:
            attachments.append({
                "filename": "Resumen_Sesion.pdf",
                "content": pdf_b64_global,
                "content_type": "application/pdf"
            })
            
        # Generar ICS consolidado
        ics_events = []
        for item in data["items"]:
            if item.due_date:
                try:
                    date_clean = str(item.due_date).replace("-", "")
                    if len(date_clean) == 8:
                        desc_clean = (item.description or "").replace("\n", "\\n").replace("\r", "")
                        title_clean = item.title.replace("\n", "").replace("\r", "")
                        ics_events.extend([
                            "BEGIN:VEVENT",
                            f"SUMMARY:{title_clean}",
                            f"DTSTART;VALUE=DATE:{date_clean}",
                            f"DTEND;VALUE=DATE:{date_clean}",
                            f"DESCRIPTION:{desc_clean}",
                            "END:VEVENT"
                        ])
                except Exception as e:
                    print(f"Error parseando fecha para ICS de {item.id}: {e}")
                    
        if ics_events:
            ics_lines = [
                "BEGIN:VCALENDAR",
                "VERSION:2.0",
                "PRODID:-//Notiva//ES"
            ] + ics_events + [
                "END:VCALENDAR"
            ]
            ics_raw = "\r\n".join(ics_lines).encode('utf-8')
            ics_b64 = base64.b64encode(ics_raw).decode('utf-8')
            attachments.append({
                "filename": "recordatorio_tareas.ics",
                "content": ics_b64,
                "content_type": "text/calendar"
            })
            
        try:
            await email_service.send_action_items_batch_email(
                to_email=email,
                owner_name=data["owner_name"],
                tasks=data["items"],
                project_name=session_obj.title,
                attachments=attachments,
                summary=session_obj.raw_summary,
                decisions=session_obj.processed_decisions,
                risks=session_obj.processed_risks,
                agreements=session_obj.processed_agreements
            )
            for item in data["items"]:
                results.append({"id": item.id, "status": "success"})
                
            # Resend Free limit is 2 requests per second. Sleep 0.6s to stay strictly below limit.
            await asyncio.sleep(0.6)
        except Exception as e:
            for item in data["items"]:
                results.append({"id": item.id, "status": "failed", "reason": str(e)})
            
    return {"status": "success", "results": results}

class DispatchPlatformsRequest(BaseModel):
    action_item_ids: list[int]

@router.post("/{session_id}/dispatch_platforms")
async def dispatch_platforms(
    session_id: int,
    request: DispatchPlatformsRequest,
    db: Session = Depends(get_session),
    tenant: Tenant = Depends(get_current_tenant),
    _writer: User = Depends(require_session_writer),
):
    """Dispatch tasks to integrations (admin/validator, tenant-scoped):
    sólo lee `IntegrationSetting` y `Routing` del propio tenant.
    """
    from models import ActionItem, Routing, IntegrationSetting
    import json
    from sqlmodel import select
    from services.integrations.trello import TrelloIntegrationService
    from services.integrations.jira import JiraIntegrationService
    from services.integrations.clickup import ClickUpIntegrationService
    from services.integrations.azure_devops import AzureDevOpsIntegrationService

    session_obj = _get_session_or_404(db, session_id, tenant)

    if not session_obj.project_id:
        raise HTTPException(status_code=400, detail="Cannot dispatch: Meeting is not related to any project routing.")

    routings = db.exec(select(Routing).where(Routing.project_id == session_obj.project_id, Routing.is_active == True)).all()
    if not routings:
        raise HTTPException(status_code=400, detail="Project has no configured routings.")

    global_settings = db.exec(
        select(IntegrationSetting).where(IntegrationSetting.tenant_id == tenant.id)
    ).all()
    settings_dict = {}
    for s in global_settings:
        try:
            settings_dict[s.provider_name] = json.loads(s.config_json)
        except (json.JSONDecodeError, TypeError):
            settings_dict[s.provider_name] = {}

    from datetime import datetime
    
    results = []
    for item_id in request.action_item_ids:
        item = db.get(ActionItem, item_id)
        if not item or item.session_id != session_id:
            continue
        
        item_success = False
        
        eff_due_date = item.due_date if item.due_date else datetime.now().strftime("%Y-%m-%d")
        
        owner_display = f"{item.owner_name} ({item.owner_email})" if item.owner_name else (item.owner_email or "N/A")
        safe_description = f"{item.description}\n\n**Metadatos de Notiva**\n- Asignado Original: {owner_display}\n- Fecha Vencimiento Asignada: {eff_due_date}"
        
        for routing in routings:
            config = json.loads(routing.destination_config or '{}')
            dest_type = routing.destination_type.lower()
            
            try:
                if "trello" in dest_type:
                    t_config = settings_dict.get("trello", {})
                    if t_config.get("isActive", False) and t_config.get("apiKey") and t_config.get("apiToken"):
                        trello_service = TrelloIntegrationService(t_config["apiKey"], t_config["apiToken"])
                        await trello_service.create_card(config.get("board_id"), config.get("list_id"), item.title, safe_description, eff_due_date, item.owner_email)
                        item_success = True
                elif "jira" in dest_type:
                    j_config = settings_dict.get("jira", {})
                    if j_config.get("isActive", False) and j_config.get("domain") and j_config.get("apiToken"):
                        jira_email = j_config.get("email", "")
                        jira_service = JiraIntegrationService(j_config["domain"], jira_email, j_config["apiToken"]) 
                        await jira_service.create_issue(config.get("project_key"), item.title, safe_description, due_date=eff_due_date, owner_email=item.owner_email)
                        item_success = True
                elif "clickup" in dest_type:
                    c_config = settings_dict.get("clickup", {})
                    if c_config.get("isActive", False) and c_config.get("apiToken"):
                        clickup_service = ClickUpIntegrationService(c_config["apiToken"]) 
                        await clickup_service.create_task(config.get("list_id"), item.title, safe_description, eff_due_date, item.owner_email)
                        item_success = True
                elif "azure" in dest_type:
                    a_config = settings_dict.get("azure", {})
                    if a_config.get("isActive", False) and a_config.get("organization") and a_config.get("project") and a_config.get("pat"):
                        azure_service = AzureDevOpsIntegrationService(a_config["organization"], a_config["project"], a_config["pat"])
                        desc = safe_description
                        if config.get("area_path"):
                            desc += f"\n\n[Destino Específico: {config['area_path']}]"
                        await azure_service.create_work_item(item.title, desc, due_date=eff_due_date, owner_email=item.owner_email)
                        item_success = True
            except Exception as e:
                print(f"Error dispatching to {dest_type}: {e}")
                pass # Proceed to next routing iteration
                
        if item_success:
            results.append({"id": item_id, "status": "success"})
        else:
            results.append({"id": item_id, "status": "failed"})

    return {"status": "success", "results": results}

@router.get("/{session_id}/export/{format}")
def export_document(
    session_id: int,
    format: str,
    db: Session = Depends(get_session),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Generate and return a document with the meeting details (tenant-scoped)."""
    from models import ActionItem, Template
    from sqlmodel import select
    import io
    import json
    import os

    session_obj = _get_session_or_404(db, session_id, tenant)

    action_items = db.exec(select(ActionItem).where(ActionItem.session_id == session_id)).all()

    # Check for template
    template = None
    if session_obj.project_id:
        template = db.exec(select(Template).where(Template.project_id == session_obj.project_id)).first()

    safe_title = (session_obj.title or "Reunion").replace(" ", "_").replace("/", "").replace("\\", "")[:30]

    # Ensure format is supported
    if format not in ['word', 'pdf']:
        raise HTTPException(status_code=400, detail="Formato no soportado")

    docx_bytes = None
    
    if template and template.file_path:
        from services.word_generator import WordGeneratorService
        generator = WordGeneratorService()
        
        formatted_date = ""
        if session_obj.date:
            try:
                import datetime
                if str(session_obj.date).isdigit():
                    dt = datetime.datetime.fromtimestamp(int(session_obj.date) / 1000)
                    formatted_date = dt.strftime("%d/%m/%Y")
                elif "T" in str(session_obj.date):
                    formatted_date = str(session_obj.date).split("T")[0]
                else:
                    formatted_date = str(session_obj.date)
            except Exception:
                formatted_date = str(session_obj.date)
        
        meeting_data = {
            "title": session_obj.title,
            "date": formatted_date,
            "summary": session_obj.raw_summary,
            "decisions": session_obj.processed_decisions,
            "risks": session_obj.processed_risks,
            "agreements": session_obj.processed_agreements,
            "action_items": [],
            "contexto_antecedentes": session_obj.raw_summary,
            "decisiones": session_obj.processed_decisions,
            "riesgos": session_obj.processed_risks,
            "compromisos": __build_corporate_data(session_obj, action_items, db).get("compromisos", []),
            "mapping_config": __build_corporate_data(session_obj, action_items, db).get("mapping_config", []),
            "theme": __build_corporate_data(session_obj, action_items, db).get("theme", {}),
            "asistentes": __build_corporate_data(session_obj, action_items, db).get("asistentes", []),
            "no_acta": __build_corporate_data(session_obj, action_items, db).get("no_acta", ""),
            "fecha_documento": __build_corporate_data(session_obj, action_items, db).get("fecha_documento", ""),
            "idioma": __build_corporate_data(session_obj, action_items, db).get("idioma", "Español"),
            "proyecto": __build_corporate_data(session_obj, action_items, db).get("proyecto", "General"),
            "subtitulo_documento": __build_corporate_data(session_obj, action_items, db).get("subtitulo_documento", session_obj.title)
        }
        for act in action_items:
            meeting_data["action_items"].append({
                "title": act.title,
                "owner_name": act.owner_name,
                "description": act.description,
                "due_date": act.due_date
            })
            
        try:
            out_path = f"/tmp/Gen_{session_obj.id}.docx"
            generator.generate_document(template.file_path, meeting_data, out_path)
            with open(out_path, "rb") as f:
                docx_bytes = f.read()
        except Exception as e:
            import traceback
            print(f"Template DOCX generation failed: {e}\n{traceback.format_exc()}")
            
    # Fallback if generation failed or no template exists
    if docx_bytes is None:
        buffer = generate_word_document_bytes(session_obj, action_items, db)
        docx_bytes = buffer.getvalue()

    if format == 'pdf':
        import requests
        try:
            r = requests.post(
                "https://demo.gotenberg.dev/forms/libreoffice/convert",
                files={"files": ("acta.docx", docx_bytes)},
                timeout=60
            )
            if r.ok:
                pdf_bytes = r.content
                return Response(
                    content=pdf_bytes,
                    media_type="application/pdf",
                    headers={
                        'Content-Disposition': f'attachment; filename="Acta_{session_obj.id}_{safe_title}.pdf"',
                        'Cache-Control': 'no-cache, no-store, must-revalidate'
                    }
                )
            else:
                print(f"Gotenberg API Error: {r.status_code} {r.text}")
                # Optional fallback to original PDF engine if Gotenberg fails
                from services.pdf_generator import CorporatePDFGenerator
                data = __build_corporate_data(session_obj, action_items, db)
                if template and template.style_config:
                    try:
                        data["theme"] = json.loads(template.style_config)
                    except (json.JSONDecodeError, TypeError):
                        pass
                if template and template.file_path:
                    data["template_path"] = template.file_path
                return Response(
                    content=CorporatePDFGenerator(data).generar_buffer().getvalue(),
                    media_type="application/pdf",
                    headers={'Content-Disposition': f'attachment; filename="Acta_{session_obj.id}_{safe_title}.pdf"'}
                )
        except Exception as e:
            print(f"Failed to reach Gotenberg: {e}")
            raise HTTPException(status_code=500, detail="Error interno al convertir PDF. La API de conversión no responde.")
            
    else:
        # Provide Word format
        return Response(
            content=docx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                'Content-Disposition': f'attachment; filename="Acta_{session_obj.id}_{safe_title}.docx"',
                'Cache-Control': 'no-cache, no-store, must-revalidate'
            }
        )
