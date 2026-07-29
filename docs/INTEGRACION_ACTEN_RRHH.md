# Contrato de Integración — Acten ↔ Plataforma de Servicios/RRHH

> Documento de trabajo entre los dos equipos.
> Modelo acordado: **Acten headless**. Acten aporta backend + base de datos;
> la plataforma de servicios aporta la interfaz. Los usuarios **nunca entran
> a Acten**.
> Estado de Acten verificado contra código el 28-jul-2026.

---

## 0. ESTADO — 30-jul-2026: lado Acten COMPLETO, sin pasos manuales

Todo lo que sigue **está construido, desplegado y verificado en vivo
contra producción**. No es diseño: funciona, y **no requiere que nadie
lance nada a mano**.

| Componente | Estado | Cómo se verificó |
|---|---|---|
| API v1 (`X-API-Key` + scopes) | ✅ | Sin clave → `401`; alcance insuficiente → `403` |
| Permisos `X-On-Behalf-Of` | ✅ | Empleado no sincronizado ve **0** — sin fugas |
| `GET /sessions`, `/tasks`, detalle, transcripción | ✅ | Devuelven datos reales del tenant |
| `PATCH`/`POST /tasks` + máquina de estados | ✅ | `cancelled → done` responde `409` |
| **`POST /ask` en v1** | ✅ **nuevo** | Respuesta con citas reales, recortada al empleado |
| **`GET /sessions/{id}/document`** | ✅ **nuevo** | PDF de 8 páginas y DOCX válidos; `format=txt` → `422` |
| **`GET /calendar/events`** | ✅ **nuevo** | **737 elementos**: tareas con fecha + reuniones + agendas |
| **Sync automática** | ✅ **nuevo** | Cada 15 min + completa a las 03:20 |
| **Conectar Jira/Trello/Slack/CRM** | ✅ **nuevo** | Catálogo, credenciales y rutas por proyecto — ver §17 |
| **Conectar agenda (Google/Microsoft)** | ✅ **nuevo** | OAuth sin sesión de Acten, con `state` firmado |
| **Enviar el acta por correo** | ✅ **nuevo** | Alcance propio `sessions:send` |
| **Artefactos por rol, comentarios, versiones** | ✅ **nuevo** | PRD, brief comercial, informe de estado |
| **Búsqueda, analítica, notificaciones** | ✅ **nuevo** | Recortadas por persona salvo la analítica |
| Sincronización PULL | ✅ | **7 personas y 6 proyectos enlazados por UUID** |
| Asistente de enlace bidireccional | ✅ | Clasifica *enlaza* / *ambiguo* / *crear* |
| Emisor de webhooks HMAC | ✅ | **Ciclo completo: `202 accepted`** |
| Idempotencia de webhooks | ✅ | Segundo envío → `202 duplicate` |
| Firma HMAC | ✅ | Firma falsa → `401 "La firma no coincide"` |

**Prueba de ciclo completo ejecutada:** crear una tarea por la API v1
disparó `task.created` → aceptado; cambiar su estado disparó
`task.updated` → aceptado. Sin intervención manual.

### 🔁 Lo que ahora ocurre solo — 30-jul-2026

Antes había dos cosas que alguien tenía que disparar. Ya no.

| Qué | Cuándo dispara | Dónde vive |
|---|---|---|
| **Aviso de sesión nueva** | En cuanto termina de procesarse el acta: `session.processed` (o `session.failed` si algo falló) | `services/transcript_pipeline.py` §7b |
| **Aviso de tarea** | Al crearse o cambiar de estado — **también si el cambio se hace desde Acten**, no solo por la v1 | `routers/integration_v1.py`, `routers/pendientes.py` |
| **Sync de personas y proyectos** | Cada 15 min (incremental) + **03:20 completa**, como reconciliación | `services/cron_service.py` |

Verificado en el arranque de producción:

```
Cron iniciado: envío automático cada 1 min + overdue check cada 1h
               + sync Servicios cada 15 min
sync Servicios [softnexus]: 7 remotos, 22 fichas ya enlazadas,
                            0 nuevas, 0 sin pareja, errores=[]
```

La cadencia va **muy por debajo de su tope de 120 req/min**: son 2
peticiones cada 15 minutos.

> **Por qué también una completa diaria.** La incremental con
> `status=all` reporta bajas, pero una completa a las 03:20 actúa como
> reconciliación por ausencia: si algo se perdió en una ventana, se
> corrige en la siguiente madrugada sin que nadie lo note.

### ⚠️ Corrección de aislamiento entre clientes (nuestra, ya desplegada)

El emisor de webhooks se configura **por variables de entorno**, así que
disparaba para **todos los tenants del despliegue**: una reunión de otro
cliente de Acten les habría enviado su título. Ustedes lo habrían
descartado —su clave es del tenant `softnexus` y el `GET` posterior
habría dado `404`—, pero el título ya habría salido.

Corregido: los eventos se acotan al tenant integrado
(`SERVICIOS_TENANT_SLUG`, por defecto `softnexus`). Lo dejamos escrito
porque era un fallo nuestro, no suyo.

### Resultado de la sincronización con datos reales

Las 7 personas quedaron enlazadas por UUID (15 fichas en total, porque
una persona tiene una ficha por proyecto):

* **Felipe** enlazó sus 3 fichas, incluida la de `@nexura.com` en el
  proyecto de ese cliente — emparejada por nombre, ya que el correo no
  coincide.
* **Natalia Gaviria** se creó automáticamente: no estaba en Acten y no
  tiene correo corporativo; entró por los integrantes del proyecto.
* La errata **«Wiliam» → «William Aragon»** se corrigió sola al tomar el
  nombre del maestro.
* Los 6 proyectos enlazados; 4 creados desde su catálogo (Contraloría,
  First Class, Mi Boleta, Yo Soy Fan).

### ✅ PRODUCCIÓN ACTIVA — 29-jul-2026

Acten apunta a `https://servicios.softnexus.io`. Los cinco
comportamientos verificados **contra su producción**:

```
1. firma válida             → 202 accepted     ✅
2. evento nuevo             → 202 accepted     ✅
3. mismo event_id           → 202 duplicate    ✅ deduplica
4. firma inválida           → 401              ✅
5. marca de tiempo vieja    → 401              ✅ anti-reenvío
```

Lectura confirmada con la clave de producción: 7 empleados, 6 proyectos,
`resolve?name=Felipe Cortés` → `ok`, `resolve?name=Cortés Burgos` →
`ambiguo` (los dos hermanos, como debe ser).

**Los tres códigos de error significan cosas distintas** — aviso suyo que
ahorra depuración: `401` = firma o secreto que no cuadran · `403` =
alcance ausente en la clave · `503` = ellos no tienen el secreto puesto.

### ⏸ Pendientes

**De Acten: nada.** Todo lo que hace Acten está expuesto en la v1 —
sesiones, tareas, calendario, actas, integraciones, artefactos por rol,
colaboración, búsqueda y analítica. Ver §17 para el mapa completo.

**Una cosa que sí necesitamos de ustedes o del cliente:** para que el
botón «conectar mi agenda» funcione hay que dar de alta una aplicación
OAuth de Google y otra de Microsoft. Hoy no existen
(`GOOGLE_OAUTH_CLIENT_ID` y `MS_OAUTH_CLIENT_ID` están vacías) y el
endpoint responde `503` con ese mensaje exacto. Todo lo demás del
calendario —tareas con fecha y reuniones— ya funciona sin eso.

**De ellos:** la interfaz (reuniones, detalle, tareas, Kanban, Ask) — el
mayor esfuerzo real, y ya no espera nada nuestro.

**Conjunto:** ~~cerrar quién crea en el asistente~~ ✅ resuelto: es
bidireccional y una sola ejecución cubre los dos lados — ver §16.

**Corrección sobre el calendario (30-jul, mismo día).** Lo dimos por
vacío. Era un fallo nuestro de alcance: devolvíamos solo las agendas
OAuth —que sí están vacías— cuando el calendario de Acten se arma sobre
todo con **tareas con fecha** y **reuniones**. Corregido: **737
elementos**, cada uno con `kind: task | session | event`. Ver §6.

### ⚠️ Nota de seguridad (hallazgo suyo, ya corregido)

