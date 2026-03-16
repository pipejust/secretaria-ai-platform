import base64
from fpdf import FPDF

pdf = FPDF()
pdf.add_page()
pdf.set_font("helvetica", size=12)
pdf.cell(200, 10, txt="Test PDF", ln=1, align="C")
pdf_bytes = pdf.output()

print("Type of pdf.output():", type(pdf_bytes))
