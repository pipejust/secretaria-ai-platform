import sys
sys.path.append("/Users/felipecortes/.gemini/antigravity/scratch/projects/secretaria/backend")
from backend.services.word_generator import WordGeneratorService

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
    gen = WordGeneratorService(templates_dir="/tmp")
    out = gen.generate_document("/tmp/test_colp.docx", data, "/tmp/debug_docx_db.docx")
    print(f"SUCCESS: Word generated at {out}")
except Exception as e:
    import traceback
    print(f"FAILED Word Generation:")
    print(traceback.format_exc())
