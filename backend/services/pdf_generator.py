"""Generador profesional de PDF (fallback FPDF cuando Gotenberg no está).

Mantiene la MISMA identidad visual que `services.docx_generator.CorporateDocxGenerator`:
- Portada con eyebrow + título + acento de marca + tarjeta metadata 2x2.
- Header/footer running con título + página X de Y.
- Secciones con badge "01" + título + línea acento.
- Tablas con header de marca, zebra alternada, sin truncar.
- Numeración secuencial (no salta 1→3).
- Bullets con hanging indent que no crashea con texto largo (bug fix:
  `multi_cell(0, ...)` después de `set_x(x+5)` rompía cuando quedaba poco
  espacio horizontal).

Usa el mismo `_BLOCK_LABELS` y resolver de orden que el DOCX para que
el usuario tenga consistencia entre ambos formatos.
"""

from __future__ import annotations

import io
import re
from datetime import datetime, timezone
from typing import Optional

from fpdf import FPDF

# Reusamos el formateo de fecha para mantener consistencia 1:1 con el DOCX
from services.docx_generator import _format_human_date


_DEFAULT_THEME = {
    "primaryColor": "#0F172A",
    "headingColor": "#0F172A",
    "headingTextColor": "#FFFFFF",
    "accentColor": "#0EA5E9",
    "tableHeaderBg": "#0F172A",
    "tableHeaderTextColor": "#FFFFFF",
    "rowAltBg": "#F8FAFC",
    "borderColor": "#E2E8F0",
    "textColor": "#111827",
    "mutedColor": "#64748B",
    "fontFamily": "helvetica",  # FPDF core: helvetica/times/courier
    "fontSize": 10,
}


_BLOCK_LABELS = {
    "meta":         "IDENTIFICACIÓN GENERAL",
    "attendees":    "ASISTENTES",
    "summary":      "RESUMEN EJECUTIVO",
    "decisions":    "DECISIONES CLAVE",
    "risks":        "RIESGOS IDENTIFICADOS",
    "agreements":   "ACUERDOS",
    "action_items": "TAREAS Y COMPROMISOS",
}


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


def _safe_latin(text) -> str:
    """FPDF core fonts solo soportan latin-1. Reemplazamos lo no representable
    con su mejor aproximación (— → -, etc.) en lugar de '?'."""
    if text is None:
        return ""
    s = str(text)
    replacements = {
        "—": "-", "–": "-", "“": '"', "”": '"', "‘": "'", "’": "'",
        "…": "...", "•": "-", "·": "-", "→": "->", "←": "<-",
        "✓": "+", "✗": "x", "✅": "+", "❌": "x", "⚠": "!", "⚠️": "!",
    }
    for k, v in replacements.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "replace").decode("latin-1")


def _strip_md_inline(text: str) -> str:
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\(([^\)]+)\)", r"\1", text)
    return text.replace("**", "").replace("__", "")


