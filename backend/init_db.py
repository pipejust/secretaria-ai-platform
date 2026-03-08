from auth_utils import get_password_hash
import os
from pydantic import BaseModel
from sqlmodel import Session, select
from database import engine, SQLModel
from models import User, Role

def init_db():
    print("Inicializando Base de Datos e inyectando roles base...")
    SQLModel.metadata.create_all(engine)
    
    with Session(engine) as session:
        # Revisar Roles
        admin_role = session.exec(select(Role).where(Role.name == "admin")).first()
        if not admin_role:
            admin_role = Role(name="admin", description="Acceso Total al Sistema")
            session.add(admin_role)
            
        validator_role = session.exec(select(Role).where(Role.name == "validator")).first()
        if not validator_role:
            validator_role = Role(name="validator", description="Validador y Curador de Actas")
            session.add(validator_role)
            
        session.commit()
        session.refresh(admin_role)
        
        # Revisar Usuario Inicial
        admin_user = session.exec(select(User).where(User.email == "admin@secretaria.ai")).first()
        if not admin_user:
            print("Creando usuario admin@secretaria.ai (pass: Admin123!)")
            hashedpw = get_password_hash("Admin123!")
            new_admin = User(
                email="admin@secretaria.ai", 
                full_name="System Admin", 
                hashed_password=hashedpw,
                role_id=admin_role.id
            )
            session.add(new_admin)
            session.commit()
            
if __name__ == "__main__":
    init_db()
