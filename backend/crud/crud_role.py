from crud.base import CRUDBase
from models import Role

class CRUDRole(CRUDBase[Role, Role, Role]):
    pass

role = CRUDRole(Role)
