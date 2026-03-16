import resend
resend.api_key = "re_invalidkey123"

attachment_dict = {
    "filename": "test.pdf",
    "content": list(b"hello world")
}

params = {
    "from": "test@example.com",
    "to": ["test2@example.com"],
    "subject": "Test",
    "html": "<p>test</p>",
    "attachments": [attachment_dict]
}

try:
    resend.Emails.send(params)
except Exception as e:
    print("Resend Error:", e)
