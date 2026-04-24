import os
import sys

# Append the current directory to sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlmodel import create_engine, Session, select
from models import MeetingSession
from config import settings

engine = create_engine(settings.database_url)

def fix_stuck_sessions():
    with Session(engine) as session:
        statement = select(MeetingSession).where(MeetingSession.status == "processing")
        stuck_sessions = session.exec(statement).all()
        
        print(f"Found {len(stuck_sessions)} stuck sessions.")
        
        for s in stuck_sessions:
            print(f"Setting session {s.id} (title: {s.title}) from processing to pending.")
            s.status = "pending"
            session.add(s)
            
        session.commit()
        print("Done fixing sessions.")

if __name__ == "__main__":
    fix_stuck_sessions()
