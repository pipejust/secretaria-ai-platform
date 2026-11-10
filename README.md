# Acten

Plataforma multiempresa para recibir reuniones, extraer actas y tareas con IA, revisarlas y distribuirlas. Incluye calendarios, búsqueda sobre reuniones, marca por empresa, facturación e integraciones. El proyecto se llamó Notiva; algunos recursos locales conservan ese nombre.

## Arquitectura

- `frontend/`: Angular 21, TypeScript, Vitest; build estático servido por Nginx.
- `backend/`: Python / FastAPI, SQLModel, autenticación, integraciones y scheduler interno.
- PostgreSQL 17 con pgvector para el stack Docker; SQLite para desarrollo y pruebas aisladas.
- Gotenberg convierte DOCX a PDF. El bot propio de reuniones se ejecuta en otro proyecto; Fireflies es otra fuente de reuniones.
- `deploy/`: configuración de producción con PostgreSQL propio o Supabase.
- `docs/`: arquitectura, contratos y operación. `mobile/` contiene una propuesta, no una app implementada.

## Desarrollo con Docker

Desde la raíz:

```bash
# Solo si no existe: no reemplazar un .env con credenciales propias.
cp -n .env.example .env
make up
```

Frontend: `http://localhost:4200`. API: `http://localhost:8000`, Swagger: `/docs`. PostgreSQL local: puerto 5433. El modo development crea el usuario de prueba `admin@notiva.local` con contraseña `notiva`; no usarlo en producción.

## Desarrollo sin Docker para la aplicación

Usar Python 3.11 (versión de la imagen) y Node compatible con Angular 21: `^20.19.0`, `^22.12.0` o `>=24.0.0`.

```bash
cd backend
python3.11 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
# Crear .env solo si no existe y revisar sus valores.
cp -n .env.example .env
uvicorn main:app --reload --port 8000
```

En otra terminal, desde `frontend/`:

```bash
npm ci
npm start
```

La API de desarrollo usa el puerto 8000. Para PDF hace falta Gotenberg; para usar PostgreSQL hay que configurar `DATABASE_URL`. No se debe apuntar el desarrollo o las pruebas a una base de producción.

## IA e integraciones

La clave de Groq se guarda cifrada **por empresa** desde la configuración de IA de la aplicación. `GROQ_API_KEY` en `.env` se conserva por compatibilidad, pero no sustituye esa configuración. `OPENAI_API_KEY` sigue siendo una variable de instalación para embeddings y los caminos de audio que la utilizan. Correo, calendarios y otras integraciones se configuran desde la UI según la empresa.

## Verificación local

Desde la raíz, con las dependencias del backend instaladas:

```bash
backend/venv/bin/python -m pytest backend/tests
```

Desde `frontend/`:

```bash
npm test -- --watch=false
npm run build -- --configuration production
```

Las pruebas del bot externo se omiten cuando `conversacionalbot` no está disponible. El entorno del backend es `backend/venv`; el antiguo `.venv` de la raíz no se usa en estas instrucciones.

## Producción

Ver [deploy/README.md](deploy/README.md). El frontend Docker recibe `NG_APP_API_URL` al compilar; cambiar el backend requiere recompilar. Un build directo usa `https://api.acten.app` por defecto.

La verificación histórica del 9 de noviembre de 2026 y sus manifiestos están en [docs/RECONCILIACION_PRODUCCION_20261109.md](docs/RECONCILIACION_PRODUCCION_20261109.md). Los cambios posteriores a esa referencia deben probarse y desplegarse por separado.
