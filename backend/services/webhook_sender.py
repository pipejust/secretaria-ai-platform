"""Emisor de webhooks Acten → plataforma de Servicios.

Los eventos son **señal, no transporte de datos**: avisan que algo pasó y
el otro lado refresca por API. Así no hay dos copias que puedan divergir.

Contrato (`docs/INTEGRACION_ACTEN_RRHH.md` §8):

* Firma HMAC-SHA256 sobre **`{timestamp}.{cuerpo crudo}`**, no solo el
  cuerpo: firmando solo el cuerpo, alterar la marca de tiempo no
  invalidaría la firma y la protección anti-reenvío no serviría de nada.
* **El secreto lo fija quien verifica** — ellos reciben, así que va el
  suyo. El que generamos nosotros se descartó.
* `X-Acten-Event-Id` para que puedan descartar duplicados: los reintentos
  son normales, procesar dos veces «tarea creada» no lo es.
* Rechazan eventos de más de 5 minutos → el reloj debe ir por NTP.
* Reintentos solo ante `503`/timeout. Un `400`/`401`/`403` es error
  nuestro y reintentarlo solo genera ruido.

Todo es **best-effort**: si el envío falla, se registra y se sigue. Un
webhook caído nunca puede tumbar el procesamiento de una sesión.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

TIMEOUT = 15.0
MAX_ATTEMPTS = 3
# Ellos rechazan lo que llegue con más de 5 min; no tiene sentido
# reintentar más allá de esa ventana.
MAX_RETRY_WINDOW_S = 240

EVENTS = (
    "session.processed", "session.failed",
    "task.created", "task.updated", "task.deleted",
    "comment.created",
)


def _config(tenant_id: Optional[int] = None) -> tuple[str, str, str]:
    """(url, secreto de firma, api key). Vacíos ⇒ emisor inactivo.

    Primero la configuración guardada de esa empresa —la que deja el
    emparejamiento— y si no la hay, las variables de entorno. El entorno
    ata el despliegue entero a **una** plataforma conectada; la fila en
    base permite una por empresa, que es lo que hace falta en cuanto hay
    un segundo cliente.
    """
    if tenant_id is not None:
        try:
            from sqlmodel import Session as _S, select as _sel
            from database import engine as _engine
            from models import OutboundIntegration as _OI
            with _S(_engine) as db:
                fila = db.exec(
                    _sel(_OI)
                    .where(_OI.tenant_id == tenant_id)
                    .where(_OI.is_active == True)  # noqa: E712
                ).first()
            if fila and fila.webhook_url and fila.webhook_secret:
                return (
                    fila.webhook_url.strip(),
                    fila.webhook_secret.strip(),
                    fila.remote_api_key.strip(),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("no se pudo leer la integración del tenant %s: %s", tenant_id, exc)
    return (
        os.getenv("SERVICIOS_WEBHOOK_URL", "").strip(),
        os.getenv("SERVICIOS_WEBHOOK_SECRET", "").strip(),
        os.getenv("SERVICIOS_API_KEY", "").strip(),
    )


def is_configured(tenant_id: Optional[int] = None) -> bool:
    url, secret, key = _config(tenant_id)
    # La clave de API es opcional: hay plataformas que verifican solo la
    # firma. Sin URL o sin secreto no hay envío posible.
    return bool(url and secret)


# El emisor se configura por entorno, así que dispararía para **todos** los
# tenants del despliegue. Un evento de otro cliente le filtraría a Servicios
# el título de una reunión que no es suya; se acota al tenant integrado.
_TENANT_ID_CACHE: dict[str, Optional[int]] = {}


def _tiene_integracion_propia(tenant_id: int) -> bool:
    """¿Esa empresa tiene su propio destino configurado?"""
    try:
        from sqlmodel import Session as _S, select as _sel
        from database import engine as _engine
        from models import OutboundIntegration as _OI
        with _S(_engine) as db:
            fila = db.exec(
                _sel(_OI)
                .where(_OI.tenant_id == tenant_id)
                .where(_OI.is_active == True)  # noqa: E712
            ).first()
        return bool(fila and fila.webhook_url and fila.webhook_secret)
    except Exception:  # noqa: BLE001
        return False


def allowed_tenant_id() -> Optional[int]:
    """Id del único tenant cuyos eventos se emiten. `None` = sin acotar."""
    slug = os.getenv("SERVICIOS_TENANT_SLUG", "softnexus").strip()
    if not slug:
        return None
    if slug in _TENANT_ID_CACHE:
        return _TENANT_ID_CACHE[slug]
    try:
        from sqlmodel import Session as _S, select as _sel
        from database import engine as _engine
        from models import Tenant as _T
        with _S(_engine) as db:
            t = db.exec(_sel(_T).where(_T.slug == slug)).first()
        _TENANT_ID_CACHE[slug] = t.id if t else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("no se pudo resolver el tenant '%s': %s", slug, exc)
        return None
    return _TENANT_ID_CACHE[slug]


def sign(secret: str, timestamp: int | str, raw_body: bytes) -> str:
    """HMAC-SHA256 sobre `{timestamp}.{cuerpo crudo}`."""
    message = f"{timestamp}.".encode() + raw_body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


async def send_event(
    event_type: str,
    data: dict[str, Any],
    *,
    event_id: Optional[str] = None,
    tenant_id: Optional[int] = None,
) -> bool:
    """Envía un evento. Devuelve True si lo aceptaron.

    Nunca lanza: los fallos se registran. El llamador no debe depender
    del resultado para continuar su trabajo.
    """
    url, secret, api_key = _config(tenant_id)
    if not (url and secret):
        logger.debug("webhook %s omitido: emisor no configurado", event_type)
        return False
    if event_type not in EVENTS:
        logger.warning("webhook: tipo desconocido '%s' — se envía igual", event_type)

    eid = event_id or str(uuid.uuid4())
    body = {"type": event_type, "event_id": eid, **data}
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    started = time.time()
    for attempt in range(1, MAX_ATTEMPTS + 1):
        # La marca de tiempo se recalcula en cada intento: si el primero
        # tardó, reenviar la vieja caería fuera de su ventana de 5 min.
        ts = int(time.time())
        headers = {
            "Content-Type": "application/json",
            "X-Acten-Signature": f"sha256={sign(secret, ts, raw)}",
            "X-Acten-Timestamp": str(ts),
            "X-Acten-Event-Id": eid,
        }
        if api_key:
            headers["X-API-Key"] = api_key
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as c:
                r = await c.post(url, content=raw, headers=headers)
        except Exception as exc:  # noqa: BLE001
            logger.warning("webhook %s intento %s: red/timeout (%s)",
                           event_type, attempt, exc)
            if time.time() - started > MAX_RETRY_WINDOW_S:
                break
            await asyncio.sleep(2 ** attempt)
            continue

        if r.status_code == 202:
            try:
                estado = (r.json() or {}).get("status", "")
            except Exception:
                estado = ""
            # `duplicate` es éxito: ya lo habían recibido.
            logger.info("webhook %s aceptado (%s) event_id=%s",
                        event_type, estado or "accepted", eid)
            return True

        if r.status_code in (400, 401, 403):
            # Error nuestro (firma, cabecera o alcance). Reintentar no lo
            # arregla y solo genera ruido en su registro.
            logger.error("webhook %s rechazado %s: %s — NO se reintenta",
                         event_type, r.status_code, r.text[:200])
            return False

        logger.warning("webhook %s intento %s → HTTP %s",
                       event_type, attempt, r.status_code)
        if time.time() - started > MAX_RETRY_WINDOW_S:
            break
        await asyncio.sleep(2 ** attempt)

    logger.error("webhook %s agotó reintentos (event_id=%s)", event_type, eid)
    return False


def send_event_bg(
    event_type: str, data: dict[str, Any], *, tenant_id: Optional[int] = None,
) -> None:
    """Dispara el envío sin bloquear al llamador.

    Si hay bucle de eventos corriendo, agenda la tarea; si no (código
    síncrono, cron), lo ejecuta en uno propio. Nunca propaga errores.

    `tenant_id` acota el envío: si no es el tenant integrado, se descarta
    en silencio. Omitirlo mantiene el comportamiento antiguo.
    """
    if not is_configured(tenant_id):
        return
    if tenant_id is not None and not _tiene_integracion_propia(tenant_id):
        # Sin fila propia se usa la configuración del entorno, que apunta a
        # una sola plataforma: hay que comprobar que el evento sea de esa
        # empresa. Con fila propia el destino ya es el suyo por definición.
        permitido = allowed_tenant_id()
        if permitido is not None and tenant_id != permitido:
            logger.debug(
                "webhook %s omitido: tenant %s no es el integrado",
                event_type, tenant_id,
            )
            return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    try:
        if loop and loop.is_running():
            loop.create_task(send_event(event_type, data, tenant_id=tenant_id))
        else:
            asyncio.run(send_event(event_type, data, tenant_id=tenant_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("webhook %s no se pudo agendar: %s", event_type, exc)
