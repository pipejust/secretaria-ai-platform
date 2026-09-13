# Continuidad de la captura propia de Acten

Actualizado: 12 de septiembre de 2026.

La guía completa y vigente está en el proyecto hermano: [CONTINUIDAD.md](../../conversacionalbot/docs/CONTINUIDAD.md). Incluye instalación, configuración, correo/ICS, grabación web, arquitectura, contratos, recuperación, pruebas y próximos pasos. Conservar ambos proyectos al transferir este desarrollo.

## Entrada rápida

- Pantalla: `/admin/meeting-bot`, accesible desde «Bot de reuniones» en el menú.
- Acciones: entrar a Meet/Teams/Zoom mediante enlace, grabar pestaña/sistema + micrófono o solo micrófono, ver dirección de invitación por correo, consultar resumen/transcripción/timeline y confirmar nombres.
- Configuración admin: origen del bot y clave de empresa. Persisten cifrados en `IntegrationSetting('owned_bot')`; no ponerlos en Angular.
- Controles backend: `backend/routers/bot_control.py`, con JWT, roles y aislamiento por empresa/propietario.
- Recepción: `backend/routers/bot_ingest.py`, con clave de API de empresa y firma HMAC. Su contrato se explica en [INTEGRACION_BOT_PROPIO.md](INTEGRACION_BOT_PROPIO.md).
- Modelos nuevos: `botinbox`, `botcontrollink`, `botaudiogrant`, `botspeakerlabel`. Verificar creación/migración en staging antes de aplicar a producción.
- Captura/asr: servicio hermano `conversacionalbot`, Skribby para reuniones, Soniox para voces y Groq para análisis. No depende de Fireflies para estas capturas.
- La grabación es en vivo; la transcripción y el resumen llegan después de terminar. Las marcas temporales navegan el audio; no son clips de vídeo.

## Validación y siguiente paso

Pruebas locales al cierre: 96 backend Acten, 20 frontend y 74 del servicio del bot. Todas aprobadas. Compilación de Angular aprobada. Las llamadas a captura/IA fueron simuladas; el ensamblado/remultiplexado de audio usa FFmpeg real con un tono sintético. No se midió precisión de voz ni se probaron reuniones externas.

Siguiente paso: configurar claves y buzón real, desplegar staging HTTPS, verificar PostgreSQL y el volumen de audio compartido, y ejecutar una reunión autorizada corta por cada entrada. Después validar 4–8 horas, navegadores reales, calidad y 5–20 sesiones concurrentes. Retención/borrado, monitoreo, límites globales y recuperación operativa tienen tareas pendientes descritas en la guía principal.

Para regenerar contrato y documentación OpenAPI desde el bot: `uv run python scripts/export_contracts.py --acten ../secretaria`; comprobar sincronía con `--check`. El contrato fuente está en `conversacionalbot/src/conversacionalbot/acten_contract.py` y se copia a `backend/services/bot_contract.py`.

No desactivar Fireflies en clientes existentes hasta completar el piloto del bot propio. Los cambios admiten ambos orígenes y preservan la curación existente.
