# Análisis competitivo — Notiva vs. mercado

**Fecha:** 2026-05-10
**Alcance:** 22 URLs revisadas (5 competidores funcionales reales + 4 colisiones fonéticas + 6 colisiones de marca + 3 dominios muertos + 4 apps móviles).

---

## TL;DR

| Categoría | Sitio | Veredicto |
|---|---|---|
| 🔴 **Amenaza alta** | Convo (`itsconvo.com`) | Mucho más feature-rich; copiar real-time + RAG |
| 🟠 **Amenaza media** | Acta.ai | Outputs role-específicos automáticos, integraciones más profundas |
| 🟠 **Amenaza media** | MyMinutes.ai | Apps móviles + multi-idioma + chat UI; 750k usuarios |
| 🟡 **Amenaza baja** | Minuta.aplivo.eu, MinutAI | Mobile-only, sin dispatch a herramientas de proyecto |
| 💀 **Muertos** | getminuta.online (404), minuta.app (530), Convoke LTI (404) | Descartados |
| ⚠️ **Naming** | notiva.framer.ai | **Mismo nombre, otra app activa** — riesgo SEO/marca |
| ⚠️ **Adyacente** | Klarity (YC S18) | Document AI; solapamiento parcial en "captura de procesos" |

---

## Baseline — qué tiene Notiva HOY

**Backend:**
- Webhook Fireflies con verificación de token (auto-generado en BD)
- Pipeline IA: Groq Llama 3.3 70B (insights) + OpenAI gpt-4o (tareas) + Whisper Groq (audios manuales)
- Subida manual de audio o texto pegado
- Generación documental: Word + PDF (templates + Gotenberg + FPDF fallback)
- Dispatch a Trello, Jira, ClickUp, Azure DevOps
- Email transaccional con Resend (plantillas Jinja, attachments PDF/DOCX)
- Auto-Dispatch parametrizable por proyecto (cron 5min) o global
- Endpoints: pendientes con buckets temporales, reportes semanales/mensuales con PDF

**Frontend (Angular SPA):**
- `/admin/dashboard`: lista de sesiones con buscador live (título / proyecto / fecha)
- `/admin/curation/:id`: edición + 2 botones de IA de uso único (sugerir campos / regenerar tareas)
- `/admin/projects` + `/admin/projects/:id`: detalle con KPIs, decisiones recientes, tareas activas
- `/admin/pendientes`: trazabilidad con KPIs por bucket + ranking de responsables
- `/admin/reportes`: selector período + descarga PDF
- `/admin/settings`: API keys de integraciones (auto-genera webhook URL Fireflies)
- `/admin/templates`, `/admin/users`, `/admin/roles`

**Modelo de datos:** Project, ProjectContact, MeetingSession, ActionItem (con `status` + `completed_at`), Routing, Template, IntegrationSetting, Role, User. Auth JWT + roles admin.

---

## 🔴 Competidores funcionales — gaps prioritarios

### 1. Convo (`itsconvo.com`) — el más fuerte

> "AI meeting assistant que corre LOCALMENTE en tu Mac, da sugerencias EN VIVO durante la llamada"

**Features que tiene Convo y Notiva NO:**

| # | Feature de Convo | Esfuerzo estimado |
|---|---|---|
| 1 | **Sugerencias en tiempo real** durante la llamada (2-3 seg, adaptadas a fase: apertura, discovery, next steps) | Alto |
| 2 | **Bot que se une a Zoom/Meet/Teams/Webex** (vs. Notiva, que solo procesa post-call vía Fireflies) | Alto |
| 3 | **Procesamiento local en Mac** (privacy-first, no sube audio a la nube) | Muy alto |
| 4 | **Document search/RAG en vivo** sobre PDFs subidos, links, Slack — durante la llamada | Alto |
| 5 | **Meeting analytics con scoring**: claridad, listening, time management, colaboración, decision-making | Medio |
| 6 | **Auto-scheduling del próximo meeting** detectado en la conversación | Bajo |
| 7 | **Selección de modelo LLM por usuario** (GPT, Claude, Gemini en runtime) | Bajo |
| 8 | **Integración con 5000+ apps vía Zapier/Make** | Medio |
| 9 | **Drafts de email post-call** automáticos | Bajo |
| 10 | **Invisible recording** (sin avisar a otros participantes) | Medio (ético) |

**Pricing:** Starter $14.99/mes · Pro $49.99/mes · Enterprise custom. Free trial 7 días.

---

### 2. Acta.ai — outputs role-específicos automáticos

> "Meeting intelligence engine que transforma reuniones en insights accionables (+40% productividad)"

**Features que tiene Acta y Notiva NO:**

