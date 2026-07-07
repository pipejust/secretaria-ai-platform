# Arquitectura Multi-Tenant — Registro, Login y Aislamiento de Datos

> Documento técnico para **replicar el modelo multi-tenant** en otro
> proyecto (con sus reportes, dashboards y aislamiento entre empresas).
> Todo lo aquí descrito está **validado contra el código real**. La
> sección 9 lista **hallazgos de la validación** (fugas a corregir).

---

## 1. Modelo mental

**Un solo backend, una sola base de datos, N empresas ("tenants")**
lógicamente aisladas por una columna `tenant_id` presente en cada tabla
de negocio. No hay una BD por empresa: el aislamiento es **a nivel de
fila** (row-level, aplicado en la capa de aplicación).

```
Plataforma (acten.app)
├── Tenant "acten"        (dueño de la plataforma)
├── Tenant "softnexus"    → sus usuarios, sesiones, proyectos, tareas…
├── Tenant "colpensiones" → …aislado…
└── Tenant "telar"        → …aislado…
```

---

## 2. Las dos tablas núcleo

### `tenant`
| Campo | Descripción |
|-------|-------------|
| `id` PK | Identificador interno |
| `slug` **unique, index** | URL-safe (`softnexus`), usado en login y `/t/{slug}` |
| `name` | Nombre legible |
| `domain` unique | Dominio custom opcional (`app.empresa.com`) |
| `branding_json` | Logo, colores, nombre visible |
| `default_language` | Fallback de idioma (es/ca/en) |
| `is_active` | Si `False`, el tenant no puede operar |

### `user`
| Campo | Descripción |
|-------|-------------|
| `id` PK | |
| `tenant_id` **FK → tenant.id, index** | A qué empresa pertenece |
| `email` **index** | NO es único global |
| `hashed_password` | bcrypt/passlib |
| `role_id` FK → role | admin / validator / (viewer…) |
| `is_superadmin` | Super-admin **de plataforma** (transversal) |

**Regla clave de identidad:**
```python
__table_args__ = (UniqueConstraint("email", "tenant_id",
                  name="uq_user_email_per_tenant"),)
```
→ El **mismo email puede existir en varias empresas**. La identidad
única es el par **(email, tenant_id)**, nunca el email solo.

Todas las tablas de negocio (`meetingsession`, `project`,
`projectcontact`, `actionitem`, `embeddingchunk`, `integrationsetting`…)
llevan `tenant_id` (FK, index).

---

## 3. Registro (provisioning)

Hay **dos niveles** de alta:

### 3.1 Alta de una EMPRESA nueva (super-admin de plataforma)
Endpoint `POST /api/tenants/` protegido por `require_superadmin`.
Crea el `tenant` + su **primer usuario admin** + envía invitación.
Solo un `is_superadmin=True` puede hacerlo.

```python
def require_superadmin(current_user = Depends(get_current_user)):
    if not current_user.is_superadmin:
        raise HTTPException(403, "Requiere super-admin de plataforma.")
    return current_user
```

### 3.2 Alta de un USUARIO dentro de una empresa (admin del tenant)
Endpoint `POST /api/auth/register`. El admin autenticado crea usuarios
**dentro de su propio tenant** (`tenant_id = admin_user.tenant_id` —
nunca puede crear en otro). Reglas:
- Unicidad verificada **dentro del tenant** (email + tenant_id).
- **Validación de dominio de correo**: el email debe pertenecer al
  dominio corporativo del tenant (`company_website`/`domain`) —
  `_validate_email_matches_tenant_domain`. Los super-admin pueden
  saltarse esta validación.

Puntos críticos: el `tenant_id` de un usuario nuevo **siempre** se toma
del admin que lo crea, jamás del payload → un admin no puede inyectar
usuarios en otra empresa.

---

## 4. Login (tenant-scoped)

Endpoint `POST /api/auth/login`. El login está **acotado al tenant**:

```python
tenant = _resolve_tenant(db, login_req.tenant_slug)   # slug → tenant
user = db.exec(
    select(User)
    .where(User.email == login_req.username)
    .where(User.tenant_id == tenant.id)                # ← scope
).first()
if not user or not verify_password(pw, user.hashed_password):
    raise 401
```

