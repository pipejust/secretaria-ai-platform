"""Las llaves de los motores de IA. Solo de la base, y solo por empresa.

La llave sale **únicamente** de lo que un administrador guardó para su
empresa, cifrado en `integrationsetting`. `GROQ_API_KEY` ya no se lee.

El respaldo del entorno existió mientras se migraba y se quitó a
propósito: era una llave compartida por todas las empresas de la
instalación, y mientras estuviera ahí el consumo seguía llegando
mezclado en una sola factura sin forma de separarlo. Además hacía que
una empresa sin llave pareciera funcionar, escondiendo que estaba
gastando contra la cuenta de otro.

Consecuencia asumida: **una empresa sin llave no tiene IA**. El
procesamiento y las preguntas devuelven 503 diciendo dónde ponerla, que
es preferible a gastar en silencio contra una cuenta ajena.

**Nunca se hereda la llave de otra empresa.** Sin `tenant_id` no hay
llave: usar la del primer tenant que apareciera sería exactamente el
gasto cruzado que esto viene a eliminar.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

from sqlmodel import Session, select

from database import engine
from models import IntegrationSetting
from services.cifrado import cifrar, descifrar, enmascarar

logger = logging.getLogger(__name__)

# Nombre de la fila en `integrationsetting` (per-tenant: user_id IS NULL).
PROVEEDORES = {
    "groq": {"clave": "groq", "etiqueta": "Groq"},
}

# La llave se pide en cada llamada al LLM. Sin caché eso es una consulta
# por llamada; con TTL corto, un cambio en pantalla se nota enseguida.
_CACHE_SEGUNDOS = 60
_cache: dict[tuple[str, Optional[int]], tuple[float, str]] = {}


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
        # Sin empresa no hay llave. Antes se caía en la del entorno; eso
        # es lo que permitía que un camino sin tenant gastara contra una
        # cuenta que nadie eligió.
        return ""

    ahora = time.time()
    guardada = _cache.get((proveedor, tenant_id))
    if guardada and ahora - guardada[0] < _CACHE_SEGUNDOS:
        return guardada[1]

    try:
        with Session(engine) as db:
            fila = _fila(db, tenant_id, proveedor)
            valor = ""
            if fila and fila.is_active:
                datos = json.loads(fila.config_json or "{}")
                valor = descifrar(datos.get("api_key", "") or "")
    except Exception as exc:  # noqa: BLE001
        # Si la base falla no hay de dónde sacarla: se avisa fuerte, porque
        # el síntoma que verá la gente —«sin llave»— no apunta a la base.
        #
        # Y **no se guarda en caché**: cachear el vacío convertiría un
        # tropiezo de un segundo en un minuto entero sin IA, y el reintento
        # siguiente lo habría resuelto solo.
        logger.error("No se pudo leer la llave de %s en la base: %s", proveedor, exc)
        return _cache.get((proveedor, tenant_id), (0.0, ""))[1]

    _cache[(proveedor, tenant_id)] = (ahora, valor)
    return valor


def clave_groq(tenant_id: Optional[int] = None) -> str:
    return clave("groq", tenant_id)


def guardar(db: Session, tenant_id: int, proveedor: str, api_key: str) -> dict:
    """Guarda la llave cifrada. Vacía la borra, y la empresa se queda sin IA."""
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
    return {
        "proveedor": proveedor,
        "etiqueta": PROVEEDORES[proveedor]["etiqueta"],
        "configurada": bool(propia),
        "origen": "empresa" if propia else "",
        "pista": enmascarar(propia),
    }