class CorporatePDFGenerator(FPDF):
    """PDF profesional A4 con la misma identidad visual que el DOCX."""

    def __init__(self, data: dict) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self.data = data or {}
        self.theme = self._resolve_theme(self.data.get("theme") or {})
        self.set_auto_page_break(auto=True, margin=20)
        self.set_margins(left=18, top=22, right=18)
        # Número de la página de portada. La cover SIEMPRE es la primera
        # página, así que lo seteamos en __init__ para que header()/footer()
        # ya tengan la info en la primera invocación de FPDF (que ocurre
        # DENTRO de add_page() durante _build_cover, antes de que ese método
        # pueda asignarlo).
        self._cover_page_no: int = 1
        self.alias_nb_pages()
        self._cached_title = (
            self.data.get("subtitulo_documento")
            or self.data.get("title")
            or ""
        )

    def _resolve_theme(self, raw: dict) -> dict:
        merged = {**_DEFAULT_THEME}
        for k, v in (raw or {}).items():
            if v is None or v == "":
                continue
            merged[k] = v
        return merged

    # ---------- header / footer running ----------------------------------

    def header(self) -> None:
        # En la portada NO hay header running.
        if self.page_no() == self._cover_page_no:
            return
        muted = _hex_to_rgb(self.theme["mutedColor"], (100, 116, 139))
        text = _hex_to_rgb(self.theme["textColor"], (17, 24, 39))
        border = _hex_to_rgb(self.theme["borderColor"], (226, 232, 240))

        self.set_y(10)
        self.set_text_color(*muted)
        self.set_font("helvetica", "", 8)
        entity = _safe_latin(self.data.get("entidad_principal") or "")
        title = _safe_latin(self._cached_title)
        version = f"v.{self.data.get('version_documento', '1.0')}"

        # Línea izquierda: entidad · título (bold)
        page_w = self.w - self.l_margin - self.r_margin
        self.set_x(self.l_margin)
        if entity:
            self.cell(self.get_string_width(entity + "  ·  "), 5, entity + "  ·  ")
        self.set_font("helvetica", "B", 8)
        self.set_text_color(*text)
        self.cell(self.get_string_width(title) + 1, 5, title)

        # Versión a la derecha
        self.set_font("helvetica", "", 8)
        self.set_text_color(*muted)
        self.set_x(self.l_margin)
        self.cell(page_w, 5, version, align="R")

        # Línea inferior tenue
        self.set_draw_color(*border)
        self.set_line_width(0.2)
        self.line(self.l_margin, 18, self.w - self.r_margin, 18)

        self.set_y(24)

    def footer(self) -> None:
        # En la portada NO hay footer running.
        if self.page_no() == self._cover_page_no:
            return
        muted = _hex_to_rgb(self.theme["mutedColor"], (100, 116, 139))
        self.set_y(-15)
        self.set_font("helvetica", "", 8)
        self.set_text_color(*muted)
        gen_at = datetime.now(timezone.utc).strftime("%d/%m/%Y")
        clasif = _safe_latin(self.data.get("clasificacion") or "Confidencial")
        page_w = self.w - self.l_margin - self.r_margin
        # Izquierda
        self.set_x(self.l_margin)
        self.cell(page_w / 2, 6, f"Generado el {gen_at}  -  {clasif}")
        # Derecha
        self.set_x(self.l_margin + page_w / 2)
        self.cell(page_w / 2, 6, f"Pagina {self.page_no()} de {{nb}}", align="R")

    # ---------- portada --------------------------------------------------

    def _build_cover(self) -> None:
        # _cover_page_no = 1 ya está seteado en __init__ para que header()
        # y footer() lo respeten desde la PRIMERA invocación que hace FPDF
        # adentro de add_page().
        self.add_page()

        muted = _hex_to_rgb(self.theme["mutedColor"], (100, 116, 139))
        text = _hex_to_rgb(self.theme["textColor"], (17, 24, 39))
        accent = _hex_to_rgb(self.theme["accentColor"], (14, 165, 233))

        # Espacio superior
        self.set_y(45)

        # Eyebrow
        eyebrow = _safe_latin(self.data.get("entidad_principal") or "Acta de reunion").upper()
        self.set_font("helvetica", "B", 9)
        self.set_text_color(*muted)
        self.cell(0, 5, eyebrow, ln=1)

        self.ln(2)

        # Título principal
        title = _safe_latin(self.data.get("titulo_documento") or "Acta de reunion").upper()
        self.set_font("helvetica", "B", 28)
        self.set_text_color(*text)
        self.cell(0, 14, title, ln=1)

        # Línea acento
        self.set_draw_color(*accent)
        self.set_line_width(1.2)
        self.line(self.l_margin, self.get_y() + 3, self.w - self.r_margin, self.get_y() + 3)
        self.ln(12)

        # Subtítulo (asunto)
        subtitle = _safe_latin(self.data.get("subtitulo_documento") or self.data.get("title") or "")
        if subtitle:
            self.set_font("helvetica", "", 14)
            self.set_text_color(*text)
            self.multi_cell(0, 7, subtitle)
            self.ln(8)

        # Tarjeta meta 2x2
        self._cover_meta_card()

    def _cover_meta_card(self) -> None:
        muted = _hex_to_rgb(self.theme["mutedColor"], (100, 116, 139))
        text = _hex_to_rgb(self.theme["textColor"], (17, 24, 39))
        border = _hex_to_rgb(self.theme["borderColor"], (226, 232, 240))

        meta_pairs = [
            ("Acta No.", self.data.get("no_acta") or "-"),
            ("Fecha de la reunion", _format_human_date(self.data.get("fecha_documento") or self.data.get("date") or "")),
            ("Proyecto", self.data.get("proyecto") or "General"),
            ("Idioma", self.data.get("idioma") or "Espanol"),
        ]
        page_w = self.w - self.l_margin - self.r_margin
        col_w = page_w / 2
        row_h = 16

        x0 = self.l_margin
        y0 = self.get_y()
        self.set_draw_color(*border)
        self.set_line_width(0.2)

        for idx, (label, value) in enumerate(meta_pairs):
            r, c = divmod(idx, 2)
            x = x0 + c * col_w
            y = y0 + r * row_h
            # Borde de cada celda
            self.rect(x, y, col_w, row_h)
            # Label
            self.set_xy(x + 4, y + 2.5)
            self.set_font("helvetica", "B", 8)
            self.set_text_color(*muted)
            self.cell(col_w - 8, 4, _safe_latin(label).upper())
            # Value
            self.set_xy(x + 4, y + 8)
            self.set_font("helvetica", "", 11)
            self.set_text_color(*text)
            self.cell(col_w - 8, 5, _safe_latin(value or "-"))

        self.set_y(y0 + 2 * row_h + 4)

    # ---------- sección header --------------------------------------------

    def _section_header(self, number: int, label: str) -> None:
        accent = _hex_to_rgb(self.theme["accentColor"], (14, 165, 233))
        text = _hex_to_rgb(self.theme["textColor"], (17, 24, 39))
        border = _hex_to_rgb(self.theme["borderColor"], (226, 232, 240))

        # Si no hay espacio para el header + 2 líneas de contenido, salta
        if self.get_y() > self.h - 60:
            self.add_page()

        self.ln(6)
        # Badge del número
        self.set_font("helvetica", "B", 10)
        self.set_text_color(*accent)
        self.cell(0, 5, f"{number:02d}", ln=1)

        # Título
        self.set_font("helvetica", "B", 13)
        self.set_text_color(*text)
        self.cell(0, 7, _safe_latin(label).upper(), ln=1)

        # Línea inferior
        self.set_draw_color(*border)
        self.set_line_width(0.2)
        y = self.get_y() + 1
        self.line(self.l_margin, y, self.w - self.r_margin, y)
        self.ln(5)

    # ---------- markdown ligero ------------------------------------------

    def _render_markdown(self, text: str) -> None:
        if not text:
            return
        text_color = _hex_to_rgb(self.theme["textColor"], (17, 24, 39))
        page_w = self.w - self.l_margin - self.r_margin

        for raw_line in str(text).split("\n"):
            line = raw_line.rstrip()
            if not line.strip():
                self.ln(2)
                continue

            # Headings
            m = re.match(r"^(#{1,3})\s+(.*)$", line)
            if m:
                clean = _safe_latin(_strip_md_inline(m.group(2)))
                self.set_font("helvetica", "B", 10.5)
                self.set_text_color(*text_color)
                self.set_x(self.l_margin)
                self.multi_cell(page_w, 6, clean)
                self.ln(1)
                continue

            # Bullets
            m = re.match(r"^[\-\*]\s+(.*)$", line.lstrip())
            if m:
                clean = _safe_latin(_strip_md_inline(m.group(1)))
                self.set_font("helvetica", "", 10)
                self.set_text_color(*text_color)
                # Ancho del bullet
                bullet = "- "
                bw = 5  # mm de indent
                self.set_x(self.l_margin)
                self.cell(bw, 5.4, bullet)
                # Texto en el resto del ancho — clave: usar (page_w - bw) NO 0
                self.multi_cell(page_w - bw, 5.4, clean)
                continue

            # Body normal
            self.set_font("helvetica", "", 10)
            self.set_text_color(*text_color)
            self.set_x(self.l_margin)
            self.multi_cell(page_w, 5.4, _safe_latin(_strip_md_inline(line)))

    # ---------- tablas ---------------------------------------------------

    def _render_data_table(
        self,
        headers: list[str],
        rows: list[list[str]],
        widths_mm: list[float],
        include_index: bool = False,
    ) -> None:
        if include_index:
            headers = ["#"] + headers
            widths_mm = [10.0] + widths_mm
            rows = [[str(i + 1)] + r for i, r in enumerate(rows)]

        # Normalizar a 174mm (page width útil con márgenes 18+18)
        page_w = self.w - self.l_margin - self.r_margin
        total = sum(widths_mm)
        if total > 0 and abs(total - page_w) > 0.5:
            factor = page_w / total
            widths_mm = [round(w * factor, 2) for w in widths_mm]

        text_color = _hex_to_rgb(self.theme["textColor"], (17, 24, 39))
        border = _hex_to_rgb(self.theme["borderColor"], (226, 232, 240))
        header_bg = _hex_to_rgb(self.theme["tableHeaderBg"], (15, 23, 42))
        header_tc = _hex_to_rgb(self.theme["tableHeaderTextColor"], (255, 255, 255))
        alt_bg = _hex_to_rgb(self.theme["rowAltBg"], (248, 250, 252))

        line_h = 5.5

        # Header
        if self.get_y() > self.h - 30:
            self.add_page()

        self.set_draw_color(*border)
        self.set_line_width(0.2)
        self.set_fill_color(*header_bg)
        self.set_text_color(*header_tc)
        self.set_font("helvetica", "B", 9)
        x0 = self.l_margin
        y = self.get_y()
        for i, h in enumerate(headers):
            self.set_xy(x0 + sum(widths_mm[:i]), y)
            self.cell(widths_mm[i], 8, _safe_latin(h), border=1, fill=True, align="L")
        self.ln(8)

        # Data rows con altura calculada
        self.set_text_color(*text_color)
        self.set_font("helvetica", "", 9)

        for r_idx, row in enumerate(rows):
            # Calcular altura máx por celda (multi_cell puede ocupar varias líneas)
            cell_lines = []
            for i, val in enumerate(row[: len(headers)]):
                txt = _safe_latin(str(val) if val is not None else "")
                lines = self.multi_cell(widths_mm[i], line_h, txt, split_only=True)
                cell_lines.append(lines or [""])
            max_lines = max(len(l) for l in cell_lines)
            row_h = max_lines * line_h + 2

            # Salto de página si no cabe
            if self.get_y() + row_h > self.h - 25:
                self.add_page()

            y_top = self.get_y()
            fill = r_idx % 2 == 1
            if fill:
                self.set_fill_color(*alt_bg)

            for i, val in enumerate(row[: len(headers)]):
                x_cell = x0 + sum(widths_mm[:i])
                # Background + border de la celda
                self.set_xy(x_cell, y_top)
                self.cell(widths_mm[i], row_h, "", border=1, fill=fill)
                # Texto
                self.set_xy(x_cell + 1.5, y_top + 1)
                txt = _safe_latin(str(val) if val is not None else "")
                self.multi_cell(widths_mm[i] - 3, line_h, txt, align="L" if i > 0 or not include_index else "C")
            self.set_y(y_top + row_h)

        self.ln(3)

    # ---------- bloques --------------------------------------------------

    def _block_meta(self) -> None:
        rows = [
            ["Acta No.", self.data.get("no_acta") or "-"],
            ["Fecha", _format_human_date(self.data.get("fecha_documento") or self.data.get("date") or "")],
            ["Proyecto", self.data.get("proyecto") or "General"],
            ["Idioma", self.data.get("idioma") or "Espanol"],
            ["Asunto", self.data.get("subtitulo_documento") or self.data.get("title") or "-"],
        ]
        # Tabla 2 cols (no incluir índice)
        text_color = _hex_to_rgb(self.theme["textColor"], (17, 24, 39))
        muted = _hex_to_rgb(self.theme["mutedColor"], (100, 116, 139))
        border = _hex_to_rgb(self.theme["borderColor"], (226, 232, 240))
        page_w = self.w - self.l_margin - self.r_margin
        w_label, w_value = 38, page_w - 38
        line_h = 5.5

        self.set_draw_color(*border)
        self.set_line_width(0.2)

        for label, value in rows:
            txt = _safe_latin(str(value) if value is not None else "-")
            lines = self.multi_cell(w_value - 3, line_h, txt, split_only=True) or [""]
            row_h = max(len(lines) * line_h + 2, 8)

            if self.get_y() + row_h > self.h - 25:
                self.add_page()

            y_top = self.get_y()
            x0 = self.l_margin
            # Label cell
            self.set_xy(x0, y_top)
            self.cell(w_label, row_h, "", border=1)
            self.set_xy(x0 + 2, y_top + 1.5)
            self.set_font("helvetica", "B", 8)
            self.set_text_color(*muted)
            self.cell(w_label - 4, 5, _safe_latin(label).upper())
            # Value cell
            self.set_xy(x0 + w_label, y_top)
            self.cell(w_value, row_h, "", border=1)
            self.set_xy(x0 + w_label + 2, y_top + 1.5)
            self.set_font("helvetica", "", 9.5)
            self.set_text_color(*text_color)
            self.multi_cell(w_value - 3, line_h, txt)
            self.set_y(y_top + row_h)

        self.ln(3)

    def _block_attendees(self) -> None:
        attendees = self.data.get("asistentes") or []
        if not attendees:
            self._render_empty("No se registraron asistentes en esta sesion.")
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
        page_w = self.w - self.l_margin - self.r_margin
        # 6:5:5 ratio
        widths = [page_w * 6 / 16, page_w * 5 / 16, page_w * 5 / 16]
        self._render_data_table(
            ["Nombre y apellidos", "Cargo / Rol", "Entidad"],
            rows, widths_mm=widths, include_index=True,
        )

    def _block_summary(self) -> None:
        text = self.data.get("contexto_antecedentes") or self.data.get("summary") or ""
        if not (text or "").strip():
            self._render_empty("No se genero resumen ejecutivo para esta sesion.")
            return
        self._render_markdown(text)

    def _block_decisions(self) -> None:
        text = self.data.get("decisiones") or self.data.get("decisions") or ""
        if not (text or "").strip():
            self._render_empty("No se registraron decisiones clave.")
            return
        self._render_markdown(text)

    def _block_risks(self) -> None:
        text = self.data.get("riesgos") or self.data.get("risks") or ""
        if not (text or "").strip():
            self._render_empty("No se identificaron riesgos relevantes.")
            return
        self._render_markdown(text)

    def _block_agreements(self) -> None:
        text = self.data.get("acuerdos") or self.data.get("agreements") or ""
        if not (text or "").strip():
            self._render_empty("No se registraron acuerdos formales.")
            return
        self._render_markdown(text)

    def _block_action_items(self) -> None:
        items = self.data.get("compromisos") or self.data.get("action_items") or []
        if not items:
            self._render_empty("No hay tareas o compromisos asignados.")
            return
        rows = []
        for it in items:
            if isinstance(it, dict):
                owner = it.get("owner_name") or it.get("owner_email") or "-"
                due = _format_human_date(it.get("due_date") or "") or "-"
                rows.append([
                    it.get("title") or "-",
                    owner,
                    due,
                    (it.get("priority") or "media").capitalize(),
                ])
            else:
                rows.append([str(it), "-", "-", "Media"])
        page_w = self.w - self.l_margin - self.r_margin
        # 7.5:4:3.5:2 → total 17 → repartir a page_w
        widths = [page_w * 7.5 / 17, page_w * 4 / 17, page_w * 3.5 / 17, page_w * 2 / 17]
        self._render_data_table(
            ["Descripcion", "Responsable", "Fecha limite", "Prioridad"],
            rows, widths_mm=widths, include_index=True,
        )

    def _render_empty(self, msg: str) -> None:
        muted = _hex_to_rgb(self.theme["mutedColor"], (100, 116, 139))
        self.set_font("helvetica", "I", 9)
        self.set_text_color(*muted)
        page_w = self.w - self.l_margin - self.r_margin
        self.set_x(self.l_margin)
        self.multi_cell(page_w, 5, _safe_latin(msg))
        self.ln(2)

    # ---------- ensamble -------------------------------------------------

    def _block_has_content(self, block_id: str) -> bool:
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

    def _resolve_block_order(self) -> list[str]:
        mapping = self.data.get("mapping_config") or []
        order = []
        for blk in mapping:
            bid = blk.get("id") if isinstance(blk, dict) else blk
            if bid in _BLOCK_LABELS and bid not in order:
                order.append(bid)
        if not order:
            order = ["meta", "attendees", "summary", "decisions", "risks", "agreements", "action_items"]
        if "meta" in order:
            order = ["meta"] + [b for b in order if b != "meta"]
        return order

    def render_all(self) -> None:
        self._build_cover()

        order = self._resolve_block_order()
        renderable = [b for b in order if self._block_has_content(b)]

        if renderable:
            self.add_page()
            for idx, block_id in enumerate(renderable, start=1):
                self._section_header(idx, _BLOCK_LABELS[block_id])
                method = getattr(self, f"_block_{block_id}", None)
                if method:
                    method()

    def generar_buffer(self) -> io.BytesIO:
        self.render_all()
        out = self.output(dest="S")
        if isinstance(out, str):
            out = out.encode("latin-1")
        elif isinstance(out, bytearray):
            out = bytes(out)
        return io.BytesIO(out)
