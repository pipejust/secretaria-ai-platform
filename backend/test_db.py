from sqlmodel import Session, select, create_engine
import os
from models import Template

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./secretaria.db") # O la url de supbase
engine = create_engine(DATABASE_URL)

try:
    with Session(engine) as session:
        templates = session.exec(select(Template)).all()
        for t in templates:
            print(t.id, t.name, t.file_path, t.mapping_config)
except Exception as e:
    print(e)
