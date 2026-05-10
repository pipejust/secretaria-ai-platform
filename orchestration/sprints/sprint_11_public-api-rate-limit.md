# Sprint 11 — API pública + rate limiting + buffer/polish

> Auto-generado desde `docs/roadmap.md`. Editar este archivo es OK; el
> regenerador no lo sobrescribe si ya existe.

**Slug:** `public-api-rate-limit`
**Estado:** pendiente

---

## Extracto del roadmap

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

---

## Plan técnico (a llenar por el rol architect)

> El rol architect debe escribir aquí el plan detallado en `runs/<RUN_ID>/agents/11_public-api-rate-limit/architect.output.json`
> y opcionalmente sincronizar un resumen en esta sección.

(pendiente)

---

## Worktree y rama

- Worktree: `.worktrees/sprint-11_public-api-rate-limit/`
- Rama:     `auto/sprint-11_public-api-rate-limit`

## Quality gates aplicables

Ver `orchestration/config/quality_gates.yaml`. Específicos para este sprint:

(a definir por architect según features que toca)
