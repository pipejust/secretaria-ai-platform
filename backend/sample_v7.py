import json
from sqlmodel import Session, select
from database import engine
from models import MeetingSession, ActionItem
from services.docx_generator import CorporateDocxGenerator

with Session(engine) as db:
    s = db.get(MeetingSession, 25)
    items = db.exec(select(ActionItem).where(ActionItem.session_id == 25)).all()
    attendees = []
    try: attendees = json.loads(s.processed_attendees or '[]')
    except: pass
    items_data = [
        {'title': i.title, 'owner_name': i.owner_name, 'owner_email': i.owner_email, 'due_date': i.due_date or '', 'priority': i.priority or 'media'}
        for i in items
    ] or [
        {'title': 'Probar autenticación SQL', 'owner_name': 'Carlos Ubaque', 'owner_email': 'carlos@oti.gov.co', 'due_date': '2026-04-15', 'priority': 'alta'},
    ]
    if not attendees:
        attendees = [{'name': 'Felipe Cortés', 'role': 'CTO', 'entity': 'Acten'}]

    data = {
        'no_acta': f'ACTA-{s.id:04}',
        'fecha_documento': s.date,
        'idioma': 'Español',
        'proyecto': 'ANH — Acuerdo Marco',
        'subtitulo_documento': s.title,
        'titulo_documento': 'ACTA DE REUNIÓN',
        'entidad_principal': 'Acten',
        'asistentes': attendees,
        'contexto_antecedentes': s.raw_summary or '',
        'decisiones': s.processed_decisions or '',
        'riesgos': s.processed_risks or '',
        'acuerdos': s.processed_agreements or '',
        'compromisos': items_data,
        'theme': {},
    }

g = CorporateDocxGenerator(data); buf = g.generar_buffer()
open('/tmp/sample_v7.docx','wb').write(buf.getvalue()); print(f'DOCX={len(buf.getvalue())}b')
