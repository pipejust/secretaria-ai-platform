from fpdf import FPDF
pdf = FPDF()
pdf.add_page()
pdf.set_font("helvetica", size=12)
pdf.cell(200, 10, text="Hello World")
out = pdf.output()
print("Output type:", type(out))
print("Output size:", len(out))
with open("test.pdf", "wb") as f:
    f.write(out)
