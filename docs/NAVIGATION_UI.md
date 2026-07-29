# Arquitectura de Navegación y UI

> Documento técnico para **replicar la estructura de navegación e
> interfaz** en otra plataforma. Validado contra el código real
> (Angular 21 standalone + FastAPI). Incluye menú, header, footer,
> búsqueda, responsive, dashboard, reportes, notificaciones, perfil,
> marca, configuración, mensajes de landing, filtros y calendario.

---

## 1. Estructura general (shell)

**Frontend**: Angular 21, componentes **standalone**, lazy-loading por
ruta (`loadComponent`). **i18n** con ngx-translate (es/ca/en).

Dos "mundos" de rutas:

| Zona | Layout | Guard |
|------|--------|-------|
| **Público** (`/`, `/login`, `/help`, `/privacy`, `/terms`, landing) | Sin shell | — |
| **App** (`/admin/*`) | `AdminLayoutComponent` (shell) | `authGuard` |
| **Multi-tenant login** (`/t/:slug/login`) | Login con branding del tenant | — |

El shell (`AdminLayoutComponent`) envuelve TODAS las páginas internas
con: **sidebar (menú)** + **topbar (header)** + `<router-outlet>`.

```
/admin  → AdminLayoutComponent (authGuard)
  ├── dashboard          (DashboardComponent)
  ├── meetings           (lista de sesiones)
  ├── projects, projects/:id
  ├── templates
  ├── ask                (Ask IA)
  ├── calendar
  ├── pendientes | tareas (ActionItems)
  ├── reportes
  ├── curation/:id       (curación de sesión)
  ├── outputs/:id        (entregables por rol)
  ├── users, roles       (admin)
  ├── settings, branding (admin)
  ├── super/tenants, landing-cms, landing-cms/mensajes (super-admin)
  └── profile
```

Ruta comodín `**` → redirige a `/`. `/t/:slug` → login del tenant.

---

## 2. Menú (sidebar)

`admin-layout.component.html` → `<aside class="sidebar">`.

- **Agrupado por secciones** con títulos: "Operaciones" y "Administración".
- Cada item: `<a routerLink="…" routerLinkActive="active">` + ícono SVG +
  texto (el texto se oculta cuando el sidebar está colapsado).
- **Colapsable**: `[class.collapsed]="isCollapsed"` — botón inferior
  `toggleSidebar()`. En colapsado solo se ven los íconos.
- **Header del sidebar**: logo del tenant (branding), enlace a dashboard.
- **Footer del sidebar**: bloque de usuario + toggle de colapso.

### Visibilidad por permisos (crítico)
Cada item se muestra según **permisos de rol**, vía `PermissionsService`:

```html
<li *ngIf="canSee('roles')"> … </li>
<li *ngIf="isSuperAdmin" class="super-only"> … </li>   <!-- tenants, landing-cms -->
```

- `canSee(module)` → `PermissionsService.canSeeModule(module)`.
- `can(module, action)` → view/create/edit/delete/manage/export.
- Items de **super-admin** (empresas, landing CMS, mensajes) solo
  visibles con `isSuperAdmin`.

La sección "Administración" completa se oculta si el usuario no ve
ninguno de sus módulos:
```html
*ngIf="canSee('usuarios') || canSee('roles') || isSuperAdmin
       || canSee('marca') || canSee('configuracion')"
```

---

## 3. Header (topbar)

`<header class="topbar">` con 3 zonas:

| Zona | Contenido |
|------|-----------|
| `topbar-left` | Botón hamburguesa (móvil) `toggleMobileMenu()` |
| `topbar-center` | CTA "Nueva reunión" + **buscador global** |
| `topbar-right` | Campana de **notificaciones** (badge) + dropdown de **perfil** + selector de idioma |

---

## 4. Búsqueda global

Frontend (`admin-layout` + `SearchService`) → Backend `GET /api/search`.

- Input en el topbar con **debounce** (`Subject` + rxjs) — dispara a
  partir de 2 caracteres.
- Devuelve resultados **agrupados** (`SearchGroup[]`): sesiones,
  proyectos, tareas, contactos.
- Backend (`routers/search.py`): busca en `MeetingSession`, `Project`,
  `ActionItem`, `ProjectContact` con **ILIKE case-insensitive**, todo
  **filtrado por `tenant_id`**.
- Click en un resultado → navega al deep-link; el dropdown se cierra al
  hacer click fuera.

> Nota: el buscador **de reuniones** (dentro de `/admin/meetings`) es
> filtrado local sobre la página cargada — ver §11.

---

## 5. Responsive

