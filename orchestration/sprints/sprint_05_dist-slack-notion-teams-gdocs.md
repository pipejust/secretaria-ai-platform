# Sprint 05 — Distribución: Slack + Notion + Teams + Google Docs

> Auto-generado desde `docs/roadmap.md`. Editar este archivo es OK; el
> regenerador no lo sobrescribe si ya existe.

**Slug:** `dist-slack-notion-teams-gdocs`
**Estado:** pendiente

---

## Extracto del roadmap

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

---

## Plan técnico (a llenar por el rol architect)

> El rol architect debe escribir aquí el plan detallado en `runs/<RUN_ID>/agents/05_dist-slack-notion-teams-gdocs/architect.output.json`
> y opcionalmente sincronizar un resumen en esta sección.

(pendiente)

---

## Worktree y rama

- Worktree: `.worktrees/sprint-05_dist-slack-notion-teams-gdocs/`
- Rama:     `auto/sprint-05_dist-slack-notion-teams-gdocs`

## Quality gates aplicables

Ver `orchestration/config/quality_gates.yaml`. Específicos para este sprint:

(a definir por architect según features que toca)
