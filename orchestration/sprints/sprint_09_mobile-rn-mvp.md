# Sprint 09 — Apps móviles (React Native) — MVP

> Auto-generado desde `docs/roadmap.md`. Editar este archivo es OK; el
> regenerador no lo sobrescribe si ya existe.

**Slug:** `mobile-rn-mvp`
**Estado:** pendiente

---

## Extracto del roadmap

### Sprint 9 — Apps móviles MVP (semanas 19-21, 3 semanas)

**Stack:** React Native + Expo + TypeScript. Compartimos types/clients con el frontend Angular vía un paquete `@notiva/sdk`.

**MVP scope (no full feature parity):**
- [ ] Login (con SSO si está disponible).
- [ ] Tab "Sesiones" — lista + filtros básicos.
- [ ] Tab "Pendientes" — tareas asignadas a mí.
- [ ] Detalle de sesión: ver, editar, marcar tareas, comentar.
- [ ] Push notifications (Firebase) para tareas vencidas o nuevas asignaciones.
- [ ] **Sin** transcripción on-device (todo el flujo sigue siendo backend).
- [ ] Compartir acta como PDF nativo (`expo-sharing`).

**Out of scope MVP (v2 mobile):**
- Curación completa (botones IA, regenerar, etc.) — solo lectura en mobile.
- Subida de audio desde móvil — agendar para v2.
- Charts del dashboard.

**Stores:**
- [ ] Apple Developer Account ($99/año).
- [ ] Google Play Developer Account ($25 one-time).
- [ ] Screenshots, copy de la store, política de privacidad.

**Métricas:** 100 descargas en el primer mes; DAU/MAU tracking.

---

---

## Plan técnico (a llenar por el rol architect)

> El rol architect debe escribir aquí el plan detallado en `runs/<RUN_ID>/agents/09_mobile-rn-mvp/architect.output.json`
> y opcionalmente sincronizar un resumen en esta sección.

(pendiente)

---

## Worktree y rama

- Worktree: `.worktrees/sprint-09_mobile-rn-mvp/`
- Rama:     `auto/sprint-09_mobile-rn-mvp`

## Quality gates aplicables

Ver `orchestration/config/quality_gates.yaml`. Específicos para este sprint:

(a definir por architect según features que toca)
