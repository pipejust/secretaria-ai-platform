import requests

RESEND_API_KEY = "re_invalid" # No need for real key to test JSON serialization

payload = {
    "from": "Acme <onboarding@resend.dev>",
    "to": ["delivered@resend.dev"],
    "subject": "Hello World",
    "html": "<p>it works!</p>",
    "attachments": [
        {
            "filename": "invoice.pdf",
            "content": list(b"hello world")
        }
    ]
}

# we just want to see if requests.post can serialize the payload
import json
try:
    print("Trying to serialize list(bytes)...")
    json.dumps(payload)
    print("Serialization of list(bytes) succeeded!")
except Exception as e:
    print("Serialization failed:", e)

payload2 = {
    "from": "Acme <onboarding@resend.dev>",
    "to": ["delivered@resend.dev"],
    "subject": "Hello World",
    "html": "<p>it works!</p>",
    "attachments": [
        {
            "filename": "invoice.pdf",
            "content": ["hello", "world"] # simulating the list
        }
    ]
}
try:
    print("Trying to serialize list of ints...")
    payload3 = payload.copy()
    payload3["attachments"][0]["content"] = list(b"hello world")
    json.dumps(payload3)
    print("Serialization of list(ints) succeeded!")
except Exception as e:
    print("Serialization failed:", e)

