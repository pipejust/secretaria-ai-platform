# Precios y costos del bot de Acten

Actualizado: 1 de noviembre de 2026. Precios en USD, sin impuestos. Wompi cobra en COP con la TRM del Banco de la República (31 de octubre de 2026: 3 182,99 COP por USD).

Los costos de proveedores salen de sus páginas de precios públicas. **Los costos fijos son una estimación**: falta confirmarlos con la facturación de cada proveedor (Hetzner, almacenamiento, correo, dominios), a la que no se pudo entrar desde el navegador sin una sesión iniciada.

## Costo variable por hora de reunión

| Concepto | USD / hora | Fuente |
|---|---|---|
| Captura con Skribby (Meet, Teams, Zoom) | 0,35 | [Skribby](https://skribby.io/docs/guides/billing) |
| Vídeo en Skribby | +0,05 | Skribby |
| Transcripción en vivo (Soniox en tiempo real, vía Skribby) | +0,17 | Skribby |
| Transcripción por archivo (Soniox, clave propia) | 0,10 | [Soniox](https://soniox.com/pricing) |
| Transcripción en tiempo real directa (Vocem) | 0,12 | Soniox |
| Análisis con Groq (`gpt-oss-20b`, unas 40 000 fichas por reunión de 45 min) | ≈ 0,01 | medido en producción |
| Almacenamiento y auxiliares | ≈ 0,02 | `escenarios_costos.csv` del bot |

| Tipo de hora | Costo total |
|---|---|
| Solo audio por Skribby | **≈ 0,48** |
| Skribby con vídeo y transcripción en vivo (peor caso) | **≈ 0,60** |
| Sala de Vocem, solo audio (captura propia) | ≈ 0,13 |
| Sala de Vocem con transcripción en vivo | ≈ 0,25 |

## Costo fijo mensual (estimado)

| Concepto | Rango mensual |
|---|---|
| Servidor de Acten (Hetzner, 2 vCPU, 4 GB, 80 GB) | 8 – 20 € |
| Servidor de Vocem (2 vCPU, 4 GB, 40 GB) | 4 – 6 € |
| Copias, IPv4, dominios, correo, almacenamiento de objetos | 10 – 20 € |
| **Total** | **≈ 25 – 50 USD** (se usa 55 para planear) |

Hetzner cambió sus tarifas en abril y junio de 2026; por eso el rango es amplio.

## Uso real

En los últimos 45 días el bot grabó 27 reuniones y 18,5 horas: 15,2 h de Softnexus y 3,3 h de Singular, con promedios de 46 y 28 minutos. Eso es unas 12 horas al mes en total, así que hoy el costo variable no llega a 10 USD al mes.

## Precios (definidos el 1 de noviembre de 2026)

El bot viene **incluido en los planes** (antes era un add-on de 29 USD, más 19 de vídeo y 19 de transcripción en vivo). Todo en dólares; Wompi cobra el equivalente en pesos con la TRM oficial del día (ver más abajo).

| Plan | USD / mes | Reuniones | Incluye | Costo variable máximo | Margen bruto mínimo |
|---|---|---|---|---|---|
| Starter | **20** | 20 (≈ 15 h) | Bot, 5 usuarios, actas Word y PDF, tareas y correos | 7,2 | 64 % |
| Business | **80** | 100 (≈ 75 h) | Todo lo de Starter, vídeo, transcripción en vivo, integraciones, plantillas, Pregúntale a la IA | 45 | 44 % |
| Enterprise | a medida | a medida | Varias empresas con marca propia, API, bot con su nombre | — | — |

El margen de Business supone el peor caso: las 75 horas con vídeo y transcripción en vivo por Skribby. Con una mezcla normal (la mitad solo audio) sube a cerca del 50 %. El margen es ajustado a propósito: se prefiere crecer en clientes y subir precios cuando el uso lo justifique.

### Add-ons (para Starter y para empresas con plan anterior)

| Add-on | Antes | Ahora | Costo por 15 h |
|---|---|---|---|
| Bot propio, solo audio | 29 | **15** | 7,2 |
| Grabación de vídeo | 19 | **8** | 0,75 |
| Transcripción en vivo | 19 | **7** | 2,55 |
| Pregúntale a la IA | 19 | **9** | ≈ 0 |
| Integraciones | 29 | **19** | ≈ 0 |
| Plantillas por proyecto | 9 | **5** | ≈ 0 |

Bot con voz, vídeo y transcripción en vivo: 15 + 8 + 7 = **30 USD**, frente a los 67 USD anteriores. Solo el bot, sin vídeo: **15 USD**.

### Punto de equilibrio

Con unos 55 USD de costos fijos al mes se necesitan unas **5 empresas en Starter** (12,8 USD de margen cada una) o **2 en Business** (35 USD cada una, en el peor caso).

### Exceso de uso

El catálogo limita por reuniones al mes, no por horas. Un exceso debería cobrarse por encima de 1,0 USD por hora de solo audio y de 1,3 USD con vídeo y transcripción en vivo (más del doble del costo). Aún no está implementado el cobro del exceso.

### Empresas actuales

- **acten**: empresa base, no se bloquea nunca; sin cambios.
- **xkape** y **ccis**: pasan a Starter.
- **telar**, **softnexus** y **singular**: Business.

## Precio en pesos: TRM automática

El precio en COP de cada plan y add-on se calcula con la **TRM oficial** que publica el Banco de la República ([datos abiertos](https://www.datos.gov.co/resource/32sa-8pi3.json), sin llave). `services/trm.py` la lee cada día a las 6:15 (hora del servidor) y al arrancar, y redondea a miles de pesos. Si la fuente falla, se conservan los precios del día anterior. El precio en COP ya no se edita a mano: se cambia el de USD. Ejemplo con la TRM de 3 182,99: Starter 64 000 COP y Business 255 000 COP.

## Cómo se aplica

- El código ya trae estos precios para el landing (`landing_content_service.py`) y para el catálogo (`billing_catalog.py`).
- El landing público lee el contenido de esos valores por defecto.
- El catálogo de producción vive en la base de datos y no se actualiza solo: se cambia en `/admin/super/tenants` → Catálogo o con una actualización de la base. Las suscripciones manuales existentes no cambian de precio.
