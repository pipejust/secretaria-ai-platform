from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Optional
from database import get_session
from sqlmodel import Session
from models import MeetingSession
import datetime
import uuid
import io
import docx

router = APIRouter(
    prefix="/api/sessions",
    tags=["Sessions"]
)

@router.get("/")
def get_sessions(db: Session = Depends(get_session)):
    """Fetch all meeting sessions (Actas) from the database."""
    from sqlmodel import select
    sessions = db.exec(select(MeetingSession).order_by(MeetingSession.id.desc())).all()
    return sessions

@router.get("/{session_id}")
def get_session_details(session_id: int, db: Session = Depends(get_session)):
    """Fetch a specific meeting session and its related action items."""
    from sqlmodel import select
    from models import ActionItem
    
    session_obj = db.get(MeetingSession, session_id)
    if not session_obj:
        raise HTTPException(status_code=404, detail="Session not found")
        
    action_items = db.exec(select(ActionItem).where(ActionItem.session_id == session_id)).all()
    # Podríamos crear un Pydantic model response, pero dict/jsonable encoder lo maneja bien
    return {
        "session": session_obj,
        "action_items": action_items
    }

class SessionUpdate(BaseModel):
    raw_summary: Optional[str] = None
    raw_transcript: Optional[str] = None
    processed_decisions: Optional[str] = None
    processed_risks: Optional[str] = None
    processed_agreements: Optional[str] = None
    status: Optional[str] = None

class RegeneratePayload(BaseModel):
    raw_transcript: Optional[str] = None

