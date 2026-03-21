import sys
sys.path.append("/Users/felipecortes/.gemini/antigravity/scratch/projects/secretaria/backend")
from database import engine
from models import MeetingSession, Template, Project
from sqlmodel import Session, select

try:
    with Session(engine) as db:
        sessions = db.exec(select(MeetingSession)).all()
        print(f"Total sessions: {len(sessions)}")
        for s in sessions[-5:]:
            print(f"Session {s.id}: Title='{s.title}', ProjectID={s.project_id}")
            
        templates = db.exec(select(Template)).all()
        print(f"\nTotal templates: {len(templates)}")
        for t in templates:
            print(f"Template {t.id}: Name='{t.name}', ProjectID={t.project_id}")
            print(f"   style_config: {t.style_config}")
            print(f"   mapping_config: {t.mapping_config}")
            print(f"   file_path: {t.file_path}")
            
except Exception as e:
    import traceback
    print(traceback.format_exc())
