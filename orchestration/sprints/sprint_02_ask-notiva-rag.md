# Sprint 02 — Ask Notiva — RAG sobre histórico de actas

> Auto-generado desde `docs/roadmap.md`. Editar este archivo es OK; el
> regenerador no lo sobrescribe si ya existe.

**Slug:** `ask-notiva-rag`
**Estado:** pendiente

---

## Extracto del roadmap

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

---

## Plan técnico (a llenar por el rol architect)

> El rol architect debe escribir aquí el plan detallado en `runs/<RUN_ID>/agents/02_ask-notiva-rag/architect.output.json`
> y opcionalmente sincronizar un resumen en esta sección.

(pendiente)

---

## Worktree y rama

- Worktree: `.worktrees/sprint-02_ask-notiva-rag/`
- Rama:     `auto/sprint-02_ask-notiva-rag`

## Quality gates aplicables

Ver `orchestration/config/quality_gates.yaml`. Específicos para este sprint:

(a definir por architect según features que toca)
