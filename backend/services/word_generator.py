import os
from docxtpl import DocxTemplate
from typing import Dict, Any

class WordGeneratorService:
    def __init__(self, templates_dir: str = "./templates"):
        self.templates_dir = templates_dir
        if not os.path.exists(self.templates_dir):
            os.makedirs(self.templates_dir)

    def generate_document(self, template_path: str, meeting_data: Dict[str, Any], output_path: str) -> str:
        """
        Genera un acta en Word incrustando los datos extraídos en la plantilla seleccionada.
        """
        local_template_path = template_path
        if template_path.startswith("http://") or template_path.startswith("https://"):
            import hashlib
            safe_name = hashlib.md5(template_path.encode()).hexdigest() + ".docx"
            local_template_path = f"/tmp/{safe_name}"
            if not os.path.exists(local_template_path):
                import urllib.request
                try:
                    req = urllib.request.Request(
                        template_path, 
                        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
                    )
                    with urllib.request.urlopen(req) as response, open(local_template_path, 'wb') as out_file:
                        out_file.write(response.read())
                except Exception as e:
                    raise Exception(f"Error downloading template from Supabase: {e}")
        
        if not os.path.exists(local_template_path):
            raise FileNotFoundError(f"No se encontró la plantilla en {local_template_path}")

        try:
            doc = DocxTemplate(local_template_path)
        except Exception as e:
            raise Exception(f"La plantilla proporcionada no es un documento Word (.docx) válido o está corrupta. Error: {e}")
            
        context = {
            "title": meeting_data.get("title", "Sin Título"),
            "date": meeting_data.get("date", ""),
            "summary": meeting_data.get("summary", "Sin resumen"),
            "decisions": meeting_data.get("decisions", "Ninguna decisión registrada"),
            "risks": meeting_data.get("risks", "Ningún riesgo detectado"),
            "agreements": meeting_data.get("agreements", "Ningún acuerdo"),
            "action_items": meeting_data.get("action_items", [])
        }
        
        doc.render(context)
        doc.save(output_path)
        
        # Post-process: Append visual builder blocks if present
        mapping_config = meeting_data.get("mapping_config") or []
        if mapping_config:
            import docx
            from docx.shared import Pt, RGBColor
            from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
            
            final_doc = docx.Document(output_path)
            
            theme = meeting_data.get("theme") or {}
            font_family = theme.get("fontFamily", "Arial")
            try:
                font_size = int(theme.get("fontSize", 10))
            except:
                font_size = 10
            heading_color_hex = str(theme.get("primaryColor", "#1e293b")).lstrip("#")
            if len(heading_color_hex) != 6:
                heading_color_hex = "1e293b"
                
            hc_r = int(heading_color_hex[0:2], 16)
            hc_g = int(heading_color_hex[2:4], 16)
            hc_b = int(heading_color_hex[4:6], 16)
            
            def add_custom_heading(text):
                final_doc.add_paragraph() # Spacing
                p = final_doc.add_paragraph()
                run = p.add_run(text)
                run.bold = True
                run.font.name = font_family
                run.font.size = Pt(font_size + 2)
                run.font.color.rgb = RGBColor(hc_r, hc_g, hc_b)
                p.paragraph_format.space_after = Pt(6)
                
            def add_custom_paragraph(text, bullet=False):
                if not text:
                    return
                clean_text = str(text).replace("**", "").replace("###", "").replace("##", "")
                for line in clean_text.split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("- "):
                        line = line[2:]
                        bullet = True
                        
                    p = final_doc.add_paragraph()
                    text_to_add = f"• {line}" if bullet else line
                    run = p.add_run(text_to_add)
                    run.font.name = font_family
                    run.font.size = Pt(font_size)
                    p.paragraph_format.space_after = Pt(4)

            # Block Appender
            for block in mapping_config:
                block_id = block.get("id") if isinstance(block, dict) else block
                
                if block_id == "meta":
                    add_custom_heading("1. IDENTIFICACIÓN GENERAL")
                    add_custom_paragraph(f"Acta No.: {meeting_data.get('no_acta', 'ACT-0000')}")
                    add_custom_paragraph(f"Fecha: {meeting_data.get('date', '')}")
                    add_custom_paragraph(f"Asunto: {meeting_data.get('title', '')}")
                elif block_id == "summary":
                    add_custom_heading("RESUMEN EJECUTIVO / CONTEXTO")
                    add_custom_paragraph(meeting_data.get("contexto_antecedentes", ""))
                elif block_id == "attendees":
                    add_custom_heading("LISTA DE ASISTENTES")
                    attendees = meeting_data.get("asistentes", [])
                    if attendees:
                        for a in attendees:
                            name = a.get("name", "")
                            role = a.get("role", "")
                            add_custom_paragraph(f"{name} ({role})", bullet=True)
                    else:
                        add_custom_paragraph("No hay asistentes registrados.")
                elif block_id == "decisions":
                    add_custom_heading("DECISIONES CLAVE")
                    add_custom_paragraph(meeting_data.get("decisiones", ""))
                elif block_id == "risks":
                    add_custom_heading("RIESGOS IDENTIFICADOS")
                    add_custom_paragraph(meeting_data.get("riesgos", ""))
                elif block_id == "agreements" or block_id == "themes":
                    add_custom_heading("ACUERDOS Y TEMAS")
                    add_custom_paragraph(meeting_data.get("agreements", ""))
                elif block_id == "action_items":
                    add_custom_heading("TAREAS Y COMPROMISOS")
                    items = meeting_data.get("compromisos", [])
                    if items:
                        for i, ai in enumerate(items):
                            act_title = ai.get("title", "")
                            act_owner = ai.get("owner_name") or ai.get("owner_email") or "Sin asignar"
                            act_due = ai.get("due_date", "Sin fecha")
                            add_custom_paragraph(f"{i+1}. {act_title} - {act_owner} (Vence: {act_due})")
                    else:
                        add_custom_paragraph("No hay compromisos.")
                        
            final_doc.save(output_path)
            
        return output_path
