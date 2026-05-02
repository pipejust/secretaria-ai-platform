import io
import json
from fpdf import FPDF

class CorporatePDFGenerator(FPDF):
    def __init__(self, data: dict):
        super().__init__(orientation='P', unit='mm', format='A4')
        self.data = data
        self.set_auto_page_break(auto=True, margin=15)
        
        self.header_img = None
        self.footer_img = None
        
        template_path = data.get("template_path")
        if template_path:
            self._extract_template_images(template_path)
            
        self.add_page()
        
    def _extract_template_images(self, docx_path):
        import zipfile
        import os
        
        if not docx_path or not os.path.exists(docx_path):
            return
            
        try:
            with zipfile.ZipFile(docx_path, 'r') as z:
                images = [n for n in z.namelist() if n.startswith('word/media/image')]
                images.sort()
                
                if len(images) > 0:
                    ext = images[0].split('.')[-1]
                    out_path = f"/tmp/fpdf_hdr_{os.path.basename(docx_path)}.{ext}"
                    with open(out_path, 'wb') as f:
                        f.write(z.read(images[0]))
                    self.header_img = out_path
                    
                if len(images) > 1:
                    ext = images[1].split('.')[-1]
                    out_path = f"/tmp/fpdf_ftr_{os.path.basename(docx_path)}.{ext}"
                    with open(out_path, 'wb') as f:
                        f.write(z.read(images[1]))
                    self.footer_img = out_path
        except Exception as e:
            print("Could not extract pdf bg images:", e)
        
    def header(self):
        if self.header_img:
            try:
                from PIL import Image
                with Image.open(self.header_img) as img:
                    w, h = img.size
                    ratio = h / w
                    h_mm = 210 * ratio
                self.image(self.header_img, x=0, y=0, w=210, h=h_mm)
                # Adjust Y so text starts below the image banner
                self.set_y(h_mm + 5)
                return
            except Exception:
                pass
                
        # Fallback text header
        self.set_font('helvetica', 'B', 8)
        self.set_text_color(122, 122, 122)
        
        self.set_y(10)
        self.set_x(10)
        self.cell(60, 4, self.data.get("entidad_principal", "Notiva")[:40], ln=1)
        self.set_x(10)
        self.cell(60, 4, self.data.get("entidad_secundaria", "Gestión Integral")[:40], ln=0)
        
        self.set_xy(140, 10)
        self.set_font('helvetica', 'B', 10)
        self.cell(60, 4, self.data.get("titulo_documento", "ACTA DE REUNIÓN")[:30].upper(), align='R', ln=1)
        self.set_font('helvetica', '', 8)
        self.set_x(140)
        self.cell(60, 4, f"V. {self.data.get('version_documento', '1.0')} | {self.data.get('clasificacion', 'Uso Corporativo')}", align='R')
        
        self.ln(15)

    def footer(self):
        if self.footer_img:
            try:
                from PIL import Image
                with Image.open(self.footer_img) as img:
                    w, h = img.size
                    ratio = h / w
                    h_mm = 210 * ratio
                self.image(self.footer_img, x=0, y=297-h_mm, w=210, h=h_mm)
                return
            except Exception:
                pass
                
        self.set_y(-15)
        self.set_font('helvetica', '', 8)
        self.set_text_color(122, 122, 122)
        self.cell(0, 10, f"{self.data.get('entidad_principal', 'Notiva')} | {self.data.get('titulo_documento', 'Acta')} | Generado automáticamente", align='C')

    def _get_color(self, hex_val, default_rgb):
        if not hex_val: return default_rgb
        try:
            h = hex_val.lstrip("#")
            return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
        except Exception:
            return default_rgb

    def _apply_text_theme(self, size_offset=0):
        theme = self.data.get("theme") or {}
        r, g, b = self._get_color(theme.get("textColor", "#111111"), (17, 17, 17))
        self.set_text_color(r, g, b)
        
        try:
            base_size = int(theme.get("fontSize", 10))
        except (TypeError, ValueError):
            base_size = 10
        return base_size + size_offset

    def add_section_bar(self, title):
        theme = self.data.get("theme") or {}

        try:
            heading_margin = float(theme.get("headingMargin", 10))
        except (TypeError, ValueError):
            heading_margin = 10.0
            
        self.ln(heading_margin / 2)
        
        r, g, b = self._get_color(theme.get("headingColor", "#C62828"), (198, 40, 40))
        tc_r, tc_g, tc_b = self._get_color(theme.get("headingTextColor", "#FFFFFF"), (255, 255, 255))
        
        self.set_fill_color(r, g, b)
        
        self.set_text_color(tc_r, tc_g, tc_b)
        self.set_font('helvetica', 'B', 10)
        self.cell(0, 8, f"  {title.upper()}", fill=True, ln=1)
        
        self.ln(heading_margin / 2)
        self.ln(3)

    def add_kv_table(self, rows):
        base_size = self._apply_text_theme()
        self.set_font('helvetica', '', base_size - 1)
        self.set_draw_color(128, 128, 128)
        self.set_line_width(0.2)
        
        col_w = [30, 65, 30, 65]
        for row in rows:
            if len(row) == 2:
                # Merge last 3 columns for v1
                self.set_font('helvetica', 'B', base_size - 1)
                self.cell(col_w[0], 7, row[0], border=1)
                self.set_font('helvetica', '', base_size - 1)
                safe_v = str(row[1]).encode('latin-1', 'replace').decode('latin-1')[:110]
                self.cell(col_w[1] + col_w[2] + col_w[3], 7, safe_v, border=1, ln=1)
            else:
                l1, v1, l2, v2 = row
                self.set_font('helvetica', 'B', base_size - 1)
                self.cell(col_w[0], 7, l1, border=1)
                self.set_font('helvetica', '', base_size - 1)
                safe_v1 = str(v1).encode('latin-1', 'replace').decode('latin-1')[:50]
                self.cell(col_w[1], 7, safe_v1, border=1)
                
                self.set_font('helvetica', 'B', base_size - 1)
                self.cell(col_w[2], 7, l2, border=1)
                self.set_font('helvetica', '', base_size - 1)
                safe_v2 = str(v2).encode('latin-1', 'replace').decode('latin-1')[:50]
                self.cell(col_w[3], 7, safe_v2, border=1, ln=1)
        self.ln(2)

    def add_data_table(self, headers, data_rows, col_widths=None):
        base_size = self._apply_text_theme()
        self.set_fill_color(217, 217, 217)
        self.set_draw_color(128, 128, 128)
        self.set_line_width(0.2)
        
        if not col_widths:
            w = 190 / len(headers)
            col_widths = [w] * len(headers)
            
        theme = self.data.get("theme") or {}
        r, g, b = self._get_color(theme.get("tableHeaderBg", "#D9D9D9"), (217, 217, 217))
        tc_r, tc_g, tc_b = self._get_color(theme.get("tableHeaderTextColor", "#111111"), (17, 17, 17))
        
        try:
            # Requires fpdf2>=2.8.0
            from fpdf.fonts import FontFace
            headings_style = FontFace(fill_color=(r, g, b), color=(tc_r, tc_g, tc_b))
            
            with self.table(col_widths=col_widths, text_align="LEFT", headings_style=headings_style) as table:
                header_row = table.row()
                for header in headers:
                    self.set_font('helvetica', 'B', base_size - 2)
                    header_row.cell(header.encode('latin-1', 'replace').decode('latin-1'))
                
                self.set_font('helvetica', '', base_size - 2)
                for index, item in enumerate(data_rows):
                    data_row = table.row()
                    for i, val in enumerate(item):
                        if i == 0 and not val:
                            val = str(index + 1)
                        safe_val = str(val).encode('latin-1', 'replace').decode('latin-1')
                        data_row.cell(safe_val)
            self.ln(2)
            return
        except AttributeError:
            # Fallback for old FPDF
            self.set_fill_color(r, g, b)
            self.set_text_color(tc_r, tc_g, tc_b)
            self.set_font('helvetica', 'B', base_size - 2)
            for i, header in enumerate(headers):
                self.cell(col_widths[i], 7, header.encode('latin-1', 'replace').decode('latin-1'), border=1, fill=True, align='C')
            self.ln()
            
            self._apply_text_theme()
            
            self.set_font('helvetica', '', base_size - 2)
            for idx, row in enumerate(data_rows):
                for i, val in enumerate(row):
                    if i == 0 and not val:
                        val = str(idx + 1)
                    safe_val = str(val).replace('\n', ' ')[:60].encode('latin-1', 'replace').decode('latin-1')
                    self.cell(col_widths[i], 7, safe_val, border=1)
                self.ln()
            self.ln(2)

    def _parsear_texto_markdown(self, texto: str):
        if not texto:
            return
        lineas = texto.split('\n')
        base_size = self._apply_text_theme()
        for linea in lineas:
            linea_str = linea.strip()
            if not linea_str:
                self.ln(2)
                continue
            
            if linea_str.startswith("### "):
                self.set_font('helvetica', 'B', 10)
                self.multi_cell(0, 5, linea_str[4:].replace("**", "").replace("__", ""))
                self.set_font('helvetica', '', 10)
                self.ln(1)
            elif linea_str.startswith("## "):
                self.set_font('helvetica', 'B', 10)
                self.multi_cell(0, 5, linea_str[3:].replace("**", "").replace("__", ""))
                self.set_font('helvetica', '', 10)
                self.ln(1)
            elif linea_str.startswith("# "):
                self.set_font('helvetica', 'B', 10)
                self.multi_cell(0, 5, linea_str[2:].replace("**", "").replace("__", ""))
                self.set_font('helvetica', '', 10)
                self.ln(1)
            elif linea_str.startswith("- ") or linea_str.startswith("* "):
                original_x = self.get_x()
                self.set_x(original_x + 5)
                # Usamos un guión normal para evitar errores de encoding latin-1
                self.multi_cell(0, 5, "- " + linea_str[2:].replace("**", "").replace("__", ""))
                self.set_x(original_x)
            else:
                self.multi_cell(0, 5, linea_str.replace("**", "").replace("__", ""))

    def render_all(self):
        # Portada Simple
        self.set_font('helvetica', 'B', 10)
        safe_sub = self.data.get("subtitulo_documento", "Asunto no especificado").encode('latin-1', 'replace').decode('latin-1')
        self.cell(0, 6, safe_sub, ln=1)
        self.set_font('helvetica', '', 10)
        self.cell(0, 6, f"Fecha: {self.data.get('fecha_documento', '')}", ln=1)
        self.ln(5)
        
        # Determinar el orden de bloques
        mapping_config = self.data.get("mapping_config", [])
        if not mapping_config:
            # Fallback a estructura estándar y original
            mapping_config = ["meta", "attendees", "summary", "decisions", "risks", "action_items", "approval"]
            
        section_idx = 1
        
        for block in mapping_config:
            block_id = block.get("id") if isinstance(block, dict) else block
            
            if block_id == "meta":
                self.add_section_bar(f"{section_idx}. IDENTIFICACION GENERAL")
                self.add_kv_table([
                    ("Acta No.:", self.data.get("no_acta", ""), "Fecha:", self.data.get("fecha_documento", "")),
                    ("Idioma:", self.data.get("idioma", "Español"), "Proyecto:", self.data.get("proyecto", "General")),
                    ("Asunto:", self.data.get("subtitulo_documento", ""))
                ])
                section_idx += 1
                
            elif block_id == "attendees":
                asistentes = self.data.get("asistentes", [])
                self.add_section_bar(f"{section_idx}. ASISTENTES IDENTIFICADOS")
                if asistentes:
                    filas_asis = [["", a.get("name", "")[:40], a.get("role", "")[:30], a.get("entity", "")[:30]] for a in asistentes]
                    self.add_data_table(["No", "Nombre y Apellidos", "Cargo / Rol", "Entidad"], filas_asis, col_widths=(15, 65, 50, 60))
                else:
                    self.set_font('helvetica', '', 10)
                    self.multi_cell(0, 5, "No hay asistentes registrados.")
                    self.ln(2)
                section_idx += 1
                
            elif block_id == "summary":
                summary = self.data.get("contexto_antecedentes", "").encode('latin-1', 'replace').decode('latin-1')
                self.add_section_bar(f"{section_idx}. RESUMEN EJECUTIVO / CONTEXTO")
                if summary:
                    self.set_font('helvetica', '', 10)
                    self.set_text_color(17, 17, 17)
                    self._parsear_texto_markdown(summary)
                else:
                    self.set_font('helvetica', '', 10)
                    self.multi_cell(0, 5, "No hay resumen ejecutivo.")
                    self.ln(2)
                section_idx += 1
                
            elif block_id == "decisions":
                decisiones = self.data.get("decisiones", "").encode('latin-1', 'replace').decode('latin-1')
                self.add_section_bar(f"{section_idx}. DECISIONES CLAVE")
                if decisiones:
                    self._parsear_texto_markdown(decisiones)
                else:
                    self.set_font('helvetica', '', 10)
                    self.multi_cell(0, 5, "No hay decisiones registradas.")
                    self.ln(2)
                section_idx += 1
                
            elif block_id == "risks":
                riesgos = self.data.get("riesgos", "").encode('latin-1', 'replace').decode('latin-1')
                self.add_section_bar(f"{section_idx}. RIESGOS IDENTIFICADOS")
                if riesgos:
                    self._parsear_texto_markdown(riesgos)
                else:
                    self.set_font('helvetica', '', 10)
                    self.multi_cell(0, 5, "No hay riesgos detectados.")
                    self.ln(2)
                section_idx += 1
                
            elif block_id == "agreements" or block_id == "themes":
                agreements = self.data.get("agreements", "").encode('latin-1', 'replace').decode('latin-1')
                title_bar = "ACUERDOS" if block_id == "agreements" else "ACUERDOS Y TEMAS CLAVE"
                self.add_section_bar(f"{section_idx}. {title_bar}")
                if agreements:
                    self._parsear_texto_markdown(agreements)
                else:
                    self.set_font('helvetica', '', 10)
                    self.multi_cell(0, 5, "No hay acuerdos registrados.")
                    self.ln(2)
                section_idx += 1
                
            elif block_id == "action_items":
                action_items = self.data.get("compromisos", [])
                self.add_section_bar(f"{section_idx}. COMPROMISOS Y TAREAS")
                if action_items:
                    filas_tareas = [["", ai.get("title", ""), ai.get("owner_email", ""), ai.get("due_date", "")] for ai in action_items]
                    self.add_data_table(["No", "Descripcion", "Responsable", "Fecha"], filas_tareas, col_widths=(15, 95, 50, 30))
                else:
                    self.set_font('helvetica', '', 10)
                    self.multi_cell(0, 5, "No hay compromisos pendientes.")
                    self.ln(2)
                section_idx += 1
                
            elif block_id == "approval":
                self.add_section_bar(f"{section_idx}. APROBACION")
                self.set_font('helvetica', '', 10)
                self.multi_cell(0, 5, "Los registros arriba mencionados constituyen el cuerpo del acta inteligenciada.")
                section_idx += 1
        
    def generar_buffer(self) -> io.BytesIO:
        self.render_all()
        pdf_bytes = self.output(dest='S')
        if isinstance(pdf_bytes, str):
            pdf_bytes = pdf_bytes.encode('latin-1')
        elif isinstance(pdf_bytes, bytearray):
            pdf_bytes = bytes(pdf_bytes)
        buffer = io.BytesIO(pdf_bytes)
        return buffer
