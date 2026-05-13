# Deploy Acten en Hetzner + Coolify (CD desde GitHub)

> **Costo total:** ~€5/mes (€4,51 servidor + dominio amortizado)
> **Tiempo:** ~1 hora de principio a fin
> **Resultado:** plataforma en `https://app.tudominio.com` con SSL,
> auto-deploy en cada `git push`, panel para administrar.

---

## Pre-requisitos (15 min)

### 1. Cuenta en Hetzner
- Regístrate en https://accounts.hetzner.com/signUp
- Verifica con tarjeta de crédito o PayPal (cobran ~€2 al inicio que se devuelven)
- Activa Hetzner **Cloud** (no Robot Server)

### 2. Dominio
- Compra un dominio donde prefieras (Cloudflare Registrar, Namecheap, GoDaddy)
- Recomendación: subdominios separados — `app.tudominio.com` (frontend) y `api.tudominio.com` (backend)
- Si tu dominio es nuevo: configúralo con Cloudflare DNS (gratis, panel mejor que el del registrar)

### 3. Llave SSH local (para conectarte al servidor)
```bash
# Si no tienes una, créala:
ssh-keygen -t ed25519 -C "tu-correo@ejemplo.com"
# Acepta los defaults. La llave queda en ~/.ssh/id_ed25519
# Tu PUBLIC key (la que subes a Hetzner) está en:
cat ~/.ssh/id_ed25519.pub
```

### 4. API keys listas (las pondrás más adelante)
- OpenAI: https://platform.openai.com/api-keys
- Groq: https://console.groq.com/keys
- (Opcional) Fireflies: https://app.fireflies.ai/settings/developer

---

## Paso 1 — Crea el servidor en Hetzner (5 min)

1. Entra a **Hetzner Cloud Console** → https://console.hetzner.cloud/
2. Crea un **Project** llamado "Acten"
3. Haz click en **Add Server**:

| Campo | Valor |
|-------|-------|
| **Location** | Falkenstein (Alemania) o Ashburn (USA) según tu audiencia |
| **Image** | Ubuntu 24.04 |
| **Type** | **CX22** (€4.51/mo, 2 vCPU, 4 GB RAM, 40 GB SSD) |
| **Networking** | Public IPv4 + IPv6 (default) |
| **SSH key** | Pega tu `id_ed25519.pub` |
| **Volumes** | Ninguno por ahora (el SSD de 40 GB sobra para empezar) |
| **Firewall** | Crea uno y abre puertos `22, 80, 443` (al final) |
| **Backups** | ⚠️ **Activa los backups automáticos** (€0.90/mes extra) |
| **Name** | `acten-prod` |

> **Si tu audiencia inicial supera 100 usuarios activos**, considera CX32 (€7.05/mo, 4 vCPU, 8 GB RAM). Puedes empezar con CX22 y escalar después en 1 click sin perder datos.

Click **Create & Buy now**. En ~30 segundos tienes IP pública.

---

## Paso 2 — Apunta el dominio al servidor (5 min)

En el panel DNS de tu registrar (o Cloudflare):

| Tipo | Nombre | Valor | Proxy (Cloudflare) |
|------|--------|-------|--------------------|
| A    | `app`  | `<IP del servidor>` | DNS only (gris) |
| A    | `api`  | `<IP del servidor>` | DNS only (gris) |
| A    | `coolify` | `<IP del servidor>` | DNS only (gris) |

> **¿Por qué DNS only y no Proxy?** Coolify maneja el SSL con Let's
> Encrypt. Si activas el proxy de Cloudflare (naranja) tienes que
> configurar SSL "Full (strict)" y subir el certificado origin. DNS-only
> simplifica todo. Cuando todo esté funcionando puedes activar el proxy
> para tener CDN/DDoS protection.

Espera 5–10 min a que propague (puedes verificar con `dig app.tudominio.com`).

---

## Paso 3 — Conéctate al servidor y endurécelo (10 min)

```bash
# Conexión inicial (acepta la fingerprint con `yes`)
ssh root@<IP del servidor>

# Actualiza el sistema
apt update && apt upgrade -y

# Crea un usuario non-root (más seguro que andar como root)
adduser felipe
usermod -aG sudo felipe

# Copia tu llave SSH al nuevo usuario
mkdir -p /home/felipe/.ssh
cp ~/.ssh/authorized_keys /home/felipe/.ssh/
chown -R felipe:felipe /home/felipe/.ssh
chmod 700 /home/felipe/.ssh
chmod 600 /home/felipe/.ssh/authorized_keys

# Bloquea el login por password y deshabilita root remoto
sed -i 's/#PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i 's/#PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl restart ssh

# Firewall (UFW). Solo abrimos 22, 80, 443.
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# Ahora cierra y reconéctate como tu usuario:
exit
ssh felipe@<IP del servidor>
```

