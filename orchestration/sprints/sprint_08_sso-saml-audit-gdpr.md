# Sprint 08 — SSO/SAML + auditoría + GDPR

> Auto-generado desde `docs/roadmap.md`. Editar este archivo es OK; el
> regenerador no lo sobrescribe si ya existe.

**Slug:** `sso-saml-audit-gdpr`
**Estado:** pendiente

---

## Extracto del roadmap

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

---

## Plan técnico (a llenar por el rol architect)

> El rol architect debe escribir aquí el plan detallado en `runs/<RUN_ID>/agents/08_sso-saml-audit-gdpr/architect.output.json`
> y opcionalmente sincronizar un resumen en esta sección.

(pendiente)

---

## Worktree y rama

- Worktree: `.worktrees/sprint-08_sso-saml-audit-gdpr/`
- Rama:     `auto/sprint-08_sso-saml-audit-gdpr`

## Quality gates aplicables

Ver `orchestration/config/quality_gates.yaml`. Específicos para este sprint:

(a definir por architect según features que toca)
