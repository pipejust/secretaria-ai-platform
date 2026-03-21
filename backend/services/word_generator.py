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
            local_template_path = f"/tmp/{os.path.basename(template_path)}"
            if not os.path.exists(local_template_path):
                import urllib.request
                try:
                    urllib.request.urlretrieve(template_path, local_template_path)
                except Exception as e:
                    raise Exception(f"Failed to download remote template from {template_path}: {e}")
        
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
        return output_path
