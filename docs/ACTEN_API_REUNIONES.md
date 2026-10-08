# Acten API — Guía de integración de reuniones

**Versión del 7 de octubre de 2026.** Para el equipo que integra Acten en su
plataforma. Cubre todo el ciclo de una reunión: crearla (subiendo un
archivo, mandando el bot o programándolo), seguirla en vivo, y leer el acta,
la transcripción y el vídeo.

Sustituye a cualquier nota anterior sobre estos endpoints. El resto del
contrato (tareas, proyectos, empleados, webhooks) no cambia.

---

## 1. Conexión

| | |
|---|---|
| Base | `https://api.acten.app` |
| Referencia en línea | `https://api.acten.app/docs` (siempre al día con lo desplegado) |
| Autenticación | Cabecera `X-API-Key: <clave de la empresa>` |
| En nombre de quién | Cabecera `X-On-Behalf-Of` |
| Formato | JSON, salvo la subida de archivos (`multipart/form-data`) |
| Límite | 120 peticiones por minuto por IP; por encima, `429` |

**`X-On-Behalf-Of`** es obligatoria en todas las lecturas (`GET`) y opcional
en las escrituras:

- UUID del empleado en su sistema → Acten responde solo lo que esa persona
  puede ver (las reuniones de los proyectos donde es miembro).
- `*` → se lee como empresa, sin recortar por persona. Exige el alcance
  `org:read` en la clave.

**Alcances que usa esta guía.** Sus claves actuales ya los tienen todos.

| Alcance | Para qué |
|---|---|
| `sessions:read` | Leer sesiones, transcripción, vídeo, estado del bot y capacidades |
| `sessions:write` | Subir archivos, mandar, programar y detener el bot |
| `integrations:read` / `integrations:write` | Leer y cambiar los ajustes de reuniones de la empresa |
| `org:read` | Usar `X-On-Behalf-Of: *` |

**Errores.** Siempre `{"detail": …}`. `detail` es normalmente un texto; en
algunos `402` (función no incluida en la suscripción) es un objeto con
`message`, `feature` y `label`. Un campo que no existe en el cuerpo responde
`422`: no se ignora en silencio.

---

## 2. Qué decide Acten y qué decide la integración

Esto condiciona el diseño de su interfaz, así que va primero.

| Lo decide **Acten**, en la suscripción de cada empresa | Lo decide **la empresa** (pantalla de Acten o esta API) |
|---|---|
| Si la empresa tiene bot propio | Por dónde entran sus reuniones: Fireflies, bot propio o ambos (entre lo que su suscripción incluya) |
| Si las reuniones se graban con **vídeo** | El nombre con el que aparece el bot |
| Si hay **transcripción en vivo** | A qué reuniones va el bot |
| Cuántos días se conserva el vídeo | |

No existe un parámetro para encender o apagar el vídeo o la transcripción en
vivo por reunión. Consulten qué tiene la empresa en `GET /capabilities` y
muestren u oculten opciones según eso.

---

## 3. Flujo recomendado

1. Al cargar su módulo de reuniones: `GET /api/v1/capabilities`.
2. Crear la sesión por una de estas vías:
   - **Archivo o texto** → `POST /api/v1/sessions`.
   - **Bot ahora** → `POST /api/v1/meetings/live`.
   - **Bot programado** → `POST /api/v1/meetings/live` con `scheduled_start`,
     o invitar a `bot@acten.app` al evento del calendario.
3. Si mandaron el bot: consultar `GET /api/v1/meetings/live/{id}` hasta que
   `acten_session_id` deje de ser `null`. Mientras graba, `realtime_url`
   permite mostrar la transcripción en vivo. Para una zona «En vivo» con
   todas las reuniones en curso, `GET /api/v1/meetings/live`.
4. Leer el resultado: `GET /api/v1/sessions/{id}`, `/transcript` y `/video`.

---

## 4. Capacidades de la empresa

### `GET /api/v1/capabilities`

Alcance `sessions:read`.

```json
{
  "plan": "business",
  "status": "active",
  "meeting_source": "both",
  "fireflies": true,
  "owned_bot": true,
  "video": true,
  "realtime": true,
  "video_retention_days": 90,
  "upload": true,
  "platforms": ["gmeet", "teams", "zoom", "element"],
  "vocem": true,
  "calendar_invitation": {
    "email": "bot@acten.app", "enabled": true,
    "authenticated_platforms": ["gmeet", "teams"]
  }
}
```

