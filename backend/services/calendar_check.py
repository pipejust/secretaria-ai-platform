"""Comprobar las credenciales sin conectar ninguna cuenta.

El truco: se pide un token con un permiso **deliberadamente inválido**.

- Si el proveedor se queja del PERMISO, reconoció la aplicación → las
  credenciales están bien.
- Si se queja de la APLICACIÓN (cliente desconocido, secreto que no
  cuadra), las credenciales están mal.

Sin esto la única forma de saberlo es que alguien falle a mitad del
viaje, en una pantalla del proveedor que no dice cuál de los dos datos
falla —y quien lo sufre no es quien las pegó—.
"""

from __future__ import annotations

import logging

import httpx

from services.calendar_providers import PROVEEDORES, zoho_dominios

logger = logging.getLogger(__name__)

TIMEOUT = 20.0
PERMISO_INVENTADO = "acten.comprobacion.invalida"

# Cómo se queja cada uno cuando el problema es el PERMISO y no la app.
_QUEJAS_DE_PERMISO = (
    "invalid_scope", "invalid scope", "scope",
    "aadsts70011",           # Microsoft: «the provided value for scope is not valid»
    "invalid_oauthscope",    # Zoho
    # Zoho contesta esto cuando la app SÍ existe y lo que no vale es el
    # código que le mandamos, que es exactamente el caso de esta prueba.
    "invalid_code",
)
# Y cuando el problema es la APLICACIÓN.
_QUEJAS_DE_APP = (
    "invalid_client", "unauthorized_client", "client not found",
    "aadsts700016",          # Microsoft: aplicación no encontrada en el directorio
    "aadsts7000215",         # Microsoft: secreto inválido
)


def _veredicto(texto: str) -> tuple[bool, str]:
    t = (texto or "").lower()
    if any(q in t for q in _QUEJAS_DE_APP):
        return False, (
            "El proveedor no reconoce la aplicación. Revisa el ID de cliente, "
            "el secreto y —en Microsoft— el directorio."
        )
    if any(q in t for q in _QUEJAS_DE_PERMISO):
        return True, "El proveedor reconoce la aplicación."
    # Ni una cosa ni la otra: no se inventa un veredicto.
    return False, f"Respuesta que no se supo interpretar: {texto[:200]}"


async def comprobar(provider: str, cfg: dict) -> dict:
    """`{"ok": bool, "detalle": str}` — nunca revienta, siempre explica."""
    cid = (cfg or {}).get("client_id") or ""
    secreto = (cfg or {}).get("client_secret") or ""
    if not cid or not secreto:
        return {"ok": False, "detalle": "Faltan el ID de cliente o el secreto."}
    if not (cfg or {}).get("redirect_uri"):
        return {"ok": False, "detalle": "Falta la dirección de retorno."}

    try:
        if provider == "google":
            url = PROVEEDORES["google"]["token_url"]
            datos = {"client_id": cid, "client_secret": secreto,
                     "grant_type": "refresh_token", "refresh_token": PERMISO_INVENTADO}
        elif provider == "microsoft":
            tenant = (cfg or {}).get("tenant") or "common"
            url = PROVEEDORES["microsoft"]["token_tpl"].format(tenant=tenant)
            datos = {"client_id": cid, "client_secret": secreto,
                     "grant_type": "client_credentials", "scope": PERMISO_INVENTADO}
        elif provider == "zoho":
            cuentas, _ = zoho_dominios((cfg or {}).get("data_center", "com"))
            url = PROVEEDORES["zoho"]["token_tpl"].format(cuentas=cuentas)
            datos = {"client_id": cid, "client_secret": secreto,
                     "grant_type": "authorization_code", "code": PERMISO_INVENTADO}
        else:
            return {"ok": False, "detalle": f"Proveedor desconocido: {provider}"}

        async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
            r = await cli.post(url, data=datos)
            ok, detalle = _veredicto(r.text)
            return {"ok": ok, "detalle": detalle, "http": r.status_code}
    except httpx.HTTPError as e:
        return {"ok": False, "detalle": f"No se pudo hablar con el proveedor: {e}"}
