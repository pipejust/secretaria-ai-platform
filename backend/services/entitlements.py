"""Qué puede usar cada empresa según su suscripción.

Una sola función responde: `de_empresa(db, tenant_id)`. Los routers que
venden una opción piden `Depends(require_feature("clave"))` y reciben 402
cuando la empresa no la tiene. El superadministrador y la empresa dueña
de la plataforma nunca se bloquean.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException
from sqlmodel import Session, select

from database import get_session
from models import AddOn, Plan, Subscription, Tenant, User
from routers.auth import get_current_user
from services import billing_catalog as cat

DIAS_GRACIA = 7  # tras vencer, se mantiene todo mientras se renueva


@dataclass
class Entitlements:
    plan_key: str | None
    status: str                      # trialing | active | past_due | expired | none
    features: set[str] = field(default_factory=set)
    addons: list[str] = field(default_factory=list)
    period_end: str | None = None
    trial_ends: str | None = None
    meetings_per_month: int | None = None
    users_included: int | None = None
    billing_mode: str = "manual"

    def tiene(self, feature: str) -> bool:
        return feature in self.features

    def dict(self) -> dict:
        return {
            "plan": self.plan_key, "status": self.status, "features": sorted(self.features),
            "addons": list(self.addons), "period_end": self.period_end,
            "trial_ends": self.trial_ends, "meetings_per_month": self.meetings_per_month,
            "users_included": self.users_included, "billing_mode": self.billing_mode,
        }


def _features_de(db: Session, plan_key: str | None, addons: list[str]) -> set[str]:
    out: set[str] = set()
    plan = db.get(Plan, plan_key) if plan_key else None
    if plan and plan.is_active:
        out.update(json.loads(plan.features_json or "[]"))
    for key in addons:
        a = db.get(AddOn, key)
        if a and a.is_active:
            out.update(json.loads(a.features_json or "[]"))
    return out


def de_empresa(db: Session, tenant_id: int, ahora: datetime | None = None) -> Entitlements:
    ahora = ahora or datetime.now()
    sub = db.exec(select(Subscription).where(Subscription.tenant_id == tenant_id)).first()
    if sub is None:
        # Empresa nueva sin plan: prueba con todo, contada desde su creación.
        t = db.get(Tenant, tenant_id)
        try:
            creada = datetime.fromisoformat(t.created_at) if t else ahora
        except (TypeError, ValueError):
            creada = ahora
        fin = creada + timedelta(days=cat.DIAS_PRUEBA)
        if ahora <= fin:
            return Entitlements(plan_key=None, status="trialing", features=set(cat.FEATURES),
                                trial_ends=fin.isoformat())
        return Entitlements(plan_key=None, status="none")
    addons = json.loads(sub.addons_json or "[]")
    plan = db.get(Plan, sub.plan_key)
    base = Entitlements(
        plan_key=sub.plan_key, status=sub.status, addons=addons,
        period_end=sub.current_period_end, billing_mode=sub.billing_mode,
        meetings_per_month=plan.meetings_per_month if plan else None,
        users_included=plan.users_included if plan else None,
    )
    if sub.status in {"cancelled", "expired"}:
        base.status = "expired"
        return base
    if sub.current_period_end:
        try:
            fin = datetime.fromisoformat(sub.current_period_end)
        except ValueError:
            fin = ahora
        if ahora > fin + timedelta(days=DIAS_GRACIA):
            base.status = "expired"
            return base
        if ahora > fin:
            base.status = "past_due"
    base.features = _features_de(db, sub.plan_key, addons)
    return base


def es_exento(user: User, db: Session) -> bool:
    if getattr(user, "is_superadmin", False):
        return True
    t = db.get(Tenant, user.tenant_id)
    return bool(t and t.slug == "acten")


def tiene(db: Session, tenant_id: int, feature: str) -> bool:
    return de_empresa(db, tenant_id).tiene(feature)


def require_feature(feature: str):
    """Dependencia FastAPI: 402 si la empresa del usuario no tiene la función."""

    def _dep(user: User = Depends(get_current_user), db: Session = Depends(get_session)) -> User:
        if es_exento(user, db):
            return user
        ent = de_empresa(db, user.tenant_id)
        if not ent.tiene(feature):
            raise HTTPException(
                402,
                {
                    "message": "Esta opción no está incluida en el plan de tu empresa.",
                    "feature": feature,
                    "label": cat.FEATURES.get(feature, feature),
                    "plan": ent.plan_key,
                    "status": ent.status,
                },
            )
        return user

    return _dep
