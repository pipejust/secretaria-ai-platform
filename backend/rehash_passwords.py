from database import engine
from sqlmodel import Session, select
from models import User
from auth_utils import get_password_hash

def rehash_users():
    with Session(engine) as session:
        users = session.exec(select(User)).all()
        for user in users:
            # Check if this user needs rehashing
            cost_factor = user.hashed_password.split('$')[2] 
            if cost_factor != "04":
                print(f"Rehashing user {user.email} from cost factor {cost_factor} to 04...")
                # Note: We cannot recover the original plaintext password.
                # However, for admin@secretaria.ai we know it is 'Admin123!'
                if user.email == "admin@secretaria.ai":
                    user.hashed_password = get_password_hash("Admin123!")
                elif user.email == "admin@colpensiones.com":
                    user.hashed_password = get_password_hash("admin")
                elif user.email == "felipe@colpensiones.com":
                    user.hashed_password = get_password_hash("felipe123")
                else:
                    # Generic placeholder
                    user.hashed_password = get_password_hash("Temporal123!")
                    print(f"Assigning Temporal123! to {user.email} since we don't know the plain password.")
                session.add(user)
        session.commit()
        print("Success: Re-hashing completed.")

if __name__ == "__main__":
    rehash_users()
