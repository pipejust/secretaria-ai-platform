# Mediciones — borde derecho de la hamburguesa del landing

Medido en el navegador contra el stack local. "Cabe" = el borde derecho del botón
queda dentro del ancho de la ventana. La página no ofrece scroll horizontal, así que
lo que se sale no se puede alcanzar de ninguna forma.

## Antes (rama `redesign/acten-v1`)

| Ancho | Borde derecho | ¿Cabe? | Ancho del documento |
|---|---|---|---|
| 320 px | 445 px | ❌ | 320 |
| 375 px | 445 px | ❌ | 375 |
| 430 px | 445 px | ❌ | 430 |
| 768 px | 733 px | ✅ | 753 |
| 1280 px | — (hamburguesa oculta) | ✅ | 1265 |

A 1280 px el encabezado muestra "Iniciar sesión" y "Solicitar demo", y la
hamburguesa está oculta: el escritorio ya era correcto.

## Después (esta rama)

| Ancho | Borde derecho | ¿Cabe? | Ancho del documento |
|---|---|---|---|
| 320 px | 304 px | ✅ | 320 |
| 375 px | 355 px | ✅ | 375 |
| 430 px | 410 px | ✅ | 430 |
| 768 px | 733 px | ✅ (cajón cerrado) | 753 |
| 1280 px | — (hamburguesa oculta) | ✅ | 1265 |

A 1280 px el encabezado sigue igual que antes: "Iniciar sesión" y "Solicitar demo"
visibles, hamburguesa oculta, barra de enlaces en `flex`. El escritorio no cambió.

## Interacciones del cajón (375 px)

- El cajón abre con los seis enlaces de sección, "Iniciar sesión" y "Solicitar demo",
  los dos botones a 335 px de ancho.
- Al tocar "Solicitar demo": el cajón se cierra, la URL no cambia (el manejador hace
  `preventDefault`) y la aplicación llama `window.scrollTo({top: 6724, behavior: 'smooth'})`,
  que es exactamente el inicio de `#contact` menos los 80 px de margen.
- "Iniciar sesión" apunta a `/login`.

**Límite de esta medición:** el navegador automatizado ignora `behavior: 'smooth'`
(un `scrollTo` sin `behavior` sí mueve la página), así que el desplazamiento animado
en sí no se pudo observar: lo que se verificó es que la aplicación lo pide con el
destino correcto. El mismo comportamiento ya existía antes del cambio en los enlaces
de sección del cajón, así que no es una regresión de este ítem. Queda para la prueba
humana en la vista previa.
