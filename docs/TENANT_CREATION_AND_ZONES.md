# Creación de Empresas (Tenants), Zona de Marca y Zona de Empresas

> Documento técnico para **replicar exactamente** el proceso de alta de
> empresas, el envío de correo, la configuración interna por empresa y
> el aislamiento de datos. Incluye el **flujo gráfico** (campos, tablas,
> botones, vistas) de las dos zonas: **Marca** y **Empresas/Tenants**.
> Validado contra el código real.

---

## ⚠️ Aclaración clave sobre "separado por base de datos"

**NO hay una base de datos por empresa.** Es **una sola base
PostgreSQL** con **aislamiento lógico por fila** (`tenant_id`). Cada
tabla de negocio lleva `tenant_id` y **toda** consulta lo filtra. El
efecto para el usuario es idéntico a "bases separadas" (nadie ve datos
de otra empresa), pero la operación/costo es mucho menor: no se carga lo
de todos porque **cada query trae solo las filas de su `tenant_id`**.

> Si se requiriera aislamiento físico real, la alternativa sería
> **schema-per-tenant** o **database-per-tenant**, pero este sistema usa
> **row-level (shared schema)** — ver §6.

---

## 1. Actores y niveles

| Actor | Puede |
|-------|-------|
| **Super-admin de plataforma** (`is_superadmin=True`) | Crear/editar/borrar **empresas**, listar todas, reenviar invitación |
| **Admin de empresa** (rol `admin`, dentro de un tenant) | Gestionar usuarios, marca, configuración, datos de **su** empresa |
| **Usuario** (validator/…) | Operar dentro de su empresa según permisos |

Solo el **super-admin** entra a la zona de Empresas (`/admin/super/tenants`).
Todo admin de empresa entra a Marca (`/admin/branding`) y Configuración
(`/admin/settings`).

---

## 2. Flujo completo de creación de una empresa (tenant)

### 2.1 Diagrama del flujo

```
Super-admin  →  [Zona Empresas]  →  botón "Crear empresa"  →  Modal (form)
      │
      ▼
POST /api/tenants/   (require_superadmin)
      │
      ├─ 1. Validar slug (regex, minúsculas/números/guiones, 2–32)
      ├─ 2. Verificar slug único + dominio único          → 409 si choca
      ├─ 3. Crear Tenant (branding_json inicial: nombre, web, 3 colores,
      │       logos opcionales en data URL, default_language)
      ├─ 4. Provisionar rol 'admin' (si no existe)
      ├─ 5. Resolver password admin:
      │       · si viene admin_password (≥8 chars) → usarla
      │       · si admin_must_change_password=True y no viene → autogenerar
      ├─ 6. Crear User admin (tenant_id del nuevo tenant, hash bcrypt,
      │       must_change_password)
      ├─ 7. SEED de IntegrationSettings vacíos (smtp, fireflies, trello,
      │       jira, clickup, azure_devops, autoCuration) → /settings listo
      ├─ 8. commit
      └─ 9. background_task → email de bienvenida al admin
      ▼
Respuesta: TenantOut + temporary_password (si se autogeneró)
```

### 2.2 Cómo se crea el USUARIO admin

- Se crea **junto con el tenant** (paso 6), con `tenant_id` = el del
  tenant recién creado (nunca del payload → no se puede inyectar en otra
  empresa).
- Rol `admin` (compartido entre tenants a nivel de catálogo, pero el
  usuario pertenece solo a su tenant).
- Password: explícita (≥8 chars) **o** autogenerada
  (`_generate_temporary_password`) si `admin_must_change_password=True`.
- `hashed_password` con bcrypt/passlib. Nunca se guarda en claro.
- Identidad única = **(email, tenant_id)** — el mismo email puede ser
  admin de varias empresas.

### 2.3 Cómo se envía el CORREO

- **Best-effort en background** (`BackgroundTasks`) — si el correo falla,
  la empresa **igual queda creada** (no rompe el alta).
- `_send_tenant_welcome_safe(user_id, tenant_id, temp_password)`:
  - Abre su propia sesión de BD.
  - Construye la **URL de login tenant-específica**:
    `{ADMIN_BASE_URL}/t/{slug}/login`.
  - Usa `EmailService(db, tenant_id)` → `send_welcome_email(...)` con
    nombre, rol, URL, password temporal y flag must_change.
  - **Branding del NUEVO tenant** en el correo (logo/colores subidos al
    crear). Si el SMTP del tenant no está configurado, **cae al SMTP del
    tenant `acten`** (fallback dentro de `EmailService`).
- El SMTP se configura por tenant en `IntegrationSetting('smtp')`
  (provider Resend por defecto en el seed).

### 2.4 Reenvío de invitación
`POST /api/tenants/{slug}/resend-invitation` (super-admin): genera una
**password temporal nueva**, invalida la anterior y reenvía el correo.

