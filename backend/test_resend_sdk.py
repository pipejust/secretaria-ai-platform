import resend

resend.api_key = "re_invalidkey123"

attachment_list = {
    "filename": "test.pdf",
    "content": list(b"hello world")
}

attachment_b64 = {
    "filename": "test2.pdf",
    "content": "aGVsbG8gd29ybGQ=" # base64 string
}

params = {
    "from": "test@example.com",
    "to": ["test2@example.com"],
    "subject": "Test",
    "html": "<p>test</p>",
    "attachments": [attachment_list, attachment_b64]
}

try:
    resend.Emails.send(params)
    print("Success") # won't happen due to key
except Exception as e:
    print("Resend Error:", type(e), str(e))
