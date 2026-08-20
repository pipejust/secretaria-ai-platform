"""Los festivos de Colombia, calculados. No hay tabla y es a propósito.

Tener una tabla con ellos sería duplicar la fuente y garantizar que un
día no coincidan: alguien crea 2027 a mano, se equivoca en uno, y el
calendario dice que se trabaja un lunes que no.

Dos reglas:

- Los de fecha fija se quedan donde están (1 de enero, 1 de mayo, 20 de
  julio, 7 de agosto, 8 de diciembre, 25 de diciembre).
- El resto se corre al lunes siguiente — es la Ley Emiliani, de 1983—, y
  los que dependen de la Pascua se cuentan desde el Domingo de Ramos.
"""

from __future__ import annotations

from datetime import date, timedelta

# Fijos: día y mes exactos, no se mueven.
FIJOS = [
    (1, 1, "Año Nuevo"),
    (5, 1, "Día del Trabajo"),
    (7, 20, "Grito de Independencia"),
    (8, 7, "Batalla de Boyacá"),
    (12, 8, "Inmaculada Concepción"),
    (12, 25, "Navidad"),
]

# Se corren al lunes siguiente (Ley Emiliani).
TRASLADABLES = [
    (1, 6, "Reyes Magos"),
    (3, 19, "San José"),
    (6, 29, "San Pedro y San Pablo"),
    (8, 15, "Asunción de la Virgen"),
    (10, 12, "Día de la Raza"),
    (11, 1, "Todos los Santos"),
    (11, 11, "Independencia de Cartagena"),
]

# Cuántos días desde el Domingo de Pascua. Los tres últimos se trasladan.
DESDE_PASCUA = [
    (-3, "Jueves Santo", False),
    (-2, "Viernes Santo", False),
    (43, "Ascensión de Jesús", True),
    (64, "Corpus Christi", True),
    (71, "Sagrado Corazón", True),
]


def pascua(anio: int) -> date:
    """Domingo de Pascua por el algoritmo de Butcher (calendario gregoriano)."""
    a = anio % 19
    b, c = divmod(anio, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes, dia = divmod(h + l - 7 * m + 114, 31)
    return date(anio, mes, dia + 1)


def _al_lunes(d: date) -> date:
    """El lunes de esa semana o el siguiente. Si ya es lunes, se queda."""
    return d + timedelta(days=(7 - d.weekday()) % 7)


def del_anio(anio: int) -> list[tuple[date, str]]:
    salida = [(date(anio, m, d), n) for m, d, n in FIJOS]
    salida += [(_al_lunes(date(anio, m, d)), n) for m, d, n in TRASLADABLES]
    p = pascua(anio)
    for offset, nombre, traslada in DESDE_PASCUA:
        cuando = p + timedelta(days=offset)
        salida.append((_al_lunes(cuando) if traslada else cuando, nombre))
    return sorted(salida)


def en_rango(desde: date, hasta: date) -> list[tuple[date, str]]:
    salida: list[tuple[date, str]] = []
    for anio in range(desde.year, hasta.year + 1):
        salida += [(d, n) for d, n in del_anio(anio) if desde <= d <= hasta]
    return sorted(salida)