| Campo | Significado |
|---|---|
| `owned_bot` | `true` → pueden mandar el bot (`POST /meetings/live`). Si es `false`, esa llamada responde `402` o `409` |
| `fireflies` | La empresa recibe reuniones por Fireflies |
| `meeting_source` | `fireflies`, `owned_bot` o `both` |
| `video` | Las reuniones que graba el bot llevan vídeo además de audio |
| `realtime` | La transcripción se puede seguir en vivo mientras dura la reunión |
| `video_retention_days` | Días que se conserva el vídeo; `null` = sin límite. El acta y la transcripción no caducan |
| `upload` | Subida de archivos disponible (siempre `true`) |
| `calendar_invitation.email` | Dirección a la que se invita al bot desde el calendario |
| `calendar_invitation.enabled` | La empresa ya autorizó las invitaciones por correo |
| `platforms` | Plataformas a las que entra el bot: `gmeet`, `teams`, `zoom` y, si la empresa tiene Vocem, `element` |
| `vocem` | La empresa tiene su servidor Element (Vocem): `POST /meetings/live` admite `create_room` |
| `calendar_invitation.authenticated_platforms` | Plataformas (`gmeet`, `teams`, `zoom`) donde el bot entra con una cuenta propia en vez de como invitado anónimo. Vacío = siempre invitado anónimo |

---

## 5. Ajustes de reuniones de la empresa

### `GET /api/v1/meetings/settings`

Alcance `integrations:read`.

```json
{
  "bot_name": "Asistente Acten",
  "meeting_source": "both",
  "fireflies": true,
  "owned_bot": true,
  "meeting_source_options": [
    {"value": "fireflies", "label": "Fireflies", "available": true},
    {"value": "owned_bot", "label": "Bot propio de Acten", "available": true},
    {"value": "both", "label": "Ambos", "available": true}
  ]
}
```

`available: false` significa que la suscripción de la empresa no incluye esa
opción; muéstrenla deshabilitada.

### `PATCH /api/v1/meetings/settings`

Alcance `integrations:write`. Solo cambia lo que venga. Responde la misma
forma que el `GET`.

| Campo | Tipo | Notas |
|---|---|---|
| `bot_name` | string, 1–50 | Nombre con el que el bot aparece en Meet, Teams y Zoom. Aplica a las reuniones siguientes, incluidas las invitadas por calendario |
| `meeting_source` | `fireflies` \| `owned_bot` \| `both` | `fireflies` = solo Fireflies (el bot queda apagado). `owned_bot` = solo el bot (Fireflies queda apagado: lo que llegue por ahí se rechaza). `both` = los dos |

```bash
curl -X PATCH https://api.acten.app/api/v1/meetings/settings \
  -H "X-API-Key: $ACTEN_KEY" -H "Content-Type: application/json" \
  -d '{"bot_name": "Notas de Acme", "meeting_source": "owned_bot"}'
```

Errores: `402` el origen pedido no está en la suscripción · `403` la llamada
va en nombre de una persona que no es administradora (son ajustes de toda
la empresa) · `422` nombre vacío o de más de 50 caracteres, u origen
desconocido · `503` se quiere cambiar el nombre y la empresa no tiene el bot
configurado.

**Sobre el aviso de «bot no verificado» y la admisión.** Teams y Meet
tratan con recelo a cualquier participante que entra sin cuenta: Teams lo
marca «no verificado» y pide confirmación al organizador; Meet lo deja
tocando la puerta hasta que alguien lo admite. Por eso Acten hace entrar al
bot **con una cuenta propia** cuando la plataforma aparece en
`capabilities.calendar_invitation.authenticated_platforms`: en Meet, al ir
invitado al evento con esa cuenta, entra sin pedir permiso; en Teams deja
de ser un invitado anónimo, aunque Microsoft puede seguir marcando bots
externos según su política. Con cuenta, el nombre que se ve es el de la
cuenta y no el `bot_name`. Sin cuenta para esa plataforma, el bot entra
como invitado y hay que admitirlo; el aviso no impide grabar.

**Con `both`, si los dos graban la misma reunión queda una sola sesión: la
del bot.** Acten reconoce que son la misma reunión comparando las
transcripciones. La copia de Fireflies se conserva, pero archivada
(`status: "archived"`, con `duplicate_of_session_id` apuntando a la del
bot): no genera tareas, correos ni eventos, y no aparece en los listados
salvo que se pida `?status=archived`. Esto solo funciona dentro de una
misma empresa.

