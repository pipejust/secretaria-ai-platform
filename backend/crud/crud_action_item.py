from crud.base import CRUDBase
from models import ActionItem

class CRUDActionItem(CRUDBase[ActionItem, ActionItem, ActionItem]):
    pass

action_item = CRUDActionItem(ActionItem)