| # | Feature de Acta | Esfuerzo |
|---|---|---|
| 1 | **Bot Zoom/Teams/Meet** que se une vía calendar | Alto |
| 2 | **Calendar integration nativa** (Google, Microsoft, Teams Calendar) — auto-detecta reuniones | Medio |
| 3 | **"Ask Acta"** — chat en lenguaje natural sobre insights extraídos (RAG) | Alto |
| 4 | **Outputs role-específicos automáticos**: PRDs, Jira tickets, deal briefs, assessment reports — sin botones manuales | Medio |
| 5 | **Dashboard con análisis de performance** del usuario | Medio |
| 6 | **Almacenamiento ilimitado** en Pro+ (Notiva no lo promociona) | Bajo |

**Pricing:** Free 15d · Pro $30/año (≈$2.50/mes) · Enterprise custom. **Mucho más barato** que Convo.

---

### 3. MyMinutes.ai — apps nativas + multi-idioma

> "Get perfect notes and transcriptions with AI" — 750k usuarios, 200M minutos procesados, SOC 2

**Features que tiene MyMinutes y Notiva NO:**

| # | Feature de MyMinutes | Esfuerzo |
|---|---|---|
| 1 | **Apps nativas iOS, macOS, Android** | Muy alto |
| 2 | **Sync de calendario nativo** en macOS | Bajo (Notiva web) |
| 3 | **Chat UI para extraer insights** (vs dashboard Angular tradicional) | Medio |
| 4 | **Carga directa de YouTube links** | Bajo |
| 5 | **50+ idiomas** soportados | Bajo (config Whisper) |
| 6 | **SOC 2 compliant** | Alto (auditoría) |
| 7 | **Grabador de audio integrado** (no solo upload) | Medio |

---

### 4. Minuta (`minuta.aplivo.eu`) — mobile-first

> "AI notekeeper for meetings, lectures & consultations"

| # | Feature | Esfuerzo |
|---|---|---|
| 1 | App **iOS nativa** (Android coming) | Muy alto |
| 2 | Importación directa de **videos YouTube** | Bajo |
| 3 | Diarización (etiquetado de oradores) en transcripción | Medio |
| 4 | Biblioteca buscable de grabaciones anteriores | Bajo |
| 5 | **50+ idiomas** | Bajo |

---

### 5. MinutAI (`minut.ai`) — gratuito y simple

| # | Feature | Esfuerzo |
|---|---|---|
| 1 | Apps iOS y Android | Muy alto |
| 2 | **Integración nativa de calendario** | Medio |
| 3 | 35+ idiomas | Bajo |
| 4 | Compartir de un clic | Bajo |
| 5 | **Free** indefinido (oferta limitada) | n/a |

---

## ⚠️ Conflictos de marca — riesgo de naming/SEO

### 🚨 ALERTA ALTA — `notiva.framer.ai`

**Mismo nombre, app activa.** Es una herramienta de note-taking con IA con su propio pricing (Free $0 · Pro $8.89/mes · Enterprise) y workspace colaborativo. Ya tiene posicionamiento orgánico bajo "Notiva".

**Implicaciones:**
- SEO: tu Notiva competirá por la misma palabra clave.
- Marca registrada: si ellos registran primero el trademark en US/EU, podría haber problemas legales.
- Confusión de usuarios al googlear: el primer resultado podría ser ellos.

**Acciones recomendadas:**
1. Revisar disponibilidad de `notiva.com` (Notiva Corporation lo tiene en otro sector — confirmado).
2. Verificar trademarks en USPTO (US), EUIPO (Europa), SIC (Colombia).
3. Considerar nombre alternativo o sufijo: `Notiva AI`, `Notiva.work`, `Notiva Meetings`.

### 🟠 ALERTA MEDIA — Klarity (YC S18)

Document AI / process intelligence. Capta procesos vía entrevistas IA y los mapea. Solapamiento parcial con Notiva en "captura de conocimiento operacional", pero el **enfoque es transformación empresarial completa**, no actas de reuniones específicas. Riesgo de naming muy menor (sólo si un nombre alternativo de Notiva fuera "Klaria/Klarity").

### 🟡 ALERTA MEDIA — Sintra (`sintra.ai`), Syntia (`syntia.online`), SinteX (`sintex-ai.com`)

Plataformas de AI assistants/automation. **Fonéticamente muy similares a "Sintia"** (variante que se barajó). Ninguna compite en actas, pero la **confusión auditiva en demos comerciales sería alta**. Si Notiva pivotea a "Sintia", desaconsejo: 3 marcas activas en sector AI.

### 🟢 RIESGO BAJO — colisiones de otros sectores

