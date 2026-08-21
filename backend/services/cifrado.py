"""Cifrado de lo que no puede estar en claro en la base: tokens y llaves.

Lo usan los calendarios (el permiso revocable de cada cuenta conectada) y
las llaves de los motores de IA que un administrador pega en pantalla.

Lo que se guarda de cada persona es un permiso revocable —el
`refresh_token`—, no su contraseña. Aun así vive en una tabla que se
copia en cada backup y se lee desde cualquier consola de la base, así que
va cifrado con Fernet.

La llave sale de `CALENDAR_SECRET_KEY` si está; si no, se deriva de
`JWT_SECRET_KEY`, que ya es obligatoria en producción. Derivarla evita
una variable más que alguien tiene que acordarse de poner —y una cuyo
olvido no se nota hasta que las conexiones dejan de funcionar—.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_MARCA = "fer1:"  # prefijo de lo ya cifrado, para poder convivir con lo viejo


def _llave() -> bytes:
    bruta = os.getenv("CALENDAR_SECRET_KEY", "") or os.getenv("JWT_SECRET_KEY", "")
    if not bruta:
        # En desarrollo no hay JWT_SECRET_KEY y no vale la pena reventar el
        # arranque por esto: se usa una llave fija de desarrollo y se avisa.
        logger.warning(
            "Ni CALENDAR_SECRET_KEY ni JWT_SECRET_KEY: los tokens de calendario "
            "se cifran con una llave de desarrollo. No usar así en producción."
        )
        bruta = "acten-desarrollo-calendarios"
    return base64.urlsafe_b64encode(hashlib.sha256(bruta.encode()).digest())


def cifrar(valor: str) -> str:
    if not valor:
        return ""
    if valor.startswith(_MARCA):
        return valor
    return _MARCA + Fernet(_llave()).encrypt(valor.encode()).decode()


def descifrar(valor: str) -> str:
    """Devuelve el valor en claro.

    Lo guardado antes de esto está en claro y no lleva marca: se devuelve
    tal cual en vez de fallar. Migrar a ciegas todas las filas viejas
    habría dejado sin conexión a quien ya la tenía.
    """
    if not valor:
        return ""
    if not valor.startswith(_MARCA):
        return valor
    try:
        return Fernet(_llave()).decrypt(valor[len(_MARCA):].encode()).decode()
    except InvalidToken:
        # Cambió la llave. Decirlo, porque el síntoma —«hay que volver a
        # conectar la cuenta»— no apunta a esto por ningún lado.
        logger.error(
            "No se pudo descifrar un token de calendario: la llave de cifrado "
            "cambió. Esas cuentas hay que reconectarlas."
        )
        return ""


def enmascarar(valor: str) -> str:
    """`abcd…wxyz` para enseñar que hay un secreto sin enseñarlo."""
    if not valor:
        return ""
    if len(valor) <= 8:
        return "••••"
    return f"{valor[:4]}…{valor[-4:]}"