**El origen elegido se respeta en los dos sentidos.** Con `fireflies`, el bot
no arranca (`409`). Con `owned_bot`, lo que llegue de Fireflies se rechaza.

---

## 6. Crear una sesión

### 6.1 Subir una grabación o un texto — `POST /api/v1/sessions`

Alcance `sessions:write`. Cuerpo en **`multipart/form-data`**, con **uno** de
`file` o `text_content`.

| Campo | Tipo | Notas |
|---|---|---|
| `title` | string | **Obligatorio.** Máx. 300 |
| `file` | archivo | Audio o vídeo (`.mp3 .wav .m4a .mp4 .mpeg .mpga .webm .flac .ogg`): Acten lo transcribe. Cualquier otro archivo se lee como texto. Máximo 100 MB |
| `text_content` | string | Transcripción o notas ya escritas, en lugar de `file` |
| `youtube_url` | string | Enlace de YouTube: se descarga el audio y se transcribe, en lugar de `file` |
| `date` | ISO 8601 | Fecha de la reunión. Por defecto, el momento de la subida |
| `language` | `es` \| `en` \| `ca` | Si falta, Acten lo detecta |
| `project_external_id` | string | La sesión nace en ese proyecto |

```bash
curl -X POST https://api.acten.app/api/v1/sessions \
  -H "X-API-Key: $ACTEN_KEY" \
  -F title="Comité de obra" -F language=es \
  -F project_external_id="6cd5df21-…" -F file=@reunion.mp3
```

Respuesta `202`:

```json
{"id": 1412, "title": "Comité de obra", "date": "2026-10-02T09:00:00-05:00",
 "status": "processing", "project_external_id": "6cd5df21-…"}
```

La transcripción del audio ocurre **dentro de la llamada** (tarda según la
duración: usen un tiempo de espera amplio). El acta y las tareas se generan
después: consulten `GET /api/v1/sessions/{id}` hasta que `status` deje de
ser `processing`.

Errores: `422` falta `title`, vienen `file` y `text_content` a la vez o
ninguno, fecha o idioma inválidos, proyecto desconocido · `400` el archivo
no dejó texto aprovechable · `413` archivo demasiado grande · `502` el
servicio de transcripción falló (se puede reintentar).

### 6.2 Mandar el bot a una reunión — `POST /api/v1/meetings/live`

Alcance `sessions:write`. El bot pide entrar a la reunión. Si la plataforma
no está en `authenticated_platforms`, **alguien de la reunión tiene que
admitirlo**.

| Campo | Tipo | Notas |
|---|---|---|
| `meeting_url` | string | Enlace directo de Google Meet, Microsoft Teams, Zoom o Element Call (`https://call.<dominio>/room/#?roomId=…&viaServers=…`). Obligatorio salvo con `create_room` |
| `create_room` | boolean | Si la empresa tiene Vocem (`capabilities.vocem`), Acten crea la sala de Element lista para la llamada, invita a los usuarios de la empresa con cuenta, mete al bot y devuelve el enlace en `meeting_url` y `join_url` (el mismo; ver «El enlace de Element» en 6.3). Quien abra el enlace necesita una cuenta en el servidor Element de la empresa; no hay acceso anónimo. Sin enlace y sin esto: `422` |
| `recording_authorized` | `true` | **Obligatorio y literal.** Quien llama declara que los asistentes saben que se graba |
| `scheduled_start` | ISO 8601 con zona | Reunión programada: el bot entra a esa hora (`2026-11-02T09:00:00-05:00`). Sin zona → `422`. Ausente = entra ahora. Hasta un año hacia adelante |
| `title` | string | Título de la sesión. Por defecto «Reunión» |
| `language` | `es` \| `en` \| `ca` | Por defecto `es` |
| `project_external_id` | string | La sesión nace en ese proyecto. Sin él, Acten lo deduce al procesarla |
| `external_id` | string | Idempotencia: repetir la llamada con el mismo valor **no** manda otro bot. Recomendado: el identificador del evento en su calendario |
| `video` | bool | Obsoleto y sin efecto. Se acepta para no romper integraciones |

