# AGENTS.md — Acten

> Fuente única de contexto para cualquier agente (Claude Code, Codex, Cursor, Copilot).
> `CLAUDE.md` solo importa este archivo. Máximo ~150 líneas: lo que no aplica a TODA tarea va en una skill o en `openspec/specs/`.
> Dueño: el líder técnico. Cambios a este archivo = PR revisado por el líder técnico.

## 1. Qué es este proyecto
- Producto: Acten convierte reuniones en actas, decisiones y tareas. Ingiere la reunión (webhook de Fireflies, bot propio de Acten o subida manual), la procesa con IA, un administrador la cura y desde ahí salen actas en Word, correos y tarjetas en Trello / Jira / ClickUp / Azure DevOps.
- Es multi-empresa (multi-tenant): cada empresa tiene su marca, su dominio propio, su política de correo y su suscripción (pagos con Wompi). También se usa *headless*, con la plataforma de Servicios/RRHH como interfaz (ver `docs/INTEGRACION_ACTEN_RRHH.md`).
- Cliente / contrato: producto propio de Softnexus.
- Líder técnico (valida sellos a distancia con `/sn-validate`): Julio Alejandro Cortes Burgos <alejandro@ac-setroc.com> · GitHub @AC-Setroc
- Prefijo de ítems: ACT  (historias y bugs en `docs/items/`, ids tipo ACT-260919-a3f2; los traídos de Altum usan su número, ACT-1863)
- Producción: https://acten.app (landing) · https://admin.acten.app (app) · https://api.acten.app (API)

## 2. Stack aprobado (no agregar dependencias fuera de esta lista sin aprobación)
- Frontend: Angular 21.2 (standalone + signals) + CSS vanilla con tokens propios + `@angular/cdk` + `@ngx-translate` (es/en/ca). Sin framework de UI ni Tailwind.
- Backend: Python 3.11 + FastAPI + SQLModel sobre PostgreSQL. Auth propia (JWT + bcrypt).
- IA: Groq y OpenAI para extracción estructurada y RAG (embeddings).
- Documentos y medios: `python-docx` para actas, Gotenberg para PDF, S3 propio por empresa para audio y vídeo.
- Tests: pytest (backend) · `@angular/build:unit-test` con Vitest + jsdom (frontend).
- Deploy: Docker sobre Hetzner + Coolify, auto-deploy desde la rama de trabajo. Ver `deploy/`.

## 3. Comandos (el agente DEBE usar estos, no inventar otros)
```bash
make up                       # levanta todo el stack local (postgres + backend + frontend + gotenberg)
make down                     # apaga el stack conservando datos
make test                     # pytest del backend dentro del contenedor
make logs-be                  # logs del backend

cd frontend && npm install    # instalar frontend
cd frontend && npm start      # dev en http://localhost:4200
cd frontend && npm test       # unit tests (Vitest)
cd frontend && npm run build  # build de producción (también valida tipos)

node scripts/check-file-size.mjs      # ningún archivo > 1000 líneas
node scripts/lint-no-hardcode.mjs     # ningún color/tipografía fuera de tokens.css
node scripts/tokens-build.mjs         # regenera frontend/src/styles/tokens.css
```
No hay `lint` ni `typecheck` sueltos: los tipos los valida `npm run build`. Ver §8.

## 4. Flujo obligatorio: Spec Driven (OpenSpec)
Todo trabajo entra por **`/sn`** (idea, bug, error, mejora, correo del cliente). `/sn` conduce el camino:
ítem → tarjeta (tipo, tamaño, riesgo) → historia Ready (`sn-story`) → spec (`openspec-propose`) → **SELLO: aprobación humana** → construir (`openspec-apply-change`, TDD) → evidencia (`sn-evidence`) → commits y PR (`sn-ship`) → **SELLO: revisión en sesión nueva + prueba humana** → merge → archivar (`openspec-archive-change`) → microlección (`sn-explain`).
`/sn-status` dice en qué etapa va cada ítem y qué sigue. `/sn-help` dice qué hacer si no sabes. Cuando un sello necesita al líder (R3–R4 plano, R2+ entrega): `/sn-request` lo pide por git y el líder valida desde su computador con `/sn-validate`. `/sn-learn` convierte errores repetidos en reglas. Cada ítem vive en `docs/items/<ID>.md` y cada commit lleva `Refs: <ID>`; `/sn-items` los muestra con su trazabilidad y `/sn-connect` los mantiene al día en sistemas externos.
Ninguna línea de código de producto sin un change aprobado en `openspec/changes/`. Única excepción: XS + R0 (texto, color, typo), que igual lleva evidencia.

