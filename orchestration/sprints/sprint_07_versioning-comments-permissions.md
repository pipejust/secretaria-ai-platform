# Sprint 07 — Versionado + comentarios + permisos granulares

> Auto-generado desde `docs/roadmap.md`. Editar este archivo es OK; el
> regenerador no lo sobrescribe si ya existe.

**Slug:** `versioning-comments-permissions`
**Estado:** pendiente

---

## Extracto del roadmap

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

---

## Plan técnico (a llenar por el rol architect)

> El rol architect debe escribir aquí el plan detallado en `runs/<RUN_ID>/agents/07_versioning-comments-permissions/architect.output.json`
> y opcionalmente sincronizar un resumen en esta sección.

(pendiente)

---

## Worktree y rama

- Worktree: `.worktrees/sprint-07_versioning-comments-permissions/`
- Rama:     `auto/sprint-07_versioning-comments-permissions`

## Quality gates aplicables

Ver `orchestration/config/quality_gates.yaml`. Específicos para este sprint:

(a definir por architect según features que toca)