```bash
curl -X POST https://api.acten.app/api/v1/meetings/live \
  -H "X-API-Key: $ACTEN_KEY" -H "Content-Type: application/json" \
  -d '{"meeting_url": "https://meet.google.com/abc-defg-hij",
       "recording_authorized": true, "title": "Comité del lunes",
       "external_id": "evento-77", "project_external_id": "6cd5df21-…",
       "scheduled_start": "2026-11-02T09:00:00-05:00"}'
```

Respuesta `202`:

```json
{"id": "f51d6936-fd0e-4e45-8370-da871e2cf2dc", "external_id": "evento-77",
 "state": "scheduled", "title": "Comité del lunes",
 "scheduled_start": "2026-11-02T09:00:00-05:00", "error_code": null,
 "realtime_url": null, "project_external_id": "6cd5df21-…",
 "acten_session_id": null}
```

Guarden `id`: es el que se usa para consultar y detener.

Para **mover** una reunión programada: deténganla y créenla de nuevo con otro
`external_id`. El mismo `external_id` con otra hora responde `409`.

Errores: `402` la empresa no tiene el bot en su suscripción · `409` la
empresa no tiene el bot como origen de reuniones, o ya hay un bot de la
empresa en ese enlace · `422` enlace no admitido, fecha sin zona o proyecto
desconocido · `429` la empresa llegó a su límite de bots simultáneos o de
reuniones programadas · `503` bot sin configurar.

### 6.3 Invitar al bot desde el calendario (sin integración)

Lo mismo que hacen otras plataformas: se añade **`bot@acten.app`** como un
invitado más del evento en Google Calendar u Outlook.

- La dirección es **una sola para todas las empresas**. El bot sabe a qué
  empresa pertenece la reunión por **quién envía la invitación**.
- **Quién puede invitar**: los usuarios activos de la empresa en Acten (se
  sincronizan solos) y los remitentes extra que su administrador añada en
  Acten. La invitación de cualquier otro remitente se ignora.
- **Qué debe traer el evento**: fecha y hora, y un enlace directo de Meet,
  Teams, Zoom o Element (`https://call.<dominio>/room/#?roomId=…&viaServers=…`).
  Los eventos de día completo se ignoran.
- **Element**: la sala tiene que existir antes y estar creada sin cifrado
  extremo a extremo y con `join_rule: public` (así la crea Vocem por API); el
  bot entra por el id de sala. Las salas «por nombre» de Element Call no sirven.

**El enlace de Element.** Una sesión de Element se comparte con **un solo
enlace**, el de Element Call con el id de la sala en la query:

```text
https://call.<dominio>/room/#?roomId=<room_id urlencoded>&viaServers=<dominio>
```

Ejemplo: `https://call.vocem.softnexus.co/room/#?roomId=%21XB9gW1Qlq…&viaServers=softnexus.co`.
Es el que devuelven `meeting_url` y `join_url` en `POST /meetings/live` con
`create_room`, el que va en `meeting_url` al mandar el bot, y el que se pone
en la invitación del calendario para la gente. Quien lo abre inicia sesión
con su cuenta del servidor y entra a la antesala con el botón **Join call**.

Lo que **no** sirve: el enlace del chat (`https://<chat>/#/room/!id`, abre el
chat y no la llamada), las salas «por nombre» de Element Call
(`https://call.<dominio>/<nombre>`, crean otra sala sin el bot) y añadir
`:dominio` al id de sala (los ids de este servidor no lo llevan).

**Crear la sala.** Lo más simple es `create_room: true` en `POST /meetings/live`:
Acten crea la sala como la exige Element Call (pública dentro del servidor, sin
federación, sin cifrado extremo a extremo, con los niveles de poder que la
llamada necesita), invita a los usuarios de la empresa con cuenta y mete al
bot. Si Altum prefiere crear la sala por la API de Vocem, debe usar el cuerpo
exacto que documenta Vocem (`preset: public_chat`, `visibility: private`,
`creation_content.m.federate: false`, `power_level_content_override` con
`m.rtc.member`, `org.matrix.msc3401.call.member` e `io.element.video.member` a
0) y luego mandar el bot con ese enlace.

