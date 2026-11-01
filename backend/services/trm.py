"""Tasa de cambio USD→COP: la TRM oficial que publica el Banco de la República.

Fuente: datos abiertos del Gobierno de Colombia (dataset 32sa-8pi3), sin llave.
Los precios están en USD; los de COP (lo que cobra Wompi) se derivan de la TRM
vigente y se refrescan cada día.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from sqlmodel import Session, select

from models import AddOn, Plan

logger = logging.getLogger(__name__)

TRM_URL = "https://www.datos.gov.co/resource/32sa-8pi3.json"
# Redondeo de los precios en COP: a miles de pesos (los centavos de COP son ×100).
REDONDEO_CENTAVOS = 1_000 * 100


class TrmNoDisponible(RuntimeError):
    pass


def obtener_trm(cliente: httpx.Client | None = None) -> float:
    """TRM vigente hoy (hora de Bogotá). Lanza TrmNoDisponible si la fuente falla."""
    hoy = datetime.now(ZoneInfo("America/Bogota")).strftime("%Y-%m-%dT00:00:00")
    params = {"$where": f"vigenciadesde <= '{hoy}'", "$order": "vigenciadesde DESC", "$limit": "1"}
    try:
        if cliente is None:
            with httpx.Client(timeout=20) as propio:
                resp = propio.get(TRM_URL, params=params)
        else:
            resp = cliente.get(TRM_URL, params=params)
        resp.raise_for_status()
        valor = float(resp.json()[0]["valor"])
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise TrmNoDisponible(f"No se pudo leer la TRM: {exc}") from exc
    if not 1000 < valor < 10000:  # fuera de lo razonable: no se usa
        raise TrmNoDisponible(f"TRM fuera de rango: {valor}")
    return valor


def cop_cents(usd_cents: int, trm: float) -> int:
    """Precio en centavos de COP, redondeado a miles de pesos."""
    if usd_cents <= 0:
        return 0
    return round(usd_cents * trm / REDONDEO_CENTAVOS) * REDONDEO_CENTAVOS


def actualizar_precios_cop(db: Session, trm: float | None = None) -> float | None:
    """Recalcula el precio en COP de planes y add-ons con la TRM. Sin TRM, no toca nada."""
    try:
        trm = trm if trm is not None else obtener_trm()
    except TrmNoDisponible as exc:
        logger.warning("%s; se conservan los precios en COP actuales.", exc)
        return None
    for fila in [*db.exec(select(Plan)).all(), *db.exec(select(AddOn)).all()]:
        nuevo = cop_cents(fila.price_usd_cents, trm)
        if fila.price_usd_cents > 0 and fila.price_cop_cents != nuevo:
            fila.price_cop_cents = nuevo
            db.add(fila)
    db.commit()
    logger.info("Precios en COP actualizados con la TRM de %s.", trm)
    return trm
