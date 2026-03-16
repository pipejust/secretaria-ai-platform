import json
from fpdf import FPDF
import base64

def hex_to_rgb_tuple(hex_str: str) -> tuple:
    hex_str = hex_str.lstrip('#')
    if len(hex_str) != 6:
        return (0, 0, 0)
    return (int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16))

style_config = {}
color_text = hex_to_rgb_tuple(style_config.get("textColor", "#1f2937"))
color_heading = hex_to_rgb_tuple(style_config.get("headingColor", "#4f46e5"))

try:
    pdf = FPDF()
    pdf.add_page()
    
    # Titulo Principal
    pdf.set_text_color(*color_heading)
    pdf.set_font("helvetica", "B", 18)
    pdf.cell(0, 10, "Secretaria AI - Resumen de Sesion", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(10)
    
    # Meta Info
    pdf.set_text_color(*color_text)
    pdf.set_font("helvetica", "B", 12)
    safe_title = "Mi Sesión de Prueba".encode('latin-1', 'replace').decode('latin-1')
    pdf.cell(0, 8, f"Proyecto: {safe_title}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, f"Fecha: 2026-03-16", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    
    # Resumen Ejecutivo
    safe_summary = "Este es un resumen de prueba.".encode('latin-1', 'replace').decode('latin-1')
    pdf.set_fill_color(*color_heading)
    pdf.set_text_color(255, 255, 255) # Texto blanco sobre fondo de color
    pdf.set_font("helvetica", "B", 14)
    # Add some padding and solid fill
    pdf.cell(0, 10, "Resumen Ejecutivo:", new_x="LMARGIN", new_y="NEXT", fill=True)
    pdf.ln(3)
    
    pdf.set_text_color(*color_text)
    pdf.set_font("helvetica", "", 11)
    pdf.multi_cell(0, 6, safe_summary)
    pdf.ln(5)
    
    pdf_bytes = list(bytes(pdf.output()))
    print("PDF GENERATION SUCCESS!")
    
except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"Error generating PDF summary: {e}")