| Marca | Sector real | ¿Confusión real? |
|---|---|---|
| Klaria Pharma (SE) | Películas farmacéuticas transdérmicas | Cero. Sectores radicalmente distintos. |
| Klara HR (FR) | Sin conexión (sitio caído al check) | Probablemente bajo |
| Convoca (BR) | Agencia digital Brasil (sitio caído) | Bajo |
| Convolo / Brightcall.ai | Sales dialer / contact center IA | Bajo, pero comparten "voz/IA" |
| JuntoAI (IE) | Networking profesional con IA | Cero |
| Juna.ai (DE) | Industrial AI (Kleiner Perkins) | Cero, pero **bien fondeada** y crecerá |

---

## 💀 Dominios muertos / inaccesibles

- `getminuta.online` → 404
- `minuta.app` → 530 (server error, posiblemente abandonado)
- `www.ltimindtree.com/convoke` → 404 (Convoke fue retirado de LTIMindtree)

Todos pueden ser **buenos candidatos para que Notiva los reclame en SEO** o incluso compre los dominios si están a la venta.

---

## 📋 Roadmap recomendado de gaps — orden de prioridad

### Tier 1 — Si entras a Tier 1, ganas paridad con Convo/Acta

1. **Bot que se une a Zoom/Meet/Teams** vía calendar invite. Hoy dependes 100% de Fireflies como middleware. Tener tu propio bot = control + sin dependencia + datos en tu nube.
2. **Calendar integration** (Google Calendar, Outlook, Microsoft Graph). Auto-detecta reuniones futuras y sincroniza.
3. **Apps móviles** (iOS + Android). Es la entrada principal de MyMinutes (750k users) y Minuta. Sin móvil pierdes el segmento "field/sales".

### Tier 2 — Diferenciación

4. **"Ask Notiva"** — chat con RAG sobre el archivo histórico de actas. Hoy el dashboard solo lista; con RAG el ejecutivo pregunta "qué decidimos sobre X" y responde citando reuniones. Es el feature que más impresiona en demos.
5. **Real-time suggestions durante la llamada** (Convo). Requiere bot propio (Tier 1.1) primero.
6. **Multi-idioma 50+** vía Whisper-large-v3 + traducción opcional al español del summary.
7. **Outputs role-específicos automáticos** (Acta): selector "Esta reunión es de tipo: comercial / producto / RRHH" → genera artefactos distintos (deal brief, PRD, evaluation report).

### Tier 3 — Madurez enterprise

8. **SSO / SAML / OIDC** para enterprise.
9. **SOC 2 / ISO 27001** (MyMinutes lo destaca).
10. **Versionado de actas** + comentarios en línea + permisos granulares (workspace collaboration).
11. **Notion / Confluence / Google Docs** como destinos adicionales.
12. **Slack / Teams integration** (post de resúmenes a un canal).
13. **CRM integration** (HubSpot, Salesforce, Pipedrive) — el resumen va al deal correspondiente.
14. **API pública** para integraciones custom.
15. **White-label / multi-tenant** si vas B2B2C.

### Tier 4 — Analytics y coaching

16. **Meeting quality scoring** (Convo): claridad, listening, distribución de tiempo de palabra.
17. **Dashboard ejecutivo de ROI**: tiempo invertido por persona, % auto-dispatch, tiempo ahorrado.
18. **Alertas push** (Slack/email/SMS) cuando una tarea está vencida.
19. **Reuniones recurrentes** con histórico ligado (qué se decidió la semana pasada vs ésta).

### Tier 5 — Quick wins (1-2 días cada uno)

20. **Auto-scheduling del next meeting** detectado por IA (Convo). Genera propuesta de Google Calendar.
21. **YouTube link import** (Minuta, MyMinutes).
22. **Selector de modelo LLM** por sesión (Convo).

---

## Conclusión ejecutiva

**Notiva HOY es competitiva en:**
- Profundidad de **dispatch** a herramientas de proyecto (Trello/Jira/ClickUp/Azure) — nadie del peer set tiene los 4.
- **Auto-Dispatch parametrizable por proyecto** — único en el set.
- **Reportes ejecutivos PDF** — único en el set.
- **Webhook Fireflies + curación humana opcional** — flujo más enterprise que las "appcitas" de transcripción móvil.

**Notiva pierde claramente en:**
- **Sin bot propio** (depende 100% de Fireflies).
- **Sin apps móviles**.
- **Sin RAG/chat** sobre el historial.
- **Sin real-time** durante la llamada.

**Decisión estratégica recomendada:**

Notiva está bien posicionada para **enterprise con flujos formales** (proyectos definidos, integraciones existentes, dispatcher a herramientas internas). El segmento de **freelancers / sales / coaching** ya está saturado con Convo, MyMinutes, Otter, Fireflies puro. **Doble tu apuesta enterprise** (Tier 3) antes que perseguir Tier 2 mobile-first donde llegas tarde.

**Sobre el nombre "Notiva":** alta probabilidad de conflicto con `notiva.framer.ai`. Recomiendo decisión de marca antes de invertir en SEO/marketing.
