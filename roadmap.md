# Hoja de ruta Notiva v1 — sin bots propios, manteniendo Fireflies

> **Alcance:** implementar todas las features del análisis competitivo
> ([`docs/competidores.md`](competidores.md)) **excepto** las que requieren un bot
> propio que se una a las llamadas o procesamiento local en cliente. Esas se
> dejan para v2. Notiva sigue usando Fireflies como ingest de transcripción.
>
> **Dimensionamiento:** 12 sprints de 2 semanas (≈6 meses para 1 dev senior;
> ≈3-3.5 meses para 2 devs en paralelo).
> **Premisa:** todo se entrega con tests + docs + observabilidad básica.

---

## TL;DR — Resumen ejecutivo

| Sprint | Tema | Esfuerzo (sem-dev) | Valor de negocio |
|---|---|---:|---|
| 0 | Foundation técnica (pgvector, refactors, tests base) | 2 | Habilita los siguientes 3 sprints |
| 1 | Quick wins (selector LLM, YouTube, multi-idioma, .ics) | 2 | Demos vendibles inmediatos |
| 2 | **Ask Notiva** (RAG sobre histórico) | 2 | Diferenciador #1 vs Acta/Convo |
| 3 | Calendar integration (Google + Microsoft) | 2 | Habilita workflows automáticos |
| 4 | Outputs role-específicos (PRD, deal brief, status) | 2 | Match con Acta.ai |
| 5 | Distribución: Slack + Notion + Teams + Google Docs | 2 | Triple cobertura del stack del cliente |
| 6 | CRM integration (HubSpot, Salesforce, Pipedrive) | 2 | Caso de uso comercial |
| 7 | Versionado + comentarios + permisos granulares | 2 | Workspace colaborativo |
| 8 | SSO/SAML + auditoría + GDPR | 2 | Enterprise-ready |
| 9 | Apps móviles (React Native) — MVP | 3 | Cierre del gap mobile |
| 10 | Analytics, ROI, alertas push, recurrentes | 2 | Sticky / retention |
| 11 | API pública + rate limiting + buffer/polish | 2 | Plataforma vs producto |

**Total:** ~25 semanas-dev. Con 2 devs y carriles paralelos cae a ~14 semanas.

---

## Filosofía de priorización

1. **ROI alto primero:** Ask Notiva (RAG) y Quick wins son baratos de hacer
   y se demostrarían en cualquier sales pitch.
2. **Dependencias técnicas tempranas:** pgvector, refactor de integraciones y
   modelo de permisos antes de los sprints que los necesitan.
3. **Apps móviles al final** porque son largas y no afectan al MVP web actual
   (Tier 1 enterprise se cierra con los sprints 4-8).
4. **Cada sprint cierra con producto desplegable** — nada queda en branch
   indefinidamente.

---

## Decisiones de arquitectura previas (acordar antes de Sprint 0)

