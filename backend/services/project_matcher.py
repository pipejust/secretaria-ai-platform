"""A qué proyecto pertenece una reunión, leyendo cómo la presentan.

La versión anterior tenía tres fallos que se sumaban:

1. **Miraba los proyectos de todas las empresas.** `select(Project)` sin
   filtrar por tenant: una sesión de Softnexus podía acabar en el «ANH»
   de otro cliente, y de hecho acabó — cinco veces.
2. **Ganaba la primera mención, no la buena.** Recorría la lista de
   proyectos en orden de id y devolvía el primero cuyo nombre apareciera
   en cualquier punto de la transcripción. Una sesión de First Class que
   nombrara Colpensiones de pasada se iba a Colpensiones.
3. **Comparaba subcadenas.** «Viaja» encaja dentro de «viajar» y
   «Connect» dentro de «connection».

Lo que hace ahora: **escucha la presentación**. En estas reuniones se
dice al principio de qué se va a hablar —«en esta sesión vamos a hablar
de ARENA USC como parte del proyecto de First Class»— y esa frase vale
más que cincuenta menciones sueltas después.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# Cuánto texto se considera «la presentación». Suele bastar con el primer
# minuto: quién soy, de qué proyecto vengo, qué vamos a ver.
INTRO_CHARS = 2500

# Cómo se nombra un proyecto cuando se está declarando, no mencionando.
# El grupo 1 es el nombre.
DECLARACIONES = (
    r"proyecto (?:de |del |la |el )?([\wáéíóúñ' -]{3,40})",
    r"sesi[oó]n (?:de |del |sobre )([\wáéíóúñ' -]{3,40})",
    r"hablar (?:de |del |sobre )([\wáéíóúñ' -]{3,40})",
    r"reuni[oó]n (?:de |del |sobre )([\wáéíóúñ' -]{3,40})",
    r"avances? (?:de |del )([\wáéíóúñ' -]{3,40})",
)

# Formas con las que se nombra un proyecto en las actas y que no coinciden
# con su nombre exacto.
ALIAS_PROYECTO = {
    "firstclass": "First Class",
    "first class": "First Class",
    "casas kali": "Casas Cali",
    "casaskali": "Casas Cali",
    "kaskali": "Casas Cali",
    "emporium": "Emporion",
    "colp": "Colpensiones",
    "mi boleta": "Mi Boleta",
    "miboleta": "Mi Boleta",
    "yo soy fan": "Yo Soy Fan",
    "yosoyfan": "Yo Soy Fan",
    "paseo fincas": "Paseo en fincas",
    "arena usc": "Arena USC",
}

# Nombres demasiado cortos o comunes para fiarse de una mención suelta:
# solo cuentan si aparecen en el título o en una declaración.
AMBIGUOS = {"acten", "viaja", "connect", "telar", "emporion", "anh"}


def _norm(v: str) -> str:
    v = unicodedata.normalize("NFKD", v or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", v.lower())).strip()


def _contiene(aguja: str, pajar: str) -> bool:
    """Con límites de palabra, para que «Viaja» no encaje en «viajar»."""
    if not aguja:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(aguja)}(?![a-z0-9])", pajar) is not None


def emparejar(
    proyectos: Iterable,
    titulo: str,
    transcripcion: str = "",
    resumen: str = "",
) -> Optional[int]:
    """Id del proyecto, o `None` si nada encaja con confianza.

    **`proyectos` debe venir ya filtrado por empresa.** La función no
    sabe de tenants y no puede protegerte de eso.
    """
    proyectos = [p for p in proyectos if getattr(p, "name", None)]
    if not proyectos:
        return None

    t_titulo = _norm(titulo)
    texto = _norm(transcripcion)
    t_intro = texto[:INTRO_CHARS]
    t_resumen = _norm(resumen)

    # Lo que se declara en la presentación: «proyecto de X», «hablar de X».
    declarado: list[str] = []
    for patron in DECLARACIONES:
        for m in re.finditer(patron, t_intro + " " + t_resumen):
            declarado.append(m.group(1).strip())
    texto_declarado = " | ".join(declarado)

    # Cada proyecto se busca por su nombre y por sus alias.
    def agujas(p) -> list[str]:
        n = _norm(p.name)
        out = [n]
        out += [_norm(a) for a, destino in ALIAS_PROYECTO.items()
                if _norm(destino) == n]
        return [a for a in out if a]

    mejor, mejor_punt, mejor_fuerte = None, 0.0, False
    for p in proyectos:
        # **El mejor alias, no la suma de todos.** «First Class» y
        # «firstclass» son la misma palabra dicha de dos formas: sumar
        # ambas puntuaciones duplicaba el resultado y una sesión titulada
        # «Softnexus - Julio 6» acababa en First Class porque el nombre
        # con alias puntuaba el doble que el del propio título.
        punt, fuerte, detalle = 0.0, False, []
        for a in agujas(p):
            sub, sub_fuerte, sub_det = 0.0, False, []
            largo = len(a.split())          # más palabras, más específico

            # El título manda. Quien escribe «Softnexus - Entrega de
            # responsabilidades» ya dijo de qué proyecto habla, y una
            # mención dentro de la charla no debería poder ganarle.
            if _contiene(a, t_titulo):
                sub += 300 + largo * 20
                sub_fuerte = True
                sub_det.append("título")
            if texto_declarado and _contiene(a, _norm(texto_declarado)):
                sub += 120 + largo * 10
                sub_fuerte = True
                sub_det.append("declarado")
            if _contiene(a, t_intro):
                sub += 40 + largo * 5
                sub_fuerte = True
                sub_det.append("presentación")

            if _norm(p.name) not in AMBIGUOS:
                menciones = len(re.findall(
                    rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", texto))
                sub += min(menciones, 10) * 2
                if menciones:
                    sub_det.append(f"×{menciones}")
                if menciones >= 6:
                    # Nombrado a lo largo de toda la reunión: ya no es una
                    # mención de pasada.
                    sub_fuerte = True

            if sub > punt:
                punt, fuerte, detalle = sub, sub_fuerte, sub_det

        # En empate gana el nombre más específico: entre «Telar» y
        # «Telar Support» debe quedarse el segundo si los dos encajan.
        if punt > mejor_punt or (
            punt == mejor_punt and punt > 0 and mejor is not None
            and len(_norm(p.name)) > len(_norm(mejor.name))
        ):
            mejor, mejor_punt, mejor_fuerte = p, punt, fuerte
            logger.debug("proyecto candidato %r → %.0f (%s)", p.name, punt, detalle)

    # Sin una señal fuerte —título, declaración, presentación o muchas
    # menciones— **se prefiere no decidir**. Una sesión sin proyecto la
    # coloca una persona en un minuto; una sesión en el proyecto de otro
    # cliente contamina sus informes y nadie la busca ahí.
    if mejor_punt < 20 or not mejor_fuerte:
        return None
    return mejor.id
