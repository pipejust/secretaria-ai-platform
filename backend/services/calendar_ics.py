"""Suscribirse a un calendario por su dirección `.ics`.

Es como se suscriben entre sí Google, Apple y Outlook: cada uno publica
una dirección por calendario y con ella cualquiera puede leerlo. Cero
credenciales, cero permisos, funciona hoy. A cambio: solo lectura y
refresco por sondeo.

**El cuidado que no es opcional.** La URL la escribe una persona y la
pide nuestro servidor. Sin filtro eso es una puerta para leer lo que solo
se ve desde dentro de la red: localhost, la base de datos, el servicio de
metadatos de la nube (169.254.169.254, que en AWS entrega credenciales).
Por eso se resuelve el nombre y se miran **todas** las direcciones que
devuelve, no se siguen redirecciones a ciegas, y se corta por tamaño y
por tiempo.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional
from urllib.parse import urlparse

import httpx
from dateutil import rrule as _rrule

logger = logging.getLogger(__name__)

MAX_BYTES = 10 * 1024 * 1024
TIMEOUT = 20.0
MAX_REDIRECCIONES = 3


class IcsError(Exception):
    """La dirección no sirve, y el motivo se puede enseñar al usuario."""


# ─────────────────────────────────────────────────────────────────────────────
# Traer el archivo sin abrirle la puerta a la red interna
# ─────────────────────────────────────────────────────────────────────────────

def normalizar_url(url: str) -> str:
    """`webcal://` es lo que copia Apple; es https por debajo."""
    u = (url or "").strip()
    if u.lower().startswith("webcal://"):
        u = "https://" + u[len("webcal://"):]
    return u


def _destino_permitido(host: str) -> None:
    """Revienta si el nombre resuelve a algo que no debería pedirse desde aquí."""
    if not host:
        raise IcsError("La dirección no tiene servidor.")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise IcsError(f"No se pudo resolver «{host}».") from e

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        # Se miran TODAS las direcciones, no la primera: un nombre puede
        # resolver a una pública y a 127.0.0.1 a la vez, y quedarse con
        # la primera es justo lo que explota ese truco.
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise IcsError(
                f"«{host}» apunta a una dirección interna ({ip}). Solo se "
                "aceptan calendarios publicados en internet."
            )


def descargar(url: str, etag: str = "") -> tuple[Optional[str], str]:
    """Devuelve (texto, etag). Texto `None` si el servidor dice «no cambió».

    Las redirecciones se siguen a mano para poder validar cada salto: un
    servidor legítimo puede redirigir a `http://169.254.169.254/` y con
    `follow_redirects=True` httpx lo pediría sin preguntar.
    """
    actual = normalizar_url(url)
    if not actual.lower().startswith(("http://", "https://")):
        raise IcsError("La dirección tiene que empezar por https:// o webcal://")

    cabeceras = {"User-Agent": "Acten/1.0 (+calendarios)"}
    if etag:
        cabeceras["If-None-Match"] = etag

    for _ in range(MAX_REDIRECCIONES + 1):
        partes = urlparse(actual)
        _destino_permitido(partes.hostname or "")
        try:
            with httpx.Client(timeout=TIMEOUT, follow_redirects=False) as cli:
                with cli.stream("GET", actual, headers=cabeceras) as r:
                    if r.status_code == 304:
                        return None, etag
                    if r.status_code in (301, 302, 303, 307, 308):
                        destino = r.headers.get("location", "")
                        if not destino:
                            raise IcsError("Redirección sin destino.")
                        actual = httpx.URL(actual).join(destino).__str__()
                        continue
                    if r.status_code >= 400:
                        raise IcsError(
                            f"El servidor del calendario respondió {r.status_code}."
                        )
                    trozos, total = [], 0
                    for trozo in r.iter_bytes():
                        total += len(trozo)
                        if total > MAX_BYTES:
                            raise IcsError("El calendario pesa más de 10 MB.")
                        trozos.append(trozo)
                    cuerpo = b"".join(trozos).decode("utf-8", errors="replace")
                    return cuerpo, r.headers.get("etag", "")
        except httpx.HTTPError as e:
            raise IcsError(f"No se pudo leer el calendario: {e}") from e

    raise IcsError("Demasiadas redirecciones.")


# ─────────────────────────────────────────────────────────────────────────────
# Leer el archivo
# ─────────────────────────────────────────────────────────────────────────────

def _desplegar(texto: str) -> list[str]:
    """iCalendar parte las líneas largas y continúa con un espacio inicial."""
    salida: list[str] = []
    for linea in texto.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if linea[:1] in (" ", "\t") and salida:
            salida[-1] += linea[1:]
        else:
            salida.append(linea)
    return salida


def _fecha(valor: str, params: str) -> tuple[Optional[datetime], bool]:
    """(momento, es_de_día_entero).

    Confundir `yyyyMMddTHHmmssZ` con `yyyyMMdd` mueve el evento de día,
    así que el formato decide y no se adivina.
    """
    v = (valor or "").strip()
    if not v:
        return None, False
    if "VALUE=DATE" in params.upper() and "DATE-TIME" not in params.upper():
        try:
            return datetime.strptime(v, "%Y%m%d").replace(tzinfo=timezone.utc), True
        except ValueError:
            return None, True
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S", "%Y%m%d"):
        try:
            d = datetime.strptime(v, fmt)
            entero = fmt == "%Y%m%d"
            return d.replace(tzinfo=timezone.utc), entero
        except ValueError:
            continue
    return None, False


def _limpiar(v: str) -> str:
    return (v.replace("\\n", "\n").replace("\\,", ",")
             .replace("\\;", ";").replace("\\\\", "\\").strip())


def parsear(texto: str) -> list[dict]:
    """Los VEVENT del archivo, sin expandir repeticiones todavía."""
    eventos: list[dict] = []
    dentro = False
    actual: dict = {}
    for linea in _desplegar(texto):
        if linea.strip() == "BEGIN:VEVENT":
            dentro, actual = True, {}
            continue
        if linea.strip() == "END:VEVENT":
            if dentro and actual.get("start"):
                eventos.append(actual)
            dentro = False
            continue
        if not dentro or ":" not in linea:
            continue
        cabeza, _, valor = linea.partition(":")
        nombre, _, params = cabeza.partition(";")
        nombre = nombre.upper()
        if nombre == "UID":
            actual["uid"] = valor.strip()
        elif nombre == "SUMMARY":
            actual["title"] = _limpiar(valor)
        elif nombre == "DESCRIPTION":
            actual["description"] = _limpiar(valor)
        elif nombre == "LOCATION":
            actual["location"] = _limpiar(valor)
        elif nombre == "URL":
            actual["url"] = valor.strip()
        elif nombre == "STATUS":
            actual["cancelled"] = valor.strip().upper() == "CANCELLED"
        elif nombre == "DTSTART":
            d, entero = _fecha(valor, params)
            actual["start"], actual["all_day"] = d, entero
        elif nombre == "DTEND":
            d, _ = _fecha(valor, params)
            actual["end"] = d
        elif nombre == "RRULE":
            actual["rrule"] = valor.strip()
        elif nombre == "EXDATE":
            d, _ = _fecha(valor.split(",")[0], params)
            actual.setdefault("exdate", []).append(d)
    return eventos


def expandir(
    eventos: Iterable[dict], desde: datetime, hasta: datetime,
    tope_por_evento: int = 400,
) -> list[dict]:
    """Una entrada por cada vez que ocurre, dentro de la ventana.

    Sin esto una reunión semanal saldría una sola vez, que es el error
    clásico de pintar `.ics` leyendo solo el DTSTART.
    """
    salida: list[dict] = []
    for ev in eventos:
        inicio = ev.get("start")
        if not inicio:
            continue
        dur = (ev.get("end") - inicio) if ev.get("end") else timedelta(hours=1)
        if dur.total_seconds() <= 0:
            dur = timedelta(hours=1)

        if not ev.get("rrule"):
            if desde <= inicio <= hasta:
                salida.append({**ev, "instancia": inicio, "fin": inicio + dur})
            continue

        try:
            regla = _rrule.rrulestr(f"RRULE:{ev['rrule']}", dtstart=inicio)
        except (ValueError, TypeError) as e:
            logger.warning("RRULE ilegible (%s): %s", ev.get("uid"), e)
            if desde <= inicio <= hasta:
                salida.append({**ev, "instancia": inicio, "fin": inicio + dur})
            continue

        excluidas = {d for d in ev.get("exdate", []) if d}
        for n, cuando in enumerate(regla.between(desde, hasta, inc=True)):
            if n >= tope_por_evento:
                logger.warning(
                    "El evento %s repite más de %s veces en la ventana; se corta.",
                    ev.get("uid"), tope_por_evento)
                break
            if cuando in excluidas:
                continue
            salida.append({**ev, "instancia": cuando, "fin": cuando + dur})
    return salida


def clave_instancia(ev: dict) -> str:
    """El identificador de UNA ocurrencia.

    El UID es el mismo para toda la serie: usarlo tal cual dejaría una
    sola fila y las 51 semanas restantes se pisarían entre sí.
    """
    uid = ev.get("uid") or re.sub(r"\s+", "-", (ev.get("title") or "sin-uid"))[:80]
    cuando = ev.get("instancia")
    return f"{uid}#{cuando.isoformat()}" if cuando else uid
