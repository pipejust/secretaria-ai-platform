import sys
sys.path.append("/Users/felipecortes/.gemini/antigravity/scratch/projects/secretaria/backend")

from backend.services.pdf_generator import CorporatePDFGenerator
from backend.services.word_generator import WordGeneratorService
from backend.routers.sessions_upload import __build_corporate_data

class MockSession:
    def __init__(self):
        self.id = 11
        self.title = "Mock Session"
        self.date = "2026-03-21T10:00:00Z"
        self.raw_summary = "This is the executive summary\n* Point 1\n* Point 2"
        self.processed_decisions = "Decided to move forward"
        self.processed_risks = "No critical risks at the moment."
        self.processed_agreements = "Agreed on budget."
        self.processed_attendees = '[{"name": "Felipe Cortes", "role": "CEO", "entity": "Notiva"}]'
        self.project_id = 1
        self.language = "Español"

class MockActionItem:
    def __init__(self):
        self.title = "Do the thing"
        self.owner_name = "Felipe"
        self.owner_email = "felipe@notiva.com"
        self.due_date = "2026-04-01"

session_obj = MockSession()
action_items = [MockActionItem()]

# Here we mock db=None, so __build_corporate_data uses defaults for project/template
data = __build_corporate_data(session_obj, action_items, db=None)

# Inject mapping config that the visual builder would provide
data["mapping_config"] = [
    {"id": "meta", "label": "Cabecera"},
    {"id": "summary", "label": "Resumen Ejecutivo"},
    {"id": "decisions", "label": "Decisiones"},
    {"id": "risks", "label": "Riesgos"},
    {"id": "action_items", "label": "Compromisos"},
]
# Theme uses default

print("PDF GENERATION TEST:")
try:
    pdf_gen = CorporatePDFGenerator(data)
    buffer = pdf_gen.generar_buffer()
    with open("/tmp/debug_test.pdf", "wb") as f:
        f.write(buffer.getvalue())
    print(f"SUCCESS: PDF size {len(buffer.getvalue())} bytes")
except Exception as e:
    import traceback
    print(traceback.format_exc())

print("\nWORD GENERATION TEST:")
meeting_data = {
    "title": session_obj.title,
    "date": data["fecha_documento"],
    "summary": session_obj.raw_summary,
    "decisions": session_obj.processed_decisions,
    "risks": session_obj.processed_risks,
    "agreements": session_obj.processed_agreements,
    "action_items": data["compromisos"],
    "mapping_config": data["mapping_config"],
    "theme": data.get("theme", {}),
    "asistentes": data["asistentes"],
    "no_acta": data.get("no_acta", "")
}

try:
    wg = WordGeneratorService(templates_dir="/tmp")
    # For testing, we create a dummy docx as template
    import docx
    dummy_doc = docx.Document()
    dummy_doc.add_paragraph("This is my company logo template.")
    dummy_doc.save("/tmp/dummy_template.docx")
    
    out_path = wg.generate_document("/tmp/dummy_template.docx", meeting_data, "/tmp/debug_test.docx")
    print(f"SUCCESS: Word generated at {out_path}")
except Exception as e:
    import traceback
    print(traceback.format_exc())
