import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from database import engine
from sqlmodel import Session, text

try:
    with Session(engine) as session:
        session.exec(text("ALTER TABLE template ADD COLUMN style_config VARCHAR DEFAULT '{}';"))
        session.commit()
        print("Migración de DB exitosa: style_config agregado.")
except Exception as e:
    print(f"Error en migración: {e}")
