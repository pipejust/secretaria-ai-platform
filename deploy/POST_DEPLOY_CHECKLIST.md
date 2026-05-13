# Checklist post-deploy

Antes de invitar al primer usuario real, verifica que todo funcione:

## Salud del stack

- [ ] `https://api.tudominio.com/openapi.json` responde 200
- [ ] `https://api.tudominio.com/docs` muestra Swagger UI
- [ ] `https://app.tudominio.com/` carga la pantalla de login
- [ ] Certificados SSL válidos (candado verde, no warnings)
- [ ] `docker ps` en el servidor muestra los 4 containers `Up (healthy)`
- [ ] Coolify dashboard muestra todo en verde

## Funcionalidad

- [ ] Login con tu admin
- [ ] Sidebar carga las opciones (Resumen / Reuniones / Proyectos / Tareas / Pregunta a Acten / Calendario / Reportes / Plantillas / Configuración)
- [ ] Dashboard `/admin/dashboard` muestra cards (aunque sean ceros al inicio)
- [ ] Crear un proyecto en `/admin/projects`
- [ ] Subir una sesión de prueba en `/admin/upload-meeting` o vía Fireflies webhook
- [ ] Verificar que el pipeline IA procesa la sesión:
  - Crea tareas (`/admin/pendientes`)
  - Crea decisiones/riesgos/acuerdos (`/admin/curation/{id}`)
  - Genera embeddings para RAG
- [ ] Pregunta en `/admin/ask` y verifica que cita fuentes correctas
- [ ] Ve la sesión en `/admin/curation/{id}` y prueba "Regenerar tareas"
- [ ] Cambiar tema en `/admin/branding` (logo + nombre) → recargar y verificar

## Integraciones (a medida que las uses)

- [ ] **Fireflies**: copia el webhook URL de `/admin/configuración → API` y pégalo en Fireflies. Manda una grabación de prueba.
- [ ] **SMTP/Resend**: configura en `/admin/configuración → Correo`. Manda test (botón "Probar conexión") cuando lo agreguemos.
- [ ] **Google Calendar**: configura OAuth en `/admin/configuración → Integraciones`. Conecta una cuenta en `/admin/calendar`.
- [ ] **Microsoft 365**: idem.
- [ ] **Trello / Jira / ClickUp / Azure DevOps**: configura tokens en `/admin/configuración → Integraciones`. Despacha una tarea de prueba desde `/admin/curation/{id}`.

## Seguridad y backups

- [ ] **Backups automáticos de Hetzner activos** (panel → tu server → Backups)
- [ ] **Backup manual de prueba**:
  ```bash
  docker exec acten-postgres pg_dump -U acten acten > test.sql
  scp felipe@<IP>:~/test.sql ./test.sql
  # Verifica que pesa más de unos pocos KB
  ```
- [ ] **Restore de prueba** (en local, no en prod):
  ```bash
  docker exec -i acten-postgres psql -U acten -d acten < test.sql
  ```
- [ ] `JWT_SECRET_KEY` en producción es DIFERENTE al de dev
- [ ] `POSTGRES_PASSWORD` es aleatorio (no "notiva_local_dev")
- [ ] `BCRYPT_ROUNDS=12` (verifica con `docker exec acten-backend env | grep BCRYPT`)
- [ ] Firewall UFW solo abre 22, 80, 443
- [ ] SSH solo por llave (no password)

## Monitoreo

- [ ] Configura un check de uptime gratuito en https://betterstack.com/uptime o https://uptimerobot.com → te avisa por email si la app cae
- [ ] (Opcional) Conecta Sentry para errores en frontend/backend

## Performance baseline

Después de 1 semana de uso real:
- [ ] CPU promedio < 60% (Coolify dashboard o `htop`)
- [ ] RAM promedio < 75% del total
- [ ] Disco usado < 50% (sobre todo del volumen de Postgres)
- [ ] Sin errores 5xx en logs (`docker logs acten-backend | grep -E "ERROR|500"`)

Si supera estos límites consistentemente → escala a CX32 (€7.05/mo, 4 vCPU, 8 GB RAM) desde el panel de Hetzner.

## Despliegue continuo confirmado

- [ ] Hacer un cambio trivial (ej: editar un texto en el frontend)
- [ ] `git push origin redesign/acten-v1`
- [ ] Coolify dashboard muestra el deploy iniciándose en ~10s
- [ ] El cambio aparece en `https://app.tudominio.com/` en ~3 min sin downtime

---

Si algo falla, revisa logs (`docker logs -f acten-backend`) y ven al
chat con el output. La mayoría de errores son env vars faltantes.
