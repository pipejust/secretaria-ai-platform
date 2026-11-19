"""Cloudflare Turnstile — validación del token que el cliente adjunta a cada envío.

Protege formularios expuestos (contacto, login, recuperación de contraseña, canje
de códigos) y endpoints autenticados abusables (envíos masivos, uploads). No se
aplica a webhooks, callbacks OAuth, APIs server-to-server ni cron — esos van
firmados.

Flujo: el cliente renderiza el widget y, al enviar, adjunta `cf-turnstile-token`.
Antes de ejecutar el efecto, el servidor hace un `siteverify` contra Cloudflare
con el `secret` del entorno. Se exige `success: true`, que la `action` del token
coincida con la esperada por el endpoint, y que el `hostname` esté permitido para
el entorno. El token caduca a los 5 minutos y es de un solo uso.

Modo dev: si `TURNSTILE_SECRET_KEY` no está en env y `ENVIRONMENT != "production"`,
se salta la verificación (tests + local). Nunca se salta en producción.

Contrato HTTP del endpoint protegido:
  - Header opcional: `cf-turnstile-token`.
  - O en el body JSON: `turnstile_token`.
  - Si falta o falla, 422 con `{"error_code": "turnstile_required|turnstile_failed", ...}`.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import httpx
from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
# Hostnames permitidos en producción. Dev/staging se saltan por falta de secret.
# acten.softnexus.io es el alias que usa la plataforma de Softnexus para servir Acten
# embebido; el widget de Cloudflare ya acepta los dos.
ALLOWED_HOSTNAMES = {"acten.app", "acten.softnexus.io"}
# El cliente puede llamar la verify desde preview Vercel; si se necesita, añadir aquí.

# Acciones conocidas — cada endpoint declara la suya. Permiten correlar un token
# emitido para un flujo con el endpoint donde se consume: un token de `contact`
# no sirve para `login`.
#
# Público (cubiertos en este PR):
ACTION_CONTACT = "contact"
ACTION_LOGIN = "login"
ACTION_LOGIN_2FA = "login_2fa"
ACTION_FORGOT_PASSWORD = "forgot_password"
ACTION_RESET_PASSWORD = "reset_password"
# Autenticados de riesgo (pendientes de PR siguiente — frontend sin widget aún):
ACTION_SESSION_UPLOAD = "session_upload"
ACTION_DISPATCH_EMAILS = "dispatch_emails"
ACTION_DISPATCH_PLATFORMS = "dispatch_platforms"
ACTION_RESEND_INVITATION = "resend_invitation"
ACTION_REGISTER_ADMIN = "register_admin"
ACTION_CHANGE_PASSWORD = "change_password"
ACTION_AVATAR_UPLOAD = "avatar_upload"
ACTION_TEMPLATE_UPLOAD = "template_upload"


class TurnstileError(HTTPException):
    def __init__(self, code: str, detail: str):
        super().__init__(status_code=422, detail={"error_code": code, "message": detail})


def _secret() -> str | None:
    val = (os.environ.get("TURNSTILE_SECRET_KEY") or "").strip()
    return val or None


def _is_production() -> bool:
    return (os.environ.get("ENVIRONMENT") or "").strip().lower() == "production"


def _extract_token(request: Request, body: dict | None = None) -> str | None:
    """Prioridad: header `cf-turnstile-token`, luego `turnstile_token` en el body."""
    header = request.headers.get("cf-turnstile-token") or request.headers.get("x-turnstile-token")
    if header and header.strip():
        return header.strip()
    if body and isinstance(body.get("turnstile_token"), str) and body["turnstile_token"].strip():
        return body["turnstile_token"].strip()
    return None


def _client_ip(request: Request) -> str | None:
    """Mejor IP del cliente visible al backend (Cloudflare proxy, luego X-Forwarded-For, luego peer)."""
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


async def _siteverify(token: str, secret: str, remote_ip: str | None) -> dict:
    """Llama al endpoint de Cloudflare. Devuelve el JSON crudo.

    Timeout corto (5s): si Cloudflare no responde, el flujo se corta con 422;
    no dejamos pasar envíos sin verificación.
    """
    payload = {"secret": secret, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.post(SITEVERIFY_URL, data=payload)
    except httpx.HTTPError as exc:
        logger.warning("Turnstile siteverify no respondió: %s", exc)
        raise TurnstileError("turnstile_unavailable", "La verificación de seguridad no respondió. Reintenta en un momento.") from exc
    try:
        return r.json()
    except ValueError as exc:
        raise TurnstileError("turnstile_unavailable", "La verificación de seguridad respondió con un formato inesperado.") from exc


async def verificar_turnstile(request: Request, action: str, body: dict | None = None) -> None:
    """Punto de entrada: lee el token del request, lo verifica contra Cloudflare y
    exige que la `action` del token coincida con la esperada por el endpoint.

    No devuelve nada: si pasa, continúa; si falla, levanta HTTPException 422 con
    un `error_code` estable para que el frontend sepa cómo recuperar (volver a
    renderizar el widget, pedir un token nuevo).
    """
    secret = _secret()
    if not secret:
        if _is_production():
            # En producción exigimos secret configurado — no abrimos bypass silencioso.
            raise TurnstileError("turnstile_misconfigured", "La verificación de seguridad no está configurada en el servidor.")
        logger.info("Turnstile: sin TURNSTILE_SECRET_KEY, se salta (env no-prod).")
        return
    token = _extract_token(request, body)
    if not token:
        raise TurnstileError("turnstile_required", "Falta el token de verificación. Refresca la página y vuelve a enviar.")
    data = await _siteverify(token, secret, _client_ip(request))
    if not data.get("success"):
        errores = data.get("error-codes") or []
        logger.info("Turnstile falló: action=%s errores=%s", action, errores)
        raise TurnstileError("turnstile_failed", "La verificación de seguridad no pasó. Vuelve a intentarlo.")
    # Action: evita que un token emitido para `contact` se use en `login`.
    returned_action = data.get("action") or ""
    if returned_action and returned_action != action:
        logger.info("Turnstile: action mismatch esperado=%s recibido=%s", action, returned_action)
        raise TurnstileError("turnstile_action_mismatch", "La verificación corresponde a otro envío.")
    # Hostname: restringe tokens a los dominios aprobados en producción.
    hostname = (data.get("hostname") or "").lower()
    if _is_production() and hostname and hostname not in ALLOWED_HOSTNAMES:
        logger.warning("Turnstile: hostname no permitido %s", hostname)
        raise TurnstileError("turnstile_hostname_invalid", "La verificación proviene de un dominio no autorizado.")


def dep_turnstile(action: str):
    """Dependencia FastAPI que verifica Turnstile para la `action` dada.

    Se adjunta al endpoint con `Depends(dep_turnstile(ACTION_CONTACT))`. El cliente
    debe mandar el token en el header `cf-turnstile-token` para no entrar en
    conflicto con la lectura del body del handler (FastAPI consume el stream
    una sola vez). Un endpoint que lo prefiera en el body debe parsear a mano
    y llamar `verificar_turnstile(request, action, body)` desde el handler.
    """
    async def _dep(request: Request) -> None:
        await verificar_turnstile(request, action, None)
    return _dep
