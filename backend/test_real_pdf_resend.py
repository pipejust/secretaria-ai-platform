import json
from fpdf import FPDF
import base64

pdf = FPDF()
pdf.add_page()
pdf.set_font("Arial", size=12)
pdf.cell(200, 10, txt="Prueba de PDF para Resend", ln=1, align="C")

# Generate PDF raw bytes
pdf_bytes = pdf.output(dest="S").encode("latin-1")

b64_pdf = base64.b64encode(pdf_bytes).decode('utf-8')

payload_list = {
    "filename": "list_test.pdf",
    "content": list(pdf_bytes)
}

payload_b64 = {
    "filename": "b64_test.pdf",
    "content": b64_pdf
}

print(f"List length: {len(payload_list['content'])}")
print(f"B64 length: {len(payload_b64['content'])}")
print(f"B64 snippet: {payload_b64['content'][:50]}...")

try:
    json.dumps(payload_list)
    print("List serialization successful")
except Exception as e:
    print(f"List serialization failed: {e}")

try:
    json.dumps(payload_b64)
    print("B64 serialization successful")
except Exception as e:
    print(f"B64 serialization failed: {e}")
