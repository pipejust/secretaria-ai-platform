# Bot propio como entrada de Acten

Se implementó una entrada directa para reemplazar Fireflies en nuevas reuniones. El desarrollo del bot está en el proyecto hermano `conversacionalbot`. La configuración completa, responsabilidades y límites se documentan en [ACTEN.md](../../conversacionalbot/docs/ACTEN.md).

## Endpoints

- `POST /api/v1/bot/meetings`: ingreso firmado con `X-API-Key` de la empresa y scope `sessions:write`.
- `GET /api/v1/sessions/{session_id}/bot-source`: fuente estructurada y estado; scope `sessions:read` y visibilidad habitual por empresa/proyecto.
- `POST /api/v1/bot/meetings/{meeting_id}/retry`: reintento del procesamiento fallido; scope `sessions:write`.

Contrato: [bot-ingest.schema.json](bot-ingest.schema.json). La firma `X-Bot-Signature` es `v1=` más HMAC-SHA256 hexadecimal de `X-Bot-Timestamp + "." + cuerpo original`, usando la clave de API recibida y autenticada. Se verifica una tolerancia de cinco minutos. `X-Bot-Event-Id` debe coincidir con el ID del evento. Estos encabezados corresponden a la entrada del bot; los webhooks de salida `X-Acten-*` mantienen su contrato actual.

La sesión y su fuente en `botinbox` se guardan en una única transacción. La respuesta 202 contiene `event_id`, `meeting_id`, `tenant_id`, `session_id` y `state`. Una reentrega idéntica no repite la sesión ni sobrescribe correcciones; un contenido cambiado devuelve 409.

El proceso de `cron_service` revisa esa entrada cada 30 segundos, con hasta cuatro trabajos en paralelo. Llama a `process_session_with_ai`, que conserva la curación, proyectos, tareas, embeddings y eventos de Acten. No se despachan contenidos desde el receptor antes de procesarlos. Un evento aceptado no significa que ese procesamiento ya haya terminado.

El identificador legado `fireflies_id` recibe el prefijo `BOT-` para reutilizar el modelo existente. Los reintentos de Fireflies excluyen este prefijo. La fuente completa conserva los segmentos con tiempos y hablantes, puntos clave, timeline, evidencias y procedencia; `raw_transcript` y `raw_summary` se derivan de ese material.

## Activación y operación

La tabla `botinbox` se registra antes de `create_db_and_tables`. Verificar la inicialización en PostgreSQL antes de activar la nueva versión en producción. No se ejecutaron migraciones contra bases reales durante el desarrollo.

Crear una clave de API por empresa para el bot y configurar `ACTEN_TARGETS_JSON` en el servicio del bot. No se copian las llaves de Groq guardadas en Acten: su pipeline continúa resolviendo la llave de la empresa. El análisis base del bot utiliza la cuenta configurada explícitamente en ese servicio.

Los fallos del pipeline quedan como `failed`; usar el endpoint de reintento tras corregir la causa. Un trabajo interrumpido en `processing` requiere revisión operativa antes de recuperarlo; no se reinicia automáticamente mientras otro ejecutor pudiera seguir actuando. Retención, alertas y recuperación automática quedan pendientes antes de disponibilidad comercial.

La pantalla `/admin/meeting-bot` implementa entrada por enlace, grabación web, resultados y confirmación de voces. Un proceso IMAP del bot recibe invitaciones ICS; no se conectó por OAuth el calendario personal. Ver [continuidad del desarrollo](BOT_PROPIO_CONTINUIDAD.md) y el [manual completo](../../conversacionalbot/docs/CONTINUIDAD.md). Pruebas locales: 96 backend Acten, 20 frontend y 74 del bot aprobadas. No se realizaron reuniones ni envíos externos reales.

Los controles de captura usan `/api/owned-bot` con JWT, permisos de usuario y clave del servicio cifrada. La firma de ingreso cubre los bytes transmitidos, incluso cuando `Content-Encoding: gzip` está presente; el receptor autentica antes de descomprimir y acota el tamaño expandido. Las etiquetas humanas se guardan separadas del evento original.
