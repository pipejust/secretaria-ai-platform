# test_db.py
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from backend.database import engine
from sqlmodel import Session, select, func
from backend.models import MeetingSession

try:
    with Session(engine) as db:
        query = select(MeetingSession)
        total_query = select(func.count()).select_from(query.subquery())
        total_items = db.exec(total_query).one()
        print("Total items:", total_items)
except Exception as e:
    print("ERROR:", str(e))