Al auditar la clave que nos dieron encontraron que `POST /quote` y
`POST /ask` respondían `200` pese a describirse como de solo lectura —
`/quote` llama a un modelo y puede **enviar un PDF con su marca a un
correo**. Eran endpoints anteriores a los alcances, con la comprobación
vieja. Ya exigen `quotes:write` y `ask:read`, que nuestra clave no tiene.

Acten **no llamaba a ninguno de los dos**, así que el cambio no nos
afecta. Lo registramos porque el hallazgo y su divulgación proactiva son
el tipo de cosa que conviene que quede escrita.

---

## 1. Modelo acordado (y qué cambia respecto al supuesto inicial)

El documento de respuestas del equipo de RRHH asumía que Acten *empujaría*
datos hacia su plataforma y que ellos construirían un **módulo de tareas**.
**Ese módulo se cancela.**

| | Supuesto inicial | **Acordado** |
|---|---|---|
| Tareas de reunión | RRHH crea modelo + endpoints + sync | **Viven solo en Acten**; RRHH las renderiza |
| Sesiones / resúmenes / decisiones | Copiadas a RRHH | **Viven solo en Acten** |
| Duplicación de datos | Riesgo real | **Cero por construcción** |
| Trabajo backend de RRHH | Alto | **Solo un proxy delgado** |

> **RRHH no guarda nada del dominio de reuniones.** Lo lee de Acten cada vez.
> Por eso no hay dos verdades que puedan divergir.

### Reparto de dominios

| Dominio | Dueño | El otro lado |
|---|---|---|
| Empleados, contratos, nómina, vacaciones, cuentas de cobro | **RRHH** | Acten los referencia por UUID |
| Proyectos (presupuesto, miembros, allocation) | **RRHH** | Acten los espeja por `external_ref` |
| Sesiones, transcripciones, resúmenes, decisiones, acuerdos, riesgos | **Acten** | RRHH los renderiza |
| **Tareas (todas)** | **Acten** | RRHH las renderiza y cambia su estado |
| Ask IA sobre reuniones | **Acten** | expuesto dentro de la UI de RRHH |
| Ask IA sobre empresa/nómina | **RRHH** | ya existe, se queda tal cual |

---

## 2. Arquitectura de llamadas

```
┌─────────────────────── Plataforma de Servicios (RRHH) ───────────────────┐
│                                                                          │
│   Angular 21 (PrimeNG)          FastAPI (Python 3.12)                    │
│   ─────────────────             ──────────────────────                   │
│   Componentes de:               Proxy BFF                                │
│    · Reuniones          ──────► /internal/acten/*  ──┐                   │
│    · Detalle de sesión          (guarda la API Key)  │                   │
│    · Tareas                                          │                   │
│    · Ask IA                                          │                   │
└──────────────────────────────────────────────────────┼───────────────────┘
                                                       │
                            X-API-Key: acten_xxx       │
                            X-On-Behalf-Of: <uuid>     ▼
                    ┌──────────────────────────────────────────┐
                    │   Acten  ·  /api/v1/*                     │
                    │   FastAPI + PostgreSQL 17 + pgvector      │
                    │   (sesiones, tareas, IA, transcripts)     │
                    └──────────────────────────────────────────┘
```

### Reglas no negociables

1. **El navegador nunca ve la API Key.** Todas las llamadas pasan por el
   backend de RRHH (patrón *backend-for-frontend*). Es un passthrough
   delgado — no transforma ni almacena.
2. **`X-On-Behalf-Of: <employee_uuid>`** en toda petición de lectura. Sin
   ese header, una clave de empresa vería *todas* las sesiones del tenant
   sin importar qué empleado esté mirando.
   **Regla acordada:** un empleado ve las reuniones de **todos los proyectos
   donde es miembro** (`ProjectMember`). Acten resuelve el UUID → membresías
   → sesiones visibles.
3. **Aislamiento por clave**: la API Key determina el tenant. No se envía
   identificador de empresa en el cuerpo. (Mismo criterio que ustedes ya
   aplican — coincidimos.)

---

## 3. Autenticación

### 3.1 RRHH → Acten
Header `X-API-Key`. Acten ya tiene el modelo (`ApiKey`: tenant_id, hash
SHA-256, **scopes**, `rate_limit_per_min`, `revoked_at`, `last_used_at`) y
el endpoint de emisión. La clave se muestra **una sola vez**.

**Scopes disponibles:**
```
sessions:read      tasks:read      ask:query
sessions:write     tasks:write     sync:write      bot:invite
```

### 3.2 Acten → RRHH (fase 2, opcional)
Su mecanismo `X-API-Key` ya existente. Se usaría solo para consultar
ausencias aprobadas y resolver empleados.

---

## 4. Provisioning: empleados y proyectos

RRHH es el maestro. Acten hace **upsert por `external_id`** y crea los
`User`/`ProjectContact` internos que necesita para permisos y para el
matching de participantes en los transcripts.

### 4.a Modo PULL — Acten consume la API de RRHH ✅ recomendado

Su equipo ya construyó estos endpoints (entrega del 28-jul). **Acten los
consume**, así no tienen que empujar nada:

| Qué | Endpoint suyo | Alcance |
|---|---|---|
| Directorio incremental | `GET /api/v1/api/employees?status=all&updated_since=` ⚠️ | `employees:read` |
| Directorio completo (diaria) | `GET /api/v1/api/employees?status=active` | `employees:read` |
| Emparejar persona | `GET /api/v1/api/employees/resolve?work_email=` \| `?national_id=` | `employees:read` |
| Proyectos + integrantes | `GET /api/v1/api/projects?status=active` | `projects:read` |

### ⚠️ La incremental va con `status=all` — corrección importante

La primera versión de esta especificación decía `status=active` también en
la incremental. **Era un error**, detectado por el equipo de Servicios al
probarlo desactivando a una persona:

| Consulta | Resultado tras desactivar a alguien |
|---|---|
| `status=active&updated_since=` | **0 empleados — no nos enteramos** |
| `status=all&updated_since=` | 1 empleado, con `status: "inactive"` ✅ |

Al filtrar por activos, **quien deja de serlo desaparece del resultado en
vez de llegar marcado como inactivo**. Acten lo mantendría como activo para
siempre: le asignaría tareas y lo convocaría a reuniones después de haberse
ido de la empresa.

**Regla:** la incremental pide `status=all` y **Acten filtra**:
`active` → sincroniza · `inactive` / `draft` → **desactiva** en Acten.

La completa diaria sigue con `status=active` y actúa además como
**reconciliación por ausencia**: quien esté activo en Acten y no aparezca en
esa lista, se desactiva. Dos mecanismos independientes cubriendo el mismo
riesgo.

**Reglas que Acten respeta:**
- La llave es `id` (UUID). Nunca el correo — coincidimos en el argumento.
- **Solo se sincronizan `status: active`.** Los `draft` (ficha incompleta) e
  `inactive` (ya no está) **no se convocan a reuniones** ni reciben tareas.
- `preferred_name` para mostrar en pantalla y avisos; `full_name` para
  documentos formales (actas, PDF).
- `role_text` **solo se muestra**, nunca alimenta lógica. Si algún día se
  necesita para decidir algo, se acuerda antes un vocabulario común.
- Acten **no pide ni almacena** salario, datos bancarios, firma ni documentos.
  Una integración de reuniones no los necesita.

Frecuencia: sincronización incremental con `updated_since`, cada 15 min,
más una completa diaria.

### 4.b Modo PUSH — alternativa (no necesaria hoy)

Si en algún momento prefieren empujar en vez de que Acten consulte, existen
`POST /api/v1/sync/employees` y `POST /api/v1/sync/projects` con la misma
semántica de upsert por `external_id`. **Con 4.a operativo no hacen falta.**

> Estos usuarios **no pueden iniciar sesión** en la UI de Acten
> (`login_disabled=true`). Existen solo como identidad para permisos y
> atribución.

