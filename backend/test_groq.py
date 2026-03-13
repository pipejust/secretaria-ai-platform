import asyncio
from services.groq_service import GroqService

async def main():
    service = GroqService()
    transcript = "Hola, esta es una prueba de transcripción breve."
    
    print("Sending test transcript to Groq...")
    try:
        res = await service.process_transcript(transcript)
        print("Success:")
        print(res)
    except Exception as e:
        print("Failed!")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
