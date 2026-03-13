from database import engine
from sqlmodel import Session, select
from models import User

def check_admin():
    with Session(engine) as session:
        admin = session.exec(select(User).where(User.email == "admin@secretaria.ai")).first()
        if admin:
            hash_str = admin.hashed_password
            # bcrypt hashes look like: $2b$12$.... where 12 is the work factor (rounds)
            cost_factor = hash_str.split('$')[2] 
            print(f"Admin hash cost factor is: {cost_factor}")
        else:
            print("Admin not found.")

if __name__ == "__main__":
    check_admin()
