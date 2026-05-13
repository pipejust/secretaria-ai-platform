# assets/design-source

Coloca aquí los screenshots del nuevo diseño antes de ejecutar el rediseño UI.

## Convención de nombres

```
<seccion>__<estado>__<bp?>.png
```

Ejemplos:
- `dashboard__default__1440.png`
- `curation__editing__1024.png`
- `ask__empty__360.png`
- `login__error__768.png`

Formatos aceptados: `.png`, `.jpg`, `.jpeg`, `.webp`, `.fig` (export PNG si es Figma).

## Validación

`bash orchestration/scripts/05_redesign_ui.sh` falla si esta carpeta está vacía.

## Notas

- Si tu screenshot incluye múltiples breakpoints, divídelos en archivos separados; el agente
  no infiere bordes de breakpoint.
- Si tienes design tokens exportados (JSON de Figma Tokens / Style Dictionary), déjalos
  como `tokens.json` en esta misma carpeta y el agente los respetará por encima de la inferencia visual.
