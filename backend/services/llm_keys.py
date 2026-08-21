"""Las llaves de los motores de IA: de la base primero, del entorno si no.

Hasta ahora la llave de Groq solo salía de `GROQ_API_KEY`. Eso obliga a
entrar al servidor y reiniciar el contenedor para cambiarla, y hace que
todas las empresas compartan la misma —que es exactamente lo que impide
saber de dónde sale cada peso de la factura—.

Ahora cada empresa puede poner la suya desde Configuración. El orden es:

1. La que el administrador guardó para **esa empresa**, cifrada en
   `integrationsetting`.
2. Si no hay, la del entorno, que sigue sirviendo de respaldo para toda
   la instalación.

**Nunca se cae en la llave de otra empresa.** Sin `tenant_id` se usa
directamente la del entorno: usar la del primer tenant que apareciera
haría que una empresa gastara contra la cuenta de otra.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

from sqlmodel import Session, select

from config import settings
from database import engine
from models import IntegrationSetting
from services.cifrado import cifrar, descifrar, enmascarar

logger = logging.getLogger(__name__)

# Nombre de la fila en `integrationsetting` (per-tenant: user_id IS NULL).
PROVEEDORES = {
    "groq": {"clave": "groq", "etiqueta": "Groq", "env": "groq_api_key"},
}

# La llave se pide en cada llamada al LLM. Sin caché eso es una consulta
# por llamada; con TTL corto, un cambio en pantalla se nota enseguida.
_CACHE_SEGUNDOS = 60
_cache: dict[tuple[str, Optional[int]], tuple[float, str]] = {}


def _del_entorno(proveedor: str) -> str:
    return getattr(settings, PROVEEDORES[proveedor]["env"], "") or ""


def _fila(db: Session, tenant_id: int, proveedor: str) -> Optional[IntegrationSetting]:
    return db.exec(
        select(IntegrationSetting)
        .where(IntegrationSetting.tenant_id == tenant_id)
        .where(IntegrationSetting.provider_name == PROVEEDORES[proveedor]["clave"])
        .where(IntegrationSetting.user_id == None)  # noqa: E711
    ).first()


def clave(proveedor: str, tenant_id: Optional[int] = None) -> str:
    """La llave que toca usar. Cadena vacía si no hay ninguna."""
    if proveedor not in PROVEEDORES:
        raise ValueError(f"Proveedor desconocido: {proveedor}")
    if tenant_id is None:
        return _del_entorno(proveedor)

    ahora = time.time()
    guardada = _cache.get((proveedor, tenant_id))
    if guardada and ahora - guardada[0] < _CACHE_SEGUNDOS:
        return guardada[1]

    valor = ""
    try:
        with Session(engine) as db:
            fila = _fila(db, tenant_id, proveedor)
            if fila and fila.is_active:
                datos = json.loads(fila.config_json or "{}")
                valor = descifrar(datos.get("api_key", "") or "")
    except Exception as exc:  # noqa: BLE001
        # Que la base falle no puede dejar sin IA a quien tenía la llave en
        # el entorno: se avisa y se sigue con el respaldo.
        logger.warning("No se pudo leer la llave de %s en la base: %s", proveedor, exc)

    valor = valor or _del_entorno(proveedor)
    _cache[(proveedor, tenant_id)] = (ahora, valor)
    return valor


def clave_groq(tenant_id: Optional[int] = None) -> str:
    return clave("groq", tenant_id)


def guardar(db: Session, tenant_id: int, proveedor: str, api_key: str) -> dict:
    """Guarda la llave cifrada. Vacía = borra la de la empresa y vuelve al entorno."""
    if proveedor not in PROVEEDORES:
        raise ValueError(f"Proveedor desconocido: {proveedor}")
    fila = _fila(db, tenant_id, proveedor)
    limpia = (api_key or "").strip()

    if not limpia:
        if fila:
            db.delete(fila)
            db.commit()
        _cache.pop((proveedor, tenant_id), None)
        return estado(db, tenant_id, proveedor)

    if not fila:
        fila = IntegrationSetting(
            tenant_id=tenant_id, provider_name=PROVEEDORES[proveedor]["clave"],
            config_json="{}", is_active=True,
        )
    fila.config_json = json.dumps({"api_key": cifrar(limpia)})
    fila.is_active = True
    db.add(fila)
    db.commit()
    _cache.pop((proveedor, tenant_id), None)
    return estado(db, tenant_id, proveedor)


def estado(db: Session, tenant_id: int, proveedor: str) -> dict:
    """Lo que se puede enseñar en pantalla. La llave nunca sale en claro."""
    fila = _fila(db, tenant_id, proveedor)
    propia = ""
    if fila and fila.is_active:
        try:
            propia = descifrar(json.loads(fila.config_json or "{}").get("api_key", ""))
        except (json.JSONDecodeError, TypeError):
            propia = ""
    del_entorno = _del_entorno(proveedor)
    return {
        "proveedor": proveedor,
        "etiqueta": PROVEEDORES[proveedor]["etiqueta"],
        "configurada": bool(propia or del_entorno),
        "origen": "empresa" if propia else ("entorno" if del_entorno else ""),
        "pista": enmascarar(propia or del_entorno),
        # Para que se entienda por qué sigue funcionando sin haber puesto nada.
        "hay_respaldo_de_entorno": bool(del_entorno),
    }