---

## 3. Cómo cada empresa se configura internamente

Al crearse, el tenant queda con:
- **Branding inicial** (`tenant.branding_json`): nombre, web, 3 colores,
  logos (si se subieron), idioma por defecto.
- **IntegrationSettings vacíos** sembrados → el admin entra a
  `/admin/settings` y solo llena las claves (Fireflies, Resend, Trello,
  Jira, ClickUp, Azure DevOps, auto-curación).
- El admin ajusta su marca en `/admin/branding` y da de alta usuarios en
  `/admin/users` (con validación de dominio de correo contra
  `company_website`).

Todo lo que configure vive **scopeado a su `tenant_id`** — no afecta ni
ve la config de otras empresas.

---

## 4. ZONA DE EMPRESAS / TENANTS (flujo gráfico)

Ruta `/admin/super/tenants` → `SuperTenantsComponent` (solo super-admin).

### 4.1 Vistas
- **KPIs superiores** (4 tarjetas con delta ↑/↓/=): total empresas,
  activas, usuarios, dominios.
- **Toolbar**: buscador (texto), botón "Filtros", **toggle de vista
  Lista / Grid**.
- **Barra de filtros** (colapsable): estado (activo/inactivo), dominio,
  usuarios, fecha de creación.
- **Lista/Grid** de empresas con paginación.
- **Modales**: Crear, Editar, Ver detalle, Set dominio.

### 4.2 Tabla (vista lista)
Columnas (`grid-template-columns: 60px 2fr 3fr 1fr 1fr 1fr 80px`):
| # | Empresa (logo+nombre) | Dominio/slug | Usuarios | Estado | Creado | Acciones (kebab) |

### 4.3 Botones / acciones
| Botón | Acción | Endpoint |
|-------|--------|----------|
| **Crear empresa** (CTA) | Abre modal de alta | `POST /api/tenants/` |
| **Ver detalle** (kebab) | Modal solo-lectura | `GET /api/tenants/{slug}` |
| **Editar** (kebab) | Modal de edición | `PUT /api/tenants/{slug}` |
| **Editar dominio** (kebab) | Set/actualiza dominio | `PUT /api/tenants/{slug}` |
| **Reenviar invitación** (kebab) | Nueva pass temporal + correo | `POST /api/tenants/{slug}/resend-invitation` |
| **Activar/Desactivar** (kebab) | Toggle `is_active` | `PUT /api/tenants/{slug}` |
| **Eliminar** (kebab, danger) | Borra empresa | `DELETE /api/tenants/{slug}` |

### 4.4 Campos del formulario de creación (`TenantCreate`)
| Campo | Tipo | Req. | Nota |
|-------|------|------|------|
| `slug` | string 2–32 | ✅ | regex minúsculas/números/guiones, único |
| `name` | string 2–120 | ✅ | Nombre legible |
| `domain` | string | ❌ | Dominio custom, único |
| `company_website` | url | ✅ | **valida el email de cada usuario nuevo** |
| `admin_email` | email | ✅ | Primer admin |
| `admin_full_name` | string 2–120 | ✅ | |
| `admin_password` | string ≥8 | ❌ | opcional si must_change |
| `admin_must_change_password` | bool | ❌ | autogenera pass si no viene |
| `default_language` | es/ca/en | ❌ | default 'es' |
| `primary/secondary/accent_color` | hex | ❌ | fallback paleta Acten |
| `company_tagline/email/phone/address` | string | ❌ | branding opcional |
| `logo_data_url` / `logo_dark_data_url` / `icon_data_url` / `favicon_data_url` | data URL (≤3–4 MB) | ❌ | validados por `_is_valid_data_url` |

`GET /api/tenants/` devuelve `TenantOut` (incluye `user_count`).

---

## 5. ZONA DE MARCA (flujo gráfico)

Ruta `/admin/branding` → `BrandingSettingsComponent`. Backend
`GET/PUT /api/branding` (resuelto por `X-Tenant-Slug`/`?tenant=`, o el
tenant del usuario).

### 5.1 Vistas / secciones
- **Header** con botones **Vista previa** y **Guardar**.
- **KPIs** (ej. colores activos).
- **Toolbar**: buscador, filtros (sección: identidad/logo/color;
  estado), toggle Lista/Grid.
- **Tarjetas (cards) por sección**:
  1. **Identidad**: nombre, tagline, email, **website (req.)**, teléfono,
     dirección, **idioma por defecto**.
  2. **Logo claro** (preview sobre fondo primario) — subir/reemplazar/quitar.
  3. **Logo oscuro** — variante para fondos oscuros.
  4. **Icono/monograma** y **favicon**.
  5. **Colores**: primary / secondary / accent.

