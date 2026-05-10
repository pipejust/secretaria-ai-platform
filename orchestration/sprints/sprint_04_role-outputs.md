# Sprint 04 — Outputs role-específicos (PRD, deal brief, status)

> Auto-generado desde `docs/roadmap.md`. Editar este archivo es OK; el
> regenerador no lo sobrescribe si ya existe.

**Slug:** `role-outputs`
**Estado:** pendiente

---

## Extracto del roadmap

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

---

## Plan técnico (a llenar por el rol architect)

> El rol architect debe escribir aquí el plan detallado en `runs/<RUN_ID>/agents/04_role-outputs/architect.output.json`
> y opcionalmente sincronizar un resumen en esta sección.

(pendiente)

---

## Worktree y rama

- Worktree: `.worktrees/sprint-04_role-outputs/`
- Rama:     `auto/sprint-04_role-outputs`

## Quality gates aplicables

Ver `orchestration/config/quality_gates.yaml`. Específicos para este sprint:

(a definir por architect según features que toca)
