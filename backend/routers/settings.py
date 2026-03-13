from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select
import json
from database import get_session
from models import IntegrationSetting

router = APIRouter(
    prefix="/api/settings",
    tags=["Settings"]
)

# Simularemos que requiere autenticación como Admin. Para no bloquear el flujo actual, lo mantenemos sin auth estricta
# pero idealmente se usaría Depends(get_current_admin_user)

@router.get("")
def get_all_settings(session: Session = Depends(get_session)):
    settings = session.exec(select(IntegrationSetting)).all()
    result = {}
    for s in settings:
        try:
            result[s.provider_name] = json.loads(s.config_json)
        except:
            result[s.provider_name] = {}
    return result

@router.post("")
def save_settings(payload: dict, session: Session = Depends(get_session)):
    for provider_name, config_obj in payload.items():
        existing = session.exec(select(IntegrationSetting).where(IntegrationSetting.provider_name == provider_name)).first()
        
        config_json_str = json.dumps(config_obj)
        
        if existing:
            existing.config_json = config_json_str
            session.add(existing)
        else:
            new_setting = IntegrationSetting(
                provider_name=provider_name,
                config_json=config_json_str,
                is_active=True
            )
            session.add(new_setting)
            
    session.commit()
    return {"status": "success", "message": "Settings updated successfully"}
