# Despliegue de Acten

El stack de producción contiene frontend Angular/Nginx, backend FastAPI y Gotenberg. Puede usar PostgreSQL 17 con pgvector en el mismo servidor o una base externa Supabase. El bot de reuniones se configura aparte.

## Coolify

La instalación verificada usa el repositorio `pipejust/secretaria-ai-platform`, rama `redesign/acten-v1`, Base Directory `/` y Compose Location `/deploy/docker-compose.prod.yml`. Configurar dominio HTTPS para frontend (80) y backend (8000); PostgreSQL y Gotenberg permanecen internos.

- Variables: partir de `.env.production.example`, sin publicar credenciales en Git.
- Marcar `PUBLIC_BASE_URL` como variable de build: se compila en el frontend.
- En una instalación nueva, establecer `ACTEN_COOLIFY_UUID` con el UUID del recurso. Las etiquetas Traefik interpolan esa variable. El valor de respaldo conserva el recurso existente, no identifica recursos nuevos.
- Usar un solo worker del backend: el scheduler vive dentro del proceso. Varios workers duplicarían jobs sin coordinación adicional.
- Confirmar salud, dominios, certificados y backups después de desplegar. Un healthcheck no garantiza por sí solo despliegues sin interrupción ni rollback automático de Compose.

La guía de instalación está en [HETZNER_COOLIFY.md](HETZNER_COOLIFY.md) y las verificaciones en [POST_DEPLOY_CHECKLIST.md](POST_DEPLOY_CHECKLIST.md). Las cifras históricas de capacidad y precios de la guía no constituyen una cotización vigente.

## Compose manual

Ejecutar desde la raíz del repositorio. Conservar el `.env` de desarrollo; crear un archivo separado para producción:

```bash
cp -n deploy/.env.production.example .env.production
# Editar .env.production con URLs y credenciales reales.
./deploy/compose.sh prod config --quiet
# Ejecutar únicamente cuando se quiera desplegar:
./deploy/compose.sh prod up -d --build
```

El wrapper fija `--project-directory` a la raíz para resolver `./backend` y `./frontend` correctamente. Puede invocarse desde otro directorio. Equivalente explícito desde la raíz:

```bash
docker compose --project-directory . --env-file .env.production \
  -f deploy/docker-compose.prod.yml config --quiet
```

Configurar un reverse proxy HTTPS aparte: el Compose solo declara puertos internos. Las etiquetas para Coolify no crean por sí solas routers manuales.

Para Supabase, completar `DATABASE_URL`, activar la extensión `vector` y usar:

```bash
./deploy/compose.sh supabase config --quiet
./deploy/compose.sh supabase up -d --build
```

El volumen `acten_uploads` conserva los archivos subidos en ambas variantes. Si una instalación anterior de Supabase tenía uploads dentro del contenedor, respaldarlos y migrarlos al volumen antes de recrearlo. Un volumen vacío no recupera archivos antiguos.

`ACTEN_ENV_FILE=/ruta/archivo.env` permite usar otro archivo sin sobrescribir el local.

## Configuración posterior

Crear el administrador según la guía; configurar Groq por empresa desde la aplicación. La variable de entorno `GROQ_API_KEY` no habilita por sí sola el procesamiento de una empresa. OpenAI sigue usando `OPENAI_API_KEY` para embeddings/audio.

Conservar la clave de cifrado al restaurar datos: `CALENDAR_SECRET_KEY`, o `JWT_SECRET_KEY` si no existe una clave independiente. Cambiarla sin migrar los secretos cifrados inutiliza las credenciales guardadas. Verificar backups de PostgreSQL y uploads con una restauración aislada.

`render.yaml` queda como alternativa histórica del backend, no como descripción del despliegue completo actual.
