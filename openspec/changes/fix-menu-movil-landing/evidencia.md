# Evidencia — fix-menu-movil-landing

Ítem `ACT-260924-a1f3` · Tamaño **S** · Riesgo **R1** · Rama
`fix/ACT-260924-a1f3-menu-movil-landing` · Commit `7e31be8`

Todo se verificó contra el stack local levantado con `make up`, con el contenedor
del frontend reconstruido (`docker compose up -d --build frontend`), no con
`ng serve`: así se prueba el mismo artefacto que se despliega.

## 1. Guardas

| Guarda | Resultado |
|---|---|
| `lint` | **No existe** en este proyecto. Anotado como deuda en `AGENTS.md` §8; no se inventó un comando. |
| `typecheck` | No existe suelto: los tipos los valida `npm run build`, abajo. |
| `npm test` (frontend) | ✅ 7 archivos, **32 pruebas**, todas en verde. |
| `npm run build` (frontend) | ✅ termina en **0**. Solo avisos previos del compilador (NG8113, NG8107); ninguno nuevo. |
| `make test` (backend) | ✅ **126 pasan**, 2 saltadas (`conversacionalbot`, módulo externo opcional). |
| `test:e2e` | No aplica: el proyecto no tiene suite E2E y este cambio no toca un flujo con datos. |
| `node scripts/check-file-size.mjs` | ✅ 451 archivos, **0 sobre 1000 líneas**, 33 de deuda heredada. |

El archivo tocado es deuda heredada (2104 líneas de línea base), así que el guardia
de tamaño bloqueó dos intentos de edición que lo hacían crecer. El cambio final lo
deja en **2100 líneas**: cuatro menos que antes.

## 2. Trazabilidad spec → prueba

| Escenario de la spec | Cubierto por | Estado |
|---|---|---|
| El menú se abre en un teléfono (375 px) | Medición: borde derecho de la hamburguesa en 355 px ≤ 375; al pulsarla el cajón abre con los seis enlaces y los dos botones | ✅ |
| El ancho mínimo soportado sigue siendo usable (320 px) | Medición: 304 px ≤ 320 | ✅ |
| El teléfono grande se comporta igual (430 px) | Medición: 410 px ≤ 430 | ✅ |
| El límite móvil/escritorio no muestra las dos navegaciones (768 px) | Medición: hamburguesa en 733 px, `.al-nav` sin la clase `--open` (cajón cerrado) | ✅ |
| En escritorio la navegación no cambia (1280 px) | Medición: hamburguesa en `display:none`, "Iniciar sesión" y "Solicitar demo" en `flex`, lista de enlaces en `flex` | ✅ |
| La página nunca se desplaza en horizontal | Medición del ancho del documento en los cinco anchos: 320/375/430 exactos, 753 ≤ 768, 1265 ≤ 1280 | ✅ |
| "Solicitar demo" sigue funcionando desde el cajón | El cajón se cierra, la URL no cambia (`preventDefault`) y la aplicación llama `window.scrollTo({top: 6724, behavior:'smooth'})`, que es el inicio de `#contact` menos los 80 px de margen | ⚠️ parcial |
| "Iniciar sesión" sigue funcionando desde el cajón | Pulsado desde el cajón a 375 px: navega fuera del landing y llega a la aplicación (con sesión activa aterriza en el resumen) | ✅ |

**El parcial, explicado:** el navegador automatizado ignora `behavior: 'smooth'` —un
`scrollTo` sin `behavior` sí mueve la página—, así que el desplazamiento animado no
se pudo observar. Lo verificado es que la aplicación lo pide con el destino correcto.
El mismo comportamiento ya existía antes del cambio en los enlaces de sección del
cajón, así que **no es una regresión de este ítem**, pero queda para la prueba humana
en la vista previa.

## 3. Interfaz

| Ancho | Antes | Después |
|---|---|---|
| 320 px | hamburguesa en 445 px, **fuera de la pantalla** | 304 px, dentro |
| 375 px | 445 px, fuera | 355 px, dentro |
| 430 px | 445 px, fuera | 410 px, dentro |
| 768 px | 733 px, dentro | 733 px, sin cambio |
| 1280 px | hamburguesa oculta | sin cambio |

La tabla completa está en `mediciones.md`. Visto a 375 px con el cajón abierto: el
encabezado queda con logo, selector de idioma y hamburguesa; el cajón muestra los
seis enlaces de sección, "Iniciar sesión" y un "Solicitar demo" a 335 px de ancho.

Contra `DESIGN.md`: no se introdujo ningún color, tipografía ni espaciado nuevo — el
cambio solo activa y desactiva reglas `display` que ya existían.

**Diferencia conocida y buscada:** el encabezado móvil ya no muestra "Solicitar
demo". Se comprobó que el botón sigue disponible en cinco lugares más de la página
(héroe a 335 px y visible sin desplazarse, cajón, los tres planes, llamada final y
pie), y que el del encabezado **tampoco era alcanzable antes**.

## 4. Autochequeo del diff

- Diff del código: **1 línea agregada, 5 borradas**, en un solo archivo.
- Sin secretos, sin `console.log`, sin `debugger`.
- Sin paquetes nuevos: `package.json` no se tocó.
- Sin endpoints ni permisos: el cambio es CSS de una página pública, sin sesión ni
  datos de ninguna empresa.
- Nada fuera del alcance: los otros archivos del diff son los artefactos del propio
  change y el campo `branch:` del ítem.
- Se borró un comentario que decía lo contrario de lo que ahora hace el código; se
  reemplazó por uno que explica la medición y referencia el ítem.

## 5. ¿Qué es lo más probable que se rompa?

Que alguien del equipo comercial note que el botón ya no está arriba en el teléfono
y lo lea como una pérdida. No lo es —antes estaba cortado y era intocable—, pero es
el cambio visible y conviene avisarlo.

Lo segundo, más técnico: el selector ahora agrupa `.al-btn--ghost-light` y
`.al-btn--primary` dentro de `.al-header__actions`. Si mañana se agrega un tercer
botón primario al encabezado pensando que se verá en móvil, quedará oculto sin que
sea obvio por qué. El comentario en esa línea lo explica.

Riesgo de regresión en escritorio: nulo. Las reglas viven dentro de
`@media (max-width: 768px)` y se midió que por encima de ese ancho nada cambió.
