from crud.base import CRUDBase
from models import Template

class CRUDTemplate(CRUDBase[Template, Template, Template]):
    pass

template = CRUDTemplate(Template)
