"""Generación de actas Word usando una plantilla .docx custom del cliente.

Flujo:
1. Cargar la plantilla del cliente (`{{title}}`, `{{summary}}`, etc.).
2. `DocxTemplate.render()` llena los placeholders.
3. Si hay `mapping_config`, APPEND los bloques (Identificación, Asistentes,
   Resumen, Decisiones, Riesgos, Acuerdos, Tareas) usando exactamente el
   mismo sistema visual que `CorporateDocxGenerator` — para que el output
   se vea consistente con la opción sin plantilla.

El sistema visual está aislado en `CorporateDocxGenerator`. Acá lo
reutilizamos vía un wrapper que fuerza al generador a operar sobre el
documento ya cargado en lugar de crear uno nuevo.
"""

from __future__ import annotations

import os
from typing import Any, Dict

from docxtpl import DocxTemplate

from services.docx_generator import (
    CorporateDocxGenerator,
    _BLOCK_LABELS,
)


class WordGeneratorService:
    def __init__(self, templates_dir: str = "./templates") -> None:
        self.templates_dir = templates_dir
        if not os.path.exists(self.templates_dir):
            os.makedirs(self.templates_dir)

    def generate_document(
        self, template_path: str, meeting_data: Dict[str, Any], output_path: str
    ) -> str:
        """Genera el acta llenando la plantilla del cliente y append de bloques."""
        local_template_path = self._materialize_template(template_path)

        if not os.path.exists(local_template_path):
            raise FileNotFoundError(
                f"No se encontró la plantilla en {local_template_path}"
            )

        try:
            doc = DocxTemplate(local_template_path)
        except Exception as e:  # noqa: BLE001
            raise Exception(
                "La plantilla proporcionada no es un documento Word (.docx) válido "
                f"o está corrupta. Error: {e}"
            ) from e

        # 1) Render del contexto en la plantilla
        context = {
            "title": meeting_data.get("title", "Sin Título"),
            "date": meeting_data.get("date", ""),
            "summary": meeting_data.get("summary", "Sin resumen"),
            "decisions": meeting_data.get("decisions", "Ninguna decisión registrada"),
            "risks": meeting_data.get("risks", "Ningún riesgo detectado"),
            "agreements": meeting_data.get("agreements", "Ningún acuerdo"),
            "action_items": meeting_data.get("action_items", []),
        }
        doc.render(context)
        doc.save(output_path)

        # 2) Si hay mapping_config, append de los bloques en el orden indicado
        #    usando EXACTAMENTE el mismo sistema visual que el generador
        #    standalone (CorporateDocxGenerator). Esto garantiza que tanto la
        #    opción "sin plantilla" como "con plantilla" generen secciones que
        #    se ven idénticas.
        mapping_config = meeting_data.get("mapping_config") or []
        if mapping_config:
            self._append_pro_blocks(output_path, meeting_data, mapping_config)

        return output_path

    # ---------- helpers ---------------------------------------------------

    def _materialize_template(self, template_path: str) -> str:
        """Si la plantilla es URL, la descarga a /tmp para poder abrirla."""
        if template_path.startswith(("http://", "https://")):
            import urllib.request
            import uuid

            local = f"/tmp/{uuid.uuid4()}.docx"
            req = urllib.request.Request(
                template_path,
                headers={"User-Agent": "Mozilla/5.0 (Notiva-Generator)"},
            )
            try:
                with urllib.request.urlopen(req) as resp, open(local, "wb") as out:
                    out.write(resp.read())
            except Exception as e:  # noqa: BLE001
                raise Exception(
                    f"Error descargando plantilla desde {template_path}: {e}"
                ) from e
            return local
        return template_path

    def _append_pro_blocks(
        self,
        doc_path: str,
        meeting_data: Dict[str, Any],
        mapping_config: list,
    ) -> None:
        """Append de bloques pro al final del documento usando el mismo
        sistema visual de CorporateDocxGenerator."""
        from docx import Document

        # Reabrimos el doc post-render
        existing_doc = Document(doc_path)

        # Trick: instanciar CorporateDocxGenerator pero reemplazar su `self.doc`
        # con el documento existente. Re-registramos los estilos `act_*` en el
        # documento del cliente, PERO sin sobrescribir Normal — eso destruiría
        # la tipografía corporativa que el cliente puso en su plantilla.
        gen = CorporateDocxGenerator(meeting_data)
        gen.doc = existing_doc
        gen._register_styles(override_normal=False)

        # NO agregar page break automático. Si la plantilla del cliente
        # ya tiene contenido (placeholders rendereados con docxtpl), el
        # bloque pro se appendea inmediatamente debajo sin desperdiciar
        # una página vacía.
        # Si el cliente NECESITA forzar un salto, puede agregarlo en su
        # propio template como parte del diseño.

        # Render de los bloques con numeración secuencial sobre los que
        # tienen contenido — misma lógica que el standalone.
        order = []
        for blk in mapping_config:
            bid = blk.get("id") if isinstance(blk, dict) else blk
            if bid in _BLOCK_LABELS and bid not in order:
                order.append(bid)
        if not order:
            order = list(_BLOCK_LABELS.keys())

        renderable = [b for b in order if gen._block_has_content(b)]
        for idx, block_id in enumerate(renderable, start=1):
            gen._section_header(idx, _BLOCK_LABELS[block_id])
            method = getattr(gen, f"_block_{block_id}", None)
            if method:
                method()

        existing_doc.save(doc_path)
