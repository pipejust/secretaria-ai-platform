"""Por dónde entran las reuniones de cada empresa: Fireflies, el bot propio o ambos.

Antes había una sola entrada, Fireflies. El bot propio añade otra, y la
pregunta «¿cuál usa esta empresa?» tiene que responderse en un solo
sitio, porque de ella dependen tres cosas a la vez: qué webhook se
acepta, qué pantalla se enseña y qué proveedor factura.

Se guarda en `Tenant.meeting_source`. No en `IntegrationSetting`: no es
una credencial, es una decisión de la empresa, y viaja con su ficha.
"""

from __future__ import annotations

from typing import Optional

from sqlmodel import Session

from models import Tenant

FIREFLIES = "fireflies"
OWNED_BOT = "owned_bot"
BOTH = "both"
FUENTES = (FIREFLIES, OWNED_BOT, BOTH)

ETIQUETAS = {
    FIREFLIES: "Fireflies",
    OWNED_BOT: "Bot propio de Acten",
    BOTH: "Ambos",
}


def fuente_de(db: Session, tenant_id: int) -> str:
    """La fuente configurada. Si la fila no la tiene, la de siempre."""
    t = db.get(Tenant, tenant_id)
    valor = (getattr(t, "meeting_source", "") or "").strip() if t else ""
    return valor if valor in FUENTES else FIREFLIES


def admite(db: Session, tenant_id: int, fuente: str) -> bool:
    """¿Esta empresa acepta reuniones que lleguen por `fuente`?

    Es lo que consultan las entradas antes de crear una sesión. Rechazar
    aquí —y no más adelante— evita que una empresa que eligió el bot siga
    recibiendo duplicados por el webhook de Fireflies que nunca apagó.
    """
    actual = fuente_de(db, tenant_id)
    return actual == BOTH or actual == fuente


def cambiar(db: Session, tenant_id: int, fuente: str) -> str:
    if fuente not in FUENTES:
        raise ValueError(f"Fuente desconocida: {fuente}. Válidas: {', '.join(FUENTES)}")
    t = db.get(Tenant, tenant_id)
    if not t:
        raise ValueError("Empresa no encontrada")
    t.meeting_source = fuente
    db.add(t)
    db.commit()
    return fuente


def estado(db: Session, tenant_id: int) -> dict:
    actual = fuente_de(db, tenant_id)
    return {
        "source": actual,
        "label": ETIQUETAS[actual],
        "options": [{"value": f, "label": ETIQUETAS[f]} for f in FUENTES],
        "accepts_fireflies": admite(db, tenant_id, FIREFLIES),
        "accepts_owned_bot": admite(db, tenant_id, OWNED_BOT),
    }


def mensaje_rechazo(fuente: str, tenant_nombre: Optional[str] = None) -> str:
    quien = f"«{tenant_nombre}»" if tenant_nombre else "esta empresa"
    return (
        f"{quien} no recibe reuniones por {ETIQUETAS.get(fuente, fuente)}. "
        "Se cambia en Configuración → Integraciones → Origen de las reuniones."
    )
