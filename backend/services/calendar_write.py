"""Crear, cambiar, mover y borrar allá cuando se toca aquí.

Cuando alguien elige un calendario conectado como destino de un evento,
el evento se crea allá: es lo que hace que la hora quede ocupada para
quien mire esa agenda desde su móvil, que es justo para lo que se conectó
la cuenta.

Si el proveedor rechaza el evento **no se pierde nada aquí, pero tampoco
se traga**: se anota el motivo en `external_error` y se enseña. Un evento
que aquí se ve normal y allá no existe manda a la gente a una hora que
para el resto está libre, y eso es peor que un error a la cara.

Lo que sigue faltando, dicho para que no sorprenda: **de fuera hacia
dentro no hay conflicto resuelto**. Si alguien cambia en Google un evento
que salió de aquí, la próxima lectura lo descarta y aquí no se entera. Se
descarta a propósito —es la única forma de no duplicarlo— pero significa
que el lado que manda es este.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlmodel import Session

from models import Calendar, CalendarAccount, CalendarEntry
from services.calendar_sync import MODULOS, token_de

logger = logging.getLogger(__name__)


def _conectado(cal: Calendar) -> bool:
    return cal.origin in MODULOS and bool(cal.account_id)


def _payload(entrada: CalendarEntry) -> dict:
    return {
        "title": entrada.title,
        "description": entrada.description,
        "location": entrada.location,
        "start_at": entrada.start_at,
        "end_at": entrada.end_at,
        "all_day": entrada.all_day,
        # La marca que evita el duplicado al releer: el evento vuelve de
        # Google con ella y se descarta en vez de aparecer dos veces.
        "acten_id": entrada.id,
    }


async def _llamar(db: Session, cal: Calendar, accion: str, *args):
    cuenta = db.get(CalendarAccount, cal.account_id)
    if not cuenta:
        raise RuntimeError("El calendario no tiene cuenta conectada.")
    mod = MODULOS[cal.origin]
    token = await token_de(db, cuenta)
    fn = getattr(mod, accion)
    if cal.origin == "zoho":
        return await fn(token, cal.external_id, *args, cuenta.data_center)
    return await fn(token, cal.external_id, *args)


async def crear(db: Session, cal: Calendar, entrada: CalendarEntry) -> None:
    """Lleva el evento al proveedor. Deja el motivo si lo rechaza."""
    if not _conectado(cal):
        return
    if cal.read_only:
        entrada.external_error = (
            "Ese calendario es de solo lectura para esta cuenta: el evento "
            "queda en Acten pero no se creó en el proveedor."
        )
        db.add(entrada); db.commit()
        return
    try:
        uid = await _llamar(db, cal, "crear_evento", _payload(entrada))
        entrada.external_uid = uid or ""
        entrada.external_error = ""
    except Exception as e:  # noqa: BLE001
        entrada.external_error = str(e)[:500]
        logger.warning("No se pudo crear el evento en %s: %s", cal.origin, e)
    db.add(entrada); db.commit()


async def actualizar(db: Session, cal: Calendar, entrada: CalendarEntry) -> None:
    if not _conectado(cal):
        return
    if not entrada.external_uid:
        # Nació sin copia allá —por ejemplo porque el intento anterior
        # falló—; el cambio es la ocasión de crearla.
        await crear(db, cal, entrada)
        return
    try:
        await _llamar(db, cal, "actualizar_evento", entrada.external_uid, _payload(entrada))
        entrada.external_error = ""
    except Exception as e:  # noqa: BLE001
        entrada.external_error = str(e)[:500]
        logger.warning("No se pudo actualizar el evento en %s: %s", cal.origin, e)
    db.add(entrada); db.commit()


async def borrar(db: Session, cal: Calendar, entrada: CalendarEntry) -> None:
    if not _conectado(cal) or not entrada.external_uid:
        return
    try:
        await _llamar(db, cal, "borrar_evento", entrada.external_uid)
    except Exception as e:  # noqa: BLE001
        logger.warning("No se pudo borrar el evento en %s: %s", cal.origin, e)


async def mover(db: Session, origen: Calendar, destino: Calendar,
                entrada: CalendarEntry) -> None:
    """Cambiar de calendario **quita el evento del de origen**.

    Sin esto, mover un evento de Google a la agenda de Acten lo dejaría
    ocupando esa hora en Google para siempre, y nadie volvería a mirar
    ahí para caer en la cuenta.
    """
    if origen.id == destino.id:
        return
    await borrar(db, origen, entrada)
    entrada.external_uid = ""
    entrada.calendar_id = destino.id
    db.add(entrada); db.commit()
    await crear(db, destino, entrada)
