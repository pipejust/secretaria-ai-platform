import sys
sys.path.append("/Users/felipecortes/.gemini/antigravity/scratch/projects/secretaria/backend")
from backend.services.pdf_generator import CorporatePDFGenerator

data = {
    "entidad_principal": "Notiva",
    "titulo_documento": "ACTA DE REUNIÓN",
    "subtitulo_documento": "Testing PDF",
    "fecha_documento": "10/10/2026",
    "proyecto": "General",
    "asistentes": [],
    "contexto_antecedentes": "Test context",
    "decisiones": "Test decision",
    "riesgos": "Test risks",
    "compromisos": [],
    "mapping_config": None,
    "theme": {}
}

try:
    pdf_gen = CorporatePDFGenerator(data)
    buffer = pdf_gen.generar_buffer()
    with open("/tmp/debug_pdf_empty_map.pdf", "wb") as f:
        f.write(buffer.getvalue())
    print(f"SUCCESS: PDF size {len(buffer.getvalue())} bytes")
except Exception as e:
    import traceback
    print(traceback.format_exc())
