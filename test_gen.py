import logging
import sys

# Agregamos la ruta del backend
sys.path.append("/Users/felipecortes/.gemini/antigravity/scratch/projects/secretaria/backend")

from models import MeetingSession, ActionItem, Template
from sqlmodel import Session, select, create_engine
import json
import os
import urllib.request
from docxtpl import DocxTemplate

logging.basicConfig(level=logging.INFO)

# Connect to the local SQLite DB
engine = create_engine("sqlite:////Users/felipecortes/.gemini/antigravity/scratch/projects/secretaria/backend/notiva.db")
with Session(engine) as db:
    session_obj = db.exec(select(MeetingSession).order_by(MeetingSession.id.desc())).first()
    action_items = db.exec(select(ActionItem).where(ActionItem.session_id == session_obj.id)).all() if session_obj else []

    if session_obj:
        from routers.sessions_upload import __build_corporate_data
        data = __build_corporate_data(session_obj, action_items, db)
        print("Data keys derived from __build_corporate_data:")
        print(data.keys())

        # PDF Test
        from services.pdf_generator import CorporatePDFGenerator
        try:
            pdf_gen = CorporatePDFGenerator(data)
            buffer = pdf_gen.generar_buffer()
            print(f"PDF generated successfully, size: {len(buffer.getvalue())}")
            with open("/tmp/debug_test.pdf", "wb") as f:
                f.write(buffer.getvalue())
        except Exception as e:
            import traceback
            print(f"PDF generation failed: {e}\n{traceback.format_exc()}")
            
        # Word
        from services.word_generator import WordGeneratorService
        mapping_config = data.get("mapping_config") or []
        theme = data.get("theme") or {}
        meeting_data = {
            "title": session_obj.title,
            "date": data["fecha_documento"],
            "summary": session_obj.raw_summary,
            "decisions": session_obj.processed_decisions,
            "risks": session_obj.processed_risks,
            "agreements": session_obj.processed_agreements,
            "action_items": [],
            "mapping_config": mapping_config,
            "theme": theme,
            "asistentes": data["asistentes"],
            "no_acta": data.get("no_acta", "")
        }
        
        template_obj = db.exec(select(Template).where(Template.project_id == session_obj.project_id)).first()
        if template_obj and template_obj.file_path:
            print("Template target:", template_obj.file_path)
            # we need to simulate Word generation
            wg = WordGeneratorService(templates_dir="/tmp")
            try:
                wg.generate_document(template_obj.file_path, meeting_data, f"/tmp/debug_test.docx")
                print("Word document generated manually.")
            except Exception as e:
                import traceback
                print(f"Word failed: {e}\n{traceback.format_exc()}")