| Decisión | Recomendación |
|---|---|
| **Vector DB** | `pgvector` directo en Supabase (extensión instalable; ya tenemos Postgres). Alternativa: Pinecone managed, pero suma $/mes y latencia. |
| **Embeddings** | `text-embedding-3-small` de OpenAI ($0.02/M tokens). Alternativa: BGE-small en Hugging Face self-hosted si volumen sube. |
| **Auth federado** | Pasar a [Supabase Auth](https://supabase.com/docs/guides/auth) para SSO/SAML out-of-the-box vs construir SAML propio. Trade-off: amarra a Supabase. |
| **Push notifications** | Firebase Cloud Messaging (gratis hasta volúmenes altos). |
| **Apps móviles** | **React Native** (Expo). Compartimos contexto/types con el frontend Angular si aislamos bien. Alternativa Flutter (mejor UX nativa) pero stack nuevo. |
| **Audit logs** | Tabla `audit_log` en Postgres + retention 90 días. Para SOC 2 será suficiente. |
| **CRM API** | Cliente unificado tipo `IntegrationSetting` (ya lo tenemos para Trello/Jira/etc) — extender el mismo factory. |
| **Costos LLM** | Mantener Groq por defecto, OpenAI como override. Trackear $ por sesión. |

> **Acción upfront:** una sesión de 2h para validar las 7 decisiones con el equipo. Bloquea el resto.

---

## Sprints en detalle

### Sprint 0 — Foundation técnica (semanas 1-2)

**Objetivo:** dejar la plataforma lista para los siguientes sprints sin
deuda técnica nueva.

**Entregables:**
- [ ] Habilitar extensión `pgvector` en Supabase (`CREATE EXTENSION IF NOT EXISTS vector`).
- [ ] Migración: nueva tabla `embedding_chunk` (session_id, kind: summary/decision/risk/agreement/transcript, content TEXT, embedding VECTOR(1536), created_at).
- [ ] Helper `services/embedding_service.py` con `embed_text(text) -> vec` usando OpenAI.
- [ ] Backfill script: re-procesa las 46 sesiones existentes y crea sus embeddings.
- [ ] **Test suite base con pytest** (hoy hay 0 tests). Mínimo: pipeline transcript_pipeline + endpoints crud.
- [ ] CI básico en GitHub Actions: lint + tests + docker build.
- [ ] Métricas: instrumentar `OPENAI_TOKENS_USED` y `GROQ_TOKENS_USED` con counter por sesión.

**Riesgos:**
- Pgvector requiere Postgres ≥11 (Supabase OK).
- Backfill puede tomar 5-10 min para las 46 sesiones × ~5 chunks = 230 embeddings × $0.02/M = centavos.

**DoD:** un nuevo MeetingSession dispara generación de embeddings al final del pipeline. Tests pasan en CI.

---

### Sprint 1 — Quick wins (semanas 3-4)

Cuatro features chicas pero vendibles individualmente.

| # | Feature | Esfuerzo | Cómo |
|---|---|---|---|
| 1 | **Selector de modelo LLM por sesión** | 1d | Campo `llm_provider` en MeetingSession (`groq`/`openai`/`auto`). UI: select en /admin/curation. Pipeline lo respeta. |
| 2 | **YouTube link import** | 2d | `yt-dlp` en backend para extraer audio → reusa pipeline de upload manual con Whisper Groq. |
| 3 | **Multi-idioma 50+** | 2d | Whisper-large-v3-turbo ya soporta. Agregar campo `language_code` en Project (es/en/pt/fr/...). UI: dropdown ISO-639. Prompt LLM ajustado para output bilingüe opcional. |
| 4 | **Auto-schedule next meeting** | 3d | Prompt extra al pipeline: "¿se acordó próxima reunión?" → genera bloque iCalendar `.ics` adjunto al email + botón "Agregar a Calendar" en UI. |

**Métricas de éxito:**
- ≥5% de sesiones usan modelo override (validar uso real).
- ≥2 imports de YouTube en la primera semana.
- ≥1 acta no-español procesada correctamente.

---

### Sprint 2 — "Ask Notiva" (RAG) — semanas 5-6

**El feature de mayor valor demo.** Un ejecutivo abre la app y pregunta:
"¿qué decidimos sobre el rebranding?" → obtiene respuesta citando las
reuniones específicas.

**Entregables backend:**
- [ ] Endpoint `POST /api/ask` con body `{question: str, project_id?: int, top_k?: int}`.
- [ ] Pipeline:
  1. Embed `question` → vec.
  2. `SELECT * FROM embedding_chunk ORDER BY embedding <-> :vec LIMIT k` (filtrado por project si se pasó).
  3. Construir contexto con los chunks y citas.
  4. LLM (Groq) con prompt de "responde citando IDs de sesión".
- [ ] Respuesta incluye `answer: str, citations: [{session_id, snippet}]`.

**Entregables frontend:**
- [ ] Nueva ruta `/admin/ask` con `AskComponent`.
- [ ] UI tipo chat: historia de preguntas, link clickeable a la sesión citada.
- [ ] Selector de proyecto opcional (filtro).

**Métricas:**
- Latencia P95 < 3s para top_k=5.
- Tasa de citación válida > 90% (validar con eval set manual de 20 preguntas).

---

### Sprint 3 — Calendar integration (semanas 7-8)

**Entregables:**
- [ ] OAuth Google Calendar (scope `calendar.events.readonly`).
- [ ] OAuth Microsoft Graph (scope `Calendars.Read`).
- [ ] Tabla `calendar_account` (user_id, provider, access_token cifrado, refresh_token cifrado, expires_at).
- [ ] Cron de sync: cada 30 min trae próximas 7 días de eventos del usuario.
- [ ] Tabla `calendar_event` ligada a `meeting_session` cuando matchee título/hora.
- [ ] UI en `/admin/dashboard`: panel "Próximas reuniones" + "Sin acta aún".
- [ ] Webhook de Fireflies: si llega un transcript_id que matchea un calendar_event por título+hora, autoasigna `project_id` y `attendees`.

**Métricas:** ≥80% de calendar_events de los últimos 7 días terminan con MeetingSession asociada.

---

### Sprint 4 — Outputs role-específicos (semanas 9-10)

Match con Acta.ai. El usuario dice "esta reunión es de tipo: comercial /
producto / RRHH / 1:1 / status / kickoff" y Notiva genera el artefacto
adecuado además del acta estándar.

**Entregables:**
- [ ] Tabla `output_template` con columnas: `name, role_type (commercial|product|hr|status|kickoff|...), prompt_template, schema_json, is_active`.
- [ ] Seed de 6 templates por defecto (PRD, Deal Brief, Status Update, 1:1 Notes, Kickoff Doc, Eval Report).
- [ ] Endpoint `POST /api/sessions/{id}/generate_output?template_id=X` → llama OpenAI con el `prompt_template` + transcript → guarda en `session_output` (tabla nueva).
- [ ] UI en `/admin/curation/:id`: dropdown "Generar artefacto adicional" + render de los outputs guardados.
- [ ] Selector "Tipo de reunión" en MeetingSession (auto-sugerido por LLM al crear).
- [ ] Export por output: descargar Word/PDF individual.

**Métricas:**
- ≥30% de sesiones generan al menos un output extra.
- Templates más usados → priorizar futuros.

---

### Sprint 5 — Distribución: Slack + Notion + Teams + Google Docs (semanas 11-12)

Aprovechar el factory de `services/integrations/` ya existente.

**Entregables:**
- [ ] `services/integrations/slack.py` — post resumen + tareas a un canal vía webhook URL o Slack Bot OAuth.
- [ ] `services/integrations/notion.py` — crear página en una database con propiedades mapeadas.
- [ ] `services/integrations/microsoft_teams.py` — post a canal de Teams vía Graph API.
- [ ] `services/integrations/google_docs.py` — crear Google Doc con el acta usando templates.
- [ ] Update del modelo `Routing.destination_type` para aceptar `slack|notion|teams|gdocs`.
- [ ] UI en `/admin/projects/:id` (sección routings): nuevos targets.
- [ ] `IntegrationSetting` con su config_json para cada uno.

**Métricas:** ≥1 routing nuevo configurado por la mitad de proyectos activos.

---

### Sprint 6 — CRM integration (semanas 13-14)

**Caso de uso:** reunión con cliente → buscar el deal correspondiente en
HubSpot/Salesforce/Pipedrive → adjuntar el acta como nota.

**Entregables:**
- [ ] `services/integrations/hubspot.py`, `salesforce.py`, `pipedrive.py`.
- [ ] Cada uno expone:
  - `find_deal_by_email(email)` → busca contactos/deals por email del attendee.
  - `attach_note(deal_id, html_content)` → agrega la nota.
- [ ] Pipeline post-curación: si la sesión tiene CRM routing activo, intenta matchear deal por email del primer attendee externo y crear la nota.
- [ ] UI: indicador "✓ Sincronizado con HubSpot deal #123" en la curación.

**Métricas:** % de sesiones que se sincronizan automáticamente con un deal vs requieren matching manual.

---

### Sprint 7 — Versionado + comentarios + permisos granulares (semanas 15-16)

Workspace colaborativo. Hoy una sola persona edita el acta y se sobreescribe.

**Entregables:**
- [ ] Tabla `meeting_session_version` (snapshot completo del acta antes de cada edit del usuario).
- [ ] Tabla `comment` (session_id, section: summary|decisions|risks|agreements|task, ref_id, author_user_id, body, created_at, resolved_at).
- [ ] Tabla `session_permission` (session_id, user_id, role: viewer|editor|admin).
- [ ] Endpoint `GET /api/sessions/{id}/versions` y `POST /api/sessions/{id}/restore/{version_id}`.
- [ ] Endpoints CRUD comments + threading.
- [ ] UI en curación:
  - Botón "Versiones" con timeline tipo Google Docs.
  - Comentarios sticky por sección (similar a Notion sidebar).
  - Settings de permisos por sesión.

**Riesgos:** snapshots crecen rápido. Mitigación: solo guardar hasta 20 versiones por sesión, después rotar.

---

### Sprint 8 — SSO + auditoría + GDPR (semanas 17-18)

Bloque "enterprise-ready". Sin esto no hay venta corporativa.

**Entregables:**
- [ ] Migración a Supabase Auth o Auth0 para SSO/SAML out-of-the-box.
- [ ] Soporte para SAML (Okta, Azure AD, Google Workspace).
- [ ] OIDC genérico.
- [ ] Tabla `audit_log` (user_id, action, resource_type, resource_id, ip, user_agent, payload_diff, created_at). Hook en endpoints sensibles (login, edit settings, dispatch, delete).
- [ ] Endpoint `GET /api/me/export` — devuelve ZIP con todos los datos del usuario en formato JSON (GDPR Right to Data Portability).
- [ ] Endpoint `DELETE /api/me/account` — soft delete con job de purga real a 30 días (Right to Erasure).
- [ ] Política de privacidad pública (markdown servido desde el repo).
- [ ] Cifrado at-rest verificado (Supabase ya lo da; documentar).

**Métricas:** primer cliente enterprise con SSO funcionando.

---

### Sprint 9 — Apps móviles MVP (semanas 19-21, 3 semanas)

**Stack:** React Native + Expo + TypeScript. Compartimos types/clients con el frontend Angular vía un paquete `@notiva/sdk`.

**MVP scope (no full feature parity):**
- [ ] Login (con SSO si está disponible).
- [ ] Tab "Sesiones" — lista + filtros básicos.
- [ ] Tab "Pendientes" — tareas asignadas a mí.
- [ ] Detalle de sesión: ver, editar, marcar tareas, comentar.
- [ ] Push notifications (Firebase) para tareas vencidas o nuevas asignaciones.
- [ ] **Sin** transcripción on-device (todo el flujo sigue siendo backend).
- [ ] Compartir acta como PDF nativo (`expo-sharing`).

**Out of scope MVP (v2 mobile):**
- Curación completa (botones IA, regenerar, etc.) — solo lectura en mobile.
- Subida de audio desde móvil — agendar para v2.
- Charts del dashboard.

**Stores:**
- [ ] Apple Developer Account ($99/año).
- [ ] Google Play Developer Account ($25 one-time).
- [ ] Screenshots, copy de la store, política de privacidad.

**Métricas:** 100 descargas en el primer mes; DAU/MAU tracking.

---

### Sprint 10 — Analytics + alertas push + recurrentes (semanas 22-23)

**Entregables:**
- [ ] **Meeting quality scoring** — pipeline post-procesamiento que calcula 5 scores sobre el transcript:
  - Claridad (% palabras descartables tipo "uhm", "este")
  - Listening balance (distribución de tiempo de palabra entre attendees)
  - Time on topic (% del tiempo en cada theme detectado)
  - Decisions density (decisiones / minuto)
  - Action items density
- [ ] Vista `/admin/analytics` con gráficos de tendencia mensual.
- [ ] **Dashboard ROI ejecutivo** — tiempo total invertido en reuniones por persona, % auto-curado, tareas completadas vs creadas.
- [ ] **Alertas push** — Firebase + email cuando una tarea está vencida (cron diario).
- [ ] **Reuniones recurrentes** — detección automática por título similar (fuzzy match) → vista "histórico de esta serie".

**Riesgos:** scoring puede dar falsos positivos en idiomas no español. Documentar limitación.

---

### Sprint 11 — API pública + rate limiting + polish (semanas 24-25)

**Plataforma vs producto.** Permitir integraciones custom de clientes.

**Entregables:**
- [ ] Tabla `api_key` (user_id, name, hashed_key, created_at, last_used_at, scopes JSON).
- [ ] Endpoint `POST /api/me/api-keys` para generarlas.
- [ ] Middleware FastAPI: si llega header `X-API-Key`, autentica vía esa tabla en lugar de JWT.
- [ ] Rate limiting con `slowapi`: 60 req/min por API key.
- [ ] OpenAPI docs públicos en `/api/docs` (FastAPI ya los genera, refinar).
- [ ] Buffer para bugs reportados de los sprints 1-10.
- [ ] Polish UI: loading states, error states, empty states de las nuevas vistas.

---

## Carriles paralelos (si hay 2 devs)

```
Dev A: Sprint 0 → 1 → 2 → 4 → 7 → 9 (mobile)
Dev B:           1 → 3 → 5 → 6 → 8 → 10 → 11
```

Sprint 1 lo trabajan ambos en paralelo (4 quick wins independientes).

---

## Costos esperados (servicios externos)

| Servicio | Costo mensual | Nota |
|---|---|---|
| pgvector en Supabase | $0 | Extensión gratuita |
| OpenAI embeddings | ~$1-5 | text-embedding-3-small a $0.02/M |
| Auth0 Essentials (alternativa SSO) | $30 (hasta 1k usuarios activos) | Solo si no migramos a Supabase Auth |
| Firebase Cloud Messaging | $0 | Free tier suficiente |
| Apple Developer | $99/año | One-time anual |
| Google Play | $25 | One-time |
| Gotenberg (DOCX→PDF) | $0 (self-hosted o demo) | Opcional: $5/mo en Render dedicado si volumen alto |
| Notion API | $0 | Free para integraciones |
| Slack/Teams APIs | $0 | Webhook gratis |
| HubSpot API | $0 | Free tier hasta cierto volumen |
| Salesforce API | depende del cliente | Cliente trae su org |

**Costos NO incluidos pero a considerar:**
- **SOC 2 Type 1** auditoría externa: USD 15-25k. Solo si un cliente enterprise lo exige por contrato.
- **SOC 2 Type 2:** USD 30-50k. Después de 6 meses operando con controles.
- **Penetration testing** anual: USD 5-15k.

> **Recomendación:** posponer SOC 2 hasta tener un cliente que lo pague. Mientras tanto, documentar controles internos y prepararse.

---

## Métricas globales del roadmap

Por sprint debes poder responder:

| Métrica | Línea base hoy | Meta tras roadmap |
|---|---|---|
| Tiempo de curación humana por sesión | ~5-10 min | <2 min con outputs auto-generados |
| % sesiones auto-despachadas | ~0% (cron arreglado pero isEnabled=false) | >40% |
| % de tareas completadas en plazo | sin medir | >60% |
| Diferenciador "RAG" demostrable | NO | SÍ (ASK Notiva) |
| Apps móviles publicadas | 0 | iOS + Android |
| Integraciones de salida disponibles | 4 (Trello, Jira, ClickUp, Azure) | 9+ (+ Slack, Notion, Teams, GDocs, HubSpot/Salesforce/Pipedrive) |
| Idiomas soportados | 1 (es) en prompts | 50+ via Whisper |
| Clientes con SSO/SAML | 0 | 1+ |

---

## Riesgos transversales

| Riesgo | Mitigación |
|---|---|
| Costos LLM escalan con uso | Tracking por sesión + plan que limita Groq vs OpenAI según tier del cliente |
| Migración SSO rompe sesiones activas | Hacer en mantenimiento programado + email previo a usuarios |
| pgvector no escala más allá de 100k chunks | Migrar a índice IVFFlat o Pinecone managed |
| React Native deuda técnica | Usar Expo managed workflow + EAS Build para evitar tocar código nativo |
| Apple Store rechaza app por ToS | App Review check-list previo (privacy policy, login funcional, screenshots reales) |
| Cliente exige SOC 2 antes de tenerlo | Carta de "controls in progress" + pen-test reciente como puente |

---

## Decisiones que necesito de ti antes de Sprint 0

1. **Stack mobile:** React Native + Expo (recomendado) ó Flutter ó nativo iOS/Android.
2. **Auth federado:** migrar a Supabase Auth ó Auth0 ó construir SAML propio.
3. **Vector DB:** pgvector (recomendado) ó Pinecone managed.
4. **Precio del Tier de auto-curación:** ¿se cobra extra por la feature o va incluida en el plan base?
5. **Compliance:** ¿hay algún cliente concreto pidiendo SOC 2 ya? Define si Sprint 8 sube a Sprint 5.
6. **Idiomas prioridad:** ¿es/en suficiente o también pt-BR (mercado brasileño grande para SaaS)?
7. **Apps móviles:** ¿iOS-first o Android-first? Determina sprint 9 (1.5 sem por plataforma).
