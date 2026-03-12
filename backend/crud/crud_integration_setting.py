from crud.base import CRUDBase
from models import IntegrationSetting
from sqlmodel import Session, select
from typing import Optional

class CRUDIntegrationSetting(CRUDBase[IntegrationSetting, IntegrationSetting, IntegrationSetting]):
    def get_by_provider(self, session: Session, *, provider_name: str) -> Optional[IntegrationSetting]:
        statement = select(IntegrationSetting).where(IntegrationSetting.provider_name == provider_name)
        return session.exec(statement).first()

integration_setting = CRUDIntegrationSetting(IntegrationSetting)
