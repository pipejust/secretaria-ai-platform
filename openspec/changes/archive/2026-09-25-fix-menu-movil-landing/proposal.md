# Proposal

Ítem: `docs/items/ACT-260924-a1f3.md` · Tamaño: **S** · Riesgo: **R1**

## Why

El landing público de acten.app no tiene navegación en teléfonos. El botón de la
hamburguesa se dibuja fuera del ancho de la pantalla y el contenido se recorta sin
barra de desplazamiento, así que no hay forma de tocarlo: quien entra desde el
celular no puede llegar a Producto, Precios, Contacto ni Iniciar sesión.

Es la puerta de entrada comercial del producto y hoy está cerrada para la mitad del
tráfico típico de una web. La corrección son dos reglas de CSS ya escritas, pero
invertidas.

## What Changes

- En `@media (max-width: 768px)`, el encabezado deja de mostrar el botón primario
  "Solicitar demo" (`.al-header__actions .al-btn--primary`), que es el que empuja la
  hamburguesa fuera de la pantalla.
- En ese mismo bloque, el cajón móvil deja de ocultar su propio "Solicitar demo"
  (`.al-nav__actions .al-nav__cta`), que ya está maquetado y con su comportamiento.
- Resultado: en el encabezado móvil quedan el selector de idioma y la hamburguesa;
  las dos llamadas a la acción ("Iniciar sesión" y "Solicitar demo") viven dentro del
  cajón, que es donde el diseño ya las tenía previstas.

No hay cambios de comportamiento en escritorio: por encima de 768 px nada de esto
aplica.

## Capabilities

### New Capabilities
- `landing-publico`: el sitio público de Acten — su navegación, incluida la del
  encabezado en teléfonos, tabletas y escritorio.

### Modified Capabilities
<!-- Ninguna: el proyecto todavía no tiene specs; esta es la primera. -->

## Impact

- **Código:** una sola hoja de estilos,
  `frontend/src/app/components/landing/landing.component.css`, dentro del bloque
  `@media (max-width: 768px)` (líneas 1751 y 1773). No se toca HTML ni TypeScript:
  los dos botones ya existen en las dos ubicaciones.
- **Datos y permisos:** ninguno. Es una página pública, sin sesión y sin datos de
  ninguna empresa, así que no hay nada que aislar por tenant.
- **Dependencias:** ninguna nueva.
- **Riesgo de regresión:** que alguien dependa hoy del botón "Solicitar demo" en el
  encabezado móvil. No se pierde: queda a un toque, dentro del cajón.

## Fuera de alcance

- El resto del landing en móvil: la tarjeta de demostración (que parte palabras a
  375 px), los tamaños de tipografía y el espaciado de las secciones.
- El pie del landing (enlaces repetidos y teléfono de relleno) → `ACT-260924-e6f2`.
- La firma de marca inconsistente → `ACT-260924-d5e1`.
- Cualquier cambio al cajón móvil que no sea dejar de ocultar el botón que ya tiene.
