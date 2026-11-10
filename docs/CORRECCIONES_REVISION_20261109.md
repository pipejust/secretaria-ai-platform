# Correcciones tras reconciliar producción — 9 de noviembre de 2026

Estos cambios son locales y posteriores a la referencia de producción del informe de reconciliación. No se hizo push, despliegue, reinicio ni cambio de datos en producción.

## Calendarios

El fallo de cuatro repeticiones era una prueba dependiente de la fecha real: su serie de septiembre salía parcialmente de la ventana de importación de 60 días. La prueba fija ahora el reloj de sincronización sin alterar la ventana funcional.

En ese recorrido se reprodujo un defecto de importación: el alta de una suscripción descargaba el archivo y lo pedía de nuevo con su ETag; una respuesta 304 podía dejar la nueva suscripción sin eventos. Ahora se importa el cuerpo inicial una sola vez. Los refrescos 304 conservan la copia y devuelven su número de eventos; un calendario válido que queda vacío retira su copia anterior. Respuestas HTML y calendarios truncados se rechazan sin borrar los eventos guardados. La regresión cubre estos comportamientos por la API.

## Frontend y Nginx

- API local alineada con el puerto 8000; build directo de producción alineado con `https://api.acten.app`. Docker sigue admitiendo `NG_APP_API_URL` para otras instalaciones.
- Micrófono permitido al mismo origen, conservando la autorización del navegador.
- Encabezados de seguridad incluidos también en los `location` con `add_header` propio; antes Nginx dejaba de heredar los encabezados globales.
- Eliminadas la importación Angular sin uso y las cadenas opcionales redundantes señaladas por el compilador.

## Despliegue y documentación

- `deploy/compose.sh` fija la raíz del proyecto para que los contextos de build resuelvan correctamente desde cualquier directorio. Las instrucciones usan `.env.production` y no sobrescriben el `.env` de desarrollo.
- Las etiquetas Traefik admiten `ACTEN_COOLIFY_UUID`; su valor de respaldo conserva la instalación existente. Configurar el UUID correspondiente antes de usar otro recurso.
- La variante Supabase conserva `/app/uploads` en volumen y fuerza `LOG_LEVEL=info` para Gotenberg. Antes de aplicar este volumen a una instalación antigua, migrar los uploads que estén dentro de su contenedor.
- Variables opcionales para clave estable de cifrado y orígenes CORS adicionales declaradas en ambos Compose.
- La plantilla `.env.production.example` ya puede versionarse: estaba excluida por `.env.*`. Solo contiene ejemplos; los archivos de secretos reales siguen ignorados.
- README y guías actualizados para Angular 21, entorno `backend/venv`, Groq por empresa, OpenAI de instalación y rutas actuales. Se retiraron promesas de cero interrupciones o rollback automático basadas solo en healthchecks.
- `backend/venv` actualizado desde `requirements.txt`; el antiguo `.venv` raíz se conserva y no es el entorno indicado para ejecutar las pruebas.

## Validación

- Backend: **223 pruebas aprobadas, 2 omitidas**, ninguna fallida, con dependencias instaladas desde requirements. Las omitidas requieren el proyecto externo `conversacionalbot`.
- `pip check`: sin dependencias incompatibles.
- Frontend: **59 pruebas aprobadas**; compilación de producción sin advertencias.
- Imagen Docker del frontend construida localmente y `nginx -t` correcto.
- HTTP local de Nginx: comprobados encabezados y política del micrófono en raíz, index, ruta SPA, robots, manifest, favicon, CSS y respuesta 403 de `.env`; conservada la política de no cachear HTML.
- Ambas variantes Compose renderizadas desde `/tmp` mediante el wrapper: contextos existentes, URL de API, volumen de uploads y nivel de logs correctos. UUID alternativo interpolado correctamente en las etiquetas de producción.
- Documento pendiente `docs/GUIA_ALTUM_ACTEN_VOCEM.md`: hash original conservado y fuera de los commits de corrección.

Los logs locales están en `/Volumes/Información/Proyectos/secretaria-resguardo-20261109`. No se probaron servicios externos con credenciales reales ni una grabación de audio completa en navegador; las comprobaciones de despliegue no iniciaron el stack de producción.
