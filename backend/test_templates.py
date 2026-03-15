import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlmodel import Session, select
from models import Template
from database import engine
import requests

print("--- REVISANDO PLANTILLAS EN BASE DE DATOS ---")
try:
    with Session(engine) as session:
        templates = session.exec(select(Template)).all()
        if not templates:
            print("No hay plantillas registradas en la DB.")
            sys.exit(0)
            
        for t in templates:
            print(f"\nPlantilla ID: {t.id} | Nombre: {t.name} | Proyecto: {t.project_id}")
            print(f"Ruta: {t.file_path}")
            
            if not t.file_path:
                print(">> ERROR: La ruta del archivo está vacía.")
                continue
                
            if t.file_path.startswith("http"):
                try:
                    r = requests.head(t.file_path, allow_redirects=True, timeout=5)
                    r.raise_for_status()
                    print(f">> OK: URL accesible (Status: {r.status_code})")
                except requests.exceptions.HTTPError as errh:
                    print(f">> ERROR HTTP: {errh}")
                except Exception as e:
                    print(f">> ERROR AL ACCEDER URL: {e}")
            else:
                if os.path.exists(t.file_path):
                    size = os.path.getsize(t.file_path)
                    print(f">> OK: Archivo local existe (Tamaño: {size} bytes)")
                else:
                    print(f">> ERROR: Archivo local NO ENCONTRADO en el disco del servidor. Ruta buscada: {os.path.abspath(t.file_path)}")
except Exception as e:
    print(f"Error conectando a la DB: {e}")
