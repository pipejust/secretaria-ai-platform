# DESIGN.md — Acten

> Extraído del código (`frontend/src/styles/tokens.css` y los componentes) el 2026-09-24.
> **Nada de esto se escribe a mano en un componente.** Los valores viven en
> `frontend/tokens/*.json` y `node scripts/tokens-build.mjs` genera `tokens.css`.
> Un color, una tipografía o un espaciado literal en un componente es un error:
> lo detecta `node scripts/lint-no-hardcode.mjs`.

## Tema visual
Sobrio y editorial, no "dashboard de SaaS". Fondo crema, paneles blancos, barra
lateral azul noche casi negra, un solo acento azul profundo y titulares en serif.
La interfaz es una herramienta de trabajo para leer actas largas: prima la
legibilidad y el silencio visual sobre el color.

## Paleta (roles, no nombres de color)
| Rol | Token | Valor |
|---|---|---|
| Fondo de la aplicación | `--color-bg-base` | `#FAF7F2` |
| Panel / tarjeta | `--color-bg-panel` | `#FFFFFF` |
| Superficie elevada | `--color-bg-elevated` | `#FBF9F4` |
| Barra lateral | `--color-bg-sidebar` / `-2` | `#0B1330` / `#152042` |
| Barra superior | `--color-bg-topbar` | `#FFFFFF` |
| Texto principal | `--color-fg-default` | `#111318` |
| Texto secundario | `--color-fg-muted` | `#687280` |
| Texto sobre oscuro | `--color-fg-on-dark` | `#F7F4EE` |
| Acento (acciones) | `--color-accent` / `-hover` | `#002273` / `#001A5C` |
| Éxito | `--color-success` | `#1B7F67` |
| Aviso | `--color-warning` | `#D9A441` |
| Error | `--color-danger` | `#E03D3D` |
| Información | `--color-info` | `#2F6DBE` |
| Borde | `--color-border` / `-strong` | `#E5E7EB` / `#D1D5DB` |
| Foco | `--color-border-focus` | `#2F6DBE` |

**Marca por empresa:** `--brand-primary`, `--brand-secondary` y `--brand-accent` los
reescribe `BrandingService` en tiempo de ejecución con los colores de cada cliente.
Un componente que pinte la marca usa esos tres, nunca los `--brand-*-500` fijos.

## Tipografía
- Titulares: `--font-display` — Playfair Display (serif).
- Interfaz y cuerpo: `--font-sans` — Sora, con Inter y la del sistema de respaldo.
- Código y datos: `--font-mono` — JetBrains Mono.
- Tamaños: escala fija `--text-11` … `--text-72` (en rem). No hay tamaños intermedios.
- Pesos: `--fw-regular` 400 · `--fw-medium` 500 · `--fw-semibold` 600 · `--fw-bold` 700.
- Interlineado: `--lh-display` 1.1 · `--lh-heading` 1.25 · `--lh-body` 1.55 · `--lh-tight` 1.2.
- Espaciado entre letras: `--tracking-tightest` … `--tracking-label` (las etiquetas en
  mayúsculas usan `--tracking-label`).

## Espaciado, radios y profundidad
- Espaciado: escala `--space-0` … `--space-96` (base 4 px). Todo margen y relleno sale de ahí.
- Radios: `--radius-xs` 6px · `--radius-sm`/`--radius-md` 10px · `--radius-lg` 20px ·
  `--radius-xl` 28px · `--radius-full` para píldoras y avatares.
- Sombras: `--shadow-xs` … `--shadow-xl`, todas en el gris de marca (`#111318`) con opacidad baja.
  El foco usa `--shadow-ring-focus`, nunca `outline: none` a secas.
- Movimiento: `--dur-fast` 150ms · `--dur-base` 220ms · `--dur-slow` 360ms, con `--ease-out`
  para entradas y `--ease-in-out` para cambios de estado.

## Layout
- Barra lateral oscura fija a la izquierda con la navegación; barra superior blanca con el
  contexto de la empresa; contenido sobre el fondo crema.
- El contenido vive en paneles blancos con borde `--color-border` y `--shadow-sm`.
- Los componentes viven en `frontend/src/app/components/<nombre>/` con su propio `.css`.
  Lo transversal está en `frontend/src/styles/` (`tokens.css`, `ap-modal.css`, `avatar-tooltip.css`).

## Reglas de sí / no
- **Sí** usar variables CSS en todo: color, tipografía, espaciado, radio, sombra, duración.
- **Sí** dejar respirar: en una pantalla de lectura, antes más espacio que más densidad.
- **No** escribir un hex, un `rgb()`, un `px` suelto de espaciado ni un `font-family` en un componente.
- **No** editar `tokens.css` a mano: es generado y se sobrescribe.
- **No** añadir una librería de componentes ni un framework de CSS: el sistema es propio.
- **No** inventar un color de estado nuevo; si falta un rol, se agrega al token primero.

## Responsive
- Móvil primero en las pantallas de consulta; la barra lateral se colapsa.
- Las tablas de tareas y reuniones se vuelven tarjetas apiladas en pantallas angostas.
- Ningún texto por debajo de `--text-12`.

## Deuda conocida
Los componentes anteriores al sistema de tokens todavía tienen colores quemados y
`frontend/src/styles.css` conserva un bloque de variables viejas (tema oscuro) que convive
con los alias de `tokens.css`. `node scripts/lint-no-hardcode.mjs` los lista. La migración es
incremental: se corrige el componente que se toque, con su propio ítem.
