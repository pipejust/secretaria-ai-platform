"""Catálogo de planes y add-ons, y qué habilita cada uno.

Los precios en USD son los del landing. Los de COP son los que cobra
Wompi (solo acepta COP): se siembran con una tasa de referencia y el
superadministrador los ajusta desde /api/billing/catalog. Los add-ons son
opciones sueltas que se suman a cualquier plan.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlmodel import Session, select

from models import AddOn, Plan, Subscription, Tenant

# Tasa solo para sembrar el precio en COP la primera vez. Editable después.
TASA_USD_COP_SIEMBRA = 4000

# Días de prueba con todo activo para una empresa nueva sin plan asignado.
DIAS_PRUEBA = 14

# Claves de funciones. Los routers piden `require_feature(<clave>)`.
F_FIREFLIES = "meetings.fireflies"
F_OWNED_BOT = "meetings.owned_bot"
F_VIDEO = "meetings.video"
F_DOCUMENTS = "documents.export"   # actas en Word y PDF
F_REPORTS = "reports"
F_CALENDAR = "calendar"
F_TEMPLATES = "templates"
F_INTEGRATIONS = "integrations"
F_ASK = "ask_ai"

FEATURES = {
    F_FIREFLIES: "Reuniones por Fireflies",
    F_OWNED_BOT: "Bot propio de Acten (Meet, Teams, Zoom y grabación web)",
    F_VIDEO: "Grabación de vídeo de la reunión",
    F_DOCUMENTS: "Actas en Word y PDF",
    F_REPORTS: "Reportes ejecutivos",
    F_CALENDAR: "Calendario",
    F_TEMPLATES: "Plantillas por proyecto",
    F_INTEGRATIONS: "Integraciones (Jira, Trello, ClickUp, Azure DevOps)",
    F_ASK: "Pregúntale a la IA",
}

PLANES = [
    {
        "key": "starter", "name": "Starter", "price_usd_cents": 4900, "sort_order": 1,
        "meetings_per_month": 20, "users_included": 5,
        "features": [F_FIREFLIES, F_DOCUMENTS, F_REPORTS, F_CALENDAR],
    },
    {
        "key": "business", "name": "Business", "price_usd_cents": 14900, "sort_order": 2,
        "meetings_per_month": 100, "users_included": None,
        "features": [F_FIREFLIES, F_DOCUMENTS, F_REPORTS, F_CALENDAR, F_TEMPLATES,
                     F_INTEGRATIONS, F_ASK],
    },
    {
        "key": "enterprise", "name": "Enterprise", "price_usd_cents": 0, "sort_order": 3,
        "meetings_per_month": None, "users_included": None, "is_public": False,
        "features": list(FEATURES),
    },
]

# Precios provisionales: no existen en el landing. Ajustar en el catálogo.
ADDONS = [
    {"key": "owned_bot", "name": "Bot propio de Acten", "price_usd_cents": 2900,
     "features": [F_OWNED_BOT], "requires": [], "sort_order": 1},
    {"key": "video_recording", "name": "Grabación de vídeo", "price_usd_cents": 1900,
     "features": [F_VIDEO], "requires": ["owned_bot"], "sort_order": 2},
    {"key": "ask_ai", "name": "Pregúntale a la IA", "price_usd_cents": 1900,
     "features": [F_ASK], "requires": [], "sort_order": 3},
    {"key": "integrations", "name": "Integraciones", "price_usd_cents": 2900,
     "features": [F_INTEGRATIONS], "requires": [], "sort_order": 4},
    {"key": "templates", "name": "Plantillas por proyecto", "price_usd_cents": 900,
     "features": [F_TEMPLATES], "requires": [], "sort_order": 5},
]


def _cop(usd_cents: int) -> int:
    return usd_cents * TASA_USD_COP_SIEMBRA


def sembrar_catalogo(db: Session) -> None:
    """Crea los planes y add-ons que falten. No toca los que ya existen."""
    for p in PLANES:
        if db.get(Plan, p["key"]) is None:
            db.add(Plan(
                key=p["key"], name=p["name"], price_usd_cents=p["price_usd_cents"],
                price_cop_cents=_cop(p["price_usd_cents"]), sort_order=p["sort_order"],
                meetings_per_month=p["meetings_per_month"], users_included=p["users_included"],
                is_public=p.get("is_public", True), features_json=json.dumps(p["features"]),
            ))
    for a in ADDONS:
        if db.get(AddOn, a["key"]) is None:
            db.add(AddOn(
                key=a["key"], name=a["name"], price_usd_cents=a["price_usd_cents"],
                price_cop_cents=_cop(a["price_usd_cents"]), sort_order=a["sort_order"],
                features_json=json.dumps(a["features"]), requires_json=json.dumps(a["requires"]),
            ))
    db.commit()


def asegurar_suscripciones_existentes(db: Session, plan_key: str = "business") -> int:
    """Empresas anteriores a la facturación: plan Business manual, sin vencimiento.

    Se ejecuta una vez por empresa (solo si no tiene fila). Las empresas
    creadas después empiezan en periodo de prueba (ver entitlements).
    """
    hoy = datetime.now()
    con_fila = {s.tenant_id for s in db.exec(select(Subscription)).all()}
    creadas = 0
    for t in db.exec(select(Tenant)).all():
        if t.id in con_fila:
            continue
        try:
            antigua = datetime.fromisoformat(t.created_at) < hoy - timedelta(days=DIAS_PRUEBA)
        except (TypeError, ValueError):
            antigua = True
        if not antigua:
            continue  # empresa reciente: periodo de prueba
        db.add(Subscription(
            tenant_id=t.id, plan_key=plan_key, status="active", billing_mode="manual",
            current_period_start=hoy.isoformat(), current_period_end=None,
            notes="Asignado al activar la facturación; empresa anterior al catálogo.",
        ))
        creadas += 1
    db.commit()
    return creadas


def plan_dict(p: Plan) -> dict:
    return {
        "key": p.key, "name": p.name, "price_usd_cents": p.price_usd_cents,
        "price_cop_cents": p.price_cop_cents, "interval": p.interval,
        "features": json.loads(p.features_json or "[]"),
        "meetings_per_month": p.meetings_per_month, "users_included": p.users_included,
        "is_public": p.is_public, "is_active": p.is_active, "sort_order": p.sort_order,
    }


def addon_dict(a: AddOn) -> dict:
    return {
        "key": a.key, "name": a.name, "price_usd_cents": a.price_usd_cents,
        "price_cop_cents": a.price_cop_cents, "features": json.loads(a.features_json or "[]"),
        "requires": json.loads(a.requires_json or "[]"), "is_active": a.is_active,
        "sort_order": a.sort_order,
    }


def catalogo(db: Session, incluir_privados: bool = False) -> dict:
    planes = [plan_dict(p) for p in db.exec(select(Plan).order_by(Plan.sort_order)).all()
              if p.is_active and (incluir_privados or p.is_public)]
    addons = [addon_dict(a) for a in db.exec(select(AddOn).order_by(AddOn.sort_order)).all()
              if a.is_active]
    return {"plans": planes, "addons": addons, "features": FEATURES, "currency": "COP"}
