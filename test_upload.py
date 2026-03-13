import urllib.request
import urllib.parse
import urllib.error

url = "https://secretaria-ai-platform.onrender.com/api/sessions/upload"
boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"

body = bytearray()
body.extend(f"--{boundary}\r\n".encode())
body.extend(b'Content-Disposition: form-data; name="title"\r\n\r\n')
body.extend(b'Test Audio\r\n')
body.extend(f"--{boundary}\r\n".encode())
body.extend(b'Content-Disposition: form-data; name="file"; filename="dummy.mp3"\r\n')
body.extend(b'Content-Type: audio/mpeg\r\n\r\n')
body.extend(b'Fake audio content\r\n')
body.extend(f"--{boundary}--\r\n".encode())

req = urllib.request.Request(url, data=bytes(body), method='POST')
req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')

try:
    with urllib.request.urlopen(req) as response:
        print("Status", response.status)
        print(response.read().decode())
except urllib.error.HTTPError as e:
    print("Error HTTP", e.code)
    print("Detail:", e.read().decode())
except Exception as e:
    print("Error:", e)
