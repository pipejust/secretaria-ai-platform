"""Remitentes autorizados del bot = usuarios activos de la empresa.

Una sola dirección de invitación para todas las empresas; el bot decide a
cuál pertenece un correo por quien lo envía. Para que eso funcione sin
que nadie mantenga listas a mano, Acten copia a la política del bot los
correos de los usuarios activos de cada empresa que acepte el bot propio,
más los remitentes extra que el administrador añada.
"""

from __future__ import annotations

import json
import logging

from sqlmodel import Session, select

from models import IntegrationSetting, Tenant, User
from services import meeting_source

logger = logging.getLogger(__name__)


def usuarios_remitentes(db: Session, tenant_id: int) -> list[str]:
    """Correos de usuarios activos, solo si la empresa acepta el bot propio.

    Si no lo acepta, no se sincronizan: un mismo correo en dos empresas
    (p. ej. el consultor que administra ambas) dejaría ambiguo el destino y
    el bot rechazaría la invitación para las dos.
    """
    if not meeting_source.admite(db, tenant_id, meeting_source.OWNED_BOT):
        return []
    rows = db.exec(select(User).where(User.tenant_id == tenant_id, User.is_active == True)).all()  # noqa: E712
    return sorted({(u.email or "").strip().lower() for u in rows if u.email and "@" in u.email})


def combinar(db: Session, tenant_id: int, extra: list[str]) -> list[str]:
    todos = {e.strip().lower() for e in extra if e and "@" in e}
    todos.update(usuarios_remitentes(db, tenant_id))
    return sorted(todos)


def empresas_con_bot(db: Session) -> list[int]:
    rows = db.exec(
        select(IntegrationSetting).where(
            IntegrationSetting.provider_name == "owned_bot",
            IntegrationSetting.user_id.is_(None),
            IntegrationSetting.is_active == True,  # noqa: E712
        )
    ).all()
    return [r.tenant_id for r in rows if db.get(Tenant, r.tenant_id)]


async def sincronizar(db: Session, tenant_id: int, extra: list[str] | None = None) -> dict | None:
    """Reenvía la política al bot con los remitentes actuales.

    `extra` = lista que escribió el administrador (se guarda tal cual en
    `extra_senders` para no perderla en la siguiente sincronización). Sin
    política previa y sin `extra`, no hay nada que sincronizar.
    """
    from routers.bot_control import bot_call, bot_name_of

    estado = await bot_call(db, tenant_id, "GET", "/v1/mail-policy")
    actual = estado.get("policy") if isinstance(estado, dict) else None
    if actual is None and extra is None:
        return None
    manuales = list(extra) if extra is not None else list((actual or {}).get("extra_senders") or [])
    body = {
        "allowed_senders": combinar(db, tenant_id, manuales),
        "extra_senders": sorted({e.strip().lower() for e in manuales if e and "@" in e}),
        "recording_authorized": bool((actual or {}).get("recording_authorized", False)),
        "timezone": (actual or {}).get("timezone") or "America/Bogota",
        "bot_name": bot_name_of(db, tenant_id),
    }
    return await bot_call(db, tenant_id, "PUT", "/v1/mail-policy", body=body)


async def sincronizar_todas(db: Session) -> int:
    hechas = 0
    for tenant_id in empresas_con_bot(db):
        try:
            if await sincronizar(db, tenant_id) is not None:
                hechas += 1
        except Exception as exc:  # noqa: BLE001 — una empresa no debe frenar a las demás
            logger.warning("Sincronización de remitentes de la empresa %s falló: %s", tenant_id, exc)
    return hechas
