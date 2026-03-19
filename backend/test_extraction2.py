import asyncio
from services.groq_service import GroqService

transcript_test = """
Listo. Buenos días. Este proyecto o esta sesión está enfocada en una presentación Andorra Business de una serie de productos de inteligencia artificial. Mi nombre es Felipe Cortés, CPO de Escape y está Gerente General, Master Lead de Andorra Business. No tanto, no tanto, Frank. Y la idea de poder hacer esta sesión es poder encontrarnos un poco más, que nos conozcan, poder estar en directorios digitales, poder estar en el conocimiento de las empresas. Aquí la idea en general es poder apoyarnos entre todos. Entonces, finalizando esta sesión, el día de mañana, 17 de marzo, enviaré un informe sobre lo que vamos a hacer. Y Frank el día 18 estará respondiéndonos el correo.

Action items
Felipe Cortés
Enviar informe sobre el proyecto y actividades acordadas el 17 de marzo (00:00)

Frank
Responder el correo del informe enviado por Felipe Cortés el 18 de marzo (00:00)
"""

project_contacts_test = [
    {"name": "Felipe Cortés", "email": "felipe@test.com"},
    {"name": "Frank", "email": "frank@test.com"}
]

async def run_test():
    svc = GroqService()
    print("Testing transcript extraction...")
    try:
        res = await svc.process_transcript(transcript_test, project_contacts_test)
        print("\n====== SUMMARY ======")
        print(res.get("summary"))
        print("\n====== ACTION ITEMS ======")
        items = res.get("action_items", [])
        if not items:
            print("WARNING: No action items found!")
        else:
            print(f"SUCCESS! Found {len(items)} action items:")
            for item in items:
                print(f" - [{item.get('owner_name')}] {item.get('title')}: {item.get('description')}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(run_test())