## 5. Reglas que nunca se negocian
- Nunca secretos en código, commits, logs ni prompts. Solo variables de entorno; `.env*` nunca se lee ni se versiona.
- Nunca escribir en base de datos de producción desde un agente. MCPs de producción: solo lectura.
- Nunca borrar ni editar migraciones ya aplicadas; siempre crear una nueva.
- Nunca desactivar tests, lint, tipos o RLS para "hacer que pase".
- Nunca agregar un paquete sin verificar que existe en el registro y está en el stack aprobado (evita paquetes alucinados).
- Todo endpoint nuevo valida entrada y verifica autorización. Nada de `// TODO auth`.
- **Todo lo que toca datos filtra por empresa.** Este sistema es multi-tenant: una consulta sin `company_id` es una fuga de datos entre clientes, no un bug de estilo.
- Cambios mínimos: tocar solo lo que la tarea exige. Sin refactors "de paso". La mejor línea es la que no se escribe (skill `ponytail`).
- Ningún archivo de código supera **1000 líneas** (aviso desde 800; lo sano es 200–400). El código nuevo nace en módulos pequeños: si un archivo se acerca al límite, lo nuevo va en otro módulo en la misma tarea. Los archivos heredados que ya pasaban el límite están en `.sn-size-baseline`: no pueden crecer y se reducen con `sn-split`.
- "Funciona" no es evidencia. Evidencia = salida de comandos + screenshot + test.
- El agente que escribió el código no aprueba el código.

## 6. Matriz de riesgo (se aplica ÍTEM POR ÍTEM, no al proyecto entero)
| Nivel | Ejemplos | Revisión mínima |
|---|---|---|
| R0 | texto, CSS, contenido | agente + screenshot |
| R1 | componente UI aislado | agente reviewer + prueba funcional |
| R2 | API, lógica de negocio, tabla nueva | reviewer + tests + humano |
| R3 | auth, permisos, pagos, datos personales, migraciones, aislamiento entre empresas | tech lead + security review |
| R4 | producción, infraestructura, borrado masivo | aprobación explícita del tech lead antes de empezar |

Un proyecto no tiene un nivel de riesgo: lo tiene cada cambio. Un texto es R0 aunque el sistema maneje pagos, y una migración es R3 aunque el proyecto sea pequeño. El riesgo se decide al recibir el ítem (`/sn`, paso 3).

## 7. Convenciones del código
- Backend por capas: `backend/routers/<tema>.py` expone HTTP y valida permisos; `backend/services/<tema>.py` tiene la lógica; `backend/models.py` los modelos SQLModel. Ejemplo: `backend/routers/billing.py` + `backend/services/wompi.py`.
- Los tests del backend viven en `backend/tests/test_<tema>.py`, uno por área, con los fixtures de `backend/tests/conftest.py`. Ejemplo: `backend/tests/test_billing.py`.
- Frontend: un componente por carpeta en `frontend/src/app/components/<nombre>/` con `.ts`, `.html`, `.css` y `.spec.ts` al lado. Ejemplo: `frontend/src/app/components/billing/`.
- El acceso HTTP no va en los componentes: va en un servicio tipado en `frontend/src/app/services/<tema>.service.ts`. Ejemplo: `frontend/src/app/services/billing.service.ts`.
- Los permisos de ruta se resuelven en `frontend/src/app/guards/`. Ejemplo: `frontend/src/app/guards/admin.guard.ts`.
- Textos visibles siempre por `@ngx-translate`, con la clave en los tres idiomas (`frontend/public/assets/i18n/{es,en,ca}.json`). Nada de texto quemado en la plantilla.
- Diseño visual: seguir `DESIGN.md`. No inventar colores, tipografías ni espaciados: salen de `frontend/src/styles/tokens.css`, que es **generado** (se editan `frontend/tokens/*.json` y se corre `node scripts/tokens-build.mjs`).

## 8. Cosas que el agente suele hacer mal en ESTE repo
<!-- Se alimenta con /sn-learn. Una línea por lección, con fecha. -->
- 2026-09-24 · Deuda existente al adoptar la metodología: no hay `lint` ni `typecheck` como comandos propios (los tipos solo se validan al construir) y no hay CI. No se arregla "de paso": cada cosa necesita su ítem.
- 2026-09-24 · `scripts/lint-no-hardcode.mjs` no corre si la ruta del repositorio tiene espacios ("AI Projects"): arma la ruta desde `import.meta.url` sin decodificar el `%20`. Falla antes de revisar nada. Pendiente de ítem propio.
- 2026-09-24 · Deuda de tamaño: 33 archivos ya pasaban las 1000 líneas y quedaron en `.sn-size-baseline` (los peores: `frontend/src/styles.css` 1450, `templates.component.css` 1340, `super-tenants.component.css` 1332). No pueden crecer; se reducen con `sn-split` cuando haya que tocarlos.
- 2026-09-24 · No se formatea al guardar en este repo: hay `.prettierrc` pero 80 archivos no están formateados con prettier, así que cualquier formateo automático convierte un cambio de 4 líneas en un diff de 700. Se probó un hook de formato y se quitó el mismo día.
- 2026-09-24 · Después de un `git pull` hay que correr `npm install` en `frontend/`: las dependencias cambian seguido y los tests fallan con un error de tipos que parece del código y no lo es.
- 2026-09-24 · La rama `main` está congelada desde hace meses; el trabajo y producción viven en `redesign/acten-v1`. No partas ramas de `main` ni asumas que es lo desplegado.
- 2026-09-24 · El `README.md` está desactualizado (dice "Notiva", Angular 18 y Supabase). Para saber qué hay hoy, lee el código o `docs/`, no el README.
