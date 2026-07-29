"""Emparejar Acten con la plataforma de una empresa, sin manejar claves.

El problema que resuelve: hasta ahora conectar las dos plataformas pedía
que un humano copiase una clave de API de cincuenta caracteres y un
secreto HMAC, los pasase por un canal cifrado y los pegase en el sitio
correcto. Eso falla de tres maneras — la clave acaba en el historial de
un chat, se pega mal, o alguien la guarda «un momento» en un fichero que
no borra nunca.

Aquí el humano solo ve un **código corto de un solo uso**:

    ACTEN-4K7M-Q2XP

1. Un administrador de Acten pulsa «Conectar plataforma» y le sale ese
   código, válido quince minutos.
2. En la otra plataforma lo pega en su pantalla de ajustes.
3. **El servidor de ellos** llama a `POST /api/v1/pair/redeem`. En esa
   única llamada reciben la clave de API y entregan su URL y su secreto
   de webhook.

La clave nunca pasa por un navegador, ni por un chat, ni por las manos de
nadie. El código sí, pero cuando llega a alguien que no debía ya está
canjeado o caducado.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from database import get_session
from models import ApiKey, IntegrationPairing, OutboundIntegration, Tenant, User
from routers.auth import get_current_tenant, require_admin

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Emparejamiento de plataformas"])

VIGENCIA_MIN = 15
# Sin I, O, 0 ni 1: el código se lee en voz alta o se copia a mano, y esas
# cuatro son las que se confunden.
ALFABETO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

# Lo que necesita una plataforma para pintar toda la interfaz de Acten.
# Se conceden juntos a propósito: pedirle al administrador que elija entre
# quince alcances es devolverle justo la complejidad que esto quita.
ALCANCES_COMPLETOS = [
    "sessions:read", "sessions:send", "tasks:read", "tasks:write",
    "ask:query", "calendar:read", "calendar:write",
    "integrations:read", "integrations:write",
    "outputs:read", "outputs:write",
    "comments:read", "comments:write",
    "analytics:read", "notifications:read",
]


def _hash(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _normalizar(codigo: str) -> str:
    """Deja solo los ocho caracteres útiles.

    Quien copia el código lo pega con el guión, sin él, en minúsculas o
    con un espacio de más. Rechazarlo por eso sería una crueldad
    innecesaria.

    Ojo con el prefijo: `ACTEN` está hecho de letras que también forman
    parte del alfabeto del código, así que hay que quitarlo **antes** de
    filtrar. Si no, la forma normalizada mide trece caracteres y ningún
    código válido pasa la comprobación.
    """
    limpio = "".join(c for c in (codigo or "").upper() if c.isalnum())
    if limpio.startswith("ACTEN"):
        limpio = limpio[5:]
    return "".join(c for c in limpio if c in ALFABETO)


def _generar_codigo() -> tuple[str, str]:
    """(código con guiones para leer, forma normalizada para comparar)."""
    crudo = "".join(secrets.choice(ALFABETO) for _ in range(8))
    return f"ACTEN-{crudo[:4]}-{crudo[4:]}", crudo


# ══════════════════════════════════════════════════════════════════════
# LADO ACTEN — un administrador pide el código
# ══════════════════════════════════════════════════════════════════════

class CodigoOut(BaseModel):
    codigo: str
    expira_el: str
    vigencia_min: int
    instrucciones: str


@router.post("/api/integrations/pairing", response_model=CodigoOut)
def crear_codigo(
    db: Session = Depends(get_session),
    tenant: Tenant = Depends(get_current_tenant),
    admin: User = Depends(require_admin),
):
    """Emite un código de emparejamiento. Solo administradores."""
    # Los códigos anteriores sin canjear se invalidan: dos códigos vivos a
    # la vez es un código de más que alguien puede usar por error.
    ahora = datetime.now()
    for viejo in db.exec(
        select(IntegrationPairing)
        .where(IntegrationPairing.tenant_id == tenant.id)
        .where(IntegrationPairing.redeemed_at == None)  # noqa: E711
    ).all():
        viejo.expires_at = ahora.isoformat()
        db.add(viejo)

    legible, crudo = _generar_codigo()
    expira = ahora + timedelta(minutes=VIGENCIA_MIN)
    db.add(IntegrationPairing(
        tenant_id=tenant.id,
        code_hash=_hash(crudo),
        scopes=json.dumps(ALCANCES_COMPLETOS),
        created_by_user_id=admin.id,
        expires_at=expira.isoformat(),
    ))
    db.commit()

    return CodigoOut(
        codigo=legible,
        expira_el=expira.isoformat(),
        vigencia_min=VIGENCIA_MIN,
        instrucciones=(
            "Pega este código en la pantalla de conexión de la otra "
            "plataforma. Caduca en 15 minutos y solo sirve una vez."
        ),
    )


@router.get("/api/integrations/pairing/status")
def estado_conexion(
    db: Session = Depends(get_session),
    tenant: Tenant = Depends(get_current_tenant),
    admin: User = Depends(require_admin),
):
    """¿Hay una plataforma conectada? ¿Hay un código esperando?"""
    ahora = datetime.now().isoformat()
    pendiente = db.exec(
        select(IntegrationPairing)
        .where(IntegrationPairing.tenant_id == tenant.id)
        .where(IntegrationPairing.redeemed_at == None)  # noqa: E711
        .where(IntegrationPairing.expires_at > ahora)
    ).first()
    conectada = db.exec(
        select(OutboundIntegration)
        .where(OutboundIntegration.tenant_id == tenant.id)
        .where(OutboundIntegration.is_active == True)  # noqa: E712
    ).first()
    return {
        "conectada": bool(conectada),
        "plataforma": conectada.name if conectada else None,
        "conectada_el": conectada.created_at if conectada else None,
        "recibe_eventos": bool(conectada and conectada.webhook_url),
        "codigo_pendiente": bool(pendiente),
        "codigo_expira_el": pendiente.expires_at if pendiente else None,
    }


@router.delete("/api/integrations/pairing")
def desconectar(
    db: Session = Depends(get_session),
    tenant: Tenant = Depends(get_current_tenant),
    admin: User = Depends(require_admin),
):
    """Corta la conexión: revoca la clave y deja de mandar eventos."""
    ahora = datetime.now().isoformat()
    n = 0
    for integ in db.exec(
        select(OutboundIntegration).where(OutboundIntegration.tenant_id == tenant.id)
    ).all():
        integ.is_active = False
        integ.updated_at = ahora
        db.add(integ)
        n += 1
    for clave in db.exec(
        select(ApiKey)
        .where(ApiKey.tenant_id == tenant.id)
        .where(ApiKey.revoked_at == None)  # noqa: E711
    ).all():
        if clave.name.startswith("Emparejamiento:"):
            clave.revoked_at = ahora
            db.add(clave)
            n += 1
    db.commit()
    return {"status": "desconectada", "cambios": n}


# ══════════════════════════════════════════════════════════════════════
# LADO DE LA OTRA PLATAFORMA — canjea el código
# ══════════════════════════════════════════════════════════════════════

class CanjeIn(BaseModel):
    codigo: str
    plataforma: str = Field(
        description="Nombre de su plataforma. Sale en la pantalla de Acten.",
    )
    # Para que Acten les avise cuando llega una sesión o cambia una tarea.
    webhook_url: Optional[str] = None
    # Lo fija quien verifica — ustedes reciben, así que el secreto es suyo.
    webhook_secret: Optional[str] = None
    # La dirección contraria: con qué llama Acten a su API.
    api_base_url: Optional[str] = None
    api_key: Optional[str] = None


@router.post("/api/v1/pair/redeem")
def canjear(
    payload: CanjeIn,
    request: Request,
    db: Session = Depends(get_session),
):
    """Canjea el código y devuelve la clave de API. **Un solo uso.**

    No lleva autenticación porque quien llama todavía no tiene ninguna
    credencial — el código *es* la credencial. Por eso es de un solo uso,
    caduca en quince minutos y se guarda su hash: los tres a la vez.

    Llámenlo **desde su servidor**, nunca desde el navegador. Si lo hace
    el navegador, la clave llega al navegador, y ahí ya no es un secreto.
    """
    crudo = _normalizar(payload.codigo)
    if len(crudo) != 8:
        raise HTTPException(422, "El código no tiene la forma ACTEN-XXXX-XXXX.")

    fila = db.exec(
        select(IntegrationPairing).where(IntegrationPairing.code_hash == _hash(crudo))
    ).first()
    # El mismo mensaje para «no existe», «ya se usó» y «caducó»: distinguir
    # los tres le diría a quien prueba códigos al azar cuándo ha acertado.
    invalido = HTTPException(
        400,
        "El código no es válido, ya se usó o caducó. Pide uno nuevo en Acten.",
    )
    if not fila or fila.redeemed_at:
        raise invalido
    if fila.expires_at <= datetime.now().isoformat():
        raise invalido

    tenant = db.get(Tenant, fila.tenant_id)
    if not tenant or not tenant.is_active:
        raise invalido

    dueno = db.exec(
        select(User)
        .where(User.tenant_id == tenant.id)
        .where(User.is_active == True)  # noqa: E712
        .order_by(User.id)
    ).first()
    if not dueno:
        raise HTTPException(503, "La empresa no tiene usuarios activos.")

    plataforma = (payload.plataforma or "plataforma externa").strip()[:60]
    clave_plana = "nv_" + secrets.token_urlsafe(32)
    clave = ApiKey(
        user_id=dueno.id,
        tenant_id=tenant.id,
        name=f"Emparejamiento: {plataforma}",
        hashed_key=_hash(clave_plana),
        scopes=fila.scopes or json.dumps(ALCANCES_COMPLETOS),
        rate_limit_per_min=120,
    )
    db.add(clave)
    db.flush()

    ahora = datetime.now().isoformat()
    integ = db.exec(
        select(OutboundIntegration).where(OutboundIntegration.tenant_id == tenant.id)
    ).first()
    if not integ:
        integ = OutboundIntegration(tenant_id=tenant.id)
    integ.name = plataforma
    integ.webhook_url = (payload.webhook_url or "").strip()
    if payload.webhook_secret:
        integ.webhook_secret = payload.webhook_secret.strip()
    if payload.api_base_url:
        integ.remote_base_url = payload.api_base_url.strip().rstrip("/")
    if payload.api_key:
        integ.remote_api_key = payload.api_key.strip()
    integ.is_active = True
    integ.updated_at = ahora
    db.add(integ)

    fila.redeemed_at = ahora
    fila.redeemed_by = plataforma
    fila.api_key_id = clave.id
    db.add(fila)
    db.commit()

    logger.info(
        "emparejamiento canjeado: tenant=%s plataforma=%r desde %s",
        tenant.slug, plataforma, request.client.host if request.client else "?",
    )

    return {
        "api_key": clave_plana,          # ← único momento en que existe en claro
        "empresa": {"slug": tenant.slug, "nombre": tenant.name},
        "api_base": "https://api.acten.app/api/v1",
        "scopes": json.loads(clave.scopes or "[]"),
        "limite_por_minuto": clave.rate_limit_per_min,
        "cabeceras": {
            "X-API-Key": "la clave de arriba",
            "X-On-Behalf-Of": "UUID del empleado, sacado de su sesión — nunca del navegador",
        },
        "eventos": {
            "recibiran": ["session.processed", "session.failed", "task.created", "task.updated"],
            "url": integ.webhook_url or None,
            "firma": "HMAC-SHA256 sobre '{timestamp}.{cuerpo crudo}', cabecera X-Acten-Signature",
            "ventana_seg": 300,
        },
        "aviso": (
            "Guárdela cifrada ahora. No se vuelve a mostrar y no hay endpoint "
            "que la devuelva."
        ),
    }
