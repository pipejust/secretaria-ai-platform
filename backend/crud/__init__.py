from .crud_project import project
from .crud_user import user
from .crud_template import template
from .crud_routing import routing
from .crud_meeting_session import meeting_session
from .crud_action_item import action_item
from .crud_role import role
from .crud_integration_setting import integration_setting

from models import ProjectContact
from .base import CRUDBase
project_contact = CRUDBase[ProjectContact, ProjectContact, ProjectContact](ProjectContact)

# For a new basic set of items for which you don't even want to create a new file, you could do:
# from crud.base import CRUDBase
# from models import Item
# item = CRUDBase[Item, Item, Item](Item)
