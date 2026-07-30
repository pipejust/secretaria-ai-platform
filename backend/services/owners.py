"""Quién es el responsable de una tarea — y cuándo no hay ninguno.

El extractor deja un marcador de texto («Por asignar») cuando la reunión
no nombró responsable. Visto desde fuera, eso es **indistinguible de una
persona real que el otro sistema no tiene fichada**, y en un proyecto de
cliente esas son la mayoría.

Consecuencia concreta, que nos reportó el equipo de Servicios: su
pantalla oculta a la gente ajena al proyecto, y la tarea sin dueño se
colaba en ese grupo. Se escondía justo la que más conviene ver.

Vive aquí, y no dentro de un router, porque la misma pregunta se hace en
tres sitios —la lista de pendientes, la API pública y el calendario— y
tres copias del literal es una que alguien traducirá algún día.
"""

from __future__ import annotations

from typing import Any, Optional

# Marcadores en cualquiera de los idiomas que produce el extractor. Se
# comparan en minúsculas y sin espacios sobrantes.
SIN_RESPONSABLE = frozenset({
    "por asignar", "sin asignar", "no asignado", "sin responsable",
    "por definir", "sin definir", "pendiente", "todos", "equipo",
    # Estos aparecieron en producción **después** de escribir la lista:
    # seis tareas decían «No especificado» y se contaban como si fueran
    # una persona con ese nombre. El extractor inventa variantes nuevas,
    # así que la lista se amplía cuando los datos enseñan una.
    "no especificado", "no especificada", "no proporcionado",
    "no proporcionada", "no definido", "no definida", "sin asignar aún",
    "desconocido", "desconocida", "varios", "n.a.", "ninguno",
    "n/a", "na", "-", "--", "?",
    "unassigned", "tbd", "to be assigned", "nobody", "none", "team",
    "not specified", "not provided", "unknown",
    "per assignar", "sense assignar", "no especificat",
})


def es_marcador(valor: Optional[str]) -> bool:
    """¿Ese texto es un marcador de «nadie», no un nombre?"""
    return (valor or "").strip().lower() in SIN_RESPONSABLE


def tiene_responsable(nombre: Optional[str], email: Optional[str]) -> bool:
    """¿Hay una persona detrás de esta tarea?

    El nombre decide. El marcador se cuela también en el correo —en
    producción hay filas con `owner_email = "Por asignar"`— así que mirar
    el correo primero devolvería «sí» para una tarea que no tiene dueño.
    """
    if es_marcador(nombre):
        return False
    correo = (email or "").strip()
    if correo and not es_marcador(correo):
        return True
    return bool((nombre or "").strip())


def limpiar(valor: Optional[str]) -> Optional[str]:
    """El texto, o `None` si era un marcador o estaba vacío."""
    v = (valor or "").strip()
    return None if (not v or es_marcador(v)) else v
