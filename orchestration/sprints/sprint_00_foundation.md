# Sprint 00 — Foundation técnica (pgvector, embeddings, tests, CI)

> Auto-generado desde `docs/roadmap.md`. Editar este archivo es OK; el
> regenerador no lo sobrescribe si ya existe.

**Slug:** `foundation`
**Estado:** pendiente

---

## Extracto del roadmap

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

---

## Plan técnico (a llenar por el rol architect)

> El rol architect debe escribir aquí el plan detallado en `runs/<RUN_ID>/agents/00_foundation/architect.output.json`
> y opcionalmente sincronizar un resumen en esta sección.

(pendiente)

---

## Worktree y rama

- Worktree: `.worktrees/sprint-00_foundation/`
- Rama:     `auto/sprint-00_foundation`

## Quality gates aplicables

Ver `orchestration/config/quality_gates.yaml`. Específicos para este sprint:

(a definir por architect según features que toca)
