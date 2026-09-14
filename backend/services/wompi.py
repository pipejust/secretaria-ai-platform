"""Wompi (Bancolombia): firma de integridad, verificación de eventos y consulta.

Las llaves viven cifradas en `IntegrationSetting('wompi')` de la empresa
dueña de la plataforma; nunca en el entorno ni en el navegador (solo la
pública viaja al widget).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass

import httpx
from sqlmodel import Session, select

from models import IntegrationSetting, Tenant
from services.cifrado import cifrar, descifrar

logger = logging.getLogger(__name__)

BASES = {
    "sandbox": "https://sandbox.wompi.co/v1",
    "production": "https://production.wompi.co/v1",
}
WIDGET_SCRIPT = "https://checkout.wompi.co/widget.js"
CHECKOUT_URL = "https://checkout.wompi.co/p/"


@dataclass
class WompiConfig:
    public_key: str
    private_key: str
    events_secret: str
    integrity_secret: str
    environment: str = "sandbox"

    @property
    def base(self) -> str:
        return BASES[self.environment]

    @property
    def configured(self) -> bool:
        return bool(self.public_key and self.integrity_secret and self.events_secret)


def tenant_duenio(db: Session) -> Tenant | None:
    return db.exec(select(Tenant).where(Tenant.slug == "acten")).first()


def _fila(db: Session):
    t = tenant_duenio(db)
    if not t:
        return None, None
    row = db.exec(
        select(IntegrationSetting).where(
            IntegrationSetting.tenant_id == t.id,
            IntegrationSetting.provider_name == "wompi",
            IntegrationSetting.user_id.is_(None),
        )
    ).first()
    return t, row


def leer_config(db: Session) -> WompiConfig:
    _, row = _fila(db)
    cfg = json.loads(row.config_json) if row else {}
    env = cfg.get("environment", "sandbox")
    return WompiConfig(
        public_key=cfg.get("public_key", ""),
        private_key=descifrar(cfg.get("private_key", "")),
        events_secret=descifrar(cfg.get("events_secret", "")),
        integrity_secret=descifrar(cfg.get("integrity_secret", "")),
        environment=env if env in BASES else "sandbox",
    )


def guardar_config(db: Session, *, public_key: str, private_key: str | None,
                   events_secret: str | None, integrity_secret: str | None,
                   environment: str) -> dict:
    t, row = _fila(db)
    if not t:
        raise ValueError("No existe la empresa dueña de la plataforma (slug 'acten')")
    if environment not in BASES:
        raise ValueError("environment debe ser sandbox o production")
    prefijo = "pub_test_" if environment == "sandbox" else "pub_prod_"
    if not public_key.startswith(prefijo):
        raise ValueError(f"La llave pública de {environment} empieza por {prefijo}")
    old = json.loads(row.config_json) if row else {}
    cfg = {
        "environment": environment,
        "public_key": public_key.strip(),
        # Vacío = conservar lo guardado.
        "private_key": cifrar(private_key.strip()) if private_key else old.get("private_key", ""),
        "events_secret": cifrar(events_secret.strip()) if events_secret else old.get("events_secret", ""),
        "integrity_secret": cifrar(integrity_secret.strip()) if integrity_secret else old.get("integrity_secret", ""),
    }
    if not row:
        row = IntegrationSetting(tenant_id=t.id, provider_name="wompi")
    row.config_json = json.dumps(cfg)
    row.is_active = True
    db.add(row)
    db.commit()
    return estado_config(db)


def estado_config(db: Session) -> dict:
    cfg = leer_config(db)
    return {
        "environment": cfg.environment,
        "public_key": cfg.public_key,
        "has_private_key": bool(cfg.private_key),
        "has_events_secret": bool(cfg.events_secret),
        "has_integrity_secret": bool(cfg.integrity_secret),
        "configured": cfg.configured,
    }


def firma_integridad(reference: str, amount_in_cents: int, currency: str,
                     integrity_secret: str, expiration_time: str | None = None) -> str:
    """SHA-256 de `<reference><amount><currency>[<expiration>]<secret>`."""
    partes = [reference, str(amount_in_cents), currency]
    if expiration_time:
        partes.append(expiration_time)
    partes.append(integrity_secret)
    return hashlib.sha256("".join(partes).encode()).hexdigest()


def _valor(data: dict, ruta: str) -> str:
    actual: object = data
    for parte in ruta.split("."):
        if not isinstance(actual, dict):
            return ""
        actual = actual.get(parte)
    if actual is None:
        return ""
    if isinstance(actual, bool):
        return "true" if actual else "false"
    return str(actual)


def verificar_evento(body: dict, events_secret: str) -> bool:
    """Checksum = SHA-256(valores de signature.properties + timestamp + secreto)."""
    firma = body.get("signature") or {}
    propiedades = firma.get("properties") or []
    esperado = str(firma.get("checksum") or "")
    if not events_secret or not propiedades or not esperado:
        return False
    cadena = "".join(_valor(body.get("data") or {}, p) for p in propiedades)
    cadena += str(body.get("timestamp", "")) + events_secret
    calculado = hashlib.sha256(cadena.encode()).hexdigest()
    return calculado.lower() == esperado.lower()


def consultar_transaccion(cfg: WompiConfig, transaction_id: str) -> dict | None:
    """GET /transactions/{id}; la respuesta manda sobre lo que diga el navegador."""
    try:
        with httpx.Client(timeout=20) as client:
            r = client.get(
                f"{cfg.base}/transactions/{transaction_id}",
                headers={"Authorization": "Bearer " + cfg.private_key} if cfg.private_key else {},
            )
        if not r.is_success:
            logger.warning("Wompi GET transaction %s → %s", transaction_id, r.status_code)
            return None
        return r.json().get("data")
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Wompi GET transaction falló: %s", exc)
        return None
