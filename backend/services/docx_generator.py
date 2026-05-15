"""Generador profesional de actas DOCX (sin plantilla).

Diseño:
- Portada limpia con metadata en grid de tarjetas.
- Tipografía: Calibri 10/11/14, line-height 1.25, alineación izquierda.
- Numeración SECUENCIAL de secciones (1,2,3,…) — recalculada según las
  secciones que efectivamente tienen contenido. Nunca aparecen huecos.
- Tablas con anchos proporcionales fijos, sin truncación, header en color
  de marca, filas alternadas.
- Pie con "Página X de Y" y aviso de confidencialidad.
- Bullet lists con hanging-indent real (no se desalinean al envolver).

Lo único administrable desde la UI es `mapping_config` — el orden y la
selección de bloques. Toda la estética la fija este archivo.
"""

from __future__ import annotations

import io
import re
from datetime import datetime, timezone
from typing import Iterable, Optional

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

# ---------- Paleta de marca por defecto -----------------------------------

# Fallback estable cuando `theme` no provee un color válido. Estos valores
# son neutros y se ven bien con cualquier branding razonable.
_DEFAULTS = {
    "primaryColor": "#0F172A",      # slate-900 (sección)
    "headingColor": "#0F172A",      # alias legacy
    "headingTextColor": "#FFFFFF",
    "accentColor": "#0EA5E9",       # sky-500 (línea acento)
    "tableHeaderBg": "#0F172A",
    "tableHeaderTextColor": "#FFFFFF",
    "rowAltBg": "#F8FAFC",          # slate-50
    "borderColor": "#E2E8F0",       # slate-200
    "textColor": "#111827",         # gray-900
    "mutedColor": "#64748B",        # slate-500
    "fontFamily": "Calibri",
    "fontSize": 10,
}


# ---------- Helpers de estilo low-level (XML) -----------------------------


def _hex_to_rgb(value: str, default: tuple[int, int, int] = (0, 0, 0)) -> tuple[int, int, int]:
    if not value:
        return default
    h = str(value).lstrip("#")
    if len(h) != 6:
        return default
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return default


def _hex_clean(value: str, default: str) -> str:
    h = str(value or "").lstrip("#")
    if len(h) != 6 or not all(c in "0123456789abcdefABCDEF" for c in h):
        return default.lstrip("#")
    return h.upper()


def _set_cell_bg(cell, color_hex: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), color_hex)


def _set_cell_padding(cell, top: int = 60, bottom: int = 60, left: int = 100, right: int = 100) -> None:
    """Padding en twentieths-of-a-point (60 ≈ 0.04″ ≈ 1mm)."""
    tc_pr = cell._tc.get_or_add_tcPr()
    mar = tc_pr.find(qn("w:tcMar"))
    if mar is None:
        mar = OxmlElement("w:tcMar")
        tc_pr.append(mar)
    for side, val in (("top", top), ("bottom", bottom), ("left", left), ("right", right)):
        node = mar.find(qn(f"w:{side}"))
        if node is None:
            node = OxmlElement(f"w:{side}")
            mar.append(node)
        node.set(qn("w:w"), str(val))
        node.set(qn("w:type"), "dxa")


def _set_table_borders(table, color: str = "E2E8F0", sz: int = 4, hide: bool = False) -> None:
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.find(qn("w:tblBorders"))
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tbl_pr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        node = borders.find(qn(f"w:{edge}"))
        if node is None:
            node = OxmlElement(f"w:{edge}")
            borders.append(node)
        if hide:
            node.set(qn("w:val"), "nil")
        else:
            node.set(qn("w:val"), "single")
            node.set(qn("w:sz"), str(sz))
            node.set(qn("w:space"), "0")
            node.set(qn("w:color"), color)


