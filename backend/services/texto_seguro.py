"""Texto que Postgres acepta guardar.

Un modelo puede devolver un byte NUL en mitad de una frase. Postgres
rechaza `\\x00` en cualquier columna de texto, y como el fallo salta al
hacer *flush*, **tumba la transacción entera**: la sesión 822 perdió sus
campos y, de rebote, sus tareas, porque el paso siguiente ya encontró la
transacción muerta.

Es texto generado; no hay nada que preservar en un carácter de control.
"""

from __future__ import annotations

import re

# NUL y los demás controles C0 que no aportan nada. Se conservan salto de
# línea, retorno y tabulador, que sí significan algo en un acta.
_CONTROLES = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def limpiar_texto(v):
    """Devuelve el texto sin caracteres que la base no admite."""
    if v is None:
        return v
    if not isinstance(v, str):
        v = str(v)
    return _CONTROLES.sub("", v)