### `POST /api/v1/sync/employees`
```json
{
  "employees": [
    {
      "external_id": "3f2b1c9a-...",        // employees.id (UUID) — LLAVE CANÓNICA
      "full_name": "Danny Vera",
      "work_email": "dvera@softnexus.io",   // opcional
      "national_id": "1143826502",          // opcional
      "position": "Desarrollador",
      "is_active": true
    }
  ]
}
```
→ `200 {"created": 3, "updated": 4, "skipped": 0}`

### `POST /api/v1/sync/projects`
```json
{
  "projects": [
    {
      "external_id": "PRJ-2026-014",
      "name": "Portal Ciudadano",
      "client_name": "Colpensiones",
      "status": "active",
      "start_date": "2026-03-01",
      "due_date": "2026-12-15",
      "members": [
        {"employee_external_id": "3f2b1c9a-...", "role_text": "Líder técnico"}
      ]
    }
  ]
}
```
→ `200 {"created": 1, "updated": 0}`

**Efecto lateral valioso:** con la nómina completa sincronizada, el pipeline
de Acten deja de inventar participantes. Hoy, en sesiones con diarización
anónima (`[Speaker 1]`), la IA rellenaba con los contactos cargados a mano
o alucinaba nombres. Con el directorio real de empleados el matching es
determinístico.

---

## 5. Cómo entran las reuniones (no requiere desarrollo de su lado)

**El empleado dispara el bot de grabación él mismo**, como lo hace hoy.
Acten **captura** lo que ese bot produce y ejecuta todo el procesamiento.

```
Empleado pone el bot  ──►  Bot graba  ──►  Acten captura y procesa
                                            transcript → resumen
                                            → decisiones → tareas
                                            → embeddings para IA
                                                   │
                         su plataforma  ◄── webhook (señal)
```

**Implicación para ustedes: no hay nada que construir ni llamar para esto.**
Las reuniones aparecen solas en `GET /api/v1/sessions` ya procesadas, con
su resumen, decisiones y tareas. Reciben el aviso por el webhook
`session.processed` (§8).

**La captura y el proveedor de grabación son responsabilidad exclusiva de
Acten** — un detalle interno que no forma parte de este contrato. Si Acten
cambia de proveedor o pasa a bot propio, los datos y los endpoints **son
idénticos** y ustedes no se enteran.

---

## 6. Endpoints de lectura (lo que pinta la UI de RRHH)

### `POST /api/v1/sync/projects` — proyectos nuevos, al instante

Llámenlo justo después de crear, renombrar o reactivar un proyecto y
aparece en Acten **en esa misma llamada**. Alcance `sync:write`, ya en su
clave.

```json
{
  "external_id": "6336c09f-fa59-457d-b2ee-b83da406e54f",
  "name": "Mi Boleta",
  "client_name": "First Class",
  "status": "active",
  "members": [{"employee_id": "...", "email": "...", "role": "..."}]
}
```

Acepta **las dos formas**. El contrato documentaba una y la
implementación aceptaba la otra; rechazar la documentada solo hacía
perder el rato a quien empezara por leerla:

| Cuerpo | Qué hace |
|---|---|
| `{"external_id": …, "name": …}` | un proyecto |
| `{"projects": [ {...}, {...} ]}` | un lote |
| sin cuerpo | resincroniza el catálogo entero |

**Los opcionales aceptan `null`.** `client_name: null` devolvía `422`, y un
proyecto interno no tiene cliente. Peor: el fallo era invisible, porque
avisarnos no interrumpe el alta de su lado — el proyecto existiría allá y
no aquí hasta que alguien fuera a buscarlo.

**Idempotente**: llamarlo dos veces con el mismo `external_id` no
duplica. **Nunca archiva**: mandar un proyecto no dice nada sobre los
demás.

Probado en producción: alta → `creado`; segunda llamada → `creado: []`;
cambio de nombre → `renombrado: [{de, a}]`; y los otros nueve proyectos
intactos.

> **Sigue habiendo red por debajo.** Acten tira del catálogo de proyectos
> **cada 3 minutos** (una sola petición) y hace la sincronización completa
> cada 15. Si no llaman a este endpoint, un proyecto nuevo aparece igual;
> solo tarda un poco más.

### Leer como empresa, no como persona — `org:read`

Una cuenta de administración **no es un empleado**: no tiene ficha, así
que `X-On-Behalf-Of` no puede nombrar a nadie. La alternativa era
preguntar una vez por cada persona y sumar los resultados, que es una
tanda de llamadas por pantalla y una vista reconstruida que cambia sola
si cambia nuestro recorte.

```http
X-API-Key: …
X-On-Behalf-Of: *          ← «esta llamada es de la empresa»
```

Requiere el alcance **`org:read`**, aparte y revocable por su cuenta —
igual que `sessions:send`. Una clave capaz de leer el tenant entero sin
recorte no es una clave de lectura normal.

| | Con `*` | Con el UUID de William |
|---|---|---|
| `/sessions` | 131 | 64 |
| `/tasks` | 897 | 398 |
| `/calendar/events` | 1010 | 450 |

**Qué cubre.** Todas las lecturas, incluidos `/link/directory/people` y
`/link/directory/projects`, que son operaciones de sistema y pedían una
persona sin sentido.

**Qué no cubre, a propósito.** Lo que necesita un autor de verdad
—comentar, generar un artefacto, enviar el acta— sigue exigiendo el UUID
de alguien: «la empresa» no firma nada. Responden `400` pidiendo la
persona.

Sin el alcance, `*` responde `403` con ese motivo exacto.

### Curación — corregir lo que sacó la IA

| Endpoint | Alcance |
|---|---|
| `PATCH /api/v1/sessions/{id}` | **`sessions:write`** |
| `PATCH /api/v1/tasks/{id}` · `DELETE /api/v1/tasks/{id}` | `tasks:write` |
| `POST /api/v1/sessions/{id}/regenerate-tasks` | `tasks:write` |
| `POST /api/v1/sessions/{id}/suggest-fields` | `sessions:write` |

`PATCH /sessions/{id}` acepta `title`, `summary`, `decisions`,
`agreements`, `risks`, `language`, `status` y `project_external_id`. Solo
se toca lo que venga.

> **Se guarda lo que se manda, sin volver a pasar por el modelo.** Quien
> corrige un acta lo hace porque el modelo se equivocó; regenerar al
> guardar borraría la corrección sin que nadie se entere. Para pedir
> sugerencias está `suggest-fields`, que es una llamada aparte.

**Forma canónica de `decisions`, `agreements` y `risks`: texto libre.** Se
guardan literalmente. Acten los emite en markdown —normalmente una lista
con viñetas— pero **no impone forma al escribir**: lo que manden es
exactamente lo que se devolverá después.

`DELETE /tasks/{id}` **borra**, no cancela. Una tarea que la IA se inventó
no es una tarea cancelada, y dejarla ensucia el recuento de todo el
mundo. Para «esto ya no se hace» está `status: cancelled`.

`PATCH /tasks/{id}` acepta además **`owner_name` y `owner_email`** en
texto libre, para la gente que no está en el directorio — la mayoría en
un proyecto de cliente. Si viene `owner_external_id`, manda ése.

**Las dos acciones de IA son de un solo uso por sesión**, igual que en
Acten: la segunda llamada responde `409`. Regenerar borra las tareas
actuales, y quien ya corrigió una a mano no debería perderla por pulsar
dos veces. `suggest-fields` **no toca el resumen ejecutivo**.

### Plantillas documentales — `.docx`, no markdown

Son **dos cosas distintas con nombres parecidos**, y conviene separarlas:

| | Qué es |
|---|---|
| `/output-templates` | Artefactos que escribe un modelo: PRD, Deal Brief, informe de estado. Markdown |
| `/document-templates` | Los **archivos Word** con los que se genera el acta formal |

| Endpoint | Alcance |
|---|---|
| `GET /document-templates` (`?tipo=`) | `outputs:read` |
| `GET /document-templates/categories` | `outputs:read` |
| `GET /document-templates/layout/blocks` | `outputs:read` |
| `POST /document-templates` (multipart) | `outputs:write` |
| `PATCH /document-templates/{id}` | `outputs:write` |
| `PUT /document-templates/{id}/file` | `outputs:write` |
| `DELETE /document-templates/{id}` (`?definitivo=`) | `outputs:write` |
| `GET`/`PUT /document-templates/{id}/layout` | `outputs:read` / `write` |
| `GET /document-templates/{id}/versions` | `outputs:read` |
| `GET /document-templates/{id}/preview` | `outputs:read` |