---

## Paso 4 — Instala Coolify (5 min)

Como tu usuario non-root:

```bash
# Coolify se instala con un solo comando oficial
curl -fsSL https://cdn.coollabs.io/coolify/install.sh | sudo bash
```

Esperá unos minutos. Coolify se descarga (es un Docker compose grande), arranca su Postgres + Redis interno, y te muestra al final:

```
Coolify is running on http://<IP>:8000
```

> 8000 es el puerto del panel de Coolify, no el puerto del backend de Acten (eso lo veremos después con dominio + SSL).

Abre `http://<IP>:8000` en el browser:
1. Crea tu usuario admin (email + password fuerte). **Guarda esto en un password manager.**
2. Coolify te lleva al dashboard.

### Apunta `coolify.tudominio.com` al panel

En **Settings → Instance Settings**:
- "Instance's Domain": `coolify.tudominio.com`
- Click **Save** y **Generate SSL Certificate**

Espera 1–2 min. Ahora puedes entrar por `https://coolify.tudominio.com` (con SSL automático). Cierra el `:8000` directo en UFW si quieres:
```bash
sudo ufw delete allow 8000
```

---

## Paso 5 — Conecta GitHub (3 min)

Coolify necesita acceso a tu repo para hacer pull en cada push.

1. **Coolify → Sources → GitHub App**
2. Click **Configure** → te lleva a GitHub para autorizar
3. Instala la app **solo en el repo `pipejust/secretaria-ai-platform`** (no des acceso a todos)
4. Vuelves a Coolify y verás "GitHub App" como Source listo

---

## Paso 6 — Crea el recurso Docker Compose (10 min)

1. **Coolify → Projects → +Create New** → llámalo "Acten"
2. Dentro del proyecto: **+New Resource → Docker Compose**
3. Llena:

| Campo | Valor |
|-------|-------|
| **Source** | Tu GitHub App |
| **Repository** | `pipejust/secretaria-ai-platform` |
| **Branch** | `redesign/acten-v1` (o `main` si ya hiciste merge) |
| **Build Pack** | Docker Compose |
| **Base Directory** | `/` |
| **Docker Compose Location** | `deploy/docker-compose.prod.yml` |

Click **Save**. Coolify analizará el compose y mostrará los servicios detectados (postgres, backend, frontend, gotenberg).

### 6.1 Variables de entorno

Tab **Environment Variables → +Add**. Una por una, copia desde
`deploy/.env.production.example` con los valores reales:

```
PUBLIC_BASE_URL=https://api.tudominio.com
FRONTEND_URL=https://app.tudominio.com
POSTGRES_DB=acten
POSTGRES_USER=acten
POSTGRES_PASSWORD=<genera con: openssl rand -base64 48>
JWT_SECRET_KEY=<genera con: python3 -c "import secrets; print(secrets.token_urlsafe(64))">
OPENAI_API_KEY=sk-...
GROQ_API_KEY=gsk_...
FIREFLIES_API_KEY=
LOG_LEVEL=INFO
```

⚠️ **Marca el checkbox "Is Build Time?" en `PUBLIC_BASE_URL`** — el frontend lo usa como build-arg.

### 6.2 Dominios por servicio

Coolify detecta los servicios. Para cada uno, en **Domains**:

| Servicio | Domain | Port |
|----------|--------|------|
| `frontend` | `https://app.tudominio.com` | 80 |
| `backend`  | `https://api.tudominio.com` | 8000 |
| `postgres` | (sin dominio) | — |
| `gotenberg` | (sin dominio) | — |

Coolify configura Traefik + Let's Encrypt automáticamente al guardar.

### 6.3 Auto-deploy

Tab **Configuration → General**:
- **Auto Deploy** = ON
- **Deploy on push** = ON
- **Branch** = `redesign/acten-v1`

Coolify configura el webhook de GitHub solo. Cualquier `git push` a esa branch dispara un deploy.

### 6.4 Healthchecks

El compose ya tiene healthchecks definidos. Coolify los respeta y solo hace cutover de tráfico cuando el servicio nuevo está sano.

---

## Paso 7 — Primer deploy 🚀

Click el botón verde grande **Deploy**.

Lo que pasa:
1. Coolify hace `git clone` del repo
2. Levanta los containers según `deploy/docker-compose.prod.yml`
3. Build de backend (Python deps): ~2-3 min
4. Build de frontend (Angular production): ~1-2 min
5. Pull de pgvector/pg17 + gotenberg: ~30s
6. Postgres aplica migraciones automáticas (`_apply_lightweight_migrations`)
7. Backend arranca y pasa healthcheck
8. Traefik genera certs SSL y enruta los dominios

Total: **~5-8 minutos** la primera vez, **~2-3 minutos** los siguientes deploys.

