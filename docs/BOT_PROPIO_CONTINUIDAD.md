# Continuidad de la captura propia de Acten

Actualizado: 13 de septiembre de 2026.

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

## Selector de origen por empresa (13 de septiembre de 2026)

- `Tenant.meeting_source` ∈ `fireflies` | `owned_bot` | `both` (por defecto `fireflies`; migración `ADD COLUMN IF NOT EXISTS` en `database.py`).
- Lógica en `backend/services/meeting_source.py` (`fuente_de`, `admite`, `cambiar`, `estado`). Endpoints `GET/PUT /api/settings/meeting-source` (admin). `/auth/me` expone `tenant.meeting_source`.
- Efecto: el webhook de Fireflies responde `{"status":"ignored","reason":"meeting_source"}` si la empresa no lo admite; el bot propio se rechaza en `bot_ingest` del mismo modo. Rehidratar/refetch-summary ignoran ids `MANUAL-`/`manual_`/`BOT-` (400).
- Pantalla: Configuración → «Origen de las reuniones» (radios); el ítem «Bot de reuniones» del menú solo aparece si la empresa admite el bot propio. Textos en `es/en/ca`.
- Estado en producción: softnexus (`tenant_id` 7) en «Ambos»; conexión al bot guardada en `IntegrationSetting('owned_bot')` apuntando a `https://bot.acten.app`; `GET /api/owned-bot/capabilities` devuelve `acten_tenant_id: 7`.

## Correcciones aplicadas tras la revisión

- `bot_control.own()`: el administrador ve todos los enlaces de su empresa; el resto solo los propios (404 en caso contrario). `put_config` rechaza `service_url` que apunte a direcciones internas (anti-SSRF, `calendar_ics._destino_permitido`).
- `services/bot_ingest.py`: filas `processing` con más de 30 min pasan a `failed` (`processing_interrupted`) al inicio de cada ciclo del cron.
- `routers/bot_ingest.py`: `/retry` devuelve 409 a partir de 5 intentos.
- `routers/integration_v1.py`: `origin` distingue `manual` / `bot` / `web`.
- `test_bot_contract_parity.py`: huella SHA-256 de `services/bot_contract.py`, idéntica a la del bot.
- Pantalla `meeting-bot`: `owner`/`isAdmin` como getters; errores de carga visibles.

Prueba real: evento firmado → 202, buzón `completed`, sesión con proyecto, 2 tareas con responsable y asistentes. Pruebas: 106 backend, compilación Angular y spec `meeting-bot` (9) aprobadas.

## Pendiente conocido

- Bot sin rate limiting ni heartbeat de lease; claves de cliente sin recarga en caliente.
- `list_incomplete_sessions` no filtra `BOT-`; `ingest()` confía en el caller; falta `PRAGMA foreign_keys` en las pruebas SQLite.
- Pantalla del bot sin i18n completa ni servicio HTTP dedicado; polling silencia errores; colores fijos.
- `npm audit` de la dependencia de pruebas: 40 avisos pendientes de revisión.
- Reuniones reales exigen `SKRIBBY_API_KEY`, `SONIOX_API_KEY` y `MAILBOXES_JSON` en Coolify (app del bot). Memoria del servidor: ~2 GB disponibles.

## Invitaciones por correo: buzón compartido (13 de septiembre de 2026)

- El bot lee **un solo buzón IMAP** (`MAILBOX_JSON` en Coolify, app del bot). Cada empresa recibe la dirección `<cliente>@<dominio>` (p. ej. `softnexus@reuniones.acten.app`); el dominio necesita un catch-all en el proveedor de correo hacia ese buzón.
- La política por empresa (remitentes autorizados, autorización de grabación, zona horaria) se guarda en el bot y se administra desde `/admin/meeting-bot` → «Conexión del bot · Administración» → «Invitaciones por correo». Endpoints Acten: `GET/PUT /api/owned-bot/mail-policy` (admin), proxy de `/v1/mail-policy` del bot.
- `capabilities.invitation_email` muestra la dirección; `mail_enabled` solo es verdadero con política autorizada. Sin `MAILBOX_JSON` en el bot, la tarjeta lo indica y el resto de entradas (enlace, grabación web) siguen funcionando.
- Alta de una empresa nueva: crear su clave en `BOT_CLIENTS_JSON` (identificador en minúsculas, válido como local-part) y `ACTEN_TARGETS_JSON`, guardar la conexión en su pantalla y fijar la política. Nada más que tocar en DNS ni en correo.
