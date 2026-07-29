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

EVENTS = ("session.processed", "session.failed", "task.created", "task.updated")


def _config() -> tuple[str, str, str]:
    """(url, secreto de firma, api key). Vacíos ⇒ emisor inactivo."""
    return (
        os.getenv("SERVICIOS_WEBHOOK_URL", "").strip(),
        os.getenv("SERVICIOS_WEBHOOK_SECRET", "").strip(),
        os.getenv("SERVICIOS_API_KEY", "").strip(),
    )


def is_configured() -> bool:
    url, secret, key = _config()
    return bool(url and secret and key)


def sign(secret: str, timestamp: int | str, raw_body: bytes) -> str:
    """HMAC-SHA256 sobre `{timestamp}.{cuerpo crudo}`."""
    message = f"{timestamp}.".encode() + raw_body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


async def send_event(
    event_type: str,
    data: dict[str, Any],
    *,
    event_id: Optional[str] = None,
) -> bool:
    """Envía un evento. Devuelve True si lo aceptaron.

    Nunca lanza: los fallos se registran. El llamador no debe depender
    del resultado para continuar su trabajo.
    """
    url, secret, api_key = _config()
    if not (url and secret and api_key):
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
            "X-API-Key": api_key,
            "X-Acten-Signature": f"sha256={sign(secret, ts, raw)}",
            "X-Acten-Timestamp": str(ts),
            "X-Acten-Event-Id": eid,
        }
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


def send_event_bg(event_type: str, data: dict[str, Any]) -> None:
    """Dispara el envío sin bloquear al llamador.

    Si hay bucle de eventos corriendo, agenda la tarea; si no (código
    síncrono, cron), lo ejecuta en uno propio. Nunca propaga errores.
    """
    if not is_configured():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    try:
        if loop and loop.is_running():
            loop.create_task(send_event(event_type, data))
        else:
            asyncio.run(send_event(event_type, data))
    except Exception as exc:  # noqa: BLE001
        logger.warning("webhook %s no se pudo agendar: %s", event_type, exc)
