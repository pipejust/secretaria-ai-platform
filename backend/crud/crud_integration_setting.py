from crud.base import CRUDBase
from models import IntegrationSetting, PER_USER_INTEGRATION_PROVIDERS
from sqlmodel import Session, select
from typing import Optional


class CRUDIntegrationSetting(CRUDBase[IntegrationSetting, IntegrationSetting, IntegrationSetting]):
    def get_by_provider(
        self,
        session: Session,
        *,
        provider_name: str,
        tenant_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> Optional[IntegrationSetting]:
        """Carga un IntegrationSetting filtrando por tenant/usuario.

        Reglas:
        - Si `user_id` viene Y el provider es per-user → busca la fila
          (provider, tenant, user). Sin fallback al per-tenant: si el
          usuario no tiene credenciales propias, NO le robamos las del
          admin "por suerte".
        - Si `user_id` viene Y el provider es per-tenant (Slack, Notion,
          Resend, …) → buscamos la fila per-tenant (user_id IS NULL).
        - Si NO viene `user_id` → comportamiento legacy: primera fila con
          ese provider. Solo usado por código transitorio (auto-curation
          fallback, scripts). Evitar.
        """
        statement = select(IntegrationSetting).where(
            IntegrationSetting.provider_name == provider_name
        )
        if tenant_id is not None:
            statement = statement.where(IntegrationSetting.tenant_id == tenant_id)

        if user_id is not None:
            if provider_name in PER_USER_INTEGRATION_PROVIDERS:
                statement = statement.where(IntegrationSetting.user_id == user_id)
            else:
                statement = statement.where(IntegrationSetting.user_id == None)  # noqa: E711
        return session.exec(statement).first()


integration_setting = CRUDIntegrationSetting(IntegrationSetting)
