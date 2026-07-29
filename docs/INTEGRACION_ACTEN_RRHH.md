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
| **`GET /calendar/events`** | ✅ **nuevo** | Responde `200`; hoy sin datos (ver aviso abajo) |
| **Sync automática** | ✅ **nuevo** | Cada 15 min + completa a las 03:20 |
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

**De Acten: nada.** Los dos que quedaban —agendar el sync y exponer
`/ask`— están cerrados, junto con calendario y documentos. El lado
Acten no bloquea ninguna tarea suya.

**De ellos:** la interfaz (reuniones, detalle, tareas, Kanban, Ask) — el
mayor esfuerzo real, y ya no espera nada nuestro.

**Conjunto:** cerrar quién crea en el asistente de enlace — ver §16.

**Aviso honesto sobre el calendario.** `GET /calendar/events` responde
`200` y está probado, pero hoy devuelve `{"items": [], "total": 0}`:
**no hay ninguna cuenta de Google/Microsoft conectada** en el despliegue
(0 cuentas, 0 eventos en base). El endpoint no está en blanco por un
fallo — está esperando a que alguien conecte una agenda en Acten. Si su
interfaz lo va a pintar, conviene saberlo antes de depurar una lista
vacía.

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
✅ **Implementado.** Eventos de agenda sincronizados desde Google o
Microsoft, con el vínculo al acta cuando ya llegó.

Query: `project_external_id`, `date_from`, `date_to`, `page`, `limit`
(default 50, máx 200). Alcance: **`calendar:read`** — ya añadido a su
clave, **es la misma clave, no cambia de valor**.

```json
{
  "items": [{
    "id": 91,
    "title": "Colpensiones - Seguimiento API",
    "start_at": "2026-07-30T15:00:00Z",
    "end_at": "2026-07-30T16:00:00Z",
    "meeting_url": "https://meet.google.com/...",
    "project_external_id": "10864d68-bcb4-44b4-9cec-8ee910714b05",
    "session_id": 653,
    "attendees": [{"email": "...", "name": "..."}]
  }],
  "total": 12, "page": 1, "limit": 50
}
```

`session_id` es el puente: `null` mientras la reunión no tenga acta, y el
id del acta en cuanto llega. Sirve para pintar «acta disponible» sin
cruzar nada del lado de ustedes.

**Visibilidad:** la persona ve su propia agenda **y** los eventos de los
proyectos donde es miembro.

> ⚠️ **Hoy devuelve lista vacía.** No hay cuentas de calendario
> conectadas en el despliegue. El endpoint funciona; los datos aparecen
> cuando alguien conecte Google o Microsoft desde Acten.

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
- Alcances de Acten: `sessions:read`, `tasks:read`, `tasks:write`,
  `ask:query`, **`calendar:read`**, `sync:write`.
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

### Paginación (común a `GET /sessions` y `GET /tasks`)

`page` (default 1) · `limit` (default 20, máx 100) · respuesta con
`{items, total, page, limit}`. Para sincronización incremental usen
`updated_since` en ISO-8601 — mismo criterio que su `GET /employees`.

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
| 10 | **`GET /calendar/events`** | ✅ **hecho** — sin datos hasta conectar una agenda |
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
| 12 | Agenda conectada (Google/Microsoft) | ⏸ el endpoint existe; faltan cuentas conectadas |
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

## 16. Asistente de enlace — quién crea

Su propuesta: **crea quien lanza el asistente**, en un solo sentido por
ejecución. **De acuerdo, y es lo correcto.**

El razonamiento es sólido: si ambos lados crean a la vez, la idempotencia
no salva nada, porque cada uno daría de alta por su cuenta **antes** de
ver lo del otro. La llave `external_ref` solo evita duplicados *después*
de que exista el enlace, no durante una carrera entre dos procesos.

### Regla acordada

> **Una ejecución = un sentido.** Quien pulsa *Siguiente* ve las dos
> listas, confirma y las altas salen de ahí. El otro lado nunca crea por
> iniciativa propia durante esa ejecución.

Acten ya expone lo necesario para ambos sentidos:

| Sentido | Cómo |
|---|---|
| Lanzan desde Servicios → crean en Acten | `POST /api/v1/link/directory/projects` y `/people` (idempotentes) |
| Lanzan desde Acten → crean en Servicios | Acten lee su directorio y llama a los `POST` simétricos que ustedes expongan |

**Y en ambos sentidos vale la misma regla de oro:** ante coincidencia
parcial de nombre no se crea nada — se devuelven los candidatos y decide
un humano. Dar de alta a ciegas un «Cortés Burgos» crearía un tercer
hermano que no existe, y ese error no se descubre hasta que alguien lee
un acta que no le corresponde.

---

## 12. Contactos técnicos

- **Acten**: `https://api.acten.app` · FastAPI + PostgreSQL 17 + pgvector
- **Servicios/RRHH**: `https://servicios.softnexus.io` · FastAPI + PostgreSQL 16 + Angular 21
  - QA: `http://servicios.softnexus.io:8081` → migrando a `qa.servicios.softnexus.io` con TLS
- **Tenant de la integración**: **`softnexus`** ✅ confirmado en ambos lados
