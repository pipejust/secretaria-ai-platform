import requests

payload = {
  "raw_transcript": "Listo. Buenos días. Este proyecto o esta sesión está enfocada en una presentación Andorra Business de una serie de productos de inteligencia artificial. Mi nombre es Felipe Cortés, CPO de Escape y está Gerente General, Master Lead de Andorra Business. No tanto, no tanto, Frank. Y la idea de poder hacer esta sesión es poder encontrarnos un poco más, que nos conozcan, poder estar en directorios digitales, poder estar en el conocimiento de las empresas. Aquí la idea en general es poder apoyarnos entre todos. Entonces, finalizando esta sesión, el día de mañana, 17 de marzo, enviaré un informe sobre lo que vamos a hacer. Y Frank el día 18 estará respondiéndonos el correo.\n\nAction items\nFelipe Cortés\nEnviar informe sobre el proyecto y actividades acordadas el 17 de marzo (00:00)\n\nFrank\nResponder el correo del informe enviado por Felipe Cortés el 18 de marzo (00:00)"
}

try:
    resp = requests.post("http://localhost:8009/api/sessions/8/regenerate_tasks", json=payload)
    print("STATUS", resp.status_code)
    print(resp.json())
except Exception as e:
    print(e)