El `layout` es lo que guarda el constructor visual:

```json
{
  "bloques": ["meta", "attendees", "summary", "decisions",
              "risks", "agreements", "action_items"],
  "estilos": {"familia": "Roboto", "tamano_pt": 12, "fondo_cabeceras": "#df1616"}
}
```

**La lista de bloques se sirve desde `GET /layout/blocks`** — no la
escriban a mano. El día que se añada uno, aparece solo en su selector en
vez de faltar sin que nadie se entere. Un bloque desconocido en el `PUT`
responde `422` diciendo cuál.

Dos sitios donde la respuesta honesta es «no»:

* **`/versions` es un registro de acciones, no un archivo por versión.**
  Acten guarda un solo `.docx` y lo sustituye, así que no se puede
  descargar una versión anterior. La respuesta lo dice en
  `guarda_archivos_anteriores: false` en vez de insinuar lo contrario.
* **`DELETE` desactiva por defecto.** Un acta generada hace meses apunta a
  esa plantilla, y borrarla deja el documento sin explicación de cómo se
  produjo. Con `?definitivo=true` se borra de verdad.

### `duracion` y `origen` en el listado

`GET /sessions` trae ahora `origin`, `language`, `duration_min` y
`duration_is_estimate`.

* **`origin`** (`manual` | `web`) es un dato real: sale del id de la
  grabación.
* **`duration_min` es una estimación, no una medida.** Acten nunca guardó
  la duración de la reunión; se calcula a 150 palabras por minuto sobre la
  transcripción. Por eso viene con `duration_is_estimate: true` — decidan
  ustedes si la pintan, pero no la presenten como medida.

### `GET /api/v1/sessions`
Query: `project_external_id`, `updated_since`, `status`, `page`, `limit`, `search`
```json
{
  "items": [{
    "id": 560,
    "title": "Softnexus - Julio 6",
    "date": "2026-07-06T15:00:00Z",
    "project_external_id": "PRJ-2026-014",
    "status": "completed",
    "duration_min": 62,
    "participants": [{"employee_external_id": "3f2b1c9a-...", "name": "Danny Vera"}],
    "counts": {"tasks": 9, "decisions": 4, "risks": 2}
  }],
  "total": 137, "page": 1, "limit": 20
}
```

### `GET /api/v1/sessions/{id}`
Devuelve además: `summary`, `decisions`, `agreements`, `risks`,
`participants[]`, `tasks[]`.

### `GET /api/v1/sessions/{id}/transcript`
Texto completo con marcadores de speaker. Separado porque pesa.

### `GET /api/v1/sessions/{id}/document?format=pdf|docx`
✅ **Implementado.** Devuelve el binario con `Content-Disposition:
attachment`, no un JSON con base64 — así lo pueden servir directo desde
su proxy sin recodificar.

* `format=pdf` (por defecto) · `format=docx` · cualquier otro → `422`
* Usa la plantilla corporativa del tenant (misma acta que envía Acten por
  correo). El PDF sale de convertir el DOCX con Gotenberg; si Gotenberg
  no responde, cae a un generador local — **siempre devuelve un PDF**,
  nunca un `500` por eso.
* Alcance: `sessions:read`. Respeta `X-On-Behalf-Of`: si la persona no es
  miembro del proyecto → `404`.

Probado en producción: `200`, `application/pdf`, 8 páginas · DOCX
`Microsoft OOXML` válido.

### `GET /api/v1/calendar/events`
✅ **Implementado.** Alcance **`calendar:read`** — ya añadido a su clave,
**es la misma clave, el valor no cambió**.

> **Corrección sobre lo que dijimos antes.** Lo dimos por «vacío hasta que
> alguien conecte una agenda». Era falso: el calendario de Acten se arma
> sobre todo con **tareas que tienen fecha de vencimiento** y con las
> **reuniones**. Las agendas de Google/Microsoft son el tercer
> ingrediente, y ésas sí están vacías. Devolvíamos solo ese tercio.
> Corregido: hoy responde con **737 elementos reales**.

Cada elemento trae `kind`: `task` · `session` · `event`.

Query: `project_external_id`, `date_from`, `date_to`, `include`
(`tasks,sessions,events` — coma; por defecto los tres), `page`, `limit`
(default 50, máx 200).

```json
{
  "items": [{
    "kind": "task",
    "id": 4821,
    "title": "Importar repositorio de FC para trabajar en Mi Boleta",
    "start_at": "2026-07-15",
    "end_at": "2026-07-15",
    "all_day": true,
    "status": "pending",
    "priority": "media",
    "owner": {"employee_external_id": "3f2b1c9a-...", "name": "Danny Vera"},
    "project_external_id": "10864d68-bcb4-44b4-9cec-8ee910714b05",
    "session_id": 560,
    "meeting_url": null,
    "attendees": []
  }],
  "total": 737, "page": 1, "limit": 50
}
```

* `all_day` es `true` cuando la tarea no tiene hora; con hora, `start_at`
  llega como `YYYY-MM-DDTHH:MM`.
* `session_id` es el puente al acta. En `kind: "event"` vale `null`
  mientras la reunión no tenga acta.
* **`due_date` es `YYYY-MM-DD` o no existe.** La columna es de texto y
  arrastró basura del extractor viejo («No especificada»); esas filas ya se
  limpiaron a `null` y ahora la validación corre en cada escritura, así que
  no pueden volver. El filtro de salida se mantiene igual por si acaso. Si
  les llega algo que no sea `YYYY-MM-DD`, es un fallo nuestro, avísennos.

**Diferencia de visibilidad que conviene tener clara.** En Acten, un
administrador ve en su calendario **las tareas de todo el tenant**. Por la
API v1 con `X-On-Behalf-Of` se recorta **a los proyectos donde la persona
es miembro** — que es lo acordado para su plataforma. No es una
inconsistencia: son dos públicos distintos. Si en algún momento quieren la
vista completa de administrador, se resuelve con un alcance aparte; hoy no
existe.

> Las agendas OAuth (`kind: "event"`) siguen sin datos: **0 cuentas
> conectadas**. Aparecerán solas en cuanto alguien conecte Google o
> Microsoft desde Acten, sin cambios de su lado.

### `GET /api/v1/tasks`
Query: `project_external_id`, `owner_external_id`, `status`, `updated_since`
```json
{
  "items": [{
    "id": 4821,
    "title": "Importar repositorio de FC para trabajar en Mi Boleta",
    "description": "...",
    "status": "pending",                    // pending | done | blocked | cancelled
    "priority": "media",
    "owner": {"employee_external_id": "3f2b1c9a-...", "name": "Danny Vera"},
    "due_date": "2026-07-15",
    "source_session_id": 560,
    "project_external_id": "PRJ-2026-014",
    "origin": "meeting"                     // meeting | manual
  }]
}
```

---

## 7. Endpoints de escritura

### `PATCH /api/v1/tasks/{id}`

**Campos modificables** (todos opcionales; se aplica solo lo que envíen):

| Campo | Tipo | Notas |
|---|---|---|
| `status` | enum | Ver transiciones abajo |
| `owner_external_id` | uuid \| null | UUID del empleado. `null` = sin asignar |
| `title` | string | |
| `description` | string | |
| `due_date` | date \| null | `YYYY-MM-DD` |
| `due_time` | string \| null | `HH:MM` 24h |
| `priority` | `alta`\|`media`\|`baja` | |
| `column` | string \| null | Columna del Kanban (§13.1) |
| `order` | integer \| null | Posición dentro de la columna |

**No modificables:** `id`, `source_session_id`, `project_external_id`,
`origin`, `created_at`. Enviarlos → `400`.

### Estados y transiciones

```
        ┌──────────────────────────────────┐
        ▼                                  │
   ┌─────────┐  ──────────►  ┌────────┐    │
   │ pending │               │  done  │────┘   (reabrir)
   └─────────┘  ◄──────────  └────────┘
        │  ▲
        ▼  │
   ┌─────────┐               ┌───────────┐
   │ blocked │ ────────────► │ cancelled │   (terminal)
   └─────────┘               └───────────┘
```

