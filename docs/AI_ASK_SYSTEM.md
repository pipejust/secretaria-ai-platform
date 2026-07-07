# Sistema de IA "Ask Acten" — Arquitectura de Búsqueda y Respuesta

> Documento técnico para **replicar el sistema en otra plataforma**.
> Describe cómo la IA llega a los datos, con qué gestor, cómo lee tablas
> y campos, y toda la lógica de recuperación (retrieval) y generación.

---

## 1. Resumen en una frase

Ask Acten es un **pipeline RAG (Retrieval-Augmented Generation) híbrido**:
combina búsqueda **vectorial** (pgvector), búsqueda **literal** (SQL
ILIKE / regex), **comprensión de la pregunta con un LLM** y **generación
de respuesta con un LLM**, todo sobre **PostgreSQL 17 + pgvector**,
aislado por `tenant_id` (multi-tenant).

---

## 2. Stack de datos

| Capa | Tecnología |
|------|------------|
| Gestor de base de datos | **PostgreSQL 17** (`pgvector/pgvector:pg17`) |
| Extensión vectorial | **pgvector** (`CREATE EXTENSION vector`) |
| ORM / acceso | **SQLModel** (sobre SQLAlchemy 2.x, driver `psycopg2`) |
| Conexión | `postgresql+psycopg2://…`, `pool_size=10`, `pool_pre_ping=True` |
| Embeddings | **OpenAI `text-embedding-3-small`**, 1536 dims |
| LLM comprensión de query | **Groq `llama-3.1-8b-instant`** (rápido/barato) |
| LLM respuesta | **Groq `llama-3.3-70b-versatile`** |
| LLM tareas/campos (curación) | **OpenAI `gpt-4o`** con fallback a Groq |

Ninguna credencial va en código: `OPENAI_API_KEY`, `GROQ_API_KEY`,
`DATABASE_URL`, `JWT_SECRET_KEY` viven en variables de entorno.

---

## 3. Cómo se llega a las tablas

### 3.1 Modelo de datos (las tablas que importan)

**`meetingsession`** — una fila por reunión. Campos que la IA lee:

| Campo | Uso en IA |
|-------|-----------|
| `tenant_id` | **Aislamiento multi-tenant** — TODA query lo filtra |
| `project_id` | Scope por proyecto/cliente |
| `title`, `date` | Selección por fecha, "scope X en Y", citas |
| `raw_transcript` | Texto literal (fuente de verdad para citas) |
| `raw_summary` | Resumen ejecutivo |
| `processed_decisions` / `_agreements` / `_risks` | Sec. curadas |
| `processed_attendees` | Participantes (JSON) |
| `status`, `processing_error` | Estado del procesamiento |

**`embeddingchunk`** — chunks vectorizados de cada sesión:

```sql
id             SERIAL PK
session_id     FK → meetingsession.id   (index)
kind           TEXT   -- 'summary'|'decisions'|'risks'|'agreements'|'transcript'
chunk_index    INT
content        TEXT   -- el texto del fragmento
embedding_vector  vector(1536)          -- pgvector
created_at     TEXT
```

