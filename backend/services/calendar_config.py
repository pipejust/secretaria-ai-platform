"""Las credenciales de la aplicación: dónde viven y quién las puede ver.

Que el usuario no teclee claves no significa que no haya credenciales.
Significa que **las credenciales son de la aplicación, no de la
persona**: es lo mismo que hace cualquier app que ofrece «entrar con
Google». Alguien la registró una vez; después cada quien conecta la suya
con dos clics.

Se guardan por empresa en `IntegrationSetting` (`user_id IS NULL`), y el
secreto va cifrado. Ningún endpoint lo devuelve en claro: la pantalla
enseña `abcd…wxyz` y ya.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

from sqlmodel import Session, select

from models import IntegrationSetting
from services.calendar_crypto import cifrar, descifrar, enmascarar
from services.calendar_providers import CLAVE_CONFIG, PROVEEDORES

logger = logging.getLogger(__name__)

# Variables de entorno que sirven de respaldo para la instalación entera.
# Existían antes de que esto fuera configurable por empresa y se respetan
# para no dejar sin calendario a quien ya lo tenía funcionando.
ENV_RESPALDO = {
    "google": ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET",
               "GOOGLE_OAUTH_REDIRECT_URI"),
    "microsoft": ("MS_OAUTH_CLIENT_ID", "MS_OAUTH_CLIENT_SECRET",
                  "MS_OAUTH_REDIRECT_URI"),
    "zoho": ("ZOHO_OAUTH_CLIENT_ID", "ZOHO_OAUTH_CLIENT_SECRET",
             "ZOHO_OAUTH_REDIRECT_URI"),
}


def _fila(db: Session, tenant_id: int, provider: str) -> Optional[IntegrationSetting]:
    clave = CLAVE_CONFIG.get(provider)
    if not clave:
        return None
    return db.exec(
        select(IntegrationSetting)
        .where(IntegrationSetting.tenant_id == tenant_id)
        .where(IntegrationSetting.provider_name == clave)
        .where(IntegrationSetting.user_id == None)  # noqa: E711
    ).first()


def cargar(db: Session, tenant_id: int, provider: str) -> dict:
    """Las credenciales listas para usar, con el secreto ya descifrado."""
    fila = _fila(db, tenant_id, provider)
    cfg: dict = {}
    if fila and fila.is_active:
        try:
            cfg = json.loads(fila.config_json or "{}")
        except (json.JSONDecodeError, TypeError):
            logger.warning("Configuración de %s ilegible para el tenant %s",
                           provider, tenant_id)
            cfg = {}
    if cfg.get("client_secret"):
        cfg["client_secret"] = descifrar(cfg["client_secret"])

    env = ENV_RESPALDO.get(provider)
    if env and not cfg.get("client_id"):
        cid, secreto, redir = (os.getenv(e, "") for e in env)
        if cid:
            cfg = {"client_id": cid, "client_secret": secreto,
                   "redirect_uri": redir, "origen": "entorno"}
    return cfg


def guardar(db: Session, tenant_id: int, provider: str, datos: dict) -> dict:
    """Guarda cifrando el secreto. Un secreto vacío no borra el que había.

    Quien vuelve a esta pantalla a cambiar solo el ID de cliente no
    reescribe el secreto —no lo tiene delante, la pantalla nunca se lo
    enseñó—, y guardarlo en blanco dejaría la integración muerta sin
    ningún aviso.
    """
    clave = CLAVE_CONFIG.get(provider)
    if not clave:
        raise ValueError(f"Proveedor desconocido: {provider}")

    fila = _fila(db, tenant_id, provider)
    actual: dict = {}
    if fila:
        try:
            actual = json.loads(fila.config_json or "{}")
        except (json.JSONDecodeError, TypeError):
            actual = {}

    nuevo = dict(actual)
    for campo in ("client_id", "redirect_uri", "tenant", "data_center"):
        if datos.get(campo) is not None:
            nuevo[campo] = (datos.get(campo) or "").strip()
    if datos.get("client_secret"):
        nuevo["client_secret"] = cifrar(datos["client_secret"].strip())

    if not fila:
        fila = IntegrationSetting(tenant_id=tenant_id, provider_name=clave,
                                  config_json="{}", is_active=True)
    fila.config_json = json.dumps(nuevo)
    fila.is_active = True
    db.add(fila); db.commit()
    return estado(db, tenant_id, provider)


def estado(db: Session, tenant_id: int, provider: str) -> dict:
    """Lo que se puede enseñar en pantalla. Nunca el secreto en claro."""
    cfg = cargar(db, tenant_id, provider)
    listo = bool(cfg.get("client_id") and cfg.get("client_secret")
                 and cfg.get("redirect_uri"))
    return {
        "provider": provider,
        "etiqueta": PROVEEDORES.get(provider, {}).get("etiqueta", provider),
        "configurado": listo,
        "client_id": cfg.get("client_id", ""),
        "client_secret_pista": enmascarar(cfg.get("client_secret", "")),
        "redirect_uri": cfg.get("redirect_uri", ""),
        "tenant": cfg.get("tenant", ""),
        "data_center": cfg.get("data_center", ""),
        "origen": cfg.get("origen", "empresa" if listo else ""),
        "usa_centros": PROVEEDORES.get(provider, {}).get("centros", False),
    }


def redireccion_sugerida(provider: str, base_url: str) -> str:
    """La dirección de retorno que hay que pegar en la consola del proveedor.

    Se enseña ya escrita y con un botón para copiarla porque si no
    coincide carácter por carácter —la barra final incluida— el
    proveedor devuelve `redirect_uri_mismatch`, y eso no hay forma de
    saberlo antes de probar.
    """
    return f"{base_url.rstrip('/')}/api/v1/calendars/{provider}/callback"
