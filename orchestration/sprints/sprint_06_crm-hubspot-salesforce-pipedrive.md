# Sprint 06 — CRM integration (HubSpot, Salesforce, Pipedrive)

> Auto-generado desde `docs/roadmap.md`. Editar este archivo es OK; el
> regenerador no lo sobrescribe si ya existe.

**Slug:** `crm-hubspot-salesforce-pipedrive`
**Estado:** pendiente

---

## Extracto del roadmap

### Sprint 6 — CRM integration (semanas 13-14)

**Caso de uso:** reunión con cliente → buscar el deal correspondiente en
HubSpot/Salesforce/Pipedrive → adjuntar el acta como nota.

**Entregables:**
- [ ] `services/integrations/hubspot.py`, `salesforce.py`, `pipedrive.py`.
- [ ] Cada uno expone:
  - `find_deal_by_email(email)` → busca contactos/deals por email del attendee.
  - `attach_note(deal_id, html_content)` → agrega la nota.
- [ ] Pipeline post-curación: si la sesión tiene CRM routing activo, intenta matchear deal por email del primer attendee externo y crear la nota.
- [ ] UI: indicador "✓ Sincronizado con HubSpot deal #123" en la curación.

**Métricas:** % de sesiones que se sincronizan automáticamente con un deal vs requieren matching manual.

---

---

## Plan técnico (a llenar por el rol architect)

> El rol architect debe escribir aquí el plan detallado en `runs/<RUN_ID>/agents/06_crm-hubspot-salesforce-pipedrive/architect.output.json`
> y opcionalmente sincronizar un resumen en esta sección.

(pendiente)

---

## Worktree y rama

- Worktree: `.worktrees/sprint-06_crm-hubspot-salesforce-pipedrive/`
- Rama:     `auto/sprint-06_crm-hubspot-salesforce-pipedrive`

## Quality gates aplicables

Ver `orchestration/config/quality_gates.yaml`. Específicos para este sprint:

(a definir por architect según features que toca)
