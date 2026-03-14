import urllib.parse
from sqlalchemy import create_engine, text
url = "postgresql://postgres:Te3WwuDDww5TSwBa@db.oqrzuuwwmsbxhlmkwzps.supabase.co:5432/postgres"
engine = create_engine(url)
with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE meetingsession ADD COLUMN processed_themes VARCHAR DEFAULT '';"))
        conn.commit()
        print("created themes")
    except Exception as e:
        print(f"Error themes: {e}")
    print("Done")
