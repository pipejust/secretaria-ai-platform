from crud.base import CRUDBase
from models import Routing

class CRUDRouting(CRUDBase[Routing, Routing, Routing]):
    pass

routing = CRUDRouting(Routing)