**`actionitem`** — tareas extraídas (fuente autoritativa de "quién hace
qué"): `session_id`, `owner_name`, `owner_email`, `title`, `status`,
`due_date`, `priority`.

**`project`** / **`projectcontact`** — proyectos y sus contactos
(nombre, rol, empresa, email) → conectan personas reales con la IA.

### 3.2 Indexación (cómo entran los datos al índice vectorial)

Al procesar una sesión (`services/embedding_service.embed_session`):

1. Se **trocea** el contenido (`_split_transcript`, ~2400 chars/chunk).
2. Cada chunk se **envuelve con contexto** (ej. `[Reunión: Colpensiones
   - Kyndryl]\n…`) para no perder el tema en el espacio vectorial.
3. Se pide el embedding a OpenAI (`embed_batch`).
4. Se inserta con SQL crudo (SQLModel no tiene tipo pgvector nativo):

```sql
INSERT INTO embeddingchunk (session_id, kind, chunk_index, content, embedding_vector, created_at)
VALUES (:sid, :kind, :ci, :content, CAST(:vec AS vector), NOW()::text)
```

---

## 4. Cómo se lee/busca (retrieval) — el corazón del sistema

Endpoint: `POST /api/ask`. Pipeline por pregunta:

### Etapa 0 — Comprensión de la pregunta (LLM, en paralelo)
Un LLM barato (`llama-3.1-8b-instant`) analiza la pregunta **mientras**
corre la búsqueda vectorial (`asyncio.gather` → latencia extra ≈ 0) y
devuelve:
- `entities` — nombres propios/productos/personas.
- `search_terms` — 5-10 términos coloquiales para búsqueda literal.
- `reformulated_query` — la pregunta reescrita en forma canónica.
- Recibe además los **últimos turnos del hilo** para resolver
  referencias ("eso", "él") → memoria conversacional.

### Etapa 1 — Búsqueda vectorial (pgvector)
```sql
SELECT ec.session_id, ec.kind, ec.content,
       ec.embedding_vector <=> CAST(:qvec AS vector) AS distance
FROM embeddingchunk ec
JOIN meetingsession ms ON ms.id = ec.session_id
WHERE ms.tenant_id = :tenant       -- aislamiento
ORDER BY ec.embedding_vector <=> CAST(:qvec AS vector) ASC   -- distancia coseno
LIMIT :k
```
`<=>` = distancia coseno (0 = idéntico, 1 = ortogonal). Se corre una
**segunda** búsqueda con la `reformulated_query` y se hace merge por
menor distancia.

### Etapa 2 — Filtro de relevancia dinámico
No hay cutoff fijo: se ancla al mejor chunk (`best + DELTA`), con tope
duro (0.95) y mínimo de chunks garantizado. Evita contaminar el contexto
con reuniones no relacionadas.

### Etapa 3 — Búsqueda literal (recall que el vector pierde)
Sobre `raw_transcript`, `raw_summary`, `processed_*`:
- **ILIKE** (case-insensitive) para nombres/frases.
- **Regex con word-boundary** (`~* '\yTR\y'`) para **siglas cortas**
  (≤3 chars: TR, ADM, INF) — evita que "TR" matchee dentro de "otro".
- **Expansión por vocabulario de dominio** (`_DOMAIN_SYNONYMS`): un
  concepto se busca con todas sus variantes/alias (ej. "boletería" →
  tiquetera, etiquetera, Mi Boleta; "homologar" → "igual a la sede",
  "como está en sede", "nada nuevo").

### Etapa 4 — Precisión sobre recall
- Términos **genéricos** (sede, app, proyecto, sesión…) se descartan
  como semilla de búsqueda: matchean casi todo → ruido.
- Se prefieren **frases específicas** (multi-palabra) y se rankea cada
  sesión por cuántos términos-concepto contiene.
- **Poda de ruido**: si hay evidencia del concepto, se descartan chunks
  vectoriales genéricos que no lo mencionan.
- **Scope "X en Y"**: si la pregunta nombra un proyecto, la búsqueda se
  restringe a sus sesiones.
- **Cap** de 16 sesiones máximo al LLM (evita respuestas diluidas).

### Etapa 5 — Loaders determinísticos por intención
La pregunta se clasifica y se cargan datos exactos (marcados
`distance=0`, saltan el filtro de relevancia):
- **"resume las últimas N sesiones de X"** → selección exacta por
  fecha DESC + dossier rico por sesión (resumen + decisiones + acuerdos
  + riesgos + tareas reales).
- **"en la última reunión de X"** → LA sesión más reciente del tema
  (determinístico por fecha, no por vector).
- **preguntas de tareas/pendientes** → inyecta filas reales de
  `actionitem` (fuente autoritativa del estado).
- **evidencia yes-no** / **definición de siglas** / **dossier howtech**.

---

## 5. Cómo se genera la respuesta

Se construye un **contexto** con cada chunk + su metadato (sesión,
título, fecha formateada legible, proyecto) y se manda a
`llama-3.3-70b-versatile` pidiendo **JSON estricto**:

```json
{ "intro": "...", "intro_source_sessions": [id],
  "decisions": [{"text","source_sessions"}],
  "action_items": [{"title","owner","due_date","status","source_sessions"}],
  "risks": [...], "agreements": [...] }
```

El prompt lleva **~34 reglas** acumuladas de casos reales, entre ellas:
- Cada afirmación **cita la sesión origen** (`source_sessions`) → trazable.
- **Anti-boilerplate**: prohibido párrafos genéricos que servirían para
  cualquier sesión.
- **Anti-hedge**: si la evidencia contiene el dato, afirmarlo; prohibido
  "no está claramente establecido" cuando sí aparece.
- **Inferencia de identidad**: entidades renombradas en el tiempo
  (etiquetera → Mi Boleta) se unifican con su línea de tiempo.
- **Definición de siglas** desde aposición ("la carpeta TR, términos de
  referencia").
- `max_tokens` dinámico (2000–7000) según densidad de evidencia.

---

## 6. Seguridad y aislamiento (crítico para multi-tenant)

- **Todas** las queries filtran `tenant_id` — sin excepción. Una empresa
  nunca recupera datos de otra.
- Auth por **JWT** (claims: `sub`, `tenant_id`, `is_superadmin`).
- Permisos de escritura vía dependencia (`require_session_writer`).

---

## 7. Cómo replicarlo en otra plataforma (checklist)

1. **Postgres + pgvector**: `CREATE EXTENSION vector` + columna
   `vector(N)` (N = dims del modelo de embeddings elegido).
2. **Tabla de contenido** (tu equivalente a `meetingsession`) con
   `tenant_id` + los campos de texto que quieras indexar.
3. **Tabla de chunks** (`embeddingchunk`) con FK, `kind`, `content`,
   `embedding_vector`.
4. **Pipeline de indexación**: trocear → envolver con contexto → embed
   por lotes → insertar con `CAST(:vec AS vector)`.
5. **Endpoint de pregunta** con las 5 etapas: analizador LLM en paralelo
   → vector search (`<=>` + LIMIT) → filtro dinámico → literal ILIKE/regex
   + sinónimos de dominio → loaders determinísticos por intención.
6. **Generador**: LLM con salida JSON estricta, citas obligatorias,
   reglas anti-boilerplate/anti-hedge.
7. **Aislamiento**: `tenant_id` en cada query; JWT.
8. **Ajustar el vocabulario de dominio** (`_DOMAIN_SYNONYMS`) a tu
   negocio — es lo que más sube la calidad por poco esfuerzo.

---

## 8. Archivos clave (referencia de implementación)

| Archivo | Responsabilidad |
|---------|-----------------|
| `backend/services/embedding_service.py` | Embeddings, indexación, `search_similar` (vector + título) |
| `backend/routers/ask.py` | Pipeline completo de retrieval + generación (~34 reglas) |
| `backend/services/transcript_pipeline.py` | Procesa la sesión: resumen, decisiones, tareas, attendees, embeddings |
| `backend/services/groq_service.py` | Cliente LLM (OpenAI gpt-4o + fallback Groq) |
| `backend/models.py` | `MeetingSession`, `EmbeddingChunk`, `ActionItem`, `Project`, `ProjectContact` |
| `backend/database.py` | Engine, migraciones ligeras, `CREATE EXTENSION vector` |

---

## 9. Decisiones de diseño que valen la pena copiar

- **Híbrido vector + literal**: el vector solo no basta — pierde siglas,
  nombres aislados y frases exactas. El literal lo rescata.
- **LLM de comprensión en paralelo**: genera sinónimos por-pregunta
  (generaliza a cualquier tema) sin costar latencia.
- **Loaders determinísticos por intención**: para "última reunión",
  "resume N", "tareas de X" → NO confiar en similitud vectorial; ir por
  fecha/tabla exacta. La precisión sube muchísimo.
- **`distance=0` para evidencia dura**: separa lo determinístico de lo
  probabilístico y permite priorizar y calcular confianza.
- **Precisión > recall en tenants mono-proyecto**: los términos que
  matchean todo son ruido; hay que descartarlos como semilla.
