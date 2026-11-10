# Reconciliación local con producción — 9 de noviembre de 2026

## Resultado

El frontend reconstruido localmente coincide byte por byte con los 68 archivos del frontend en producción. Se comparó SHA-256 por ruta: 68 iguales, 0 distintos, 0 faltantes y 0 adicionales.

- Producción: `40bfb804fa2d1652b0637262b19828c5777871bc`, rama `redesign/acten-v1`.
- Base local conservada: `65d39146781fb7fd24b238aeca71a80c8addb96a`.
- Entre ambos commits solo cambian `deploy/HETZNER_COOLIFY.md` y `deploy/POST_DEPLOY_CHECKLIST.md`; no cambia código de `frontend/` ni `backend/`.
- La compilación anterior en el host usaba la URL de Render de `environment.prod.ts`. La reconstrucción con el Dockerfile y `NG_APP_API_URL=https://api.acten.app` reproduce exactamente los artefactos desplegados. No era una divergencia del código fuente.
- La comparación anterior del backend confirmó 150 archivos idénticos de 152 examinados. Las excepciones fueron el Dockerfile con declaraciones ARG añadidas por Coolify y `.gitignore` ausente en la imagen.

## Preservación local

- Rama original conservada: `docs/firewall-perimeter-two-layer`.
- Rama de reconciliación: `codex/reconciliacion-produccion-20261109`.
- Worktree de referencia separado: `/Volumes/Información/Proyectos/secretaria-produccion`, detached en el commit de producción.
- Respaldo externo: `/Volumes/Información/Proyectos/secretaria-resguardo-20261109`.
- `docs/GUIA_ALTUM_ACTEN_VOCEM.md` permanece sin seguimiento y sin modificaciones. Su respaldo y hash están en el directorio externo; no se incorpora a este commit.
- No se usaron reset, clean ni restauraciones sobre archivos del usuario.

## Reproducción del frontend

Desde el worktree de referencia:

```bash
docker build --platform linux/amd64 \
  --build-arg NG_APP_API_URL=https://api.acten.app \
  -t secretaria-frontend-reconciliacion:40bfb804 frontend

docker run --rm --network none --platform linux/amd64 \
  --entrypoint sh secretaria-frontend-reconciliacion:40bfb804 \
  -c 'cd /usr/share/nginx/html && find . -type f -exec sha256sum {} \; | sort'
```

Los manifiestos y el resultado están en `docs/verificacion-produccion-20261109/`. Los logs de construcción quedan en el respaldo externo. Las imágenes base usan tags móviles: la igualdad se comprobó en esta ejecución, no garantiza reproducciones futuras si cambian esas imágenes.

## Precisión sobre el despliegue

El comando manual documentado debe resolver el contexto desde la raíz del repositorio:

```bash
docker compose --project-directory . --env-file .env \
  -f deploy/docker-compose.prod.yml config --quiet
```

Esto valida configuración, no despliega. El despliegue existente de Coolify funciona con su configuración efectiva. Sus etiquetas Traefik incluyen un UUID específico; no deben copiarse sin revisión a otro recurso.

## Alcance

Solo se realizaron lecturas en producción: metadatos de despliegue, archivos, hashes y consultas de estado. No hubo deploy, reinicio, cambios de configuración ni escrituras de datos de aplicación. Las construcciones Docker se hicieron localmente. Igualdad de artefactos no sustituye las pruebas funcionales ni resuelve el fallo de calendario detectado en la revisión anterior.