**Cuándo le aparece la llamada a la gente.** Con `create_room` Acten no
invita a nadie al crear la sala: el enlace es lo que se reparte. La llamada
aparece en el chat de los usuarios de la empresa con cuenta **dos minutos
antes de `scheduled_start`**, o de inmediato si la sesión es ahora o falta
menos de dos minutos. Así una sesión de dentro de tres días no aparece hoy
en el chat de nadie. Lo mismo vale para los eventos que se crean desde el
calendario de Acten con «Crear la reunión en Element».
- **Cambios y cancelaciones**: el calendario manda la actualización y el bot
  la sigue. Los eventos recurrentes se programan solos, ocurrencia por
  ocurrencia.
- Requiere `calendar_invitation.enabled: true`.

**No usen las dos vías para la misma reunión** (invitación por calendario y
`scheduled_start`): entrarían dos bots.

---

## 7. Seguir y detener una reunión del bot

### `GET /api/v1/meetings/live`

Alcance `sessions:read`. Las reuniones del bot de la empresa que **aún no
terminan**: programadas, entrando, grabando o preparando el acta. Es lo que
se necesita para pintar una zona «En vivo».

```json
{"items": [
  {"id": "db7227b5-…", "external_id": "evento-77", "state": "recording",
   "title": "Licitaciones contexto general", "scheduled_start": null,
   "error_code": null, "realtime_url": "wss://…", "project_external_id": "…",
   "acten_session_id": null}
]}
```

- `?status=all` devuelve también las terminadas (las últimas 100).
- Leyendo como empresa (`X-On-Behalf-Of: *`) salen **todas**, incluidas las
  invitadas por calendario. En nombre de una persona, solo las que pidió ella
  o las de sus proyectos.
- Refresquen cada 5–10 segundos mientras la zona esté a la vista: una reunión
  que termina desaparece de la lista y aparece como sesión.

### `GET /api/v1/meetings/live/{id}`

Alcance `sessions:read`. Misma forma que la respuesta de creación.

| `state` | Qué pasa |
|---|---|
| `scheduled` | Programada; el bot aún no entra |
| `queued`, `dispatching` | En cola para entrar |
| `joining` | Entrando o esperando a que lo admitan |
| `recording` | Dentro y grabando |
| `transcribing`, `analyzing` | La reunión terminó; se prepara el acta |
| `completed` | Lista. `acten_session_id` trae la sesión |
| `failed`, `analysis_failed`, `cancelled`, `needs_attention` | Terminó sin sesión; el motivo va en `error_code` |

Traten cualquier valor desconocido como «en curso». Motivos frecuentes en
`error_code`: `capture_not_admitted` (nadie admitió al bot en 10 minutos),
`capture_no_speech` (no hubo voz que transcribir),
`scheduled_start_missed` (no pudo entrar en los 15 minutos siguientes a la
hora programada).

Solo responde por reuniones pedidas a través de Acten (esta API o su
pantalla). Las invitadas por calendario no tienen `id` consultable aquí:
aparecen directamente como sesión al terminar.

### Transcripción en vivo — `realtime_url`

Si `capabilities.realtime` es `true`, mientras el bot está en la reunión la
respuesta trae `realtime_url`: un WebSocket (`wss://…`) de **solo lectura**
que se puede abrir directamente desde el navegador. Es `null` antes de que
el bot entre y después de que salga.

Mensajes JSON con la forma `{"type": …, "data": …}`:

| `type` | Contenido |
|---|---|
| `connected` | Primer mensaje. `data.transcripts` trae todo lo dicho hasta ese momento (sirve para reconectar sin perder nada) y `data.status` el estado |
| `ts` | Un segmento nuevo: `{"transcript": "…", "start": 1.23, "end": 4.56, "speaker": 0, "speaker_name": "Ana Ruiz"}` |
| `status-update` | `data.new_status`. Con `finished` la reunión terminó |

`start` y `end` son segundos desde el inicio de la grabación. Ignoren los
tipos de mensaje que no conozcan.

**Nombres de quien habla.** `speaker` es el número de la voz y no cambia en
toda la reunión. Los primeros segmentos de cada voz llegan con un nombre
provisional (`"Speaker 1"`) y los siguientes ya con el del participante. Para
que la misma persona no salga con dos etiquetas: guarden por cada `speaker`
el último `speaker_name` que **no** sea de la forma `Speaker N` y úsenlo
también para sus segmentos anteriores. Una voz que nunca se identifica
(por ejemplo, varias personas en una misma sala) se queda con su número.

