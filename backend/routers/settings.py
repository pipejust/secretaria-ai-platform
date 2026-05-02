import json
import logging

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from database import get_session
from models import IntegrationSetting, User
from routers.auth import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/settings",
    tags=["Settings"],
)


@router.get("")
def get_all_settings(
    session: Session = Depends(get_session),
    _admin: User = Depends(require_admin),
):
    """Devuelve la configuración de todas las integraciones. Solo admins."""
    settings = session.exec(select(IntegrationSetting)).all()
    result: dict = {}
    for s in settings:
        try:
            result[s.provider_name] = json.loads(s.config_json)
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "config_json inválido en IntegrationSetting %s", s.provider_name
            )
            result[s.provider_name] = {}
    return result


@router.post("")
def save_settings(
    payload: dict,
    session: Session = Depends(get_session),
    _admin: User = Depends(require_admin),
):
    """Crea o actualiza configuración de integraciones. Solo admins."""
    for provider_name, config_obj in payload.items():
        existing = session.exec(
            select(IntegrationSetting).where(
                IntegrationSetting.provider_name == provider_name
            )
        ).first()

        is_active = (
            config_obj.get("isActive", True)
            if existing is None
            else config_obj.get("isActive", existing.is_active)
        )
        config_json_str = json.dumps(config_obj)

        if existing:
            existing.config_json = config_json_str
            existing.is_active = is_active
            session.add(existing)
        else:
            session.add(
                IntegrationSetting(
                    provider_name=provider_name,
                    config_json=config_json_str,
                    is_active=is_active,
                )
            )

    session.commit()
    return {"status": "success", "message": "Settings updated successfully"}
