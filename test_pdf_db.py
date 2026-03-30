import sys
sys.path.append("/Users/felipecortes/.gemini/antigravity/scratch/projects/secretaria/backend")
from backend.services.pdf_generator import CorporatePDFGenerator

class DebugPDFGenerator(CorporatePDFGenerator):
    def add_section_bar(self, title):
        print(f"DEBUG: add_section_bar called with {title}")
        super().add_section_bar(title)

    def add_kv_table(self, rows):
        print(f"DEBUG: add_kv_table called")
        super().add_kv_table(rows)

    def _parsear_texto_markdown(self, texto):
        print(f"DEBUG: _parsear_texto_markdown called with len {len(texto)}")
        super()._parsear_texto_markdown(texto)

data = {
    "entidad_principal": "Notiva",
    "titulo_documento": "ACTA DE REUNIÓN",
    "subtitulo_documento": "Testing PDF DB",
    "fecha_documento": "10/10/2026",
    "proyecto": "General",
    "asistentes": [{"name": "Foo", "role": "Bar", "entity": "Baz"}],
    "contexto_antecedentes": "Test context sumamry",
    "decisiones": "Test decision",
    "riesgos": "Test risks",
    "compromisos": [{"title": "Do thing", "owner_email": "hello@foo.com", "due_date": "2026-05-01"}],
    "mapping_config": ["meta","attendees","summary"],
    "theme": {"fontFamily":"Roboto","fontSize":12,"textColor":"#5b10b7","headingColor":"#df1616","tableHeaderBg":"#39f22c","tableHeaderTextColor":"#ffffff"}
}

try:
    pdf_gen = DebugPDFGenerator(data)
    buffer = pdf_gen.generar_buffer()
    with open("/tmp/debug_pdf_db.pdf", "wb") as f:
        f.write(buffer.getvalue())
    print(f"SUCCESS: PDF size {len(buffer.getvalue())} bytes")
except Exception as e:
    import traceback
    print(traceback.format_exc())
