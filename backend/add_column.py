import os
from sqlalchemy import text, create_engine
from dotenv import load_dotenv

load_dotenv()

# We use the new IPv4 URL that we confirmed works
url = os.environ.get("DATABASE_URL")
if not url:
    url = "postgresql://postgres:Te3WwuDDww5TSwBa@db.oqrzuuwwmsbxhlmkwzps.supabase.co:5432/postgres"

engine = create_engine(url)

with engine.connect() as conn:
    print("Running ALTER TABLE...")
    conn.execute(text("ALTER TABLE project ADD COLUMN IF NOT EXISTS description VARCHAR DEFAULT '';"))
    conn.commit()
    print("Column added successfully.")
