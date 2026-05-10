# Sprint 10 — Analytics + ROI + alertas push + recurrentes

> Auto-generado desde `docs/roadmap.md`. Editar este archivo es OK; el
> regenerador no lo sobrescribe si ya existe.

**Slug:** `analytics-roi-alerts-recurrent`
**Estado:** pendiente

---

## Extracto del roadmap

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

---

## Plan técnico (a llenar por el rol architect)

> El rol architect debe escribir aquí el plan detallado en `runs/<RUN_ID>/agents/10_analytics-roi-alerts-recurrent/architect.output.json`
> y opcionalmente sincronizar un resumen en esta sección.

(pendiente)

---

## Worktree y rama

- Worktree: `.worktrees/sprint-10_analytics-roi-alerts-recurrent/`
- Rama:     `auto/sprint-10_analytics-roi-alerts-recurrent`

## Quality gates aplicables

Ver `orchestration/config/quality_gates.yaml`. Específicos para este sprint:

(a definir por architect según features que toca)