```js
const segments = [];
const names = new Map();               // voz → nombre real
const generic = /^speaker\s*\d+$/i;

const add = (s) => {
  segments.push(s);
  if (s.speaker_name && !generic.test(s.speaker_name)) names.set(s.speaker, s.speaker_name);
};
const label = (s) => names.get(s.speaker) ?? `Hablante ${s.speaker}`;

const ws = new WebSocket(realtimeUrl);
ws.onmessage = (e) => {
  const { type, data } = JSON.parse(e.data);
  if (type === 'connected') data.transcripts.forEach(add);
  if (type === 'ts') add(data);
  if (type === 'status-update' && data.new_status === 'finished') ws.close();
  render(segments, label);             // vuelve a pintar con los nombres ya conocidos
};
```

### `POST /api/v1/meetings/live/{id}/stop`

Alcance `sessions:write`. Si la reunión está **programada**, la cancela y el
bot no entra. Si está **en curso**, el bot sale y la reunión se procesa con
lo grabado hasta ese momento. Repetir la llamada no hace daño.

Es la forma limpia de retirar el bot. Si alguien lo expulsa desde la
reunión, en Teams puede pedir entrada una vez más (durante un minuto como
máximo) y la sesión queda marcada como posiblemente incompleta.

---

## 8. Leer el resultado

### `GET /api/v1/sessions`

Alcance `sessions:read`. Filtros: `project_external_id`, `status`, `search`
(por título), `updated_since` (ISO), `page`, `limit` (máx. 200).

```json
{
  "items": [
    {"id": 1405, "title": "Seguimiento de proyectos", "date": "2026-10-02T15:31:08+00:00",
     "project_external_id": "6cd5df21-…", "status": "pending", "language": "Español",
     "origin": "bot", "duration_min": 9, "duration_is_estimate": true,
     "has_video": true, "counts": {"tasks": 5}}
  ],
  "total": 1, "page": 1, "limit": 20, "pages": 1, "has_more": false
}
```

| Campo | Notas |
|---|---|
| `origin` | `bot` (bot propio), `manual` (archivo o texto subido), `web` (Fireflies) |
| `status` | `processing` mientras se genera el acta; después `pending` (lista, pendiente de revisión) y los estados de su flujo de curación |
| `has_video` | Hay vídeo guardado; se pide en `/video` |
| `duration_min` | **Estimación** a partir de la transcripción, no una medida |

### `GET /api/v1/sessions/{id}`

```json
{"id": 1405, "title": "…", "date": "…", "project_external_id": "…",
 "status": "pending", "has_video": true, "duplicate_of_session_id": null,
 "summary": "…", "decisions": "…", "agreements": "…", "risks": "…",
 "participants": [{"name": "Ana Ruiz", "role": "…", "entity": "…", "email": "…"}],
 "tasks": [ … ]}
```

En las reuniones del bot, `participants` son las personas que estuvieron en
la sala (sin los grabadores automáticos).

### `GET /api/v1/sessions/{id}/transcript`

`{"session_id": 1405, "transcript": "[Ana Ruiz] Buenos días…\n[Luis Pérez] …"}`.
Una línea por intervención, con el nombre de quien habla entre corchetes.
Con `?format=txt` se entrega como **archivo de texto** descargable
(`Content-Disposition: attachment`, nombre `Transcripcion_Sesion_<id>_<título>.txt`).

### `GET /api/v1/sessions/{id}/summary`

El resumen del acta, solo: `{"session_id": 1405, "title": "…", "summary": "### Resumen\n- …"}`.
Con `?format=md` se entrega como **archivo Markdown** descargable.

### `GET /api/v1/sessions/{id}/video`

Alcance `sessions:read`. Solo cuando `has_video` es `true`.

```json
{"url": "https://…firmada…", "expires_in": 900, "content_type": "video/mp4",
 "available_until": "2026-12-31T15:42:23"}
```

- `url` va directa como `src` de un `<video controls>` y admite saltar a
  cualquier punto.
- Con `?download=true` la `url` **guarda el archivo** (`.mp4`) en vez de
  reproducirlo; sirve para un botón «Descargar vídeo». También caduca.
- **Caduca a los `expires_in` segundos.** No la guarden: pidan otra al abrir
  el reproductor. Si la reproducción falla por caducidad, pidan otra y
  continúen desde el mismo segundo.
- `available_until`: fecha en que se borra el vídeo según la suscripción
  (`null` = no caduca). El acta y la transcripción no se borran.
