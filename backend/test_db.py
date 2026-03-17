from sqlmodel import Session, select
from database import engine
from models import MeetingSession

with Session(engine) as db:
    session_obj = db.get(MeetingSession, 8)
    if session_obj:
        print(f"Session 8 Title: {session_obj.title}")
        print(f"Session 8 Project ID: {session_obj.project_id}")
    else:
        print("Session 8 not found")
