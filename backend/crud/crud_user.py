from typing import Optional
from sqlmodel import Session, select
from crud.base import CRUDBase
from models import User

class CRUDUser(CRUDBase[User, User, User]):
    def get_by_email(self, session: Session, *, email: str) -> Optional[User]:
        statement = select(User).where(User.email == email)
        return session.exec(statement).first()
        
    def get_active_users(self, session: Session) -> list[User]:
        statement = select(User).where(User.is_active == True)
        return session.exec(statement).all()
        
    def deactivate(self, session: Session, *, db_obj: User) -> User:
        db_obj.is_active = False
        session.add(db_obj)
        session.commit()
        return db_obj

user = CRUDUser(User)
