"""Normalización de fechas que entran a la base de datos.

`ActionItem.due_date` es una columna de texto, así que nada impide guardar
lo que sea. Históricamente el extractor escribió ahí frases del LLM («No
especificada», «No proporcionado»), y esas filas se colaron al calendario
como si fueran tareas con fecha. La regla es simple y vive aquí para que
todos los puntos de escritura la compartan:

    o es `YYYY-MM-DD` de verdad, o es NULL.

La hora vive aparte en `due_time`, así que cualquier sufijo horario que
venga pegado a la fecha se descarta al normalizar.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

# Fecha ISO al inicio del valor: tolera «2026-07-28T10:00» y se queda con
# los primeros 10 caracteres.
_ISO_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}")


def normalize_due_date(value: Any) -> Optional[str]:
    """Devuelve `YYYY-MM-DD` si `value` es una fecha real, si no `None`.

    Rechaza texto libre, fechas mal formadas y días inexistentes
    (`2026-02-31`). Nunca lanza: los llamadores deciden si un `None` es
    aceptable (extractor IA) o merece un 422 (input humano o de API).
    """
    if value is None:
        return None

    texto = str(value).strip()
    if not texto or not _ISO_DATE_PREFIX.match(texto):
        return None

    solo_fecha = texto[:10]
    try:
        date.fromisoformat(solo_fecha)
    except ValueError:
        return None
    return solo_fecha


def is_valid_due_date(value: Any) -> bool:
    """True si `value` está vacío o es una fecha ISO válida.

    Pensado para validar input explícito: un campo vacío significa «sin
    fecha» y es legítimo, pero un texto que no es fecha es un error del
    llamador y no debe guardarse en silencio.
    """
    if value is None or not str(value).strip():
        return True
    return normalize_due_date(value) is not None