- **Resolución del tenant** (`_resolve_tenant`): por `slug` explícito, o
  cae al `DEFAULT_TENANT_SLUG`. También existe `get_tenant_from_request`
  que resuelve por header `X-Tenant-Slug` o `?tenant=` (usado en
  endpoints públicos como `/api/branding`).
- Como el email no es único global, **el par (email, tenant) identifica
  al usuario**. Dos empresas pueden tener `juan@correo.com` distintos.
- **2FA gate**: si el usuario tiene 2FA, NO se emite token; se manda
  código y el frontend pide el segundo factor.
- Login fallido → `audit.log` (registro de auditoría).

### Token JWT
Al autenticar se emite un JWT (HS256) con estos claims:
```json
{ "sub": "<email>", "role": "<role>",
  "tenant_id": <int>, "tenant_slug": "<slug>",
  "is_superadmin": <bool>, "exp": <ts> }
```
El `tenant_id` viaja **dentro del token firmado** → no se puede
manipular sin la `JWT_SECRET_KEY`.

---

## 5. Aislamiento en cada consulta (el corazón)

### 5.1 Dependencias de FastAPI
Cada endpoint protegido inyecta:

```python
def get_current_user(token, db):
    payload = jwt.decode(token, SECRET_KEY, [ALGORITHM])
    email, tenant_id = payload["sub"], payload["tenant_id"]
    user = db.exec(select(User)
        .where(User.email == email)
        .where(User.tenant_id == tenant_id)).first()   # doble verificación
    if not user or not user.is_active: raise 401
    return user

def get_current_tenant(current_user, db):
    tenant = db.get(Tenant, current_user.tenant_id)
    if not tenant or not tenant.is_active: raise 403
    return tenant
```

### 5.2 Regla de oro
**TODA query de negocio filtra `tenant_id`**, tomándolo del
`get_current_tenant`/`get_current_user`, **nunca** de un parámetro del
cliente. Ejemplo (listado de sesiones):

```python
query = select(MeetingSession).where(MeetingSession.tenant_id == tenant.id)
```

### 5.3 Defensa en profundidad
Tablas hijas (ej. `actionitem`) llevan su propio `tenant_id`
denormalizado y las queries lo filtran **además** de filtrar por la
sesión padre. Ejemplo en reportes:

```python
sessions = select(MeetingSession).where(MeetingSession.tenant_id == tid)
items    = select(ActionItem).where(ActionItem.session_id.in_(sids))
                             .where(ActionItem.tenant_id == tid)  # defensa extra
```

### 5.4 Roles dentro del tenant
`require_session_writer` limita escritura a `admin`/`validator`.
Los permisos son **por tenant** (un admin de la empresa A no es admin de
la B). El `is_superadmin` es el único rol transversal, y **solo** para
provisioning de tenants (no para leer datos de negocio de otros).

---

## 6. Super-admin vs Admin (no confundir)

| | Alcance | Puede |
|---|---|---|
| `is_superadmin` | Plataforma (transversal) | Crear/editar/borrar **tenants**, entrar a cualquiera |
| rol `admin` | Su tenant | Gestionar usuarios, proyectos, sesiones **de su empresa** |
| rol `validator` | Su tenant | Curar sesiones y tareas |

Los endpoints de `/api/tenants/*` (crear empresa, listar todas, editar)
exigen `require_superadmin`. El resto exige `get_current_tenant` y
filtra por la empresa del usuario.

---

## 7. Reportes

`backend/routers/reports.py` — `_build_report(db, tenant_id, …)`:
> "CRÍTICO multi-tenant: TODAS las queries (sesiones, proyectos, action
> items) filtran por `tenant_id`."

- Sesiones, proyectos y action items del tenant → dataset del reporte.
- Ventana temporal opcional (`_resolve_window`).
- Clasificación de proyectos por avance, timeline de decisiones, top
  owner por proyecto, % de progreso.
- Todo **scopeado al tenant** por diseño.

---

## 8. Dashboards

`backend/routers/analytics.py`:
- `GET /api/analytics/roi` — minutos de reunión estimados, tareas
  completadas/pendientes, tasa de completitud, top owners.