### 5.2 Campos (form.*)
| Campo | Tipo | Persistencia |
|-------|------|--------------|
| `company_name` | text (120) | `branding_json.company_name` |
| `company_tagline` | text (240) | idem |
| `company_email` | email | idem |
| `company_website` | url (**req.**) | idem — valida emails de usuarios |
| `company_phone` | tel | idem |
| `company_address` | text | idem |
| `default_language` | select es/ca/en | `tenant.default_language` |
| `logo_data_url` | file → data URL | `branding_json.logo_data_url` |
| `logo_dark_data_url` | file → data URL | idem |
| `icon_data_url` / `favicon_data_url` | file → data URL | idem |
| `primary/secondary/accent_color` | color | idem |

### 5.3 Botones
| Botón | Acción |
|-------|--------|
| **Guardar** | `PUT /api/branding` (persiste `branding_json` + `default_language`) |
| **Vista previa** | Modal con el branding aplicado |
| **Subir/Reemplazar logo** | `<input type=file hidden>` → data URL |
| **Quitar logo** | limpia el campo |
| **Limpiar filtros** | resetea la toolbar |

El branding alimenta: logo del sidebar, login por tenant (`/t/{slug}`),
correos, y el favicon/título del navegador.

---

## 6. Aislamiento en base de datos (cómo NADIE ve lo de otro)

### 6.1 Modelo
- **Shared schema, row-level isolation**: 1 BD, columna `tenant_id`
  (FK + index) en cada tabla de negocio (`meetingsession`, `project`,
  `projectcontact`, `actionitem`, `embeddingchunk`, `integrationsetting`,
  `user`, `notification`…).
- Identidad de usuario = `UniqueConstraint(email, tenant_id)`.

### 6.2 Enforcement (la regla de oro)
Cada request autenticado inyecta el tenant desde el **JWT firmado**
(claim `tenant_id`), no del cliente:

```python
get_current_user   → valida (email, tenant_id) del token
get_current_tenant → Tenant activo del usuario
```

Y **toda** query de negocio filtra:
```python
select(Model).where(Model.tenant_id == tenant.id)
```

Búsquedas, listados, reportes, dashboards, Ask IA, export GDPR — todos
aplican el filtro. Tablas hijas llevan `tenant_id` denormalizado
(defensa en profundidad). Un usuario **nunca** carga ni ve filas de otro
tenant porque la BD solo devuelve las de su `tenant_id`.

> Auditoría recomendada al portar: `grep "select(" ` y verificar que
> ninguna consulta de negocio omita `tenant_id` (así se encontraron y
> corrigieron 4 fugas — ver `MULTITENANCY.md §9`).

### 6.3 Por qué NO "una BD por empresa"
- Menor costo/operación (1 pool, 1 backup, 1 migración).
- El aislamiento lógico es equivalente para el usuario final.
- Escala a cientos de tenants sin aprovisionar infra por cada uno.
- Trade-off: exige disciplina (filtrar `tenant_id` siempre). Si el
  negocio exige aislamiento físico/regulatorio, migrar a
  schema-per-tenant.

---

## 7. Checklist para replicar en otra plataforma

1. Tabla `tenant` (slug único, domain único, branding_json,
   default_language, is_active) + `user` con `UniqueConstraint(email,
   tenant_id)`.
2. `tenant_id` (FK+index) en **todas** las tablas de negocio.
3. Endpoint `POST /tenants` **solo super-admin**: valida slug/dominio,
   crea tenant + admin + seed de settings, envía correo en background.
4. Password admin: explícita o autogenerada con flag must-change.
5. **Correo best-effort** con URL `/t/{slug}/login` y branding del tenant;
   SMTP por tenant con fallback al tenant plataforma.
6. JWT lleva `tenant_id` firmado; `get_current_tenant` en cada endpoint.
7. **Regla de oro**: toda query filtra `tenant_id`.
8. Zona Empresas (super-admin): KPIs + tabla/grid + filtros + CRUD por
   modales (crear/ver/editar/dominio/reenviar/activar/eliminar).
9. Zona Marca (admin de tenant): cards por sección, upload de logos a
   data URL, colores, idioma; GET/PUT `/branding`.
10. Seed de IntegrationSettings vacíos al crear el tenant.

---

## 8. Archivos de referencia

| Área | Archivo |
|------|---------|
| Creación/CRUD de tenants + correo | `backend/routers/tenants.py` |
| Auth, JWT, get_current_tenant, register, validación dominio | `backend/routers/auth.py` |
| Envío de correos | `backend/services/email_service.py` |
| Branding | `backend/routers/branding.py` · `frontend/.../branding-settings/*` |
| Zona Empresas (UI) | `frontend/.../super-tenants/*` |
| Modelos | `backend/models.py` (`Tenant`, `User`, `IntegrationSetting`) |
| Aislamiento (detalle) | `docs/MULTITENANCY.md` |