| Desde → Hacia | `pending` | `blocked` | `done` | `cancelled` |
|---|---|---|---|---|
| **`pending`** | — | ✅ | ✅ | ✅ |
| **`blocked`** | ✅ | — | ✅ | ✅ |
| **`done`** | ✅ | ❌ | — | ❌ |
| **`cancelled`** | ❌ | ❌ | ❌ | — |

`cancelled` es terminal. Transición inválida → `409` con el estado actual.
Al pasar a `done`, Acten sella `completed_at` automáticamente.

### `POST /api/v1/tasks`

**Obligatorios:** `title`, y **uno de** `project_external_id` o `source_session_id`.

```json
{
  "title": "Revisar contrato del cliente",
  "description": "",                       // opcional
  "project_external_id": "6cd5df21-...",   // uuid de SU proyecto
  "owner_external_id": "4bf3321e-...",     // uuid de SU empleado
  "due_date": "2026-08-15",                // opcional
  "priority": "media",                     // opcional, default media
  "column": "Por hacer"                    // opcional (Kanban)
}
```
→ `201` con el objeto completo. `origin` se fija en `"manual"`.

**Validación:** si `owner_external_id` no corresponde a un empleado
sincronizado (§4), responde `422` — no se acepta un responsable fantasma.
`due_date` responde `422` si no es exactamente `YYYY-MM-DD` — sin sufijo
horario, porque la hora va en `due_time` y recortarla en silencio sería
peor que rechazarla. Para dejar la tarea sin fecha, mándenlo como `null` u
omítanlo. Aplica igual en el `PATCH`.

### Autenticación de su servidor contra Acten

```http
X-API-Key: <clave que emite Acten para su tenant>
X-On-Behalf-Of: <employee_id (uuid) del usuario de la sesión>
```

- La **API Key** identifica su empresa y sus alcances. Se emite desde Acten,
  se muestra una sola vez, es revocable y registra `last_used_at`.
- **`X-On-Behalf-Of`** es obligatorio en lecturas y en `PATCH`. Acten
  resuelve UUID → proyectos donde es miembro → tareas y sesiones visibles.
  Sin la cabecera → `400`.
- Alcances de Acten: `sessions:read`, `sessions:send`, `tasks:read`,
  `tasks:write`, `ask:query`, `calendar:read`, `calendar:write`,
  `integrations:read`, `integrations:write`, `outputs:read`,
  `outputs:write`, `comments:read`, `comments:write`, `analytics:read`,
  `notifications:read`, `sync:write` y **`org:read`**.
- **Su clave de producción ya tiene** `sessions:read`, `tasks:read`,
  `tasks:write`, `ask:query` y `calendar:read`. **Es la misma clave: el
  valor no cambió**, solo se le sumó el alcance de calendario.
- `sync:write` ya no hace falta de su lado: la sincronización corre sola
  cada 15 minutos. `POST /sync/run` sigue existiendo por si quieren
  forzarla, pero nadie depende de ella.

**Códigos:** `401` sin clave o inválida · `403` alcance insuficiente o el
empleado no es miembro del proyecto · `404` la tarea no existe o no es
visible para ese empleado · `409` transición de estado inválida · `422`
datos inconsistentes.

### Paginación (común a todos los listados)

`page` (default 1) · `limit` (**máx 200 en todos**, default 20 salvo el
calendario que es 50). Antes `/tasks` y `/sessions` cortaban en 100 y
`/calendar/events` en 200, sin más razón que el orden en que se
escribieron.

La respuesta trae `{items, total, page, limit, pages, has_more}`.

> **`has_more` está por una razón concreta**, y nos la señaló su equipo:
> el riesgo no es el `422` al pedir de más —ése se ve enseguida— sino
> pedir **una** página y pintarla como si fuera todo. Con 653 tareas se
> verían 100 y la pantalla no parecería rota; solo le faltarían cosas.

Para sincronización incremental usen `updated_since` en ISO-8601 — mismo
criterio que su `GET /employees`.

### Tareas sin dueño: `owner: null`

Una tarea que nadie ha cogido viene con **`owner: null`** y
`unassigned: true`.

> **Antes venía como `{"employee_external_id": null, "name": "Por
> asignar"}`**, que desde fuera es indistinguible de una persona real sin
> fichar — y en un proyecto de cliente ésas son la mayoría. Obligaba a
> comparar el literal contra una lista, y ese código se rompe el día que
> alguien traduzca la interfaz.
>
> No era cosmético: su pantalla oculta a la gente ajena al proyecto, y la
> tarea sin dueño caía en ese grupo. Se escondía justo la que más conviene
> ver. Corregido a petición suya.

### `POST /api/v1/ask`
✅ **Implementado** (alcance `ask:query`, ya en su clave).
```json
{
  "question": "¿Qué debe hacer Danny en la última reunión del proyecto?",
  "project_external_id": "PRJ-2026-014",
  "session_ids": [560],
  "top_k": 8,
  "prior_turns": [{"question": "...", "answer": "..."}]
}
```
Respuesta: `answer` (markdown) + `structured` con `intro`,
`decisions[]`, `action_items[]`, `risks[]`, `agreements[]`, y
`citations[]` (con `session_id`, `session_title`, `session_date`) — cada
afirmación trazable a su sesión origen. `prior_turns` da contexto a las
preguntas de seguimiento («¿y quién lo hace?»).

**`citations` trae una entrada por trozo leído**, así que la misma reunión
se repite si la respuesta se apoyó en su acuerdo, su riesgo y su
transcripción. Es lo correcto para trazar, pero `session_id` **no sirve
como clave de lista**.

Por eso se añade **`cited_sessions`**, ya agrupada — una entrada por
reunión, con `fragmentos` (cuántos trozos aportó) y `mejor_distancia`,
ordenada por relevancia:

```json
"cited_sessions": [
  {"session_id": 369, "session_title": "First Class - Evento - api-tiquetera",
   "session_date": "2026-05-20T...", "fragmentos": 2, "mejor_distancia": 0.31}
]
```

Ejemplo real: 20 `citations` → 13 `cited_sessions`. La lista detallada se
queda como estaba.

**El recorte de permisos ocurre ANTES del motor**, no después: con
`X-On-Behalf-Of` la búsqueda se limita a las sesiones de los proyectos
donde esa persona es miembro. Filtrar la respuesta después no serviría —
el modelo ya habría leído actas ajenas para redactarla. Si la persona no
tiene ninguna reunión visible, la respuesta es
`"No tiene reuniones visibles todavía."` con `citations: []`, no un
error.

