import json
import base64
import requests
from sqlmodel import Session, select, create_engine
from models import IntegrationSetting
import os

engine = create_engine(os.environ.get("DATABASE_URL", "sqlite:///test.db"))

api_key = None
with Session(engine) as session:
    setting = session.exec(select(IntegrationSetting).where(IntegrationSetting.provider_name == 'smtp')).first()
    if setting and setting.is_active:
        config = json.loads(setting.config_json)
        api_key = config.get("apiKey")

if not api_key:
    print("NO API KEY FOUND")
    exit(1)

print("API Key found. Testing Resend API directly...")

headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}

# Create a tiny mock PDF
content_str = "hello world PDF simulation"
content_bytes = content_str.encode('utf-8')
content_b64 = base64.b64encode(content_bytes).decode('utf-8')

# Test 1: Base64 String
payload_b64 = {
    "from": "onboarding@resend.dev",
    "to": "felipe.cortes@moshwasi.com", # Safe destination test
    "subject": "Test B64 Attachment",
    "html": "<p>Test</p>",
    "attachments": [
        {
            "filename": "test1.pdf",
            "content": content_b64,
            "content_type": "application/pdf"
        }
    ]
}

print("\n--- Test 1: Base64 String ---")
res1 = requests.post("https://api.resend.com/emails", headers=headers, json=payload_b64)
print(res1.status_code)
print(res1.text)

# Test 2: List of Ints
payload_ints = {
    "from": "onboarding@resend.dev",
    "to": "felipe.cortes@moshwasi.com", 
    "subject": "Test Int List Attachment",
    "html": "<p>Test</p>",
    "attachments": [
        {
            "filename": "test2.pdf",
            "content": list(content_bytes),
            "content_type": "application/pdf"
        }
    ]
}

print("\n--- Test 2: List of Ints ---")
res2 = requests.post("https://api.resend.com/emails", headers=headers, json=payload_ints)
print(res2.status_code)
print(res2.text)
