# Deployment de Acten

Esta carpeta contiene **todo lo necesario** para subir Acten a un servidor
de producción con **despliegue continuo desde GitHub**.

---

## TL;DR — La opción recomendada

> **Hetzner Cloud (CX22, ~€4,5/mes) + Coolify**
>
> Un solo servidor, todo el stack (backend + frontend + Postgres +
> Gotenberg + reverse proxy con SSL automático), auto-deploy en cada
> `git push` a `main`, panel web para administrar.

| Tema | Costo | Por qué |
|------|-------|---------|
| Hetzner CX22 | **€4,51/mes** (≈ COP 18 000) | 2 vCPU, 4 GB RAM, 40 GB SSD. Suficiente para los primeros 50–100 usuarios. |
| Dominio | ~€10/año | Cualquier registrar. Cloudflare registrar es el más barato. |
| Coolify | **€0** | Self-hosted, open source. La alternativa libre a Vercel/Heroku. |
| **Total** | **~€5/mes** + dominio | Versus Render Pro €19+/mes |

Sigue **[`HETZNER_COOLIFY.md`](./HETZNER_COOLIFY.md)** paso a paso.

---

## ¿Por qué esta opción y no otras?

### Render
- ✅ Cero ops.
- ❌ €19/mes el plan Pro (€7 web service + €7 DB + €5 background worker + huevos).
- ❌ Cold starts en planes baratos.
- ❌ 4× más caro para el mismo hardware.

### Vercel + Backend en Hetzner
- ✅ Frontend con CDN global gratis.
- ❌ 2 superficies que mantener (Vercel + Hetzner) y configurar CORS.
- ❌ Para una app interna B2B, el CDN no aporta tanto como sí lo haría
  para una landing pública.
- 👉 Si más adelante quieres mover el frontend a Vercel, es trivial:
  el build de Angular ya es estático y solo hay que apuntar
  `NG_APP_API_URL` al backend.

### Contabo VPS
- ✅ Más barato (€3–4/mes por specs similares).
- ❌ Red más lenta y con más quejas de uptime que Hetzner.
- ❌ IPv4 cuesta extra en algunos planes.
- 👉 Funciona si el presupuesto es muy ajustado, pero por €1/mes más,
  Hetzner es más fiable.

### Self-hosted en tu propia máquina
- ❌ La máquina tiene que estar 24/7 con IP pública o túnel.
- ❌ Cualquier corte de luz/internet → la plataforma cae.
- ❌ No es viable para clientes externos.

---

## Archivos en esta carpeta

| Archivo | Para qué |
|---------|----------|
| [`HETZNER_COOLIFY.md`](./HETZNER_COOLIFY.md) | **Guía principal**: setup completo paso a paso. |
| [`docker-compose.prod.yml`](./docker-compose.prod.yml) | Compose de producción (sin volúmenes de dev, sin reload, con healthchecks). |
| [`.env.production.example`](./.env.production.example) | Template con TODAS las variables que necesitas. |
| [`POST_DEPLOY_CHECKLIST.md`](./POST_DEPLOY_CHECKLIST.md) | Qué probar después del primer deploy. |

---

## Despliegue continuo (CD)

Coolify hace esto out-of-the-box:

1. Conectas tu repo de GitHub (`pipejust/secretaria-ai-platform`)
2. Eliges la rama (`redesign/acten-v1` o `main`)
3. Activas "Auto Deploy"
4. **Cada `git push` dispara el build y despliegue automático**

No necesitas escribir GitHub Actions. Coolify expone un webhook que
GitHub llama al push y Coolify hace `git pull` + `docker compose up
--build` + healthcheck + cutover.

Si algo falla → Coolify mantiene la versión anterior corriendo y
muestra el log. Si funciona → swap atómico, cero downtime.

---

## Pasos siguientes inmediatos

1. **Decide tu dominio** (ej: `acten.ai`, `app.acten.com`, etc.).
2. Lee [`HETZNER_COOLIFY.md`](./HETZNER_COOLIFY.md) — dura ~30 minutos
   leerlo y ~1 hora ejecutarlo end-to-end.
3. Avísame cuando tengas el servidor creado y te ayudo con la config
   específica de variables.
