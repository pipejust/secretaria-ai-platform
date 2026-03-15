import sys
import os
from dotenv import load_dotenv

load_dotenv()

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from services.supabase_service import upload_file_to_bucket

# Create dummy
with open("dummy.docx", "wb") as f:
    f.write(b"dummy")

try:
    url = upload_file_to_bucket("templates", "dummy.docx", "test/dummy.docx")
    print("SUCCESS: ", url)
except Exception as e:
    print("FAILED: ", e)