- `GET /api/analytics/recurring` — reuniones recurrentes (por título
  normalizado).
- `GET /api/analytics/sessions/{id}/quality` — score de calidad de una
  sesión.

> ⚠️ **Ver sección 9**: estos tres endpoints hoy NO filtran por
> `tenant_id` — es una fuga que hay que corregir antes de replicar el
> patrón.

---

## 9. Hallazgos de la validación (⚠️ bugs de aislamiento a corregir)

Al validar el código encontré **3 endpoints de analytics que NO aíslan
por tenant** — mezclan datos de todas las empresas:

| Endpoint | Problema | Severidad |
|----------|----------|-----------|
| `GET /api/analytics/roi` | `select(MeetingSession)` y `select(ActionItem)` **sin** `where(tenant_id==…)` → agrega métricas de **todas** las empresas | **CRÍTICO** |
| `GET /api/analytics/recurring` | `select(MeetingSession).order_by(...)` sin filtro de tenant | **CRÍTICO** |
| `GET /api/analytics/sessions/{id}/quality` | `db.get(MeetingSession, id)` sin verificar que la sesión sea del tenant del usuario → un usuario puede leer la calidad de una sesión ajena por ID | **ALTO** |

**Fix recomendado** (patrón correcto, igual al resto del código):
```python
# roi / recurring
current_user = Depends(get_current_user)
sessions = db.exec(select(MeetingSession)
    .where(MeetingSession.tenant_id == current_user.tenant_id)).all()
actions  = db.exec(select(ActionItem)
    .where(ActionItem.tenant_id == current_user.tenant_id)).all()

# session_quality
sess = db.get(MeetingSession, session_id)
if not sess or sess.tenant_id != current_user.tenant_id:
    raise HTTPException(404, "session not found")
```

> El resto del sistema (sesiones, reportes, proyectos, tareas, Ask) SÍ
> aísla correctamente. La fuga está acotada a estos 3 endpoints de
> métricas.

---

## 10. Checklist para replicar el modelo en otro proyecto

1. **Columna `tenant_id`** (FK + index) en **todas** las tablas de
   negocio, incluidas las hijas (defensa en profundidad).
2. **Unicidad compuesta** en usuarios: `UniqueConstraint(email, tenant_id)`.
   Nunca email único global.
3. **JWT lleva `tenant_id`** firmado. Nunca confiar en un `tenant_id`
   que venga del body/query del cliente.
4. **Dependencias centrales**: `get_current_user` (valida email+tenant),
   `get_current_tenant` (verifica tenant activo), `require_superadmin`
   (transversal solo para provisioning).
5. **Login tenant-scoped**: resolver tenant por slug/domain ANTES de
   buscar el usuario por (email, tenant_id).
6. **Registro con `tenant_id` heredado** del creador, nunca del payload.
   Validación de dominio de correo opcional.
7. **Regla de oro en cada query**: `.where(Model.tenant_id == tenant.id)`.
   Hacer un **audit periódico** buscando `select(` sin `tenant_id`
   (justo el tipo de fuga de la sección 9).
8. **Roles por tenant** (`admin`/`validator`) separados del super-admin
   de plataforma.
9. **Auditoría**: log de logins fallidos y acciones sensibles.
10. **Reportes y dashboards**: pasar `tenant_id` explícito a la función
    que arma el dataset; jamás agregar sobre toda la tabla.

---

## 11. Archivos de referencia

| Archivo | Responsabilidad |
|---------|-----------------|
| `backend/models.py` | `Tenant`, `User` (+ `UniqueConstraint` email/tenant) |
| `backend/routers/auth.py` | login, register, JWT, `get_current_user`, `get_current_tenant`, `require_superadmin`, validación de dominio, 2FA |
| `backend/routers/tenants.py` | CRUD de empresas (solo super-admin) |
| `backend/routers/reports.py` | Reportes scopeados a `tenant_id` |
| `backend/routers/analytics.py` | Dashboards (⚠️ 3 endpoints a corregir, §9) |
| `backend/database.py` | Engine, migraciones ligeras |
