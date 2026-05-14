import io
import json
from datetime import datetime
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

def set_cell_background_color(cell, color_hex):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    shd = tcPr.first_child_found_in("w:shd")
    if shd is None:
        shd = OxmlElement('w:shd')
        tcPr.append(shd)
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), color_hex)

def set_table_borders(table, color="808080", sz=4):
    tbl = table._tbl
    tblPr = tbl.tblPr
    tblBorders = tblPr.first_child_found_in("w:tblBorders")
    if tblBorders is None:
        tblBorders = OxmlElement('w:tblBorders')
        tblPr.append(tblBorders)
    
    for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        element = tblBorders.find(qn(f'w:{edge}'))
        if element is None:
            element = OxmlElement(f'w:{edge}')
            tblBorders.append(element)
        element.set(qn('w:val'), 'single')
        element.set(qn('w:sz'), str(sz))
        element.set(qn('w:space'), '0')
        element.set(qn('w:color'), color)

def set_repeat_table_header(row):
    tr = row._tr
    trPr = tr.get_or_add_trPr()
    tblHeader = OxmlElement('w:tblHeader')
    tblHeader.set(qn('w:val'), "true")
    trPr.append(tblHeader)


class CorporateDocxGenerator:
    def __init__(self, data: dict):
        self.doc = Document()
        self.data = data
        self._configurar_pagina()
        self._crear_estilos()

    def _configurar_pagina(self):
        section = self.doc.sections[0]
        section.page_width = Cm(21.0)
        section.page_height = Cm(29.7)
        section.top_margin = Cm(2.0)
        section.bottom_margin = Cm(2.0)
        section.left_margin = Cm(2.2)
        section.right_margin = Cm(2.2)

    def _add_style(self, name, font_name='Arial', size=10, bold=False, color=RGBColor(17, 17, 17), align=WD_ALIGN_PARAGRAPH.LEFT, space_after=Pt(6)):
        try:
            style = self.doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
        except ValueError:
            style = self.doc.styles[name]
            
        font = style.font
        font.name = font_name
        font.size = Pt(size)
        font.bold = bold
        font.color.rgb = color
        
        para_format = style.paragraph_format
        para_format.alignment = align
        para_format.space_after = space_after
        para_format.space_before = Pt(0)
        para_format.line_spacing = 1.08
        return style

    def _crear_estilos(self):
        gray_dark = RGBColor(122, 122, 122)
        
        self._add_style('estilo_encabezado_institucional', size=9, bold=True, color=gray_dark, align=WD_ALIGN_PARAGRAPH.LEFT, space_after=Pt(0))
        self._add_style('estilo_titulo_documento', size=13, bold=True, align=WD_ALIGN_PARAGRAPH.RIGHT, space_after=Pt(2))
        self._add_style('estilo_version_documento', size=9, color=gray_dark, align=WD_ALIGN_PARAGRAPH.RIGHT, space_after=Pt(0))
        self._add_style('estilo_pie_pagina', size=8, color=gray_dark, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=Pt(0))
        
        self._add_style('estilo_titulo_seccion', size=11, bold=True, color=RGBColor(255, 255, 255), align=WD_ALIGN_PARAGRAPH.LEFT, space_after=Pt(0))
        self._add_style('estilo_subtitulo', size=10, bold=True, space_after=Pt(4))
        self._add_style('estilo_texto_base', size=10, align=WD_ALIGN_PARAGRAPH.JUSTIFY, space_after=Pt(6))
        
        self._add_style('estilo_etiqueta_campo', size=9, bold=True, space_after=Pt(0))
        self._add_style('estilo_texto_tabla', size=9, space_after=Pt(0))
        self._add_style('estilo_encabezado_tabla', size=9, bold=True, space_after=Pt(0))

    def construir_encabezados_y_pies(self):
        section = self.doc.sections[0]
        header = section.header
        htable = header.add_table(rows=1, cols=3, width=Cm(16.5))
        htable.alignment = WD_TABLE_ALIGNMENT.CENTER
        
        col_izq, col_cen, col_der = htable.columns[0].cells[0], htable.columns[1].cells[0], htable.columns[2].cells[0]
        
        p_izq1 = col_izq.paragraphs[0]
        p_izq1.style = 'estilo_encabezado_institucional'
        p_izq1.add_run(self.data.get("entidad_principal", "Notiva"))
        p_izq2 = col_izq.add_paragraph(self.data.get("entidad_secundaria", "Gestión Integral"), style='estilo_encabezado_institucional')

        p_cen = col_cen.paragraphs[0]
        p_cen.alignment = WD_ALIGN_PARAGRAPH.CENTER
        col_cen.vertical_alignment = WD_ALIGN_VERTICAL.CENTER

        p_der1 = col_der.paragraphs[0]
        p_der1.style = 'estilo_titulo_documento'
        p_der1.add_run(self.data.get("titulo_documento", "ACTA DE REUNIÓN").upper())
        p_der2 = col_der.add_paragraph(f"V. {self.data.get('version_documento', '1.0')} | {self.data.get('clasificacion', 'Uso Corporativo')}", style='estilo_version_documento')
        col_der.vertical_alignment = WD_ALIGN_VERTICAL.CENTER

        footer = section.footer
        p_foot = footer.paragraphs[0]
        p_foot.style = 'estilo_pie_pagina'
        p_foot.add_run(f"{self.data.get('entidad_principal', 'Notiva')} | {self.data.get('titulo_documento', 'Acta')} | Generado automáticamente")

    def construir_portada_simple(self):
        self.doc.add_paragraph()
        safe_title = self.data.get("subtitulo_documento", "Asunto no especificado")
        self.doc.add_paragraph(safe_title, style='estilo_subtitulo')
        self.doc.add_paragraph(f"Fecha: {self.data.get('fecha_documento', '')}", style='estilo_texto_base')
        self.doc.add_paragraph()

    def agregar_barra_seccion(self, titulo):
        self.doc.add_paragraph() 
        table = self.doc.add_table(rows=1, cols=1)
        table.autofit = False
        table.columns[0].width = Cm(16.5)
        
        # Color dinámico
        theme = self.data.get("theme") or {}
        bg_col = theme.get("headingColor", "#C62828").lstrip("#")
        
        cell = table.cell(0, 0)
        set_cell_background_color(cell, bg_col)
        
        tbl = table._tbl
        tblPr = tbl.tblPr
        tblBorders = OxmlElement('w:tblBorders')
        for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
            elem = OxmlElement(f'w:{edge}')
            elem.set(qn('w:val'), 'none')
            tblBorders.append(elem)
        tblPr.append(tblBorders)

        p = cell.paragraphs[0]
        p.style = 'estilo_titulo_seccion'
        p.add_run(titulo.upper())
        self.doc.add_paragraph() 

    def construir_tabla_identificacion(self):
        self.agregar_barra_seccion("1. IDENTIFICACIÓN GENERAL")
        table = self.doc.add_table(rows=3, cols=4)
        set_table_borders(table)
        
        datos = [
            ("Acta No.:", self.data.get("no_acta", ""), "Fecha:", self.data.get("fecha_documento", "")),
            ("Idioma:", self.data.get("idioma", "Español"), "Proyecto:", self.data.get("proyecto", "General")),
            ("Asunto:", self.data.get("subtitulo_documento", ""), "", "")
        ]

        for i, (l1, v1, l2, v2) in enumerate(datos):
            row = table.rows[i]
            row.cells[0].text = l1
            row.cells[0].paragraphs[0].style = 'estilo_etiqueta_campo'
            
            row.cells[1].text = v1
            row.cells[1].paragraphs[0].style = 'estilo_texto_tabla'
            
            if l2:
                row.cells[2].text = l2
                row.cells[2].paragraphs[0].style = 'estilo_etiqueta_campo'
                row.cells[3].text = v2
                row.cells[3].paragraphs[0].style = 'estilo_texto_tabla'
            else:
                row.cells[1].merge(row.cells[3])
                
            for cell in row.cells:
                cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER

    def construir_tabla_estandar(self, headers, datos_lista, col_widths=None):
        table = self.doc.add_table(rows=1, cols=len(headers))
        set_table_borders(table)
        set_repeat_table_header(table.rows[0])
        
        for i, header in enumerate(headers):
            cell = table.cell(0, i)
            set_cell_background_color(cell, "D9D9D9")
            cell.text = header
            cell.paragraphs[0].style = 'estilo_encabezado_tabla'
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            if col_widths:
                table.columns[i].width = Cm(col_widths[i])

        for index, item in enumerate(datos_lista):
            row = table.add_row()
            # Asumimos que `item` es una lista de valores en el mismo orden que los headers
            for i, val in enumerate(item):
                if i == 0 and not val:
                    val = str(index + 1)
                row.cells[i].text = str(val) if val else ""
                row.cells[i].paragraphs[0].style = 'estilo_texto_tabla'

    def _parsear_texto_markdown(self, texto: str):
        if not texto:
            return
        lineas = texto.split('\n')
        for linea in lineas:
            linea_str = linea.strip()
            if not linea_str:
                self.doc.add_paragraph("", style='estilo_texto_base')
                continue
            
            if linea_str.startswith("### "):
                clean_line = linea_str[4:].replace("**", "").replace("__", "")
                p = self.doc.add_paragraph(clean_line, style='estilo_texto_base')
                if p.runs:
                    p.runs[0].bold = True
            elif linea_str.startswith("## "):
                clean_line = linea_str[3:].replace("**", "").replace("__", "")
                p = self.doc.add_paragraph(clean_line, style='estilo_texto_base')
                if p.runs:
                    p.runs[0].bold = True
            elif linea_str.startswith("# "):
                clean_line = linea_str[2:].replace("**", "").replace("__", "")
                p = self.doc.add_paragraph(clean_line, style='estilo_texto_base')
                if p.runs:
                    p.runs[0].bold = True
            elif linea_str.startswith("- ") or linea_str.startswith("* "):
                clean_line = linea_str[2:].replace("**", "").replace("__", "")
                p = self.doc.add_paragraph("• " + clean_line, style='estilo_texto_base')
                p.paragraph_format.left_indent = Cm(0.6)
            else:
                clean_line = linea_str.replace("**", "").replace("__", "")
                self.doc.add_paragraph(clean_line, style='estilo_texto_base')

    def construir_secciones_restantes(self):
        # Asistentes
        asistentes = self.data.get("asistentes", [])
        if asistentes:
            self.agregar_barra_seccion("2. ASISTENTES IDENTIFICADOS")
            filas_asis = []
            for a in asistentes:
                filas_asis.append(["", a.get("name", ""), a.get("role", ""), a.get("entity", "")])
            self.construir_tabla_estandar(["No", "Nombre y Apellidos", "Cargo / Rol", "Entidad"], filas_asis, [1.0, 6.0, 4.5, 5.0])

        summary = self.data.get("contexto_antecedentes", "")
        if summary:
            self.agregar_barra_seccion("3. RESUMEN EJECUTIVO / CONTEXTO")
            self._parsear_texto_markdown(summary)

        decisiones = self.data.get("decisiones", "")
        if decisiones:
            self.agregar_barra_seccion("4. DECISIONES Y DEFINICIONES")
            self._parsear_texto_markdown(decisiones)

        riesgos = self.data.get("riesgos", "")
        if riesgos:
            self.agregar_barra_seccion("5. RIESGOS Y ALERTAS CLAVE")
            self._parsear_texto_markdown(riesgos)

        # Acuerdos — sección NUEVA que faltaba en el documento generado.
        # Antes la data incluía `agreements` pero el doc no lo renderizaba.
        acuerdos = self.data.get("acuerdos", "") or self.data.get("agreements", "")
        if acuerdos:
            self.agregar_barra_seccion("6. ACUERDOS")
            self._parsear_texto_markdown(acuerdos)

        # Compromisos / Tareas (Action Items)
        action_items = self.data.get("compromisos", [])
        if action_items:
            self.agregar_barra_seccion("7. COMPROMISOS Y TAREAS DETECTADAS")
            filas_tareas = []
            for ai in action_items:
                filas_tareas.append(["", ai.get("title", ""), ai.get("owner_email", ""), ai.get("due_date", ""), ai.get("status", "")])
            self.construir_tabla_estandar(["No", "Descripción de la Tarea", "Responsable", "Fecha Límite", "Estado"], filas_tareas, [1.0, 7.5, 4.0, 2.5, 1.5])

        self.agregar_barra_seccion("8. APROBACIÓN")
        self.doc.add_paragraph("Los registros arriba mencionados constituyen el cuerpo del acta inteligenciada automáticamente.", style='estilo_texto_base')

    def generar_buffer(self) -> io.BytesIO:
        self.construir_encabezados_y_pies()
        self.construir_portada_simple()
        self.construir_tabla_identificacion()
        self.construir_secciones_restantes()
        
        buffer = io.BytesIO()
        self.doc.save(buffer)
        buffer.seek(0)
        return buffer
