# Notiva Mobile (Sprint 09)

App React Native + Expo. **NO inicializada todavía** — el architect del
Sprint 09 marca este sprint con `requires_human_review: true` para los
pasos de Apple Developer Account y Google Play submission.

## Stack acordado (ADR-005)

- React Native 0.74+ con Expo SDK 51+
- TypeScript estricto
- Expo Router (file-based)
- React Query para server state
- Zustand para client state
- `expo-secure-store` para token JWT
- Firebase Cloud Messaging para push (`expo-notifications`)
- `expo-sharing` para PDF nativo

## Inicialización (cuando arranque Sprint 09)

```bash
cd mobile
npx create-expo-app@latest . --template tabs
npm install @tanstack/react-query zustand expo-secure-store expo-sharing expo-notifications
```

## Scope MVP (Sprint 09)

- [ ] Login (email + password; SSO si está disponible)
- [ ] Tab Sesiones — lista + filtros básicos
- [ ] Tab Pendientes — tareas asignadas a mí
- [ ] Detalle sesión — ver, marcar tareas, comentar
- [ ] Push notifications (Firebase) cuando tarea vencida
- [ ] Compartir PDF nativo

## Out-of-scope MVP

- Curación completa (botones IA) — solo lectura en mobile
- Subida de audio desde móvil — agendar v2
- Charts del dashboard

## Cuentas requeridas

- Apple Developer ($99/año)
- Google Play Console ($25 one-time)
- Firebase project (free tier)