def _set_table_layout_fixed(table) -> None:
    tbl_pr = table._tbl.tblPr
    layout = tbl_pr.find(qn("w:tblLayout"))
    if layout is None:
        layout = OxmlElement("w:tblLayout")
        tbl_pr.append(layout)
    layout.set(qn("w:type"), "fixed")


def _repeat_header_row(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    th = OxmlElement("w:tblHeader")
    th.set(qn("w:val"), "true")
    tr_pr.append(th)


def _set_row_min_height(row, min_pt: float = 18) -> None:
    """Garantiza altura mínima para que cabecera y celdas no queden estrechas."""
    tr_pr = row._tr.get_or_add_trPr()
    h = OxmlElement("w:trHeight")
    h.set(qn("w:val"), str(int(min_pt * 20)))
    h.set(qn("w:hRule"), "atLeast")
    tr_pr.append(h)


def _set_row_cant_split(row) -> None:
    """Evita que el contenido de la fila se rompa entre dos páginas. Word
    moverá toda la fila a la siguiente página antes que romperla."""
    tr_pr = row._tr.get_or_add_trPr()
    cs = OxmlElement("w:cantSplit")
    tr_pr.append(cs)


def _add_bottom_border(paragraph, color: str = "0EA5E9", sz: int = 6) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    pbdr = p_pr.find(qn("w:pBdr"))
    if pbdr is None:
        pbdr = OxmlElement("w:pBdr")
        p_pr.append(pbdr)
    bot = pbdr.find(qn("w:bottom"))
    if bot is None:
        bot = OxmlElement("w:bottom")
        pbdr.append(bot)
    bot.set(qn("w:val"), "single")
    bot.set(qn("w:sz"), str(sz))
    bot.set(qn("w:space"), "1")
    bot.set(qn("w:color"), color)


def _add_page_field(paragraph, kind: str = "PAGE") -> None:
    """Agrega un campo dinámico Word (PAGE / NUMPAGES) que el Word/LibreOffice
    rellena en runtime, no al renderizar."""
    run = paragraph.add_run()
    fld_char_begin = OxmlElement("w:fldChar")
    fld_char_begin.set(qn("w:fldCharType"), "begin")
    run._r.append(fld_char_begin)

    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = f" {kind} "
    run._r.append(instr)

    fld_char_sep = OxmlElement("w:fldChar")
    fld_char_sep.set(qn("w:fldCharType"), "separate")
    run._r.append(fld_char_sep)

    fld_char_end = OxmlElement("w:fldChar")
    fld_char_end.set(qn("w:fldCharType"), "end")
    run._r.append(fld_char_end)


def _format_human_date(value) -> str:
    """Convierte ISO/epoch en una cadena legible:
       '07/04/2026 — 16:09 UTC' si trae hora, '07/04/2026' si solo trae fecha."""
    if not value:
        return ""
    raw = str(value).strip()
    has_time_in_input = "T" in raw or (":" in raw and len(raw) > 10)
    # Epoch ms o s — siempre tienen hora implícita
    if raw.replace(".", "", 1).lstrip("-").isdigit():
        try:
            ts = float(raw)
            if ts > 1e12:
                ts /= 1000.0
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return dt.strftime("%d/%m/%Y — %H:%M UTC")
        except (OverflowError, OSError, ValueError):
            return raw
    # ISO con o sin Z
    try:
        candidate = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
        dt = datetime.fromisoformat(candidate)
        if not has_time_in_input:
            return dt.strftime("%d/%m/%Y")
        if dt.tzinfo:
            return dt.strftime("%d/%m/%Y — %H:%M %Z").replace("UTC ", "UTC")
        return dt.strftime("%d/%m/%Y — %H:%M")
    except ValueError:
        pass
    # YYYY-MM-DD pelado
    try:
        dt = datetime.strptime(raw[:10], "%Y-%m-%d")
        return dt.strftime("%d/%m/%Y")
    except ValueError:
        return raw


# ---------- Generador ------------------------------------------------------


# Mapa centralizado: id de bloque → (label, builder_method_name)
_BLOCK_LABELS = {
    "meta":         "IDENTIFICACIÓN GENERAL",
    "attendees":    "ASISTENTES",
    "summary":      "RESUMEN EJECUTIVO",
    "decisions":    "DECISIONES CLAVE",
    "risks":        "RIESGOS IDENTIFICADOS",
    "agreements":   "ACUERDOS",
    "action_items": "TAREAS Y COMPROMISOS",
}


class CorporateDocxGenerator:
    def __init__(self, data: dict) -> None:
        self.data = data or {}
        self.theme = self._resolve_theme(self.data.get("theme") or {})
        self.doc = Document()
        self._configure_page()
        self._register_styles()

    # ---------- setup ------------------------------------------------------

    def _resolve_theme(self, raw: dict) -> dict:
        merged = {**_DEFAULTS}
        for k, v in raw.items():
            if v is None or v == "":
                continue
            merged[k] = v
        return merged

    def _configure_page(self) -> None:
        section = self.doc.sections[0]
        section.page_width = Cm(21.0)
        section.page_height = Cm(29.7)
        section.top_margin = Cm(2.4)
        section.bottom_margin = Cm(2.4)
        section.left_margin = Cm(2.0)
        section.right_margin = Cm(2.0)
        section.header_distance = Cm(1.0)
        section.footer_distance = Cm(1.0)

    def _register_styles(self, override_normal: bool = True) -> None:
        """Registra los estilos `act_*` que usa el generador.

        Si `override_normal=False`, NO toca el estilo `Normal` del documento
        — esto preserva la tipografía corporativa del cliente cuando este
        generador se reusa sobre una plantilla custom (ver word_generator.py).
        """
        font_family = self.theme["fontFamily"]
        text_rgb = RGBColor(*_hex_to_rgb(self.theme["textColor"], (17, 24, 39)))
        muted_rgb = RGBColor(*_hex_to_rgb(self.theme["mutedColor"], (100, 116, 139)))

        if override_normal:
            # Solo cuando construimos desde cero (CorporateDocxGenerator standalone).
            # Sobrescribir Normal en una plantilla del cliente romperia su
            # tipografía corporativa.
            normal = self.doc.styles["Normal"]
            normal.font.name = font_family
            normal.font.size = Pt(10)
            normal.font.color.rgb = text_rgb
            pf = normal.paragraph_format
            pf.space_after = Pt(4)
            pf.space_before = Pt(0)
            pf.line_spacing = 1.25

        # Estilos custom
        from docx.enum.style import WD_STYLE_TYPE

        def add_style(name: str) -> None:
            try:
                return self.doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
            except ValueError:
                return self.doc.styles[name]

        s = add_style("act_cover_eyebrow")
        s.font.name = font_family
        s.font.size = Pt(9)
        s.font.bold = True
        s.font.color.rgb = muted_rgb
        s.paragraph_format.space_after = Pt(4)

        s = add_style("act_cover_title")
        s.font.name = font_family
        s.font.size = Pt(22)
        s.font.bold = True
        s.font.color.rgb = text_rgb
        s.paragraph_format.space_after = Pt(4)
        s.paragraph_format.line_spacing = 1.1

        s = add_style("act_cover_subtitle")
        s.font.name = font_family
        s.font.size = Pt(12)
        s.font.color.rgb = text_rgb
        s.paragraph_format.space_after = Pt(10)
        s.paragraph_format.line_spacing = 1.2

        s = add_style("act_section_number")
        s.font.name = font_family
        s.font.size = Pt(10)
        s.font.bold = True
        s.font.color.rgb = RGBColor(*_hex_to_rgb(self.theme["accentColor"], (14, 165, 233)))
        s.paragraph_format.space_after = Pt(0)

        s = add_style("act_section_title")
        s.font.name = font_family
        s.font.size = Pt(13)
        s.font.bold = True
        s.font.color.rgb = text_rgb
        # Espacio generoso antes para crear separación visual con el
        # contenido anterior, sin necesitar líneas decorativas.
        s.paragraph_format.space_before = Pt(24)
        s.paragraph_format.space_after = Pt(10)
        # Sección debe quedar pegada a su contenido, no orfanada al pie.
        s.paragraph_format.keep_with_next = True
        s.paragraph_format.keep_together = True

        s = add_style("act_body")
        s.font.name = font_family
        s.font.size = Pt(10)
        s.font.color.rgb = text_rgb
        s.paragraph_format.line_spacing = 1.3
        s.paragraph_format.space_after = Pt(4)
        s.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

        s = add_style("act_body_muted")
        s.font.name = font_family
        s.font.size = Pt(10)
        s.font.italic = True
        s.font.color.rgb = muted_rgb
        s.paragraph_format.space_after = Pt(4)

        s = add_style("act_subheading")
        s.font.name = font_family
        s.font.size = Pt(10.5)
        s.font.bold = True
        s.font.color.rgb = text_rgb
        # Espacio antes generoso para que el subtítulo no quede pegado
        # al párrafo anterior.
        s.paragraph_format.space_before = Pt(14)
        s.paragraph_format.space_after = Pt(4)
        # Mantener el subheading en la MISMA página que su contenido
        # (evita el caso "título solo abajo + footer + contenido en
        # próxima página").
        s.paragraph_format.keep_with_next = True
        s.paragraph_format.keep_together = True

        s = add_style("act_bullet")
        s.font.name = font_family
        s.font.size = Pt(10)
        s.font.color.rgb = text_rgb
        s.paragraph_format.left_indent = Cm(0.55)
        s.paragraph_format.first_line_indent = Cm(-0.55)
        s.paragraph_format.line_spacing = 1.3
        s.paragraph_format.space_after = Pt(3)
        # Justificar bullets también — antes solo act_body lo hacía y los
        # bullets quedaban con borde derecho irregular.
        s.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

        s = add_style("act_table_header")
        s.font.name = font_family
        s.font.size = Pt(9)
        s.font.bold = True
        s.font.color.rgb = RGBColor(*_hex_to_rgb(self.theme["tableHeaderTextColor"], (255, 255, 255)))
        s.paragraph_format.space_before = Pt(0)
        s.paragraph_format.space_after = Pt(0)

        s = add_style("act_table_cell")
        s.font.name = font_family
        s.font.size = Pt(9.5)
        s.font.color.rgb = text_rgb
        s.paragraph_format.space_before = Pt(0)
        s.paragraph_format.space_after = Pt(0)
        s.paragraph_format.line_spacing = 1.2

        s = add_style("act_meta_label")
        s.font.name = font_family
        s.font.size = Pt(9)
        s.font.bold = True
        s.font.color.rgb = muted_rgb
        s.paragraph_format.space_after = Pt(0)

        s = add_style("act_meta_value")
        s.font.name = font_family
        s.font.size = Pt(11)
        s.font.color.rgb = text_rgb
        s.paragraph_format.space_after = Pt(0)

        s = add_style("act_footer")
        s.font.name = font_family
        s.font.size = Pt(8)
        s.font.color.rgb = muted_rgb
        s.paragraph_format.space_after = Pt(0)

    # ---------- header / footer -------------------------------------------

    def _build_running_header_footer(self) -> None:
        section = self.doc.sections[0]
        # SIN portada dedicada: el header/footer running aplica a TODAS las
        # páginas, incluida la primera. La página 1 arranca con el bloque
        # de título y sigue con las secciones, en flujo continuo.
        section.different_first_page_header_footer = False

        # Header normal: título del documento en pequeño, alineado izquierda;
        # versión a la derecha. Línea inferior tenue.
        header = section.header
        header.is_linked_to_previous = False
        header_para = header.paragraphs[0]
        header_para.style = self.doc.styles["act_footer"]
        header_para.paragraph_format.tab_stops.add_tab_stop(Cm(17.0), alignment=WD_ALIGN_PARAGRAPH.RIGHT)
        run_l = header_para.add_run(self.data.get("entidad_principal", "Acta") + "   ·   ")
        run_l.font.name = self.theme["fontFamily"]
        run_l.font.size = Pt(8)
        run_l.font.color.rgb = RGBColor(*_hex_to_rgb(self.theme["mutedColor"], (100, 116, 139)))
        run_t = header_para.add_run((self.data.get("subtitulo_documento") or self.data.get("titulo_documento") or "")[:80])
        run_t.font.name = self.theme["fontFamily"]
        run_t.font.size = Pt(8)
        run_t.font.bold = True
        run_t.font.color.rgb = RGBColor(*_hex_to_rgb(self.theme["textColor"], (17, 24, 39)))
        header_para.add_run("\t")
        run_r = header_para.add_run(f"v.{self.data.get('version_documento', '1.0')}")
        run_r.font.name = self.theme["fontFamily"]
        run_r.font.size = Pt(8)
        run_r.font.color.rgb = RGBColor(*_hex_to_rgb(self.theme["mutedColor"], (100, 116, 139)))
        _add_bottom_border(header_para, color=_hex_clean(self.theme["borderColor"], "E2E8F0"), sz=4)

        # Footer: izquierda = generación + clasificación; derecha = página X de Y
        footer = section.footer
        footer.is_linked_to_previous = False
        footer_para = footer.paragraphs[0]
        footer_para.style = self.doc.styles["act_footer"]
        footer_para.paragraph_format.tab_stops.add_tab_stop(Cm(17.0), alignment=WD_ALIGN_PARAGRAPH.RIGHT)
        gen_at = datetime.now(timezone.utc).strftime("%d/%m/%Y")
        clasif = self.data.get("clasificacion") or "Confidencial"
        footer_para.add_run(f"Generado el {gen_at}   ·   {clasif}\t")
        footer_para.add_run("Página ")
        _add_page_field(footer_para, "PAGE")
        footer_para.add_run(" de ")
        _add_page_field(footer_para, "NUMPAGES")

    # ---------- bloque de título (sin portada dedicada) -------------------

    def _build_title_block(self) -> None:
        """Bloque de título compacto al tope de la página 1.

        Antes era una portada dedicada que dejaba la primera página casi
        vacía. Ahora es un encabezado del documento que ocupa solo el
        espacio necesario y deja que las secciones empiecen a continuación
        en la misma página.
        """
        eyebrow = (self.data.get("entidad_principal") or "Acta de reunión").upper()
        p = self.doc.add_paragraph(eyebrow, style="act_cover_eyebrow")
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT

        title_text = (self.data.get("titulo_documento") or "Acta de reunión").upper()
        p = self.doc.add_paragraph(title_text, style="act_cover_title")
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT

        # Línea acento de marca debajo del título
        accent_p = self.doc.add_paragraph()
        accent_p.paragraph_format.space_after = Pt(8)
        _add_bottom_border(
            accent_p,
            color=_hex_clean(self.theme["accentColor"], "0EA5E9"),
            sz=18,
        )

        subtitle = self.data.get("subtitulo_documento") or self.data.get("title") or ""
        if subtitle:
            p = self.doc.add_paragraph(subtitle, style="act_cover_subtitle")
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            p.paragraph_format.space_after = Pt(4)

    # ---------- secciones --------------------------------------------------

    def _section_header(self, number: int, label: str) -> None:
        """Renderiza un header de sección: solo el título grande, limpio.

        El parámetro `number` se mantiene en la firma por compatibilidad
        con el resolver de bloques, pero NO se renderiza visualmente.

        SIN línea inferior — el usuario pidió quitar los underlines.
        El espaciado generoso antes/después da el respiro visual sin
        necesitar reglas decorativas.
        """
        title_p = self.doc.add_paragraph(label.upper(), style="act_section_title")
        title_p.paragraph_format.space_before = Pt(24)
        title_p.paragraph_format.space_after = Pt(10)

    def _block_meta(self) -> None:
        """Bloque 'meta': tabla limpia 2 columnas (label/value)."""
        rows = [
            ("Acta No.", self.data.get("no_acta") or "—"),
            ("Fecha", _format_human_date(self.data.get("fecha_documento") or self.data.get("date") or "")),
            ("Proyecto", self.data.get("proyecto") or "General"),
            ("Idioma", self.data.get("idioma") or "Español"),
            ("Asunto", self.data.get("subtitulo_documento") or self.data.get("title") or "—"),
        ]
        table = self.doc.add_table(rows=len(rows), cols=2)
        table.autofit = False
        table.columns[0].width = Cm(4.0)
        table.columns[1].width = Cm(13.0)
        _set_table_layout_fixed(table)
        _set_table_borders(table, color=_hex_clean(self.theme["borderColor"], "E2E8F0"), sz=4)

        for i, (label, value) in enumerate(rows):
            row = table.rows[i]
            _set_row_min_height(row, min_pt=18)
            for cell in row.cells:
                cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
                _set_cell_padding(cell, top=80, bottom=80, left=140, right=140)

            row.cells[0].text = ""
            p_lbl = row.cells[0].paragraphs[0]
            p_lbl.style = self.doc.styles["act_meta_label"]
            p_lbl.add_run(label.upper())

            row.cells[1].text = ""
            p_val = row.cells[1].paragraphs[0]
            p_val.style = self.doc.styles["act_table_cell"]
            p_val.add_run(str(value or "—"))

    def _block_attendees(self) -> None:
        attendees = self.data.get("asistentes") or []
        if not attendees:
            self.doc.add_paragraph("No se registraron asistentes en esta sesión.", style="act_body_muted")
            return
        rows = []
        for a in attendees:
            if isinstance(a, dict):
                rows.append([
                    a.get("name") or a.get("full_name") or "",
                    a.get("role") or a.get("cargo") or "",
                    a.get("entity") or a.get("company") or a.get("organization") or "",
                ])
            else:
                rows.append([str(a or ""), "", ""])
        self._render_data_table(
            headers=["Nombre y apellidos", "Cargo / Rol", "Entidad"],
            rows=rows,
            widths_cm=[6.5, 5.5, 5.0],
            include_index=True,
        )

    def _block_summary(self) -> None:
        text = self.data.get("contexto_antecedentes") or self.data.get("summary") or ""
        if not (text or "").strip():
            self.doc.add_paragraph("No se generó resumen ejecutivo para esta sesión.", style="act_body_muted")
            return
        self._render_markdown(text)

    def _block_decisions(self) -> None:
        text = self.data.get("decisiones") or self.data.get("decisions") or ""
        if not (text or "").strip():
            self.doc.add_paragraph("No se registraron decisiones clave.", style="act_body_muted")
            return
        self._render_markdown(text)

    def _block_risks(self) -> None:
        text = self.data.get("riesgos") or self.data.get("risks") or ""
        if not (text or "").strip():
            self.doc.add_paragraph("No se identificaron riesgos relevantes.", style="act_body_muted")
            return
        self._render_markdown(text)

    def _block_agreements(self) -> None:
        text = self.data.get("acuerdos") or self.data.get("agreements") or ""
        if not (text or "").strip():
            self.doc.add_paragraph("No se registraron acuerdos formales.", style="act_body_muted")
            return
        self._render_markdown(text)

    def _block_action_items(self) -> None:
        items = self.data.get("compromisos") or self.data.get("action_items") or []
        if not items:
            self.doc.add_paragraph("No hay tareas o compromisos asignados.", style="act_body_muted")
            return
        rows = []
        for it in items:
            if isinstance(it, dict):
                # Solo el nombre del responsable (el email queda implícito).
                # Antes podía venir pegado "Nombre (email@x)" desde el caller
                # legacy, pero ahora sessions_upload normaliza separados.
                owner = (it.get("owner_name") or "").strip()
                if not owner:
                    owner = (it.get("owner_email") or "").strip() or "—"
                due = _format_human_date(it.get("due_date") or "") or "—"
                rows.append([
                    it.get("title") or "—",
                    owner,
                    due,
                    (it.get("priority") or "media").capitalize(),
                ])
            else:
                rows.append([str(it), "—", "—", "Media"])
        # Anchos balanceados (suma 16cm + 1cm del # = 17cm útil):
        # - Descripción: 6.5cm (texto largo)
        # - Responsable: 4.0cm (nombre + apellido típicos)
        # - Fecha límite: 2.7cm (DD/MM/YYYY)
        # - Prioridad: 2.8cm (la palabra "Prioridad" cabe sin wrap)
        self._render_data_table(
            headers=["Descripción", "Responsable", "Fecha límite", "Prioridad"],
            rows=rows,
            widths_cm=[6.5, 4.0, 2.7, 2.8],
            include_index=True,
        )

    # ---------- markdown light renderer -----------------------------------

    def _render_markdown(self, text: str) -> None:
        """Renderer minimalista de markdown — solo lo que producen los LLMs.

        Aplica alignment explícito a cada párrafo además del style, porque
        LibreOffice (vía Gotenberg) a veces ignora la alineación heredada
        del style si no está reasentada en el párrafo concreto.
        """
        if not text:
            return
        for raw_line in str(text).split("\n"):
            line = raw_line.rstrip()
            if not line.strip():
                # Línea en blanco = pequeño respiro
                self.doc.add_paragraph().paragraph_format.space_after = Pt(2)
                continue

            # Headers (### / ## / #)
            m = re.match(r"^(#{1,3})\s+(.*)$", line)
            if m:
                clean = self._strip_md_inline(m.group(2))
                p = self.doc.add_paragraph(clean, style="act_subheading")
                p.alignment = WD_ALIGN_PARAGRAPH.LEFT
                continue

            # Bullets (- / *)
            m = re.match(r"^[\-\*]\s+(.*)$", line.lstrip())
            if m:
                clean = self._strip_md_inline(m.group(1))
                # Hanging indent simulado: bullet + tab
                p = self.doc.add_paragraph(style="act_bullet")
                p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                p.add_run("•\t" + clean)
                continue

            clean = self._strip_md_inline(line)
            p = self.doc.add_paragraph(clean, style="act_body")
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    @staticmethod
    def _strip_md_inline(text: str) -> str:
        # Quita marcadores inline (**bold**, __bold__, `code`, [link](url))
        text = re.sub(r"`([^`]+)`", r"\1", text)
        text = re.sub(r"\[([^\]]+)\]\(([^\)]+)\)", r"\1", text)
        text = text.replace("**", "").replace("__", "")
        return text.strip()

    # ---------- tabla genérica --------------------------------------------

    def _render_data_table(
        self,
        headers: list[str],
        rows: list[list[str]],
        widths_cm: list[float],
        include_index: bool = False,
    ) -> None:
        if include_index:
            headers = ["#"] + headers
            widths_cm = [1.0] + widths_cm
            rows = [[str(i + 1)] + r for i, r in enumerate(rows)]

        # Normalizar ancho total a 17cm (ancho útil)
        target = 17.0
        total = sum(widths_cm)
        if total > 0 and abs(total - target) > 0.05:
            factor = target / total
            widths_cm = [round(w * factor, 2) for w in widths_cm]

        n_cols = len(headers)
        table = self.doc.add_table(rows=1 + len(rows), cols=n_cols)
        table.autofit = False
        for col, w in zip(table.columns, widths_cm):
            col.width = Cm(w)
        _set_table_layout_fixed(table)
        _set_table_borders(table, color=_hex_clean(self.theme["borderColor"], "E2E8F0"), sz=4)

        # Header row
        header_bg = _hex_clean(self.theme["tableHeaderBg"], "0F172A")
        header_row = table.rows[0]
        _repeat_header_row(header_row)
        _set_row_min_height(header_row, min_pt=22)
        _set_row_cant_split(header_row)
        for i, label in enumerate(headers):
            cell = header_row.cells[i]
            _set_cell_bg(cell, header_bg)
            _set_cell_padding(cell, top=100, bottom=100, left=140, right=140)
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            cell.text = ""
            p = cell.paragraphs[0]
            p.style = self.doc.styles["act_table_header"]
            p.add_run(label)

        # Data rows con zebra alternada
        alt_bg = _hex_clean(self.theme["rowAltBg"], "F8FAFC")
        for r_idx, data_row in enumerate(rows):
            row = table.rows[r_idx + 1]
            _set_row_min_height(row, min_pt=18)
            _set_row_cant_split(row)
            for i, val in enumerate(data_row):
                if i >= n_cols:
                    break
                cell = row.cells[i]
                _set_cell_padding(cell, top=80, bottom=80, left=140, right=140)
                cell.vertical_alignment = WD_ALIGN_VERTICAL.TOP
                if r_idx % 2 == 1:
                    _set_cell_bg(cell, alt_bg)
                cell.text = ""
                p = cell.paragraphs[0]
                p.style = self.doc.styles["act_table_cell"]
                # Alineación del índice y prioridad → centrado; resto → izq.
                first_col = include_index and i == 0
                if first_col:
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p.add_run(str(val or ""))

    # ---------- ensamble ---------------------------------------------------

    def _resolve_block_order(self) -> list[str]:
        mapping = self.data.get("mapping_config") or []
        order = []
        for blk in mapping:
            bid = blk.get("id") if isinstance(blk, dict) else blk
            if bid in _BLOCK_LABELS and bid not in order:
                order.append(bid)
        if not order:
            order = ["meta", "attendees", "summary", "decisions", "risks", "agreements", "action_items"]
        # Garantizar que `meta` esté primero si está incluido
        if "meta" in order:
            order = ["meta"] + [b for b in order if b != "meta"]
        return order

    def _block_has_content(self, block_id: str) -> bool:
        """Decide si vale la pena renderizar la sección. `meta` siempre sí."""
        if block_id == "meta":
            return True
        if block_id == "attendees":
            return bool(self.data.get("asistentes"))
        if block_id == "summary":
            return bool((self.data.get("contexto_antecedentes") or self.data.get("summary") or "").strip())
        if block_id == "decisions":
            return bool((self.data.get("decisiones") or self.data.get("decisions") or "").strip())
        if block_id == "risks":
            return bool((self.data.get("riesgos") or self.data.get("risks") or "").strip())
        if block_id == "agreements":
            return bool((self.data.get("acuerdos") or self.data.get("agreements") or "").strip())
        if block_id == "action_items":
            return bool(self.data.get("compromisos") or self.data.get("action_items"))
        return False

    def _render_block(self, block_id: str) -> None:
        method = getattr(self, f"_block_{block_id}", None)
        if method:
            method()

    def generar_buffer(self) -> io.BytesIO:
        self._build_running_header_footer()
        self._build_title_block()

        order = self._resolve_block_order()
        renderable = [b for b in order if self._block_has_content(b)]
        for idx, block_id in enumerate(renderable, start=1):
            self._section_header(idx, _BLOCK_LABELS[block_id])
            self._render_block(block_id)

        buf = io.BytesIO()
        self.doc.save(buf)
        buf.seek(0)
        return buf
