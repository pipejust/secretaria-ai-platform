"""Un administrador gestiona las capturas de SU empresa, no las de otra.

`own()` eximía al administrador de ser el dueño de la grabación, pero la
condición cortocircuitaba y lo eximía también de que la grabación fuera
de su empresa: con solo conocer el UUID podía pedir, parar o reproducir
la de otro cliente.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlmodel import Session

from auth_utils import get_password_hash
from models import Role, Tenant, User
from routers.bot_control import BotControlLink, own


@pytest.fixture()
def dos_empresas(db_session: Session):
    from sqlmodel import select

    rol = db_session.exec(select(Role).where(Role.name == "admin")).first()
    if not rol:
        rol = Role(name="admin"); db_session.add(rol); db_session.commit(); db_session.refresh(rol)
    out = []
    for n in ("a", "b"):
        suf = uuid.uuid4().hex[:8]
        t = Tenant(slug=f"own-{n}-{suf}", name=n)
        db_session.add(t); db_session.commit(); db_session.refresh(t)
        u = User(email=f"admin-{suf}@{n}.test", full_name="Admin", hashed_password=get_password_hash("x"),
                 role_id=rol.id, tenant_id=t.id, is_active=True)
        db_session.add(u); db_session.commit(); db_session.refresh(u)
        u.role = rol
        out.append((t, u))
    return out


def test_un_admin_no_ve_la_captura_de_otra_empresa(dos_empresas, db_session):
    (ta, admin_a), (tb, admin_b) = dos_empresas
    mid = str(uuid.uuid4())
    db_session.add(BotControlLink(tenant_id=ta.id, owner_id=admin_a.id, external_id="x",
                                  meeting_id=mid, kind="meeting"))
    db_session.commit()

    assert own(db_session, admin_a, mid) is not None
    with pytest.raises(HTTPException) as exc:
        own(db_session, admin_b, mid)
    assert exc.value.status_code == 404


def test_un_admin_no_ve_una_captura_que_no_existe(dos_empresas, db_session):
    (_, admin_a), _ = dos_empresas
    with pytest.raises(HTTPException):
        own(db_session, admin_a, str(uuid.uuid4()))
