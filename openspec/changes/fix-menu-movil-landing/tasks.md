# Tasks

## 1. Dejar la medición antes de tocar nada

- [x] 1.1 Levantar el stack local (`make up`) y anotar, con el landing abierto a 320, 375, 430, 768 y 1280 px, el borde derecho del botón del menú y el ancho de la ventana. Verificación: la tabla queda escrita y reproduce el fallo (borde en 445 px a 320, 375 y 430 px).

## 2. Corregir el encabezado móvil

- [x] 2.1 En `frontend/src/app/components/landing/landing.component.css`, dentro de `@media (max-width: 768px)`, ocultar también el botón primario del encabezado (`.al-header__actions .al-btn--primary`), dejando la regla existente de `.al-btn--ghost-light`. Verificación: a 375 px el borde derecho del botón del menú queda por debajo de 375.
- [x] 2.2 En el mismo bloque, quitar la regla que oculta `.al-nav__actions .al-nav__cta` para que el cajón muestre su "Solicitar demo". Verificación: a 375 px, con el cajón abierto, se ven "Iniciar sesión" y "Solicitar demo".

## 3. Comprobar los escenarios de la spec

- [x] 3.1 Repetir la medición del paso 1.1 a 320, 375, 430, 768 y 1280 px. Verificación: en los cinco anchos ningún control del encabezado queda fuera de la ventana y el ancho del documento nunca supera el de la ventana.
- [x] 3.2 Con 375 px, abrir el menú y tocar "Solicitar demo" y luego "Iniciar sesión". Verificación: el primero cierra el cajón y baja a la sección de contacto; el segundo llega a la pantalla de acceso.
- [x] 3.3 Comprobar a 768 px que no conviven el botón del menú y la barra de escritorio, y a 1280 px que el encabezado sigue con sus dos botones. Verificación: capturas de los dos anchos.

## 4. Cerrar

- [x] 4.1 Correr las guardas: `npm test` y `npm run build` en `frontend/`, `make test` del backend y `node scripts/check-file-size.mjs`. Verificación: las cuatro en verde.
- [ ] 4.2 Reunir la evidencia con `/sn-evidence`: tabla de mediciones antes y después, capturas a 320/375/430/768/1280 px y la salida de las guardas. Verificación: `evidencia.md` existe y cubre los ocho escenarios de la spec.
