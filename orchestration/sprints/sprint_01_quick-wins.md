# Sprint 01 — Quick wins (selector LLM, YouTube, multi-idioma, .ics)

> Auto-generado desde `docs/roadmap.md`. Editar este archivo es OK; el
> regenerador no lo sobrescribe si ya existe.

**Slug:** `quick-wins`
**Estado:** pendiente

---

## Extracto del roadmap

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

---

## Plan técnico (a llenar por el rol architect)

> El rol architect debe escribir aquí el plan detallado en `runs/<RUN_ID>/agents/01_quick-wins/architect.output.json`
> y opcionalmente sincronizar un resumen en esta sección.

(pendiente)

---

## Worktree y rama

- Worktree: `.worktrees/sprint-01_quick-wins/`
- Rama:     `auto/sprint-01_quick-wins`

## Quality gates aplicables

Ver `orchestration/config/quality_gates.yaml`. Específicos para este sprint:

(a definir por architect según features que toca)
