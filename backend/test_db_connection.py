import os
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

url = "postgresql://postgres:Te3WwuDDww5TSwBa@db.oqrzuuwwmsbxhlmkwzps.supabase.co:5432/postgres"

print(f"\n--- Testing Direct Connection ---")
try:
    engine = create_engine(url, connect_args={'connect_timeout': 10})
    with engine.connect() as connection:
        print("✅ Success! Connected to the database.")
except OperationalError as e:
    print(f"❌ Failed: {e}")
except Exception as e:
    print(f"❌ Error: {e}")
