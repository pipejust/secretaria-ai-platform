import json
from fpdf import FPDF
import base64
import resend

# Mock the internal request mechanism of resend to intercept the data
original_post = resend.request.Request.perform

def mock_perform(self):
    print("----- INTERCEPTED RESEND PAYLOAD -----")
    if hasattr(self, 'params'):
        print(json.dumps(self.params, indent=2))
        
        # Test serialization of the params to ensure it doesn't break json dumps
        try:
            json.dumps(self.params)
            print("Payload is JSON serializable.")
        except Exception as e:
            print(f"Payload JSON serialization failed: {e}")
            
    else:
        print("No params found on request object.")
    print("--------------------------------------")
    return {"id": "mock_id"}

resend.request.Request.perform = mock_perform
resend.api_key = "test_key"

print("Test 1: String Base64")
try:
    resend.Emails.send({
        "from": "test@test.com",
        "to": "test@test.com",
        "subject": "Test",
        "html": "<p>Test</p>",
        "attachments": [
            {
                "filename": "test.pdf",
                "content": base64.b64encode(b"hello world").decode('utf-8')
            }
        ]
    })
except Exception as e:
    print(f"Exception 1: {e}")

print("\nTest 2: List of Ints")
try:
    resend.Emails.send({
        "from": "test@test.com",
        "to": "test@test.com",
        "subject": "Test",
        "html": "<p>Test</p>",
        "attachments": [
            {
                "filename": "test.pdf",
                "content": list(b"hello world")
            }
        ]
    })
except Exception as e:
    print(f"Exception 2: {e}")

print("\nTest 3: List of Ints with Type")
try:
    resend.Emails.send({
        "from": "test@test.com",
        "to": "test@test.com",
        "subject": "Test",
        "html": "<p>Test</p>",
        "attachments": [
            {
                "filename": "test.pdf",
                "content": list(b"hello world"),
                "content_type": "application/pdf"
            }
        ]
    })
except Exception as e:
    print(f"Exception 3: {e}")

print("\nTest 4: String Base64 with Type")
try:
    resend.Emails.send({
        "from": "test@test.com",
        "to": "test@test.com",
        "subject": "Test",
        "html": "<p>Test</p>",
        "attachments": [
            {
                "filename": "test.pdf",
                "content": base64.b64encode(b"hello world").decode('utf-8'),
                "content_type": "application/pdf"
            }
        ]
    })
except Exception as e:
    print(f"Exception 4: {e}")
