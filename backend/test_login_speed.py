import time
from sqlalchemy import create_engine
from sqlmodel import Session, select
from models import User
from config import settings
from auth_utils import verify_password
import os

print("Starting benchmark...")
start = time.time()
engine = create_engine(settings.database_url)
print(f"Engine creation: {time.time() - start:.3f}s")

start = time.time()
with Session(engine) as session:
    print(f"Session open: {time.time() - start:.3f}s")
    
    start = time.time()
    user = session.exec(select(User).where(User.email == "admin@secretaria.ai")).first()
    print(f"DB Query: {time.time() - start:.3f}s")
    
    if user:
        start = time.time()
        pw_str = "admin123" # assuming this is the test pw
        verify_password(pw_str, user.hashed_password)
        print(f"Bcrypt verify: {time.time() - start:.3f}s")
    else:
        print("User not found.")
