# Suscripciones y pagos (Wompi)

Actualizado: 14 de septiembre de 2026.

## Modelo

- **Plan** (`plan`): `starter`, `business`, `enterprise`. Precio en USD (el del landing) y en **COP** (lo que cobra Wompi; solo acepta COP). Cada plan lista las funciones que incluye (`features_json`), el tope de reuniones por mes y los usuarios incluidos. `enterprise` no es contratable en línea (`is_public = false`).
- **Add-on** (`addon`): opciones sueltas que se suman a cualquier plan: `owned_bot` (bot propio), `video_recording` (requiere `owned_bot`), `ask_ai`, `integrations`, `templates`. Sus precios son **provisionales**: no existían; ajustarlos en `/admin/super/tenants` → Catálogo.
- **Suscripción** (`subscription`, una por empresa): plan, add-ons, estado, modo (`manual` = la asigna un superadministrador; `wompi` = la renueva un pago), periodo actual.
- **Pago** (`payment`): una fila por intento de checkout, con referencia única `acten-<tenant>-<12 hex>`; Wompi la devuelve en el evento.

Funciones (`services/billing_catalog.py::FEATURES`): `meetings.fireflies`, `meetings.owned_bot`, `meetings.video`, `documents.export` (actas Word/PDF), `reports`, `calendar`, `templates`, `integrations`, `ask_ai`.

| Plan | Incluye |
|---|---|
| Starter | Fireflies, actas, reportes, calendario |
| Business | Starter + plantillas, integraciones, IA |
| Enterprise | Todo, incluido bot propio y vídeo |

## Reglas

- Empresa **nueva sin plan**: 14 días de prueba con todo (`trialing`), contados desde `tenant.created_at`. Después, `none`: sin funciones de pago.
- Empresas **anteriores** a la facturación: al arrancar, reciben `business` manual sin vencimiento (una sola vez, solo si no tienen fila). softnexus necesita además el add-on `owned_bot` (asignación manual).
- Vencimiento: 7 días de gracia (`past_due`, todo sigue activo); después `expired`.
- La empresa dueña (`slug = acten`) y los superadministradores no se bloquean nunca.
- Gating: `Depends(require_feature("clave"))` → **402** con `detail {message, feature, label, plan, status}`. Aplicado a: bot propio (`/api/owned-bot/*`), actas (`/api/sessions/{id}/export/{format}`), reporte PDF, Pregúntale a la IA, y al origen de reuniones (`/api/settings/meeting-source` solo ofrece lo que el plan incluye; el webhook de Fireflies y el receptor del bot rechazan lo que el plan no cubre).
- `/auth/me` → `tenant.entitlements` para que el front oculte lo no incluido.

## Wompi

Llaves cifradas en `IntegrationSetting('wompi')` de la empresa dueña; se editan desde `/admin/super/tenants` (superadmin) o `PUT /api/billing/wompi-config`. Nunca en el entorno. Al navegador solo va la llave pública.

Flujo:
1. `POST /api/billing/checkout` crea el pago pendiente y devuelve `reference`, `amount_in_cents`, `public_key`, `signature_integrity` = SHA-256(`reference + amount + "COP" + integrity_secret`) y `redirect_url`.
2. El front abre el widget (`https://checkout.wompi.co/widget.js`).
3. Wompi envía `transaction.updated` a **`POST https://api.acten.app/api/webhook/wompi`**; se verifica el checksum (valores de `signature.properties` + `timestamp` + events secret, SHA-256) y, si `APPROVED` con el monto y moneda esperados, se activa o extiende la suscripción (`+N meses`, renovación anticipada se suma al final). Idempotente.
4. Al volver por la redirección (`/admin/billing?reference=…&id=…`) el front llama `POST /api/billing/payments/{reference}/sync` con el id de transacción: Acten consulta `GET /v1/transactions/{id}` con la llave privada por si el evento aún no llegó.

Renovación: v1 = el administrador vuelve a pagar (aviso en la app desde 7 días antes y durante la gracia). v2 (pendiente) = cobro automático con fuente de pago tokenizada (`wompi_payment_source_id` ya existe en la tabla).

### Lo que hay que registrar en Wompi (panel del comercio)

- Entorno **sandbox** primero: `pub_test_…`, `prv_test_…`, *Eventos* secret, *Integridad* secret.
- URL de eventos: `https://api.acten.app/api/webhook/wompi`.
- Producción: `pub_prod_…`, `prv_prod_…` y sus dos secretos; cambiar `environment` a `production`.

### Endpoints

| Quién | Método y ruta |
|---|---|
| Admin de empresa | `GET /api/billing/catalog`, `GET /api/billing/me`, `POST /api/billing/checkout`, `GET /api/billing/payments`, `GET /api/billing/payments/{ref}`, `POST /api/billing/payments/{ref}/sync`, `POST /api/billing/cancel` |
| Superadmin | `GET/PUT /api/billing/wompi-config`, `GET /api/billing/catalog/all`, `PUT /api/billing/catalog/plans/{key}`, `PUT /api/billing/catalog/addons/{key}`, `GET /api/billing/tenants`, `PUT /api/billing/tenants/{id}` |
| Wompi | `POST /api/webhook/wompi` |

Pruebas: `backend/tests/test_billing.py`.
