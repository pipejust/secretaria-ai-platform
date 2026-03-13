import urllib.request
import urllib.parse
import json

url = "https://secretaria-ai-platform.onrender.com/api/sessions/4"

payload = {
    "raw_summary": "Revisión de Logs: Se solicitarán logs de las 6:00 a 9:30 pm para diagnosticar problemas de intermitencia y autenticación.\nProblemas de Conectividad: Se confirma que problemas de conexión VPN afectan acceso a servicios, no caídas del backend.\nServicio BEPS: La autenticación en el servicio BEPS está funcionando. Fallos se relacionan con conexión y no con credenciales.\nRegistro de Usuarios: Falta de usuarios registrados en Certicámara impide pruebas completas de registro.\nPreparación para Despliegue: Se planea certificar y desplegar en producción.",
    "processed_decisions": "- Continuar revisión de logs para diagnosticar intermitencia.\n- Validar permisos y rutas de acceso VPN.\n- Backend debe mantener integración y actualizar app cuando haya cambios en credenciales.",
    "processed_risks": "",
    "processed_agreements": "Se agendo sesión el próximo Lunes 16-marzo, con Raúl Gutiérrez para validación de registro en app móvil y coordinar certificación funcional.",
    "raw_transcript": "Duración 54 minutos. Extraido desde webhook de Fireflies manual."
}

data = json.dumps(payload).encode('utf-8')
req = urllib.request.Request(url, data=data, method='PUT')
req.add_header('Content-Type', 'application/json')

try:
    print(f"Calling: {url}")
    with urllib.request.urlopen(req, timeout=20) as response:
        print("Status:", response.status)
        print("Response:", response.read().decode('utf-8'))
except Exception as e:
    print("Error:", e)
