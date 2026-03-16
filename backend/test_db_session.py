from sqlmodel import Session, select, create_engine
from models import MeetingSession
import os

engine = create_engine(os.environ.get("DATABASE_URL", "sqlite:///test.db"))

with Session(engine) as session:
    try:
        stmt = select(MeetingSession).where(MeetingSession.id == 3)
        res = session.exec(stmt).first()
        if res:
            print("Title:", repr(res.title))
            print("Summary:", repr(res.raw_summary))
        else:
            print("Session 3 not found")
    except Exception as e:
        print("DB ERROR:", e)
