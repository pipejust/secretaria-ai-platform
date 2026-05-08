# Despliegue: Backend Dockerizado en Render Starter (BD se queda en Supabase)

Procedimiento para mover Notiva de **Render Free (Python)** → **Render Starter (Docker)**
manteniendo Supabase como base de datos.

- **Costo:** $7/mes Render Starter + lo que ya pagas a Supabase.
- **Tiempo total:** ~15 min.
- **Downtime:** prácticamente cero (Render hace el switch al nuevo deploy cuando está sano).
- **Beneficio principal:** mata el spin-down de Render Free.

---

## 0. Pre-flight

- Cuenta Render con tarjeta cargada (Starter es plan pago).
- Repo en GitHub conectado a Render.
- API keys que ya tienes en el dashboard actual de Render: `OPENAI_API_KEY`,
  `GROQ_API_KEY`, `FIREFLIES_API_KEY`, `DATABASE_URL` (Supabase).

---

## 1. Migración de schema en Supabase  ✅ HECHO

Las dos columnas nuevas (`ai_fields_regenerated`, `ai_tasks_regenerated`)
ya están aplicadas en producción mediante:

```sql
ALTER TABLE public.meetingsession
  ADD COLUMN IF NOT EXISTS ai_fields_regenerated BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE public.meetingsession
  ADD COLUMN IF NOT EXISTS ai_tasks_regenerated  BOOLEAN NOT NULL DEFAULT FALSE;
```

Las 46 sesiones existentes quedaron con ambos flags en `false`, lo que
significa que los botones "Sugerir Campos con IA" y "Regenerar Tareas"
seguirán habilitados en cada una.

> Si en futuras versiones agregas más columnas, `database.create_db_and_tables()`
> en `backend/database.py` ya tiene el patrón `ALTER TABLE ADD COLUMN IF NOT EXISTS`
> idempotente que se ejecuta en cada `on_startup`. Solo añade la sentencia ahí.

---

## 2. Webhook token de Fireflies  ✅ HECHO

Se pre-generó un `webhook_token` aleatorio (32 bytes URL-safe) y se persistió
en `IntegrationSetting` (provider `fireflies`, campo `config_json.webhook_token`).

**URL nueva del webhook (cópiala y pégala en Fireflies tras el deploy):**

```
https://secretaria-ai-platform.onrender.com/api/webhook/fireflies?token=<webhook_token>
```

(El token exacto está en Supabase; lo verás en `/admin/settings` → Fireflies → "Webhook URL".
También puedes consultarlo con:
`SELECT config_json::jsonb->'webhook_token' FROM integrationsetting WHERE provider_name='fireflies';`)

> **Importante:** Apenas hagas el deploy del nuevo código, los webhooks que sigan
> llegando a la URL vieja (sin `?token=…`) serán rechazados con `401 Missing
> webhook token`. Actualiza Fireflies inmediatamente después del deploy.

---

## 3. Migrar el web service a Docker Starter (10 min)

### Opción A: aplicar el Blueprint del repo (recomendado)

1. En Render dashboard → **Settings** del web service actual → **Suspend** (no lo borres aún).
2. Render dashboard → **New + → Blueprint** → conecta el repo y la rama.
3. Render lee `render.yaml` y crea `notiva-backend` como Docker Starter.
4. Render te pedirá rellenar los secrets marcados como `sync: false`:
   - `DATABASE_URL` → la misma cadena de Supabase que ya usabas (Direct connection con IPv4 add-on)
   - `OPENAI_API_KEY`, `GROQ_API_KEY`, `FIREFLIES_API_KEY` → las que ya tenías
   - `FRONTEND_URL` → `https://notiva.vercel.app` (o lo que sea)
   - `PUBLIC_BASE_URL` → déjalo vacío en este momento; lo llenas tras el primer deploy
5. Espera el primer build de Docker (~3-5 min la primera vez, después <1 min).
6. Cuando esté arriba, abre la URL pública. Debes ver:
   ```json
   {"status":"ok","message":"Notiva Backend está corriendo"}
   ```
7. Copia esa URL al env var `PUBLIC_BASE_URL` y guarda → Render redeploya.
8. **Ahora sí**, suspende/borra el web service viejo (Free, Python).

### Opción B: cambiar el actual sin Blueprint

1. En el web service actual de Render → **Settings**:
   - **Runtime**: Python → **Docker**
   - **Dockerfile path**: `./backend/Dockerfile`
   - **Docker context**: `./backend`
   - **Plan**: Free → **Starter ($7/mo)**
   - **Health Check Path**: `/`
2. **Environment**: añade lo que falte (`JWT_SECRET_KEY` con "Generate Value", `ENVIRONMENT=production`, `BCRYPT_ROUNDS=12`, `PUBLIC_BASE_URL`).
3. Manual Deploy → Latest commit.

---

## 4. Frontend (Vercel) — sin cambios

El frontend sigue apuntando a la misma URL del backend. Si la URL pública
cambia (con el Blueprint usualmente cambia), actualiza la env var
`NG_APP_API_URL` en Vercel y redeploya.

---

## 5. Settings post-deploy (2 min)

Login como admin → `/admin/settings`:

1. La sección **Fireflies** ya tiene tu API Key. **El "Webhook URL" ahora se
   muestra con el `?token=…` embebido y un botón "Copiar".**
2. Copia esa URL nueva → ve a Fireflies → Settings → Integrations → Webhook
   → reemplaza la URL vieja por esta. Marca el evento "Meeting Completed".
3. Verifica que **Resend, Trello, Jira, ClickUp, Azure** sigan con sus
   credenciales (las dejaste como estaban; nada se borró).

---

## 6. Smoke test (5 min)

```bash
# 1. Healthcheck del backend
curl -s https://<tu-url>.onrender.com/

# 2. El JWT viejo está invalidado al rotar JWT_SECRET_KEY → re-loguea desde el frontend.

# 3. Crea/edita una sesión y verifica que los nuevos botones se comportan:
#    - Quitamos "Obtener Resumen Ejecutivo" (el textarea muestra el de Fireflies).
#    - "✨ Sugerir Campos con IA" → llama OpenAI, después se vuelve "✓ Campos sugeridos".
#    - "🤖 Regenerar Tareas" → llama OpenAI, después se vuelve "✓ Tareas regeneradas".

# 4. Dispara una reunión real con Fireflies (o un POST manual con el token correcto)
#    para validar que el webhook entra, el summary nativo aparece, las tareas las saca
#    OpenAI y las decisiones/riesgos/acuerdos los saca Groq.
```

---

## Rollback

Si algo se rompe en el nuevo Docker Starter, en Render → web service viejo → **Resume**
y en Vercel apunta `NG_APP_API_URL` de vuelta a la URL vieja. La BD nunca cambió.

Para revertir la migración SQL (si fuera necesario):

```sql
ALTER TABLE public.meetingsession DROP COLUMN IF EXISTS ai_fields_regenerated;
ALTER TABLE public.meetingsession DROP COLUMN IF EXISTS ai_tasks_regenerated;
```

(No revertirá el `webhook_token`; ese queda en `config_json` y no estorba al código viejo.)