Verifica:
- `https://api.tudominio.com/openapi.json` → muestra el JSON de la API
- `https://api.tudominio.com/docs` → Swagger UI
- `https://app.tudominio.com/` → la app de Acten

---

## Paso 8 — Setup inicial post-deploy (5 min)

### 8.1 Crea el primer admin

Por defecto al primer arranque, Acten crea un tenant `acten` (default) pero **no usuarios**. Conéctate al backend y crea el admin:

```bash
ssh felipe@<IP>
docker exec -it acten-backend python -c "
from sqlmodel import Session, select
from database import engine
from models import User, Role, Tenant
from auth_utils import get_password_hash

with Session(engine) as db:
    tenant = db.exec(select(Tenant).where(Tenant.slug == 'acten')).first()
    role = db.exec(select(Role).where(Role.name == 'admin')).first()
    if not tenant or not role:
        print('Tenant/Role no encontrados — revisa logs del backend.'); exit(1)

    u = User(
        email='tu-email@empresa.com',
        full_name='Tu Nombre',
        hashed_password=get_password_hash('cambia-este-password'),
        is_active=True,
        is_superadmin=True,
        role_id=role.id,
        tenant_id=tenant.id,
    )
    db.add(u); db.commit()
    print(f'OK: usuario {u.email} creado.')
"
```

Login en `https://app.tudominio.com/` con ese email/password.

### 8.2 Configura integraciones

Una vez dentro:
- `/admin/configuración → Correo` → SMTP/Resend
- `/admin/configuración → Integraciones` → Google Calendar, Trello, Jira...
- `/admin/configuración → API y Webhooks` → Fireflies webhook
- `/admin/branding` → Logo y nombre del workspace

### 8.3 Verifica el flujo completo

Lee [`POST_DEPLOY_CHECKLIST.md`](./POST_DEPLOY_CHECKLIST.md).

---

## Despliegue continuo confirmado

A partir de ahora, cada vez que hagas `git push origin redesign/acten-v1`:

1. GitHub dispara el webhook de Coolify
2. Coolify pulea el código nuevo
3. Rebuilea el contenedor afectado
4. Sustituye el viejo por el nuevo (zero-downtime gracias a healthchecks)
5. Si falla → mantiene el viejo + te notifica

Puedes ver el progreso en **Coolify → Acten → Deployments**.

---

## Operaciones comunes

### Ver logs en vivo
```bash
ssh felipe@<IP>
docker logs -f acten-backend
docker logs -f acten-frontend
```

### Backup manual de Postgres
```bash
docker exec acten-postgres pg_dump -U acten acten > acten-$(date +%Y%m%d).sql
# Descárgalo:
scp felipe@<IP>:~/acten-*.sql ./backups/
```

### Conectarse a la DB con psql
```bash
docker exec -it acten-postgres psql -U acten -d acten
```

### Rollback de un deploy malo
**Coolify → Acten → Deployments → click en uno previo → Redeploy**.

### Aumentar specs del servidor (sin perder datos)
**Hetzner Console → tu servidor → Rescale**. Apaga, sube de CX22 a CX32, enciende. ~3 min de downtime, todos los datos intactos.

---

## Troubleshooting

| Síntoma | Causa probable | Fix |
|---------|----------------|-----|
| Error SSL "self-signed" | Cloudflare proxy activo | Bájalo a "DNS only" o configura "Full (strict)" en CF |
| Backend devuelve 502 | Healthcheck no pasa | `docker logs acten-backend` — usualmente env var faltante |
| Frontend muestra "Cannot connect to API" | `PUBLIC_BASE_URL` mal o no marcaste "Build Time" | Edita la var, marca el checkbox, redeploy |
| `pg_isready` falla | Postgres no arrancó | `docker logs acten-postgres`, suele ser POSTGRES_PASSWORD vacío |
| Coolify no detecta el push | App de GitHub mal instalada | Settings → Sources → reinstala la app |
| Build de Angular falla por OOM | CX22 con 4 GB RAM no alcanza para Angular en algunos casos | Sube temporalmente a CX32, builda, baja a CX22 — o agrega swap (ver siguiente) |

### Agregar swap (recomendado para CX22)

Angular y npm pueden consumir picos de RAM. Agregar 2GB de swap:

```bash
ssh felipe@<IP>
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h  # verifica que aparezca swap
```

---

## Costos a 12 meses

| Concepto | Mensual | Anual |
|----------|---------|-------|
| Hetzner CX22 | €4,51 | €54 |
| Backups Hetzner (20%) | €0,90 | €11 |
| Dominio | — | ~€10 |
| Coolify | — | — |
| Let's Encrypt SSL | — | — |
| **TOTAL** | **~€5,40** | **~€75 / año** |

Equivale a ~COP 320.000/año o ~USD 80/año por toda la infraestructura.

---

¿Listo? Empieza por el Paso 1 y cualquier duda me dices.
