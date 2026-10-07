"""El análisis no se da por bueno si vuelve sin decisiones, acuerdos ni riesgos."""

from __future__ import annotations

import asyncio

from services import transcript_pipeline as tp

LARGA = "Ana: revisamos el cronograma de la obra y los pagos pendientes. " * 20
COMPLETO = {"themes": [{"theme_name": "Obra"}], "decisions": "- **[Obra]** Seguir — Responsable: Ana",
            "agreements": "- Sin elementos relevantes en esta reunión.", "risks": ["Retraso en pagos", " "]}
VACIO = {"themes": [{"theme_name": "Obra"}], "decisions": "", "agreements": "  ", "risks": None}


class Modelo:
    def __init__(self, respuestas):
        self.respuestas, self.llamadas = list(respuestas), 0

    async def process_fundamentals_and_insights(self, transcript, contacts, output_language=None):
        self.llamadas += 1
        return self.respuestas[min(self.llamadas, len(self.respuestas)) - 1]


def pedir(modelo, transcript=LARGA):
    return asyncio.run(tp.insights_con_acta(modelo, transcript, [], "es", etiqueta="prueba"))


def test_se_repite_cuando_el_acta_vuelve_vacia():
    modelo = Modelo([VACIO, COMPLETO])
    r = pedir(modelo)
    assert modelo.llamadas == 2 and not tp._acta_vacia(r)
    # Una lista del modelo se guarda como viñetas, sin los elementos en blanco.
    assert tp._texto_acta(r["risks"]) == "- Retraso en pagos"


def test_si_sigue_vacia_se_devuelve_tal_cual_para_marcarla_incompleta():
    modelo = Modelo([VACIO])
    r = pedir(modelo)
    assert modelo.llamadas == 1 + tp._REINTENTOS_ACTA_VACIA and tp._acta_vacia(r)


def test_una_respuesta_completa_o_una_reunion_minima_no_se_repiten():
    completo = Modelo([COMPLETO])
    pedir(completo)
    assert completo.llamadas == 1
    saludo = Modelo([VACIO])
    pedir(saludo, "Hola, prueba de micrófono.")
    assert saludo.llamadas == 1


class ModeloConActa(Modelo):
    """Como el de producción: en transcripciones largas omite el acta, pero la da si se pide sola."""

    def __init__(self, respuestas, acta):
        super().__init__(respuestas)
        self.acta, self.pedidas_acta = acta, 0

    async def acta_only(self, transcript, output_language=None):
        self.pedidas_acta += 1
        return self.acta


def test_si_falta_el_acta_se_pide_por_separado_sin_repetir_todo():
    sin_claves = {"themes": [{"theme_name": "Obra"}], "attendees": []}
    modelo = ModeloConActa([sin_claves], {"decisions": "- **[Obra]** Seguir — Responsable: Ana",
                                          "risks": "- Sin elementos relevantes en esta reunión.", "agreements": "- Pagar"})
    r = pedir(modelo)
    assert modelo.llamadas == 1 and modelo.pedidas_acta == 1
    assert r["themes"] and r["decisions"].startswith("- **[Obra]**") and not tp._acta_vacia(r)


def test_si_el_acta_separada_tambien_vuelve_vacia_se_repite_como_antes():
    modelo = ModeloConActa([VACIO], {})
    r = pedir(modelo)
    assert modelo.pedidas_acta == 1 and modelo.llamadas == 1 + tp._REINTENTOS_ACTA_VACIA and tp._acta_vacia(r)
