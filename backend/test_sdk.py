import os
import resend
import base64

resend.api_key = "re_123456789"

try:
    print("Testing string b64")
    r = resend.Emails.send({
        "from": "onboarding@resend.dev",
        "to": "delivered@resend.dev",
        "subject": "hello world",
        "html": "<strong>it works!</strong>",
        "attachments": [
            {
                "filename": "invoice.pdf",
                "content": "SGVsbG8gV29ybGQ=" # Base64 for "Hello World"
            }
        ]
    })
    print(r)
except Exception as e:
    print(f"B64 Exception: {e}")

try:
    print("Testing list of ints")
    r = resend.Emails.send({
        "from": "onboarding@resend.dev",
        "to": "delivered@resend.dev",
        "subject": "hello world",
        "html": "<strong>it works!</strong>",
        "attachments": [
            {
                "filename": "invoice.pdf",
                "content": list(b"Hello World")
            }
        ]
    })
    print(r)
except Exception as e:
    print(f"Int List Exception: {e}")