- El vídeo aparece uno o dos minutos **después** del acta: `has_video` pasa
  a `true` cuando ya está disponible.

Errores: `404` la sesión no tiene vídeo o no es visible para esa persona ·
`503` el almacenamiento no responde (se puede reintentar).

---

## 8 bis. Participantes de una sesión

En las reuniones del bot, los asistentes salen de quién habló y de quién
estaba en la sala; en las de Fireflies, de quién habló. Cargo, empresa y
correo se toman de los **miembros del proyecto** (lo que Altum sincroniza).
La lista se puede corregir desde la API:

| Método y ruta | Alcance | Qué hace |
|---|---|---|
| `POST /api/v1/sessions/{id}/participants` | `sessions:write` | Añade a una persona: `{"name": "Marta Gil", "role": "…", "entity": "…", "email": "…"}` (solo `name` es obligatorio). Si ya estaba, completa su ficha; no duplica. Responde `201` con la lista |
| `DELETE /api/v1/sessions/{id}/participants?name=Marta%20Gil` | `sessions:write` | La quita de la lista. Sus tareas no se tocan |
| `POST /api/v1/sessions/{id}/regenerate-participants` | `sessions:write` | Vuelve a calcular quién estuvo (voces con nombre + personas en la sala), conserva lo que ya estaba, funde nombres repetidos y alinea cargos con el proyecto. Inmediato, sin modelo |
| `PATCH /api/v1/sessions/{id}` con `participants: [...]` | `sessions:write` | Reemplaza la lista entera (ya existía) |

Los nombres pasan por el registro de alias de la empresa: «JD Toro» se guarda
como «Juan Toro» si así está registrado.

**Al cambiar la sesión de proyecto** (`PATCH` con `project_external_id`, o
desde la pantalla), cargos, empresa y correos de asistentes y responsables
de tareas se vuelven a casar solos con los miembros del proyecto nuevo. Lo
que el proyecto no conoce se deja como estaba.

## 9. Resumen de endpoints

| Método y ruta | Alcance | Para qué |
|---|---|---|
| `GET /api/v1/capabilities` | `sessions:read` | Qué tiene activo la empresa |
| `GET /api/v1/meetings/settings` | `integrations:read` | Nombre del bot y origen de reuniones |
| `PATCH /api/v1/meetings/settings` | `integrations:write` | Cambiarlos |
| `POST /api/v1/sessions` | `sessions:write` | Subir grabación o texto |
| `POST /api/v1/meetings/live` | `sessions:write` | Mandar el bot, ahora o programado |
| `GET /api/v1/meetings/live` | `sessions:read` | Reuniones del bot en curso, con su enlace en vivo |
| `GET /api/v1/meetings/live/{id}` | `sessions:read` | Estado, transcripción en vivo y sesión resultante |
| `POST /api/v1/meetings/live/{id}/stop` | `sessions:write` | Cancelar o sacar al bot |
| `GET /api/v1/sessions` | `sessions:read` | Listar sesiones |
| `GET /api/v1/sessions/{id}` | `sessions:read` | Acta completa |
| `GET /api/v1/sessions/{id}/transcript` | `sessions:read` | Transcripción (`?format=txt` = archivo) |
| `GET /api/v1/sessions/{id}/summary` | `sessions:read` | Resumen (`?format=md` = archivo) |
| `GET /api/v1/sessions/{id}/video` | `sessions:read` | Enlace temporal al vídeo (`?download=true` = guardar) |
| `POST` / `DELETE /api/v1/sessions/{id}/participants` | `sessions:write` | Añadir o quitar un asistente |
| `POST /api/v1/sessions/{id}/regenerate-participants` | `sessions:write` | Recalcular los asistentes |

## 10. Lista de comprobación

- [ ] Leer `capabilities` y ocultar lo que la empresa no tiene.
- [ ] No ofrecer casillas de vídeo ni de transcripción en vivo: no existen.
- [ ] Enviar siempre `external_id` al mandar el bot (evita duplicados en reintentos).
- [ ] Enviar `scheduled_start` con zona horaria.
- [ ] Pedir una URL nueva de vídeo cada vez que se abre el reproductor.
- [ ] Tratar los `state` y los tipos de mensaje desconocidos sin fallar.
- [ ] Avisar al usuario de que tiene que admitir al bot en la reunión cuando la plataforma no está en `authenticated_platforms`.
