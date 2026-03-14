import sys
import asyncio
from dotenv import load_dotenv

load_dotenv()

sys.path.append("/Users/felipecortes/.gemini/antigravity/scratch/projects/secretaria/backend")
from services.groq_service import GroqService

async def main():
    print("Testing Groq extraction with hardcoded text")
    
    transcript = """
    Hola a todos. Hoy vamos a hablar sobre el proyecto XYZ.
    Felipe: Creo que debemos migrar la base de datos a PostgreSQL la próxima semana.
    Camila: Yo me encargo de hacer el script de migración para el viernes.
    Fernando: De acuerdo, yo valido la infraestructura.
    El riesgo principal es que haya downtime prolongado.
    Acordamos hacer la prueba en staging primero.
    """
    
    svc = GroqService()
    print("Calling Groq...")
    structured_data = await svc.process_transcript(transcript, [])
    
    print("KEYS returned:")
    print(structured_data.keys())
    
    print("\nCONTENT of summary:")
    print(structured_data.get("summary"))
    
    print("\nCONTENT of action_items:")
    print(structured_data.get("action_items"))

if __name__ == "__main__":
    asyncio.run(main())