Verificado en producción con una pregunta real: cita cuatro sesiones con
número y fecha (#42, #446, #553, #621). Un UUID inexistente recibe la
respuesta vacía — **sin fugas**.

> **Alcance del Ask de Acten: solo su propio dominio** (reuniones,
> transcripciones, decisiones). No consulta datos de nómina ni contratos.
> El asistente de RRHH sigue respondiendo sobre lo suyo. Sin solapamiento.

---

## 8. Webhooks Acten → RRHH

Destino: `POST /api/v1/api/webhooks/acten` (ya construido de su lado).
Solo son **señal**: no transportan el dato completo, su plataforma refresca
desde la API.

### Cabeceras
```http
X-API-Key:          <la clave que ellos emiten para Acten>
X-Acten-Signature:  sha256=<hmac>
X-Acten-Timestamp:  <epoch en segundos>
X-Acten-Event-Id:   <uuid>
```

### ⚠️ Firma: sobre `{timestamp}.{cuerpo crudo}`

**No sobre el cuerpo solo.** Acten implementa exactamente:

```python
mensaje = f"{timestamp}.".encode() + cuerpo_crudo
firma   = hmac.new(secreto.encode(), mensaje, hashlib.sha256).hexdigest()
```

Su razonamiento es correcto: firmando solo el cuerpo, alterar la marca de
tiempo no invalidaría la firma y la protección anti-reenvío sería inútil.
El secreto es **distinto de la API Key**.

> **🔑 Quién fija el secreto: lo fija QUIEN VERIFICA.**
> Acten **envía** y Servicios **verifica** → manda el secreto de ellos
> (`whsec_...`, ya emitido). El que habíamos generado nosotros **se
> descarta**. Si cada lado usara el suyo, el síntoma sería un `401`
> permanente que parece un bug de código y cuesta una tarde de depuración.

### Eventos

| Evento | Cuándo | Payload |
|---|---|---|
| `session.processed` | Terminó el pipeline IA | `{session_id, project_external_id, counts}` |
| `session.failed` | Falló el procesamiento | `{session_id, error}` |
| `task.created` | Tarea nueva de una reunión | `{task_id, session_id, owner_external_id}` |
| `task.updated` | Cambió estado/responsable | `{task_id, status}` |

### Entrega
- Reintentos con backoff exponencial (3 intentos) ante `503` o timeout.
- **No** se reintenta ante `400`, `401` o `403` — son errores nuestros.
- `202` con `"status": "duplicate"` se trata como éxito.
- **Relojes sincronizados por NTP** — rechazan eventos de más de 5 minutos.

---

## 9. Trabajo por lado

### Acten
| # | Tarea | Estado |
|---|---|---|
| 1 | Cablear `api_key_user()` a `/api/v1/*` + scopes | dependencia **ya existe**, sin usar |
| 2 | `external_ref` en Project / ProjectContact / User | nuevo |
| 3 | Endpoints `sync/employees`, `sync/projects` | nuevo |
| 4 | Endpoints de lectura (sesiones, tareas, documento) | ✅ **hecho** — documento devuelve binario |
| 5 | `PATCH/POST /tasks` | ✅ **hecho** |
| 6 | Exponer `/ask` en v1 | ✅ **hecho** — con `structured` y `prior_turns` |
| 7 | Webhooks salientes + HMAC | ✅ **hecho** — y acotados al tenant integrado |
| 8 | `X-On-Behalf-Of` → permisos | ✅ **hecho** |
| 9 | **Sync periódica automática** | ✅ **hecho** — 15 min + completa 03:20 |
| 10 | **`GET /calendar/events`** | ✅ **hecho** — tareas + reuniones + agendas, con `kind` |
| — | ~~Invitar bot~~ | ❌ **no aplica**: el empleado lo dispara, Acten captura |

### Plataforma de Servicios (RRHH)
| # | Tarea | Nota |
|---|---|---|
| 1 | Emitir API Key para Acten; guardar la de Acten | mecanismo **ya existe** |
| 2 | Proxy BFF `/internal/acten/*` | passthrough delgado |
| 3 | **Interfaz Angular**: reuniones, detalle, **tareas, Kanban**, Ask | ⚠️ el grueso del trabajo |
| 4 | Push de empleados y proyectos a Acten | |
| 5 | Endpoint receptor de webhooks | |
| 6 | **Poblar el módulo de proyectos** | hoy 0 en producción |
| 7 | Unicidad en `work_email` / `national_id` | deuda propia; la llave sigue siendo el UUID |

### ⚠️ Precisión sobre el "módulo de tareas"

**Sí construyen módulo de tareas — pero solo la capa visual.**

| Construyen | NO construyen |
|---|---|
| ✅ Interfaz de lista de tareas | ❌ Modelo/tablas de tareas |
| ✅ Vista Kanban | ❌ Endpoints propios de tareas |
| ✅ Filtros, asignación, cambio de estado | ❌ Lógica de sincronización |
| ✅ Formularios de creación/edición | ❌ Almacenamiento propio |

La UI lee de `GET /api/v1/tasks` y escribe con `PATCH /api/v1/tasks/{id}` y
`POST /api/v1/tasks`. **Los datos viven siempre en Acten**, por eso no hay
sincronización ni riesgo de que las dos plataformas se contradigan.

---

## 10. Riesgos y decisiones abiertas

### 🔒 CONTRATO CERRADO — 28-jul-2026

El equipo de Servicios **aceptó la especificación sin pedir cambios** y
resolvió de su lado las cuatro cuestiones abiertas:

| Punto | Resolución |
|---|---|
| PULL vs PUSH | **Solo PULL.** No construyen `POST /sync/*`. §4.b queda descartado |
| `X-On-Behalf-Of` | Lo envían en **todas** las llamadas, incluido `POST` |
| Ids de tarea (enteros vs UUID) | Los tratan como **opacos**: no los muestran ni deducen nada |
| Límite de peticiones | **Ya desplegado**: 120/min por clave, `429` + `Retry-After` |

**Estado del contrato: congelado.** Cualquier cambio a partir de aquí se
negocia, no se asume.

### Estado de pendientes

| # | Tema | Estado |
|---|---|---|
| 0 | ~~Credenciales de QA~~ | ✅ **recibidas** — en `.secrets/servicios-qa.env` (gitignored) |
| 1 | ~~TLS / acceso a QA~~ | ✅ **resuelto**: acceso abierto para la IP de Acten, datos de prueba cargados, OpenAPI actualizado |
| 2 | ~~API Key de Acten para su proxy~~ | ✅ **entregada** — con `sessions:read`, `tasks:read`, `tasks:write`, `ask:query` y ahora `calendar:read` |
| 3 | ~~Implementación del lado Acten~~ | ✅ **terminada** — nada nuestro bloquea |
| 11 | ~~Sync periódica~~ | ✅ automática cada 15 min + completa 03:20 |
| 12 | Agenda conectada (Google/Microsoft) | ⏸ solo afecta a `kind: "event"`; tareas y reuniones ya llegan |
| 13 | ~~Quién crea en el asistente~~ | ✅ **bidireccional en una ejecución** — ver §16 |
| 14 | ~~Entrega de credenciales~~ | ✅ **emparejamiento por código** — ver §18 |
| 15 | ~~Membresía al desasignar~~ | ✅ el sync retira el acceso — ver §4.a |
| 4 | 0 proyectos en producción de RRHH | ⏸ solo afecta producción; QA ya tiene datos |
| 5 | UI en Angular (tareas + Kanban) | 🔨 de su lado, arranca cuando tengan la clave |
| 6 | ~~Permisos por sesión~~ | ✅ miembro de proyecto ve las reuniones de ese proyecto |
| 7 | ~~Proveedor de grabación~~ | ✅ interno de Acten, fuera del contrato |
| 8 | ~~Tenant~~ | ✅ **`softnexus`** |
| 9 | ~~Arquitectura de la UI de tareas~~ | ✅ BFF, la clave nunca llega al navegador |
| 10 | ~~PULL vs PUSH~~ | ✅ solo PULL |

---

## 11. Fases sugeridas

**Fase 1 — Lectura (2-3 semanas).** API Key operativa + sync de empleados y
proyectos + endpoints de lectura. Su plataforma ya puede *mostrar* reuniones,
resúmenes, decisiones y tareas. El ciclo de captura ya funciona hoy.

**Fase 2 — Escritura + IA (2 semanas).** `PATCH /tasks`, `POST /ask`,
webhooks. La UI pasa a ser operativa: cambiar estados y preguntar a la IA.

**Fase 3 — Kanban (según su UI).** Vista de tablero sobre las mismas tareas
(§13.1). No requiere backend nuevo.

**Todo contra el entorno de QA** que su equipo ofreció, antes de tocar
producción.

---

## 13. Hoja de ruta de Acten (por qué el contrato es agnóstico)

Estas dos evoluciones ya están decididas del lado de Acten. Se documentan
aquí **no** para que ustedes las implementen, sino para que sepan que el
contrato de la §6/§7 ya las contempla y **no habrá que reescribir su UI**.

### 13.1 Kanban de tareas nativo en Acten
Acten tendrá su propio tablero Kanban. Como **las tareas ya viven en Acten**
(§1), esto no crea un segundo sistema: es otra vista sobre los mismos datos
que ustedes leen por `GET /api/v1/tasks`.

- Acten lo usa para sus clientes directos (Acten se mantiene como producto
  independiente).
- Ustedes pintan esas mismas tareas dentro de su plataforma.
- **Un solo backend, dos interfaces.** Un cambio de estado desde cualquiera
  de las dos se ve en la otra.
- Se añadirán campos opcionales al modelo de tarea (`column`, `order`,
  `board_id`). Son **aditivos**: si los ignoran, todo sigue funcionando.

### 13.2 Bot de grabación propio
Acten operará su propio bot de grabación. Impacto para ustedes: **ninguno**
— la captura es interna y el contrato no la expone (§5).

Lo único que mejora: hoy la diarización a veces llega anónima
(`[Speaker 1]`, `[Speaker 2]`) y obliga a inferir quién habló. Con bot
propio la identidad llega desde la invitación al calendario — y como ustedes
ya nos envían la nómina real (§4), la atribución de tareas a personas pasa
a ser exacta en vez de inferida.

---

## 15. Plan de implementación — lado Acten

Con el contrato cerrado y QA servido, esto es **lo único que falta** para
que la integración funcione. Orden de ejecución:

| # | Tarea | Estado |
|---|---|---|
| 1 | Router `/api/v1` + verificación de scopes | ✅ hecho |
| 2 | `external_ref` en `Project`, `ProjectContact`, `User` + índices | ✅ hecho |
| 3 | **Cliente PULL** de su API: employees, resolve, projects | ✅ hecho (honra `429`/`Retry-After`) |
| 4 | Job de sync: incremental 15 min + completa diaria | ⏸ el sync corre a demanda; falta agendarlo |
| 5 | Resolución `X-On-Behalf-Of` → membresías → visibilidad | ✅ hecho |
| 6 | `GET /sessions`, `/sessions/{id}`, `/transcript` | ✅ hecho |
| 7 | `GET/PATCH/POST /tasks` + máquina de estados | ✅ hecho |
| 8 | `POST /ask` en v1 | ⏸ funciona interno, falta exponerlo en v1 |
| 9 | Emisor de webhooks + HMAC sobre `{ts}.{body}` | ✅ hecho y probado |
| 10 | Asistente de enlace bidireccional | ✅ hecho |
| 11 | API Key para su proxy | ✅ emitida |

### Decisiones de diseño que salieron de la implementación real

Tres cosas se descubrieron al ejecutar contra datos reales, no al
diseñar. Se documentan porque afectan a cualquiera que toque esto:

1. **Una persona puede tener varias cuentas y varios correos.** Felipe
   existe como `@softnexus.io`, `@softnexus.co` y `@nexura.com`. La
   primera versión imponía «un empleado ↔ una cuenta» con un índice
   único y **reventaba**. Ahora todas sus fichas y cuentas apuntan al
   mismo UUID, y el correo específico de cada proyecto **nunca se pisa**.

2. **`«Softnexus (interno)»` y `«Softnexus»` son el mismo proyecto.** El
   paréntesis es una aclaración humana, no parte del nombre: se ignora al
   comparar.

3. **Coincidencia parcial de nombre ⇒ decide un humano, no se crea.**
   «Cortés Burgos» encaja con los dos hermanos; darlo de alta a ciegas
   habría creado un tercer Cortés inexistente. Se devuelven los
   candidatos, igual que su `409`.

**Campos nuevos en `ActionItem`** para soportar el Kanban (§13.1):
`column` (string, nullable), `order` (int, nullable). Aditivos — no rompen
nada existente.

### Avisos operativos de QA (del equipo de Servicios)

1. **Los UUID de QA son desechables.** No sirven al pasar a producción — no
   hardcodear ninguno en código ni en pruebas que vayan a promoverse.
2. **Desde otra IP verán silencio, no un error.** El puerto solo responde a
   la IP de Acten. Un timeout desde otro sitio *parece* un servidor caído
   pero es el firewall haciendo su trabajo.
3. **120 req/min por clave.** Al superarlo: `429` + `Retry-After` en
   segundos. El cliente PULL **debe respetar `Retry-After`**, no reintentar
   a ciegas.
4. **Rotar credenciales al pasar a producción.** Las de QA quedan
   invalidadas.

---

## 16. Asistente de enlace — bidireccional, en una sola ejecución

**Decidido: el asistente es bidireccional.** Una ejecución resuelve los
dos lados. La versión anterior de esta sección aceptaba «una ejecución =
un sentido»; se queda corta y obligaba a dar de alta a mano la otra
mitad.

### Por qué no hay carrera

El riesgo que planteaban era real pero está mal ubicado: **la carrera
aparece si dos asistentes corren a la vez**, no si uno solo hace las dos
cosas. Una ejecución la orquesta **un solo lado**, de forma secuencial:
lee ambos directorios, propone, y al confirmar aplica primero aquí y
después allá. No hay dos procesos decidiendo a ciegas.

> **La regla que sí hace falta:** no lancen el asistente por los dos lados
> al mismo tiempo. Con uno a la vez, `external_ref` basta.

### Qué devuelve ahora `POST /api/v1/link/preview`

| Lista | Qué es |
|---|---|
| `enlaces` | Coincidencia clara — se aplica |
| `ambiguos` | Varios encajan — **decide un humano** |
| `faltantes` | Están allá y no aquí → crear **en Acten** |
| **`faltantes_en_servicios`** | 🆕 Están aquí y no allá → crear **en Servicios** |

`resumen` trae `a_crear_en_acten` y **`a_crear_en_servicios`**.

Las personas de `faltantes_en_servicios` vienen **agrupadas por persona
real** con todos sus correos, igual que `/directory/people`: las tres
fichas de Felipe son una sola alta, no tres. Los proyectos traen su
conteo de `sesiones`, para que sepan cuáles tienen historial de verdad y
cuáles no vale la pena crear.

### Cómo se aplica cada sentido

| Sentido | Cómo |
|---|---|
| Crear **en Acten** | `POST /api/v1/link/directory/projects` y `/people` — idempotentes |
| Crear **en Servicios** | Con `faltantes_en_servicios`, ustedes dan de alta allá dentro de la misma ejecución |

**Si algún día quieren lanzarlo desde Acten**, necesitamos de su lado
`POST` de empleados y proyectos y los alcances `employees:write` /
`projects:write` — nuestra clave hoy solo lee. No corre prisa: mientras
la interfaz viva en Servicios, quien lanza es ustedes y con lo que hay
basta.

**Y en ambos sentidos vale la misma regla de oro:** ante coincidencia
parcial de nombre no se crea nada — se devuelven los candidatos y decide
un humano. Dar de alta a ciegas un «Cortés Burgos» crearía un tercer
hermano que no existe, y ese error no se descubre hasta que alguien lee
un acta que no le corresponde.

---

## 17. Superficie completa de la v1 — todo lo que hace Acten

El acuerdo es que sus usuarios **nunca entran a Acten**. Hasta ahora eso
solo se cumplía para leer reuniones y tareas: conectar Jira, apuntar un
proyecto a Slack o mandar el acta por correo seguía exigiendo entrar.
Ya no.

### 17.1 Conectar plataformas

| Endpoint | Alcance | Qué hace |
|---|---|---|
| `GET /api/v1/integrations/providers` | `integrations:read` | Catálogo: qué se puede conectar, qué campos pide cada uno y **qué hace con la sesión** |
| `GET /api/v1/integrations` | `integrations:read` | Qué está conectado, qué campos faltan |
| `PUT /api/v1/integrations/{id}` | `integrations:write` | Guarda credenciales |
| `DELETE /api/v1/integrations/{id}` | `integrations:write` | Desconecta |

**Las credenciales no se devuelven nunca.** La respuesta dice
`campos_puestos: ["api_token"]`, no su valor. Una API que te deja releer
el token de Jira que escribiste ayer es una filtración esperando a que
alguien reutilice la clave. Pinten «conectado» con eso y un formulario
que pida solo lo que falte.

Once plataformas, en cuatro familias:

| Familia | Plataformas | Efecto sobre la sesión |
|---|---|---|
| Tareas | Trello, Jira, ClickUp, Azure DevOps | una tarjeta/incidencia **por tarea** |
| Mensajería | Slack, Microsoft Teams | **un** mensaje con el resumen |
| Documentos | Notion, Google Docs | **una** página o documento |
| CRM | HubSpot, Salesforce, Pipedrive | nota en el negocio del asistente |

`ambito` dice dónde vive la credencial: `usuario` (Trello, Jira, ClickUp,
Azure — cada persona la suya, para que las tareas no aparezcan creadas
todas por la misma cuenta) o `empresa` (el resto, una sola).

### 17.2 A dónde va cada proyecto

| Endpoint | Alcance |
|---|---|
| `GET /api/v1/projects/{external_id}/routings` | `integrations:read` |
| `POST /api/v1/projects/{external_id}/routings` | `integrations:write` |
| `PATCH /api/v1/routings/{id}` · `DELETE /api/v1/routings/{id}` | `integrations:write` |
| `POST /api/v1/sessions/{id}/dispatch` | `integrations:write` |

El despacho automático ya ocurre al procesarse la sesión. `dispatch` es
para el botón «reenviar» de cuando alguien corrigió las tareas después.

### 17.3 Conectar una agenda sin pasar por Acten

| Endpoint | Alcance |
|---|---|
| `POST /api/v1/calendar/connect?provider=google\|microsoft` | `calendar:write` |
| `GET /api/v1/calendar/accounts` · `DELETE .../accounts/{id}` | `calendar:read` / `write` |
| `POST /api/v1/calendar/sync` | `calendar:write` |

`connect` devuelve `auth_url`: ábranla en una pestaña. El OAuth vuelve a
Acten, que valida un **`state` firmado de 15 minutos** y guarda la cuenta
a nombre de esa persona.

> **Por qué el `state` firmado.** El callback de OAuth exigía sesión de
> Acten, que sus empleados no tienen. Sin esto habría que pedirles que se
> registren en Acten solo para enganchar su calendario — justo lo que la
> integración viene a evitar. El enlace caduca a los 15 minutos: uno
> eterno acaba reenviado por chat y conectando la agenda equivocada.

> ⚠️ **Requisito pendiente:** hace falta dar de alta una aplicación OAuth
> de Google y otra de Microsoft. Hoy no existen y `connect` responde
> `503` diciéndolo. No afecta al resto del calendario.

### 17.4 El acta

| Endpoint | Alcance |
|---|---|
| `GET /api/v1/sessions/{id}/document?format=pdf\|docx` | `sessions:read` |
| `POST /api/v1/sessions/{id}/email` | **`sessions:send`** |

⚠️ **`email` es el único endpoint de la v1 que manda correo a terceros.**
Por eso tiene alcance propio, deliberadamente **no** incluido en
`sessions:read`: quien pueda leer un acta no debería poder, por descuido,
mandársela a media empresa. Los destinatarios salen de los responsables
de las tareas, no del cuerpo de la petición — una llamada no puede
dirigir el acta a una dirección arbitraria.

### 17.5 Artefactos por rol, colaboración, búsqueda y analítica

| Endpoint | Alcance | Qué es |
|---|---|---|
| `GET /api/v1/output-templates` | `outputs:read` | PRD, Deal Brief, informe de estado, kickoff… |
| `GET /api/v1/sessions/{id}/outputs` | `outputs:read` | Los ya generados |
| `POST /api/v1/sessions/{id}/outputs?template_id=` | `outputs:write` | Genera uno (llama a un modelo) |
| `GET`/`POST /api/v1/sessions/{id}/comments` | `comments:read` / `write` | Hilos sobre el acta |
| `GET /api/v1/sessions/{id}/versions` | `sessions:read` | Historial de ediciones con `snapshot` |
| `GET /api/v1/search?q=` | `sessions:read` | Búsqueda literal (distinta del `/ask`) |
| `GET /api/v1/analytics/roi` · `/recurring` | `analytics:read` | Horas, cumplimiento, temas que no cierran |
| `GET /api/v1/sessions/{id}/quality` | `sessions:read` | Puntuación del acta |
| `GET /api/v1/notifications` | `notifications:read` | Avisos de esa persona |

**La analítica es agregada de todo el tenant**, no de una persona: por eso
tiene alcance propio y **no** se recorta con `X-On-Behalf-Of`. No la
expongan a cualquier empleado sin pensarlo. Todo lo demás sí se recorta.

### 17.6 Alcances de su clave de producción

Ya los tiene todos. **Es la misma clave: el valor no ha cambiado nunca.**

```
sessions:read   sessions:send    tasks:read      tasks:write
ask:query       calendar:read    calendar:write  integrations:read
integrations:write               outputs:read    outputs:write
comments:read   comments:write   analytics:read  notifications:read
```

### 17.7 ⚠️ Un fallo que salió al construir esto

Las credenciales guardadas desde la interfaz de Acten **nunca llegaban a
las integraciones**. La interfaz escribe `apiKey`, `apiToken`, `boardId`;
todos los servicios leen `api_key`, `token`, `board_id`. El fallo es
silencioso: la integración aparece «conectada» y el despacho muere dentro
de un `try` con «faltan campos obligatorios».

Comprobado en producción sobre el tenant `softnexus`: **Trello y Jira de
Felipe, y ClickUp de otro usuario, llevaban así desde que se
configuraron.** Ya está corregido —se normaliza al leer, sin migrar
nada— y verificado: los tres resuelven.

Lo escribimos porque explica por qué, si alguien probó una integración
antes de hoy y «no hacía nada», no era su configuración.

---

## 18. Conectar sin manejar claves — emparejamiento por código

**Sustituye al intercambio manual de credenciales.** Lo de esta semana dejó
claro por qué hacía falta: la clave que emitimos el 28 no la tenía nadie —
ni ustedes ni nosotros— porque el único momento en que existe en claro es
la respuesta que la emite, y ahí se perdió.

### El flujo

1. Un administrador de Acten pulsa **Conectar plataforma** en
   *Ajustes → Integraciones*. Sale un código:

   ```
   ACTEN-4K7M-Q2XP        (válido 15 min, un solo uso)
   ```

2. Lo pegan en su pantalla de conexión.
3. **Su servidor** llama a `POST /api/v1/pair/redeem`:

```json
{
  "codigo": "ACTEN-4K7M-Q2XP",
  "plataforma": "Servicios RRHH",
  "webhook_url": "https://servicios.softnexus.io/api/v1/api/webhooks/acten",
  "webhook_secret": "<el suyo, el que verifica>",
  "api_base_url": "https://servicios.softnexus.io/api/v1/api",
  "api_key": "<la que nos dan para llamarles>"
}
```

Y reciben, **una sola vez**, la clave con los quince alcances, el `api_base`,
la lista de eventos y el contrato de firma.

> **Llámenlo desde su servidor, nunca desde el navegador.** Si lo llama el
> navegador, la clave llega al navegador, y ahí deja de ser un secreto.

### Por qué así

| Antes | Ahora |
|---|---|
| Clave de 50 caracteres por canal cifrado | Código de 8, legible en voz alta |
| Alguien la copia, la pega, la guarda | Va de servidor a servidor |
| Si se pierde, hay que reemitirla | Si el código se pierde, ya caducó |
| Secreto HMAC en un segundo mensaje | En la misma llamada |
| Un destino por despliegue (variables de entorno) | Uno por empresa, en base |

El código no lleva I, O, 0 ni 1 — son las que se confunden al leerlas. Se
acepta con guiones, sin ellos, en minúsculas o con espacios de más.

### Lo que ve quien conecta

Nada de claves. Un botón, un código con su cuenta atrás, y después
«Conectada · recibe avisos de sesiones y tareas», con un botón para
desconectar que revoca la clave y corta los eventos.

### Seguridad del canje

`redeem` **no lleva autenticación** — quien llama todavía no tiene ninguna
credencial, el código *es* la credencial. Por eso, a la vez: un solo uso,
quince minutos, se guarda el hash y no el código, y **la misma respuesta para
código inexistente, gastado o caducado** (distinguirlos le diría a quien
prueba al azar cuándo ha acertado).

Probado contra producción: canje correcto → clave de 15 alcances que responde
`200` en sesiones, tareas, calendario e integraciones · segundo canje del
mismo código → `400` · código inventado → `400`, mismo texto · código con otra
forma → `422`.

---

## 12. Contactos técnicos

- **Acten**: `https://api.acten.app` · FastAPI + PostgreSQL 17 + pgvector
- **Servicios/RRHH**: `https://servicios.softnexus.io` · FastAPI + PostgreSQL 16 + Angular 21
  - QA: `http://servicios.softnexus.io:8081` → migrando a `qa.servicios.softnexus.io` con TLS
- **Tenant de la integración**: **`softnexus`** ✅ confirmado en ambos lados