@router.post("/{session_id}/regenerate_tasks")
async def regenerate_tasks_from_transcript(session_id: int, payload: Optional[RegeneratePayload] = None, db: Session = Depends(get_session)):
    from models import ActionItem, ProjectContact
    from services.groq_service import GroqService
    from sqlmodel import delete

    session_obj = db.get(MeetingSession, session_id)
    if not session_obj:
        raise HTTPException(status_code=404, detail="Session not found")
        
    if payload and payload.raw_transcript:
        session_obj.raw_transcript = payload.raw_transcript
    
    if not session_obj.raw_transcript:
        raise HTTPException(status_code=400, detail="No transcript available to regenerate tasks from.")

    # 1. Obtenemos Contactos
    project_contacts = []
    if session_obj.project_id:
        from sqlmodel import select
        db_contacts = db.exec(select(ProjectContact).where(ProjectContact.project_id == session_obj.project_id)).all()
        project_contacts = [{"name": c.name, "email": c.email, "role": c.role} for c in db_contacts]

    # 2. Llamamos a Groq
    groq_svc = GroqService()
    try:
        structured_data = await groq_svc.process_transcript(session_obj.raw_transcript, project_contacts)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error conectando con la IA (Groq): {str(e)}")


    # 3. Insertamos Nuevas
    action_items_data = structured_data.get("action_items", [])
    if action_items_data is None:
        action_items_data = []

    if action_items_data:
        # Solo borramos las anteriores si realmente vienen nuevas
        db.exec(delete(ActionItem).where(ActionItem.session_id == session_id))
        db.commit()

    new_items_output = []
    for item_data in action_items_data:
        action_item = ActionItem(
            session_id=session_id,
            owner_name=item_data.get("owner_name", "Unknown"),
            owner_email=item_data.get("owner_email", ""),
            title=item_data.get("title", "Tarea sin título"),
            description=item_data.get("description", ""),
            due_date=item_data.get("due_date"),
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

    return {"status": "success", "action_items": new_items_output}

@router.post("/{session_id}/regenerate_fields")
async def regenerate_fields_from_transcript(session_id: int, payload: Optional[RegeneratePayload] = None, db: Session = Depends(get_session)):
    from services.groq_service import GroqService

    session_obj = db.get(MeetingSession, session_id)
    if not session_obj:
        raise HTTPException(status_code=404, detail="Session not found")
        
    if payload and payload.raw_transcript:
        session_obj.raw_transcript = payload.raw_transcript
    
    if not session_obj.raw_transcript:
        raise HTTPException(status_code=400, detail="No transcript available to regenerate fields from.")

    groq_svc = GroqService()
    # Enviamos solo con los contactos requeridos si existen (para tareas) aunque aquí saquemos los demás campos
    project_contacts = []
    if session_obj.project_id:
        from sqlmodel import select
        from models import ProjectContact
        db_contacts = db.exec(select(ProjectContact).where(ProjectContact.project_id == session_obj.project_id)).all()
        project_contacts = [{"name": c.name, "email": c.email, "role": c.role} for c in db_contacts]

    structured_data = {}
    try:
        structured_data = await groq_svc.process_transcript(session_obj.raw_transcript, project_contacts)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error conectando con la IA (Groq): {str(e)}")


    summary = structured_data.get("summary")
    if summary is not None:
        session_obj.raw_summary = summary
        
    decisions = structured_data.get("decisions")
    if decisions is not None:
        session_obj.processed_decisions = decisions
        
    risks = structured_data.get("risks")
    if risks is not None:
        session_obj.processed_risks = risks
        
    agreements = structured_data.get("agreements")
    if agreements is not None:
        session_obj.processed_agreements = agreements
    
    import json
    attendees = structured_data.get("attendees")
    if attendees is not None:
        session_obj.processed_attendees = json.dumps(attendees, ensure_ascii=False)
        
    themes = structured_data.get("themes")
    if themes is not None:
        session_obj.processed_themes = json.dumps(themes, ensure_ascii=False)

    db.add(session_obj)
    db.commit()
    db.refresh(session_obj)

    return {
        "status": "success",
        "fields": {
            "raw_summary": session_obj.raw_summary,
            "processed_decisions": session_obj.processed_decisions,
            "processed_risks": session_obj.processed_risks,
            "processed_agreements": session_obj.processed_agreements,
            "processed_attendees": session_obj.processed_attendees,
            "processed_themes": session_obj.processed_themes
        }
    }

@router.put("/{session_id}")
def update_session_content(session_id: int, payload: SessionUpdate, db: Session = Depends(get_session)):
    """Manually update the text content of a curated session."""
    session_obj = db.get(MeetingSession, session_id)
    if not session_obj:
        raise HTTPException(status_code=404, detail="Session not found")
        
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
        
    db.add(session_obj)
    db.commit()
    db.refresh(session_obj)
    return {"status": "success", "message": "Manual edits saved successfully"}

@router.put("/action_items/{item_id}")
def update_action_item_email(item_id: int, owner_email: str = Form(...), db: Session = Depends(get_session)):
    """Update the owner email of an action item manually."""
    from models import ActionItem
    item = db.get(ActionItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Action Item not found")
    
    item.owner_email = owner_email
    db.add(item)
    db.commit()
    db.refresh(item)
    return {"status": "success", "message": "Email actualizado", "item": item}


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
        import uuid
        new_session = MeetingSession(
            fireflies_id=f"manual_{uuid.uuid4()}",
            title=title,
            date=datetime.datetime.utcnow().isoformat() + "Z",
            video_url=f"manual_upload_{file.filename}",
            raw_transcript=f"Uploaded {file.filename} manually.",  # Changed to raw_transcript to match model
            status="pending",
            raw_summary="",
            processed_decisions="",
            processed_risks="",
            processed_agreements="",
            processed_attendees="[]",
            processed_themes="[]"
        )
        
        session.add(new_session)
        session.commit()
        session.refresh(new_session)
        
        return {"status": "success", "session_id": new_session.id, "message": "Archivo subido exitosamente."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class DispatchEmailsRequest(BaseModel):
    action_item_ids: list[int]

@router.post("/{session_id}/dispatch_emails")
async def dispatch_emails(session_id: int, request: DispatchEmailsRequest, db: Session = Depends(get_session)):
    """Dispatch emails for the selected action items."""
    from models import ActionItem
    from services.email_service import EmailService
    import asyncio
    from fpdf import FPDF
    
    session_obj = db.get(MeetingSession, session_id)
    if not session_obj:
        raise HTTPException(status_code=404, detail="Session not found")
        
    email_service = EmailService(db=db)
    results = []
    
    # Generate generic PDF once for the session
    try:
        from models import Template
        import json
        style_config = {}
        
        # Intentar obtener el template activo del proyecto para heredar sus estilos
        if session_obj.project_id:
            templates = db.exec(select(Template).where(Template.project_id == session_obj.project_id)).all()
            if templates and templates[0].style_config:
                try:
                    style_config = json.loads(templates[0].style_config)
                except Exception:
                    pass
                    
        def hex_to_rgb_tuple(hex_str: str) -> tuple:
            hex_str = hex_str.lstrip('#')
            if len(hex_str) != 6:
                return (0, 0, 0)
            return (int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16))
            
        color_text = hex_to_rgb_tuple(style_config.get("textColor", "#1f2937"))
        color_heading = hex_to_rgb_tuple(style_config.get("headingColor", "#4f46e5"))
        
        pdf = FPDF()
        pdf.add_page()
        
        # Titulo Principal
        pdf.set_text_color(*color_heading)
        pdf.set_font("helvetica", "B", 18)
        pdf.cell(0, 10, "Secretaria AI - Resumen de Sesion", new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.ln(10)
        
        # Meta Info
        pdf.set_text_color(*color_text)
        pdf.set_font("helvetica", "B", 12)
        safe_title = session_obj.title.encode('latin-1', 'replace').decode('latin-1')
        pdf.cell(0, 8, f"Proyecto: {safe_title}", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 8, f"Fecha: {session_obj.date}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(5)
        
        # Resumen Ejecutivo
        if session_obj.raw_summary:
            pdf.set_fill_color(*color_heading)
            pdf.set_text_color(255, 255, 255) # Texto blanco sobre fondo de color
            pdf.set_font("helvetica", "B", 14)
            # Add some padding and solid fill
            pdf.cell(0, 10, "Resumen Ejecutivo:", new_x="LMARGIN", new_y="NEXT", fill=True)
            pdf.ln(3)
            
            pdf.set_text_color(*color_text)
            pdf.set_font("helvetica", "", 11)
            safe_summary = session_obj.raw_summary.encode('latin-1', 'replace').decode('latin-1')
            pdf.multi_cell(0, 6, safe_summary)
            pdf.ln(5)
            
        import base64
        pdf_bytes = base64.b64encode(bytes(pdf.output())).decode('utf-8')
    except Exception as e:
        print(f"Error generating PDF summary: {e}")
        pdf_bytes = None
    
    for item_id in request.action_item_ids:
        item = db.get(ActionItem, item_id)
        if not item or item.session_id != session_id:
            continue
            
        if not item.owner_email:
            results.append({"id": item_id, "status": "failed", "reason": "No email provided"})
            continue
            
        attachments = []
        if pdf_bytes:
            attachments.append({
                "filename": "Resumen_Sesion.pdf",
                "content": pdf_bytes
            })
            
        if item.due_date:
            try:
                date_clean = str(item.due_date).replace("-", "")
                if len(date_clean) == 8:
                    desc_clean = (item.description or "").replace("\n", "\\n").replace("\r", "")
                    title_clean = item.title.replace("\n", "").replace("\r", "")
                    ics_lines = [
                        "BEGIN:VCALENDAR",
                        "VERSION:2.0",
                        "PRODID:-//Secretaria AI//ES",
                        "BEGIN:VEVENT",
                        f"SUMMARY:{title_clean}",
                        f"DTSTART;VALUE=DATE:{date_clean}",
                        f"DTEND;VALUE=DATE:{date_clean}",
                        f"DESCRIPTION:{desc_clean}",
                        "END:VEVENT",
                        "END:VCALENDAR"
                    ]
                    ics_bytes = base64.b64encode("\r\n".join(ics_lines).encode('utf-8')).decode('utf-8')
                    attachments.append({
                        "filename": "recordatorio.ics",
                        "content": ics_bytes
                    })
            except Exception as e:
                print(f"Error generating ICS: {e}")
            
        try:
            # Pasa los datos extra al nuevo email service (fecha, attachments)
            await email_service.send_action_item_email(
                to_email=item.owner_email,
                owner_name=item.owner_name,
                task_title=item.title,
                task_description=item.description,
                project_name=session_obj.title,
                due_date=item.due_date,
                attachments=attachments
            )
            results.append({"id": item_id, "status": "success"})
            # Resend Free limit is 2 requests per second. Sleep 0.6s to stay strictly below limit.
            await asyncio.sleep(0.6)
        except Exception as e:
            results.append({"id": item_id, "status": "failed", "reason": str(e)})
            
    return {"status": "success", "results": results}

class DispatchPlatformsRequest(BaseModel):
    action_item_ids: list[int]

@router.post("/{session_id}/dispatch_platforms")
async def dispatch_platforms(session_id: int, request: DispatchPlatformsRequest, db: Session = Depends(get_session)):
    """Dispatch selected action items to configured platforms (ClickUp, Trello, Jira, Azure)."""
    from models import ActionItem, Routing, IntegrationSetting
    import json
    from sqlmodel import select
    from services.integrations.trello import TrelloIntegrationService
    from services.integrations.jira import JiraIntegrationService
    from services.integrations.clickup import ClickUpIntegrationService
    from services.integrations.azure_devops import AzureDevOpsIntegrationService

    session_obj = db.get(MeetingSession, session_id)
    if not session_obj:
        raise HTTPException(status_code=404, detail="Session not found")

    if not session_obj.project_id:
        raise HTTPException(status_code=400, detail="Cannot dispatch: Meeting is not related to any project routing.")
        
    routings = db.exec(select(Routing).where(Routing.project_id == session_obj.project_id, Routing.is_active == True)).all()
    if not routings:
        raise HTTPException(status_code=400, detail="Project has no configured routings.")

    global_settings = db.exec(select(IntegrationSetting)).all()
    settings_dict = {}
    for s in global_settings:
        try:
            settings_dict[s.provider_name] = json.loads(s.config_json)
        except:
            settings_dict[s.provider_name] = {}

    from datetime import datetime
    
    results = []
    for item_id in request.action_item_ids:
        item = db.get(ActionItem, item_id)
        if not item or item.session_id != session_id:
            continue
        
        item_success = False
        
        eff_due_date = item.due_date if item.due_date else datetime.now().strftime("%Y-%m-%d")
        
        safe_description = f"{item.description}\n\n**Metadatos de Secretaría**\n- Asignado Original: {item.owner_email or 'N/A'}\n- Fecha Vencimiento Asignada: {eff_due_date}"
        
        for routing in routings:
            config = json.loads(routing.destination_config or '{}')
            dest_type = routing.destination_type.lower()
            
            try:
                if "trello" in dest_type:
                    t_config = settings_dict.get("trello", {})
                    if t_config.get("apiKey") and t_config.get("apiToken"):
                        trello_service = TrelloIntegrationService(t_config["apiKey"], t_config["apiToken"])
                        await trello_service.create_card(config.get("board_id"), config.get("list_id"), item.title, safe_description, eff_due_date, item.owner_email)
                        item_success = True
                elif "jira" in dest_type:
                    j_config = settings_dict.get("jira", {})
                    if j_config.get("domain") and j_config.get("apiToken"):
                        jira_email = j_config.get("email", "")
                        jira_service = JiraIntegrationService(j_config["domain"], jira_email, j_config["apiToken"]) 
                        await jira_service.create_issue(config.get("project_key"), item.title, safe_description, due_date=eff_due_date, owner_email=item.owner_email)
                        item_success = True
                elif "clickup" in dest_type:
                    c_config = settings_dict.get("clickup", {})
                    if c_config.get("apiToken"):
                        clickup_service = ClickUpIntegrationService(c_config["apiToken"]) 
                        await clickup_service.create_task(config.get("list_id"), item.title, safe_description, eff_due_date, item.owner_email)
                        item_success = True
                elif "azure" in dest_type:
                    a_config = settings_dict.get("azure", {})
                    if a_config.get("organization") and a_config.get("project") and a_config.get("pat"):
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

@router.get("/{session_id}/export/word")
def export_word(session_id: int, db: Session = Depends(get_session)):
    """Generate and return a Microsoft Word (.docx) document with the meeting details."""
    from models import ActionItem
    from sqlmodel import select
    from docx import Document
    from docx.shared import Pt, RGBColor
    from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
    import io

    session_obj = db.get(MeetingSession, session_id)
    if not session_obj:
        raise HTTPException(status_code=404, detail="Session not found")

    action_items = db.exec(select(ActionItem).where(ActionItem.session_id == session_id)).all()

    from models import Template
    from sqlmodel import select
    import requests
    import tempfile
    import os
    from docxtpl import DocxTemplate

    template_obj = db.exec(select(Template).where(Template.project_id == session_obj.project_id)).first()

    import datetime
    
    # 1. Parsear Fecha
    formatted_date = session_obj.date
    if session_obj.date and session_obj.date.isdigit():
        dt = datetime.datetime.fromtimestamp(int(session_obj.date) / 1000)
        formatted_date = dt.strftime("%d/%m/%Y %H:%M")
        
    # 2. Traducir Estado
    status_map = {
        "completed": "Completado",
        "processing": "Procesando IA",
        "pending": "Pendiente de Curación"
    }
    status_str = status_map.get(session_obj.status, session_obj.status.capitalize())

    # 3. Eliminar etiquetas en inglés de Fireflies
    clean_summary = ""
    if session_obj.raw_summary:
        clean_summary = session_obj.raw_summary.replace("Notes", "Notas de la Sesión").replace("Action items", "Elementos de Acción")

    buffer = io.BytesIO()
    doc_generated = False
    
    def add_justified_paragraph(d, text, style=None):
        try:
            p = d.add_paragraph(text, style=style)
        except Exception:
            p = d.add_paragraph(text)
        # Removed JUSTIFY alignment as requested by user
        return p

    if template_obj and template_obj.file_path:
        try:
            tmp_path = None
            if template_obj.file_path.startswith("http"):
                r = requests.get(template_obj.file_path, stream=True)
                r.raise_for_status()
                with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp:
                    for chunk in r.iter_content(chunk_size=8192):
                        tmp.write(chunk)
                    tmp_path = tmp.name
                import json
                mapping_blocks = []
                style_config = {}
                if template_obj.mapping_config:
                    try:
                        mapping_blocks = json.loads(template_obj.mapping_config)
                    except Exception:
                        pass
                
                if getattr(template_obj, "style_config", None):
                    try:
                        style_config = json.loads(template_obj.style_config)
                    except Exception:
                        pass
                
                if mapping_blocks and len(mapping_blocks) > 0:
                    from docx import Document
                    from docx.shared import RGBColor, Pt
                    from docx.oxml import OxmlElement
                    from docx.oxml.ns import qn
                    
                    def hex_to_rgb(hex_str):
                        hex_str = hex_str.lstrip('#')
                        if len(hex_str) != 6:
                            return RGBColor(0, 0, 0)
                        return RGBColor(int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16))
                        
                    def apply_font_styles(run, is_heading=False):
                        if style_config.get("fontFamily"):
                            run.font.name = style_config.get("fontFamily")
                        if not is_heading and style_config.get("fontSize"):
                            run.font.size = Pt(int(style_config.get("fontSize")))
                        if is_heading and style_config.get("headingColor"):
                            run.font.color.rgb = hex_to_rgb(style_config.get("headingColor"))
                        elif not is_heading and style_config.get("textColor"):
                            run.font.color.rgb = hex_to_rgb(style_config.get("textColor"))
                            
                    def add_styled_heading(d, text, level, align_center=False):
                        h = d.add_heading(level=level)
                        if align_center:
                            from docx.enum.text import WD_ALIGN_PARAGRAPH
                            h.alignment = WD_ALIGN_PARAGRAPH.CENTER
                            
                        # Set spacing for headers (12pt before, 6pt after)
                        h.paragraph_format.space_before = Pt(12)
                        h.paragraph_format.space_after = Pt(6)
                        
                        run = h.add_run(text)
                        apply_font_styles(run, is_heading=True)
                        return h
                        
                    def add_styled_justified_paragraph(d, text, style=None):
                        # python-docx ignores \n in text, so we split and add multiple runs with breaks
                        p = add_justified_paragraph(d, "", style=style)
                        p.paragraph_format.space_after = Pt(12) # Add spacing between paragraphs
                        
                        # Replace unicode line separators (\u2028) with standard newlines before splitting
                        clean_text = (text or "").replace('\u2028', '\n')
                        lines = clean_text.split('\n')
                        for i, line in enumerate(lines):
                            if line.strip() or i > 0:
                                run = p.add_run(line)
                                apply_font_styles(run, is_heading=False)
                                if i < len(lines) - 1:
                                    run.add_break()
                        return p

                    def apply_cell_text(cell, content):
                        cell.text = ""
                        p = cell.paragraphs[0]
                        clean_content = (content or "").replace('\u2028', '\n')
                        lines = clean_content.split('\n')
                        for i, line in enumerate(lines):
                            if line.strip() or i > 0:
                                run = p.add_run(line)
                                apply_font_styles(run, is_heading=False)
                                if i < len(lines) - 1:
                                    run.add_break()

                    def set_table_borders(table):
                        from docx.oxml import OxmlElement
                        from docx.oxml.ns import qn
                        tblPr = table._element.xpath('w:tblPr')
                        if tblPr:
                            e = OxmlElement('w:tblBorders')
                            for border_name in ['top', 'left', 'bottom', 'right', 'insideH', 'insideV']:
                                border = OxmlElement(f'w:{border_name}')
                                border.set(qn('w:val'), 'single')
                                border.set(qn('w:sz'), '4')
                                border.set(qn('w:space'), '0')
                                border.set(qn('w:color'), '000000')
                                e.append(border)
                            tblPr[0].append(e)

                    def set_cell_bg_color(cell, hex_color):
                        if hex_color:
                            hex_color = hex_color.lstrip('#')
                            shd = OxmlElement('w:shd')
                            shd.set(qn('w:val'), 'clear')
                            shd.set(qn('w:color'), 'auto')
                            shd.set(qn('w:fill'), hex_color)
                            cell._tc.get_or_add_tcPr().append(shd)
                    if tmp_path:
                        doc = Document(tmp_path)
                    else:
                        doc = Document(template_obj.file_path)
                        
                    # Remove trailing empty paragraphs from the template to prevent content from starting too low
                    while len(doc.paragraphs) > 0 and not doc.paragraphs[-1].text.strip():
                        p = doc.paragraphs[-1]._element
                        p.getparent().remove(p)
                        # We also need to remove it from doc.paragraphs so the loop updates correctly
                        # doc.paragraphs is generated dynamically in python-docx, but just to be safe:
                        pass
                    
                    for block_id in mapping_blocks:
                        if block_id == 'meta':
                            title_run = add_styled_heading(doc, "Acta de Reunión", level=0, align_center=True).runs[0]
                            if style_config.get("headingColor"):
                                title_run.font.color.rgb = hex_to_rgb(style_config.get("headingColor"))
                            else:
                                title_run.font.color.rgb = RGBColor(79, 70, 229)
                                
                            add_styled_justified_paragraph(doc, f"Proyecto / Sesión: {session_obj.title}")
                            add_styled_justified_paragraph(doc, f"Fecha: {formatted_date}")
                            add_styled_justified_paragraph(doc, f"Estado: {status_str}")
                            doc.add_paragraph()
                        elif block_id == 'summary' and clean_summary:
                            add_styled_heading(doc, 'Resumen Ejecutivo', level=1)
                            add_styled_justified_paragraph(doc, clean_summary)
                        elif block_id == 'decisions' and session_obj.processed_decisions:
                            add_styled_heading(doc, 'Decisiones Clave', level=1)
                            add_styled_justified_paragraph(doc, session_obj.processed_decisions)
                        elif block_id == 'risks' and session_obj.processed_risks:
                            add_styled_heading(doc, 'Riesgos Identificados', level=1)
                            add_styled_justified_paragraph(doc, session_obj.processed_risks)
                        elif block_id == 'agreements' and session_obj.processed_agreements:
                            add_styled_heading(doc, 'Acuerdos', level=1)
                            add_styled_justified_paragraph(doc, session_obj.processed_agreements)
                        elif block_id == 'attendees':
                            try:
                                att_list = json.loads(session_obj.processed_attendees) if session_obj.processed_attendees else []
                                if att_list:
                                    add_styled_heading(doc, 'Asistentes', level=1)
                                    for att in att_list:
                                        add_styled_justified_paragraph(doc, f"- {att.get('name', '')} ({att.get('role', '')}) - {att.get('entity', '')}")
                            except Exception:
                                pass
                        elif block_id == 'themes':
                            try:
                                thm_list = json.loads(session_obj.processed_themes) if session_obj.processed_themes else []
                                if thm_list:
                                    add_styled_heading(doc, 'Temas y Puntos de Discusión', level=1)
                                    for thm in thm_list:
                                        add_styled_heading(doc, thm.get('theme_name', ''), level=2)
                                        for pt in thm.get('discussion_points', []):
                                            add_styled_justified_paragraph(doc, f"• {pt}")
                            except Exception:
                                pass
                        elif block_id == 'action_items' and action_items:
                            add_styled_heading(doc, 'Tareas (Action Items)', level=1)
                            table = doc.add_table(rows=1, cols=4)
                            try:
                                table.style = 'Table Grid'
                            except Exception:
                                pass
                            
                            set_table_borders(table)
                            
                            hdr_cells = table.rows[0].cells
                            headers = ['Responsable', 'Tarea', 'Descripción', 'Vencimiento']
                            
                            for i, text in enumerate(headers):
                                cell = hdr_cells[i]
                                cell.text = "" # Clean default run
                                run = cell.paragraphs[0].add_run(text)
                                run.bold = True
                                apply_font_styles(run, is_heading=False)
                                if style_config.get("tableHeaderTextColor"):
                                    run.font.color.rgb = hex_to_rgb(style_config.get("tableHeaderTextColor"))
                                if style_config.get("tableHeaderBg"):
                                    set_cell_bg_color(cell, style_config.get("tableHeaderBg"))
                                
                            for item in action_items:
                                row_cells = table.add_row().cells
                                row_cells[0].text = ""
                                apply_font_styles(row_cells[0].paragraphs[0].add_run(f"{item.owner_name} ({item.owner_email})"))
                                
                                row_cells[1].text = ""
                                apply_font_styles(row_cells[1].paragraphs[0].add_run(item.title))
                                
                                row_cells[2].text = ""
                                apply_font_styles(row_cells[2].paragraphs[0].add_run(item.description or ""))
                                
                                row_cells[3].text = ""
                                apply_font_styles(row_cells[3].paragraphs[0].add_run(item.due_date or "Sin fecha"))
                    
                    doc.save(buffer)
                    doc_generated = True
                else:
                    if tmp_path:
                        doc = DocxTemplate(tmp_path)
                    else:
                        doc = DocxTemplate(template_obj.file_path)
    
                    context = {
                        "title": session_obj.title,
                        "date": formatted_date,
                        "status": status_str,
                        "summary": clean_summary,
                        "decisions": session_obj.processed_decisions or "Sin decisiones",
                        "risks": session_obj.processed_risks or "Sin riesgos",
                        "agreements": session_obj.processed_agreements or "Sin acuerdos",
                    }
                    
                    # --- Parse arrays ---
                    # Attendees
                    try:
                        attendees_list = json.loads(session_obj.processed_attendees) if session_obj.processed_attendees else []
                    except Exception:
                        attendees_list = []
                    context["attendees"] = attendees_list
                    
                    # Themes
                    try:
                        themes_list = json.loads(session_obj.processed_themes) if session_obj.processed_themes else []
                    except Exception:
                        themes_list = []
                    context["themes"] = themes_list
                    
                    # Action Items
                    formatted_items = []
                    for item in action_items:
                        formatted_items.append({
                            "owner": item.owner_name,
                            "email": item.owner_email,
                            "title": item.title,
                            "description": item.description or "",
                            "due_date": item.due_date or "Sin fecha",
                        })
                    context["action_items"] = formatted_items
                    
                    doc.render(context)
                    doc.save(buffer)
                    doc_generated = True
            
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception as e:
            print(f"Error usando docxtpl: {e}")
            if 'tmp_path' in locals() and tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)

    if not doc_generated:
        doc = Document()
        
        def add_fallback_paragraph(d, text, style=None):
            p = add_justified_paragraph(d, "", style=style)
            from docx.shared import Pt
            p.paragraph_format.space_after = Pt(12)
            # Replace unicode line separators (\u2028)
            clean_text = (text or "").replace('\u2028', '\n')
            lines = clean_text.split('\n')
            for i, line in enumerate(lines):
                if line.strip() or i > 0:
                    run = p.add_run(line)
                    if i < len(lines) - 1:
                        run.add_break()
            return p
            
        def add_fallback_heading(d, text, level):
            h = d.add_heading(text, level=level)
            from docx.shared import Pt
            h.paragraph_format.space_before = Pt(12)
            h.paragraph_format.space_after = Pt(6)
            return h

        # Title
        title_heading = add_fallback_heading(doc, "", level=0)
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        title_heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
        title_run = title_heading.add_run("Acta de Reunión")
        title_run.font.color.rgb = RGBColor(79, 70, 229)
        
        # Meta
        add_fallback_paragraph(doc, f"Proyecto / Sesión: {session_obj.title}")
        add_fallback_paragraph(doc, f"Fecha: {formatted_date}")
        add_fallback_paragraph(doc, f"Estado: {status_str}")
        doc.add_paragraph()

        # Sections
        if clean_summary:
            add_fallback_heading(doc, 'Resumen Ejecutivo', level=1)
            add_fallback_paragraph(doc, clean_summary)

        if session_obj.processed_decisions:
            add_fallback_heading(doc, 'Decisiones Clave', level=1)
            add_fallback_paragraph(doc, session_obj.processed_decisions)

        if session_obj.processed_risks:
            add_fallback_heading(doc, 'Riesgos Identificados', level=1)
            add_fallback_paragraph(doc, session_obj.processed_risks)

        if session_obj.processed_agreements:
            add_fallback_heading(doc, 'Acuerdos', level=1)
            add_fallback_paragraph(doc, session_obj.processed_agreements)

        # Action Items Table
        add_fallback_heading(doc, 'Tareas (Action Items)', level=1)
        
        if action_items:
            table = doc.add_table(rows=1, cols=4)
            try:
                table.style = 'Table Grid'
            except Exception:
                pass
                
            # Agregamos bordes XML para garantizar que se vean incluso si el estilo falla
            from docx.oxml import OxmlElement
            from docx.oxml.ns import qn
            tblPr = table._element.xpath('w:tblPr')
            if tblPr:
                e = OxmlElement('w:tblBorders')
                for border_name in ['top', 'left', 'bottom', 'right', 'insideH', 'insideV']:
                    border = OxmlElement(f'w:{border_name}')
                    border.set(qn('w:val'), 'single')
                    border.set(qn('w:sz'), '4')
                    border.set(qn('w:space'), '0')
                    border.set(qn('w:color'), '000000')
                    e.append(border)
                tblPr[0].append(e)
            
            hdr_cells = table.rows[0].cells
            hdr_cells[0].text = 'Responsable'
            hdr_cells[1].text = 'Tarea'
            hdr_cells[2].text = 'Descripción'
            hdr_cells[3].text = 'Vencimiento'
            for item in action_items:
                row_cells = table.add_row().cells
                
                # Process each cell text splitting by \n
                def apply_fallback_text(cell, content):
                    cell.text = ""
                    p = cell.paragraphs[0]
                    clean_content = (content or "").replace('\u2028', '\n')
                    lines = clean_content.split('\n')
                    for i, line in enumerate(lines):
                        if line.strip() or i > 0:
                            run = p.add_run(line)
                            if i < len(lines) - 1:
                                run.add_break()
                                
                apply_fallback_text(row_cells[0], f"{item.owner_name}\n({item.owner_email})")
                apply_fallback_text(row_cells[1], item.title)
                apply_fallback_text(row_cells[2], item.description or "")
                apply_fallback_text(row_cells[3], item.due_date or "Sin fecha")
        else:
            add_justified_paragraph(doc, "No se detectaron tareas para esta sesión.")
            
        doc.save(buffer)

    headers = {
        'Content-Disposition': f'attachment; filename="Acta_{session_obj.id}_{session_obj.title[:20]}.docx"'
    }

    return Response(content=buffer.getvalue(), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", headers=headers)
