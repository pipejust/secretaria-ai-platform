# Secretaría AI - Sistema Multi-Agente

Secretaría AI es un sistema diseñado para automatizar la captura, procesamiento, curación humana y distribución de actas y tareas provenientes de reuniones de Fireflies.ai.

## Arquitectura

El proyecto está separado en dos bloques principales:
- **Frontend (Angular 18):** Una interfaz web centrada en la "Curación", construida con CSS Vanilla (Glassmorphism aesthetics) y Single File Components, donde un administrador revisa el flujo procesado por la IA y puede corregir, eliminar o añadir tareas antes de aprobarlas.
- **Backend (Python / FastAPI):** Un orquestador robusto con persistencia en SQLModel (preparado para PostgreSQL/Supabase), que expone Webhooks para ingerir transcripciones de Fireflies. Utiliza API de Groq para transformar el texto libre en JSON estructurado (Action Items, Acuerdos, Riesgos), y luego contiene los servicios de inyección para generar actas de Word (python-docx), enviar correos (SMTP) y crear tarjetas en Trello o Work Items en Azure DevOps.

## Requisitos Previos
- Node.js >= 18.0
- Angular CLI >= 17.0
- Python >= 3.10
- Opcional: Base de datos PostgreSQL si no se usa SQLite.

## Despliegue Local

### 1. Backend
1. Navega a la carpeta backend: `cd backend`
2. Activa el entorno virtual: `source venv/bin/activate`
3. Instala dependencias (si no lo has hecho): `pip install -r requirements.txt`
4. Levanta el servidor: `uvicorn main:app --reload`
*Nota: El backend correrá en `http://localhost:8000`. Accede a `http://localhost:8000/docs` para ver Swagger con el webhook.*

### 2. Frontend
1. Navega a la carpeta frontend: `cd frontend`
2. Instala los paquetes: `npm install`
3. Levanta el servidor de desarrollo: `npm start`
*Nota: La UI correrá en `http://localhost:4200` y desplegará directamente el panel de curación para efectos de prueba.*

## Agentes Implementados
- **Arquitecto:** Estructuración del proyecto en capas (Frontend/Backend/DB).
- **Backend Developer:** FastAPI, Webhooks y Persistencia (SQLModel).
- **Integrations Developer:** Trello, Azure DevOps, python-docx, SMTP.
- **AI Agent:** Groq prompt engineering para parseo determinista (JSON schema).
- **Frontend Developer:** UI de curación con Angular.
- **QA/Documentador:** Consolidación transversal del código, pruebas simuladas y escritura de este README.
