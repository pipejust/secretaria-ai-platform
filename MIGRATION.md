# Migración: Supabase → Render (todo en Render)

Procedimiento para mover Notiva de **Supabase + Render Free** a **Render
Postgres + Render Web (Docker)**.

- **Costo final:** $13/mes (Postgres Basic-256mb $6 + Web Starter $7).
- **Tiempo total:** ~40 min (15 min de los cuales son `pg_dump`).
- **Downtime:** ~5 min, durante la repunteada del `DATABASE_URL`.

---

## 0. Pre-flight

Asegúrate de tener:

- `psql` y `pg_dump` instalados localmente, **versión >= 16** (la de tu Postgres destino):
  ```bash
  brew install postgresql@16
  echo 'export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"' >> ~/.zshrc
  ```
- Una cuenta en Render con tarjeta cargada (los planes pagos requieren método de pago).
- El repo en GitHub conectado a tu cuenta de Render.

---

## 1. Crear los recursos en Render (5 min)

### Opción A: usando el `render.yaml` que ya está en el repo (recomendado)

1. En Render dashboard → **New + → Blueprint**.
2. Conecta tu repo de GitHub y selecciona la rama `main` (o la que vas a desplegar).
3. Render lee `render.yaml` y crea `notiva-postgres` + `notiva-backend` automáticamente.
4. Render te pedirá rellenar los secrets marcados como `sync: false`:
   - `OPENAI_API_KEY`
   - `GROQ_API_KEY`
   - `FIREFLIES_API_KEY`
   - `FRONTEND_URL` → `https://notiva.vercel.app` (o lo que tengas en Vercel)
   - `PUBLIC_BASE_URL` → déjalo vacío en este momento; lo llenas después del primer deploy.

### Opción B: manual (si prefieres)

1. **New + → PostgreSQL**: nombre `notiva-postgres`, plan **Basic-256mb ($6)**, versión 16.
2. **New + → Web Service**: conecta el repo, runtime **Docker**, dockerContext `./backend`, plan **Starter ($7)**.
3. En "Environment" del web service, agrega manualmente las variables del `render.yaml` y conecta el `DATABASE_URL` al Postgres.

---

## 2. Backup de Supabase (10 min)

```bash
# Copia las dos URLs de conexión:
#   - Supabase: Project Settings → Database → Connection string (URI)
#   - Render:   tu Postgres → Connections → External Database URL
export SUPABASE_URL='postgres://postgres:PASS@db.xxxxx.supabase.co:5432/postgres'
export RENDER_URL='postgres://notiva:PASS@dpg-xxxx.oregon-postgres.render.com/notiva'

# El script genera backups/supabase-backup-TIMESTAMP.sql.gz y luego restaura.
./scripts/migrate-from-supabase.sh
```

El script:
1. Verifica que tienes `pg_dump`/`psql`/`gzip`.
2. Hace `pg_dump --schema=public --no-owner --no-privileges --clean --if-exists` de Supabase.
3. Comprime el dump.
4. Te pide confirmación antes de restaurar en Render.
5. Carga el dump con `psql --single-transaction --set ON_ERROR_STOP=on`.

> El backup queda en `backups/supabase-backup-*.sql.gz` (gitignored). Guárdalo
> aparte hasta que confirmes que todo funciona.

### Verificación post-restore

```bash
psql "${RENDER_URL}" -c '\dt'                                # debes ver todas las tablas
psql "${RENDER_URL}" -c 'SELECT COUNT(*) FROM "user";'       # cuenta de usuarios > 0
psql "${RENDER_URL}" -c 'SELECT COUNT(*) FROM meetingsession;'
```

---

## 3. Switch del backend (5 min)

Si usaste el Blueprint, el `DATABASE_URL` del web service ya apunta al
Postgres de Render. Solo necesitas:

1. **Manual deploy** del web service en Render (botón "Deploy latest commit").
2. Espera el build de Docker (~3-5 min la primera vez, después <1 min con cache).
3. Cuando esté arriba, abre la URL pública (ej. `https://notiva-backend.onrender.com/`).
   Debes ver `{"status":"ok","message":"Notiva Backend está corriendo"}`.
4. Copia esa URL al env var `PUBLIC_BASE_URL` y guarda → Render redeploya.

---

## 4. Frontend (Vercel) (2 min)

En Vercel → tu proyecto Notiva → Settings → Environment Variables:

```
NG_APP_API_URL=https://notiva-backend.onrender.com
```

(o como esté nombrada en `environment.prod.ts`). Redeploy.

---

## 5. Settings post-deploy (5 min)

Con la app arriba:

1. Login como admin.
2. Ve a `/admin/settings`.
3. Pega de nuevo:
   - **Fireflies**: API Key + guarda. La UI te genera el `Webhook URL` con el token embebido.
   - **Resend**: API Key + sender.
   - **Trello / Jira / ClickUp / Azure** según uses.
4. Copia el `Webhook URL` recién generado y pégalo en Fireflies → Settings → Integrations → Webhook (evento "Meeting Completed").

---

## 6. Limpieza (3 min)

Cuando confirmes que las dos primeras reuniones procesadas en Render funcionan
end-to-end (Fireflies → backend → DB → email):

1. **Cancela el add-on de IPv4 en Supabase** (-$4/mes).
2. **Downgrade de Supabase Pro → Free** (-$10/mes) o cancela el proyecto.
3. Guarda el último `backups/supabase-backup-*.sql.gz` en cold storage (S3 / Drive).

---

## Rollback de emergencia

Si algo se rompe en Render y necesitas volver a Supabase mientras debuggeas:

1. En Vercel cambia `NG_APP_API_URL` de vuelta a la URL vieja (la del Render Free
   con Supabase) y redeploya. El backend viejo sigue funcionando porque sigue
   apuntando a Supabase.
2. No toques los datos en Render hasta que decidas re-migrar.

Si necesitas restaurar un dump en Supabase:

```bash
gunzip -c backups/supabase-backup-*.sql.gz | psql "${SUPABASE_URL}"
```

---

## Costos comparados

| Setup | $/mes | Spin-down? |
|---|---|---|
| Hoy: Supabase Pro + IPv4 + Render Free | **$14** | Sí, 15 min |
| Render-only (este blueprint) | **$13** | No |
| Quedarse en Supabase + Render Starter | $21 | No |
