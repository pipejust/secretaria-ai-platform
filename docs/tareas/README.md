# docs/tareas — foto del backlog, con fecha y hora

Cada archivo de esta carpeta es **una foto completa de las tareas del proyecto** en
el momento en que se tomó. No se edita una foto vieja: se agrega una nueva.

## Nombre

`tareas-AAMMDD-HHMM.csv` — por ejemplo `tareas-260925-1120.csv` es la del
25 de septiembre de 2026 a las 11:20.

La más reciente, por orden alfabético, es siempre la última. La vigente es esa; las
anteriores quedan para ver cómo cambió el backlog en el tiempo.

## Formato

Las **9 columnas** de `METODOLOGIA.md` §1, en este orden y sin ninguna más:

`Título` · `Responsable` · `Descripción` · `DoR` · `Criterios de aceptación` · `DoD` · `Riesgo` · `Tamaño` · `Frente`

- Una fila por tarea, primera fila de encabezados.
- Comillas en todos los campos, UTF-8 **con BOM** (para que Excel respete las tildes).
- Sin columna de estado: el estado vive en Altum y en `/sn-status`, no acá. Una
  columna de estado en un archivo se desactualiza el mismo día que se escribe.

## Cuándo se toma una foto nueva

Cuando cambia el backlog de verdad: entran tareas nuevas, se cierra un grupo, o
cambia el riesgo o el tamaño de varias. No hace falta una por día.

## De dónde sale

Del contenido de `docs/items/*.md`, que es la fuente. El CSV es una vista para
leerlo fuera del repositorio (Excel, o cargarlo en Altum). Si los dos no coinciden,
manda `docs/items/`.

No se genera solo: `DoR` y `DoD` no están en la ficha del ítem y se escriben
pensando en cada tarea. Un generador automático los inventaría, que es peor que no
tenerlos.