Todo mobile-first con breakpoints en `admin-layout.component.css`:

| Breakpoint | Comportamiento |
|------------|----------------|
| `max-width: 768px` / `720px` | Sidebar pasa a **off-canvas** (`mobile-open`), aparece botón hamburguesa y **overlay** (`mobile-overlay`) que cierra al tocar fuera |
| `min-width: 1101px` | Sidebar fijo expandido |

- `isMobileOpen` controla el drawer; `closeMobileMenu()` se llama al
  navegar (cualquier `routerLink`) y al tocar el overlay.
- `isCollapsed` (desktop) vs `isMobileOpen` (móvil) son estados
  independientes.

---

## 6. Zona del Dashboard

Ruta `/admin/dashboard` → `DashboardComponent`. Resumen ejecutivo:
métricas/KPIs, actividad reciente, riesgos y tareas prioritarias.
Backend de métricas en `routers/analytics.py`:
- `GET /api/analytics/roi` — minutos de reunión estimados, tareas
  completadas/pendientes, tasa de completitud, top owners.
- `GET /api/analytics/recurring` — series de reuniones recurrentes.
- **Todas filtran `tenant_id`** (aislamiento, ver doc MULTITENANCY §9).

---

## 7. Reportes

Ruta `/admin/reportes` → `ReportesComponent`. Backend `routers/reports.py`:
- `GET /api/reports/data` — dataset JSON (para render en pantalla).
- `GET /api/reports/pdf` — reporte en PDF.
- `GET /api/reports/excel` — export Excel.

`_build_report(db, tenant_id, …)` — ventana temporal opcional,
clasificación de proyectos por avance, timeline de decisiones, top owner
por proyecto, % de progreso. **Todo scopeado a `tenant_id`** (sesiones,
proyectos, action items, con defensa en profundidad).

---

## 8. Notificaciones

Frontend: campana en topbar + `notif-panel` (dropdown). `NotificationService`.
Backend `routers/notifications.py`:
- `GET /api/notifications/` — lista.
- `GET /api/notifications/unread_count` — badge (99+).
- `POST /api/notifications/{id}/read` — marca leída.
- `POST /api/notifications/mark_all_read` — marca todas.

Cada notificación puede traer `link_to` (deep-link): al hacer click se
marca leída y navega. Si falla el mark-read, igual navega (fail-safe).

---

## 9. Zona de Perfil + Edición

Ruta `/admin/profile` → `ProfileComponent`. Backend en `routers/auth.py`:
- `GET /api/auth/me` — datos del perfil + preferencias (`_serialize_user_profile`).
- `PUT /api/auth/me` — **editar perfil** (nombre, teléfono, depto, cargo, etc.).
- `PUT /api/auth/me/notifications` — preferencias de notificación.
- `POST /api/auth/me/change-password` — cambio de contraseña.

**GDPR** (`routers/me.py`, prefix `/api/me`):
- `GET /api/me/export` — ZIP con los datos del usuario (portabilidad).
- `DELETE /api/me/account` — borrado de cuenta (purga a 30 días).

El dropdown de perfil del topbar (`toggleProfileDropdown`) da acceso
rápido a perfil, cambio de idioma y logout.

---

## 10. Zona de Marca (branding) y Configuración

### Marca — `/admin/branding` (`BrandingSettingsComponent`)
Backend `GET/PUT /api/branding` (resuelto por `X-Tenant-Slug`/`?tenant=`):
- Logo (claro/oscuro), colores, nombre visible, idioma por defecto.
- Guardado en `tenant.branding_json`. Alimenta el logo del sidebar y el
  login con branding por tenant.

### Configuración — `/admin/settings` (`SettingsComponent`)
- Credenciales de integraciones (Fireflies, Resend, Trello, Jira,
  ClickUp, Azure, Slack, Notion, Teams, GoogleDocs, HubSpot…).
- Modelo **per-tenant + per-user** con switches `share_integrations` /
  `share_routings` (el owner decide si comparte sus credenciales).
- Persistido en `IntegrationSetting.config_json`.

---

## 11. Mensajes desde la Landing (contacto)

Formulario público de la landing → backend, y bandeja admin:

- **Público**: `POST /api/public/landing/contact` — envía el formulario
  de contacto (nombre, email, mensaje) al email administrativo del
  tenant y **persiste el mensaje**.
- **Admin** (`/admin/landing-cms/mensajes` → `LandingMessagesComponent`,
  solo super-admin):
  - `GET /api/landing/messages` — lista mensajes recibidos.
  - `PATCH /api/landing/messages/{id}` — marcar leído/estado.
  - `DELETE /api/landing/messages/{id}` — borrar.
