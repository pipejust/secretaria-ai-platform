# Sprint 03 — Calendar integration (Google + Microsoft)

> Auto-generado desde `docs/roadmap.md`. Editar este archivo es OK; el
> regenerador no lo sobrescribe si ya existe.

**Slug:** `calendar-integration`
**Estado:** pendiente

---

## Extracto del roadmap

### Sprint 3 — Calendar integration (semanas 7-8)

**Entregables:**
- [ ] OAuth Google Calendar (scope `calendar.events.readonly`).
- [ ] OAuth Microsoft Graph (scope `Calendars.Read`).
- [ ] Tabla `calendar_account` (user_id, provider, access_token cifrado, refresh_token cifrado, expires_at).
- [ ] Cron de sync: cada 30 min trae próximas 7 días de eventos del usuario.
- [ ] Tabla `calendar_event` ligada a `meeting_session` cuando matchee título/hora.
- [ ] UI en `/admin/dashboard`: panel "Próximas reuniones" + "Sin acta aún".
- [ ] Webhook de Fireflies: si llega un transcript_id que matchea un calendar_event por título+hora, autoasigna `project_id` y `attendees`.

**Métricas:** ≥80% de calendar_events de los últimos 7 días terminan con MeetingSession asociada.

---

---

## Plan técnico (a llenar por el rol architect)

> El rol architect debe escribir aquí el plan detallado en `runs/<RUN_ID>/agents/03_calendar-integration/architect.output.json`
> y opcionalmente sincronizar un resumen en esta sección.

(pendiente)

---

## Worktree y rama

- Worktree: `.worktrees/sprint-03_calendar-integration/`
- Rama:     `auto/sprint-03_calendar-integration`

## Quality gates aplicables

Ver `orchestration/config/quality_gates.yaml`. Específicos para este sprint:

(a definir por architect según features que toca)
