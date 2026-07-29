"""`due_date` es fecha ISO o es NULL.

La regresión que motiva estos tests: el extractor guardó frases del LLM
(«No especificada», «No proporcionado») en una columna de texto, y el
calendario las trató como tareas con fecha.
"""

from __future__ import annotations

import pytest

from date_utils import is_valid_due_date, normalize_due_date


# Lo que el extractor viejo llegó a escribir en producción.
TEXTO_DEL_LLM = [
    "No especificada",
    "No especificado",
    "No proporcionado",
    "No se proporcionó",
    "próxima semana",
    "TBD",
]


@pytest.mark.parametrize("valor", TEXTO_DEL_LLM)
def test_texto_libre_del_llm_no_es_fecha(valor):
    assert normalize_due_date(valor) is None
    assert is_valid_due_date(valor) is False


@pytest.mark.parametrize("valor,esperado", [
    ("2026-07-28", "2026-07-28"),
    ("  2026-07-28  ", "2026-07-28"),
    # La hora vive en `due_time`, así que el sufijo horario se descarta.
    ("2026-07-28T10:30", "2026-07-28"),
    ("2026-07-28 10:30:00", "2026-07-28"),
    # Cola de basura pegada a una fecha buena: nos quedamos con la fecha.
    ("2026-07-28 (aprox)", "2026-07-28"),
])
def test_fechas_iso_se_normalizan_a_solo_fecha(valor, esperado):
    assert normalize_due_date(valor) == esperado


@pytest.mark.parametrize("valor", [
    "2026-07-28T10:30",
    "2026-07-28 10:30:00",
    "2026-07-28 (aprox)",
    "2026-07-283",
])
def test_input_explicito_no_acepta_nada_pegado_a_la_fecha(valor):
    """`normalize_due_date` recorta, pero validar input no puede recortar.

    Aceptar `2026-07-28T15:00` en un POST y guardar sólo la fecha tiraría
    las 15:00 sin avisar. La hora se manda en `due_time`.
    """
    assert normalize_due_date(valor) is not None   # sí se puede rescatar
    assert is_valid_due_date(valor) is False       # pero no se acepta callado


@pytest.mark.parametrize("valor", ["2026-07-28", "  2026-07-28  "])
def test_input_explicito_acepta_la_fecha_pelada(valor):
    assert is_valid_due_date(valor) is True


@pytest.mark.parametrize("valor", [
    "28-07-2026",   # formato invertido
    "2026-7-8",     # sin cero a la izquierda
    "2026-02-31",   # día que no existe
    "2026-13-01",   # mes que no existe
    "2026",
])
def test_fechas_mal_formadas_se_rechazan(valor):
    assert normalize_due_date(valor) is None
    assert is_valid_due_date(valor) is False


@pytest.mark.parametrize("vacio", [None, "", "   "])
def test_vacio_es_sin_fecha_y_es_legitimo(vacio):
    """Vacío significa «sin fecha»: se guarda NULL y no es un error."""
    assert normalize_due_date(vacio) is None
    assert is_valid_due_date(vacio) is True
