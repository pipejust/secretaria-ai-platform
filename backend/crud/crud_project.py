from typing import Optional
from sqlmodel import Session, select
from crud.base import CRUDBase
from models import Project

class CRUDProject(CRUDBase[Project, Project, Project]):
    def get_by_name(self, session: Session, *, name: str) -> Optional[Project]:
        statement = select(Project).where(Project.name == name)
        return session.exec(statement).first()
        
    def get_active_projects(self, session: Session) -> list[Project]:
        statement = select(Project).where(Project.is_active == True)
        return session.exec(statement).all()
        
    def deactivate(self, session: Session, *, db_obj: Project) -> Project:
        db_obj.is_active = False
        session.add(db_obj)
        session.commit()
        return db_obj

project = CRUDProject(Project)
