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
            from docx.shared import Pt, RGBColor, Cm
            from docx.oxml import OxmlElement
            from docx.oxml.ns import qn
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
            
            from services.docx_table_utils import set_cell_background_color, set_table_borders
            
            def add_custom_heading(text):
                final_doc.add_paragraph() # Spacing
                table = final_doc.add_table(rows=1, cols=1)
                table.autofit = False
                table.columns[0].width = Cm(16.5)
                
                cell = table.cell(0, 0)
                set_cell_background_color(cell, heading_color_hex)
                
                # remove borders
                tbl = table._tbl
                tblPr = tbl.tblPr
                tblBorders = OxmlElement('w:tblBorders')
                for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
                    elem = OxmlElement(f'w:{edge}')
                    elem.set(qn('w:val'), 'none')
                    tblBorders.append(elem)
                tblPr.append(tblBorders)
                
                p = cell.paragraphs[0]
                run = p.add_run(f"  {text.upper()}")
                run.bold = True
                run.font.name = font_family
                run.font.size = Pt(font_size + 1)
                run.font.color.rgb = RGBColor(255, 255, 255)
                final_doc.add_paragraph()
                
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

            def build_data_table(headers, data_rows):
                if not data_rows:
                    return
                table = final_doc.add_table(rows=len(data_rows)+1, cols=len(headers))
                set_table_borders(table)
                
                # Header
                for i, header in enumerate(headers):
                    cell = table.cell(0, i)
                    set_cell_background_color(cell, "D9D9D9")
                    p = cell.paragraphs[0]
                    run = p.add_run(header)
                    run.bold = True
                    run.font.name = font_family
                    run.font.size = Pt(font_size - 1)
                    
                # Data
                for r_idx, row_data in enumerate(data_rows):
                    for c_idx, val in enumerate(row_data):
                        # auto-numbering
                        if c_idx == 0 and not val:
                            val = str(r_idx + 1)
                        cell = table.cell(r_idx + 1, c_idx)
                        p = cell.paragraphs[0]
                        run = p.add_run(str(val))
                        run.font.name = font_family
                        run.font.size = Pt(font_size - 1)
                final_doc.add_paragraph()

            # Block Appender
            for block in mapping_config:
                block_id = block.get("id") if isinstance(block, dict) else block
                
                if block_id == "meta":
                    add_custom_heading("1. IDENTIFICACIÓN GENERAL")
                    table = final_doc.add_table(rows=3, cols=4)
                    set_table_borders(table)
                    
                    datos = [
                        ("Acta No.:", meeting_data.get("no_acta", ""), "Fecha:", meeting_data.get("fecha_documento", "")),
                        ("Idioma:", meeting_data.get("idioma", "Español"), "Proyecto:", meeting_data.get("proyecto", "General")),
                        ("Asunto:", meeting_data.get("subtitulo_documento", ""), "", "")
                    ]
                    for i, (l1, v1, l2, v2) in enumerate(datos):
                        row = table.rows[i]
                        p1 = row.cells[0].paragraphs[0]
                        r1 = p1.add_run(l1)
                        r1.bold = True
                        r1.font.name = font_family
                        r1.font.size = Pt(font_size - 1)
                        
                        p2 = row.cells[1].paragraphs[0]
                        r2 = p2.add_run(v1)
                        r2.font.name = font_family
                        r2.font.size = Pt(font_size - 1)
                        
                        if l2:
                            p3 = row.cells[2].paragraphs[0]
                            r3 = p3.add_run(l2)
                            r3.bold = True
                            r3.font.name = font_family
                            r3.font.size = Pt(font_size - 1)
                            
                            p4 = row.cells[3].paragraphs[0]
                            r4 = p4.add_run(v2)
                            r4.font.name = font_family
                            r4.font.size = Pt(font_size - 1)
                        else:
                            row.cells[1].merge(row.cells[3])
                    final_doc.add_paragraph()

                elif block_id == "summary":
                    add_custom_heading("RESUMEN EJECUTIVO / CONTEXTO")
                    add_custom_paragraph(meeting_data.get("contexto_antecedentes", ""))
                elif block_id == "attendees":
                    add_custom_heading("LISTA DE ASISTENTES")
                    attendees = meeting_data.get("asistentes", [])
                    if attendees:
                        filas = [["", a.get("name", ""), a.get("role", ""), a.get("entity", "")] for a in attendees]
                        build_data_table(["No", "Nombre y Apellidos", "Cargo / Rol", "Entidad"], filas)
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
                        filas = [["", ai.get("title", ""), ai.get("owner_name") or ai.get("owner_email") or "", ai.get("due_date", "")] for ai in items]
                        build_data_table(["No", "Descripción", "Responsable", "Fecha"], filas)
                    else:
                        add_custom_paragraph("No hay compromisos.")
                        
            final_doc.save(output_path)
            
        return output_path
