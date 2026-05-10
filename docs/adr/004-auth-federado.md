# ADR-004 — Auth federado para Sprint 08 (SSO/SAML)

**Estado:** Pendiente — REQUIRES_HUMAN_REVIEW
**Sprint:** 08 SSO/SAML/Audit/GDPR

## Contexto

Necesitamos SSO (SAML, OIDC) para clientes enterprise. Tres caminos:

1. **Supabase Auth** — gratis hasta 50k MAU, SAML en plan Pro ($25/mes), OIDC.
2. **Auth0** — Essentials $30/mes hasta 1k MAU.
3. **Roll-our-own SAML** con `python-saml`.

## Recomendación

**Supabase Auth** porque ya estamos en Supabase y el migration cost es bajo.
Pero: el founder debe confirmar antes de migrar el sistema actual de JWT
propio (`auth_utils.py`) — implica re-login de todos los usuarios y
reescribir el guard `get_current_user`.

## Cuándo decidir

Antes de arrancar Sprint 08. Si decidimos pivotar a Auth0, el cambio
arquitectónico es mayor.

## Si elegimos Supabase Auth

- Bloquear Sprint 8 dos días para configurar Supabase Auth con SAML.
- Migrar User table a usar `supabase.users` referenciado.
- Actualizar `routers/auth.py` para verificar tokens de Supabase.
