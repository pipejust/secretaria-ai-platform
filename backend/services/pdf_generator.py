import io
import json
from fpdf import FPDF

class CorporatePDFGenerator(FPDF):
    def __init__(self, data: dict):
        super().__init__(orientation='P', unit='mm', format='A4')
        self.data = data
        self.set_auto_page_break(auto=True, margin=15)
        self.add_page()
        
    def header(self):
        # 3 columns header like DOCX
        self.set_font('helvetica', 'B', 8)
        self.set_text_color(122, 122, 122)
        
        self.set_y(10)
        self.set_x(10)
        self.cell(60, 4, self.data.get("entidad_principal", "Notiva")[:40], ln=1)
        self.set_x(10)
        self.cell(60, 4, self.data.get("entidad_secundaria", "Gestión Integral")[:40], ln=0)
        
        self.set_xy(75, 10)
        self.cell(60, 8, "[ ESPACIO LOGO ]", align='C')
        
        self.set_xy(140, 10)
        self.set_font('helvetica', 'B', 10)
        self.cell(60, 4, self.data.get("titulo_documento", "ACTA DE REUNIÓN")[:30].upper(), align='R', ln=1)
        self.set_font('helvetica', '', 8)
        self.set_x(140)
        self.cell(60, 4, f"V. {self.data.get('version_documento', '1.0')} | {self.data.get('clasificacion', 'Uso Corporativo')}", align='R')
        
        self.ln(15)

    def footer(self):
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

    def add_section_bar(self, title):
        self.ln(5)
        theme = self.data.get("theme") or {}
        r, g, b = self._get_color(theme.get("headingColor", "#C62828"), (198, 40, 40))
        self.set_fill_color(r, g, b)
        
        self.set_text_color(255, 255, 255)
        self.set_font('helvetica', 'B', 10)
        self.cell(0, 8, f"  {title.upper()}", fill=True, ln=1)
        self.ln(3)

    def add_kv_table(self, rows):
        self.set_font('helvetica', '', 9)
        self.set_text_color(17, 17, 17)
        self.set_draw_color(128, 128, 128)
        self.set_line_width(0.2)
        
        col_w = [30, 65, 30, 65]
        for row in rows:
            if len(row) == 2:
                # Merge last 3 columns for v1
                self.set_font('helvetica', 'B', 9)
                self.cell(col_w[0], 7, row[0], border=1)
                self.set_font('helvetica', '', 9)
                safe_v = str(row[1]).encode('latin-1', 'replace').decode('latin-1')[:110]
                self.cell(col_w[1] + col_w[2] + col_w[3], 7, safe_v, border=1, ln=1)
            else:
                l1, v1, l2, v2 = row
                self.set_font('helvetica', 'B', 9)
                self.cell(col_w[0], 7, l1, border=1)
                self.set_font('helvetica', '', 9)
                safe_v1 = str(v1).encode('latin-1', 'replace').decode('latin-1')[:50]
                self.cell(col_w[1], 7, safe_v1, border=1)
                
                self.set_font('helvetica', 'B', 9)
                self.cell(col_w[2], 7, l2, border=1)
                self.set_font('helvetica', '', 9)
                safe_v2 = str(v2).encode('latin-1', 'replace').decode('latin-1')[:50]
                self.cell(col_w[3], 7, safe_v2, border=1, ln=1)
        self.ln(2)

    def add_data_table(self, headers, data_rows, col_widths=None):
        self.set_fill_color(217, 217, 217)
        self.set_text_color(17, 17, 17)
        self.set_draw_color(128, 128, 128)
        self.set_line_width(0.2)
        
        if not col_widths:
            w = 190 / len(headers)
            col_widths = [w] * len(headers)
            
        try:
            # Requires fpdf2>=2.8.0
            with self.table(col_widths=col_widths, text_align="LEFT") as table:
                header_row = table.row()
                for header in headers:
                    self.set_font('helvetica', 'B', 8)
                    header_row.cell(header.encode('latin-1', 'replace').decode('latin-1'))
                
                self.set_font('helvetica', '', 8)
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
            self.set_font('helvetica', 'B', 8)
            for i, header in enumerate(headers):
                self.cell(col_widths[i], 7, header.encode('latin-1', 'replace').decode('latin-1'), border=1, fill=True, align='C')
            self.ln()
            
            self.set_font('helvetica', '', 8)
            for idx, row in enumerate(data_rows):
                for i, val in enumerate(row):
                    if i == 0 and not val:
                        val = str(idx + 1)
                    safe_val = str(val).replace('\n', ' ')[:60].encode('latin-1', 'replace').decode('latin-1')
                    self.cell(col_widths[i], 7, safe_val, border=1)
                self.ln()
            self.ln(2)

    def render_all(self):
        # Portada Simple
        self.set_font('helvetica', 'B', 10)
        safe_sub = self.data.get("subtitulo_documento", "Asunto no especificado").encode('latin-1', 'replace').decode('latin-1')
        self.cell(0, 6, safe_sub, ln=1)
        self.set_font('helvetica', '', 10)
        self.cell(0, 6, f"Fecha: {self.data.get('fecha_documento', '')}", ln=1)
        self.ln(5)
        
    def _parsear_texto_markdown(self, texto: str):
        if not texto:
            return
        lineas = texto.split('\n')
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
                
        # Identificacion
        self.add_section_bar("1. IDENTIFICACION GENERAL")
        self.add_kv_table([
            ("Acta No.:", self.data.get("no_acta", ""), "Fecha:", self.data.get("fecha_documento", "")),
            ("Idioma:", self.data.get("idioma", "Español"), "Proyecto:", self.data.get("proyecto", "General")),
            ("Asunto:", self.data.get("subtitulo_documento", ""))
        ])
        
        # Asistentes
        asistentes = self.data.get("asistentes", [])
        if asistentes:
            self.add_section_bar("2. ASISTENTES IDENTIFICADOS")
            filas_asis = [["", a.get("name", "")[:40], a.get("role", "")[:30], a.get("entity", "")[:30]] for a in asistentes]
            self.add_data_table(["No", "Nombre y Apellidos", "Cargo / Rol", "Entidad"], filas_asis, col_widths=(15, 65, 50, 60))
            
        summary = self.data.get("contexto_antecedentes", "").encode('latin-1', 'replace').decode('latin-1')
        if summary:
            self.add_section_bar("3. RESUMEN EJECUTIVO / CONTEXTO")
            self.set_font('helvetica', '', 10)
            self.set_text_color(17, 17, 17)
            self._parsear_texto_markdown(summary)

        decisiones = self.data.get("decisiones", "").encode('latin-1', 'replace').decode('latin-1')
        if decisiones:
            self.add_section_bar("4. DECISIONES Y DEFINICIONES")
            self._parsear_texto_markdown(decisiones)

        riesgos = self.data.get("riesgos", "").encode('latin-1', 'replace').decode('latin-1')
        if riesgos:
            self.add_section_bar("5. RIESGOS Y ALERTAS CLAVE")
            self._parsear_texto_markdown(riesgos)

        action_items = self.data.get("compromisos", [])
        if action_items:
            self.add_section_bar("6. COMPROMISOS Y TAREAS")
            filas_tareas = [["", ai.get("title", ""), ai.get("owner_email", ""), ai.get("due_date", "")] for ai in action_items]
            self.add_data_table(["No", "Descripcion", "Responsable", "Fecha"], filas_tareas, col_widths=(15, 95, 50, 30))

        self.add_section_bar("7. APROBACION")
        self.set_font('helvetica', '', 10)
        self.multi_cell(0, 5, "Los registros arriba mencionados constituyen el cuerpo del acta inteligenciada.")
        
    def generar_buffer(self) -> io.BytesIO:
        self.render_all()
        pdf_bytes = self.output(dest='S')
        if isinstance(pdf_bytes, str):
            pdf_bytes = pdf_bytes.encode('latin-1')
        elif isinstance(pdf_bytes, bytearray):
            pdf_bytes = bytes(pdf_bytes)
        buffer = io.BytesIO(pdf_bytes)
        return buffer