- **CMS de landing**: `GET/PUT /api/landing/` edita el contenido del
  landing público (secciones, features, pricing, testimonios) guardado
  en `tenant.landing_content_json`.

---

## 12. Filtros y búsquedas (en listas)

Ejemplo canónico: lista de reuniones (`meetings-list.component`):
- **Sub-tabs por estado**: todas / analizadas / borradores / archivadas.
- **Filtro por origen**: manual (upload) vs web (webhook).
- **Filtro por fecha**: preset o rango custom (client-side sobre el lote
  cargado).
- **Buscador local**: filtra por título/proyecto/fecha, **case-insensitive**
  (`toLowerCase()` en ambos lados).
- **Orden** por columna (título/fecha/estado) asc/desc.
- **Paginación** server-side (el backend `GET /api/sessions` filtra
  `tenant_id`, estado, project_id, y `search` con ILIKE).

Patrón reusable: filtros server-side (tenant/estado/proyecto/paginación)
+ refinamiento client-side (búsqueda de texto, origen, fecha, orden).

---

## 13. Calendario

Ruta `/admin/calendar` → `CalendarComponent`. Backend `routers/calendar.py`:
- OAuth con **Google** y **Microsoft**:
  `GET /api/calendar/google/auth_url`, `/microsoft/auth_url`, callbacks.
- `GET /api/calendar/accounts` — cuentas conectadas.
- `POST /api/calendar/sync` — sincroniza eventos.
- `GET /api/calendar/upcoming` — próximos eventos.

Permite conectar calendarios externos y ver/sincronizar reuniones.

---

## 14. Servicios frontend clave (para reusar)

| Servicio | Rol |
|----------|-----|
| `AuthService` | Token JWT, headers, sesión |
| `PermissionsService` | `canSeeModule` / `can(module, action)` → gating de menú y acciones |
| `SearchService` | Búsqueda global agrupada |
| `NotificationService` | Notificaciones + unread count |
| `BrandingService` | Branding del tenant (logo, colores, idioma) |
| `LanguageService` | Idioma activo + locale del date formatter |
| `TitleService` | Título de página por ruta (`data.titleKey`) |
| `ToastService` | Feedback (guardado/error) |

---

## 15. Checklist para replicar la navegación

1. **Shell con layout**: un componente contenedor (sidebar + topbar +
   `<router-outlet>`) para toda la zona autenticada; guard de auth.
2. **Rutas lazy** por feature (`loadComponent`) + ruta comodín.
3. **Menú gated por permisos**: `*ngIf="canSee(module)"` /
   `*ngIf="isSuperAdmin"`. Nunca mostrar lo que el rol no puede tocar.
4. **Topbar**: hamburguesa (móvil), CTA, buscador global con debounce,
   notificaciones con badge, dropdown de perfil + idioma.
5. **Responsive**: sidebar off-canvas + overlay en móvil, colapsable en
   desktop; cerrar drawer al navegar.
6. **Buscador global** server-side agrupado, filtrado por tenant.
7. **Notificaciones** con `link_to` (deep-link) + mark-read.
8. **Perfil**: GET/PUT + preferencias de notificación + cambio de
   contraseña + GDPR export/delete.
9. **Marca + configuración** por tenant (branding_json, integraciones).
10. **Landing pública + bandeja de contacto** persistida y gestionable
    por admin.
11. **Listas** con patrón filtros server-side + refinamiento client-side.
12. **i18n** en toda etiqueta (`| translate`).

---

## 16. Archivos de referencia

| Área | Archivo |
|------|---------|
| Rutas | `frontend/src/app/app.routes.ts` |
| Shell (menú/header/footer/responsive) | `frontend/src/app/components/admin-layout/*` |
| Búsqueda | `services/search.service.ts` · `backend/routers/search.py` |
| Notificaciones | `services/notification.service.ts` · `backend/routers/notifications.py` |
| Perfil | `components/profile/*` · `backend/routers/auth.py` (`/me`) · `backend/routers/me.py` |
| Marca | `components/branding-settings/*` · `backend/routers/branding.py` |
| Configuración | `components/settings/*` · `backend/routers/settings*.py` |
| Reportes | `components/reportes/*` · `backend/routers/reports.py` |
| Dashboard | `components/dashboard/*` · `backend/routers/analytics.py` |
| Landing + mensajes | `components/landing*/*` · `backend/routers/landing.py` |
| Calendario | `components/calendar/*` · `backend/routers/calendar.py` |
| Permisos | `services/permissions.service.ts` |
