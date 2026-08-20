"""Que una instalación desde cero levante.

El arranque creaba el tenant «acten» con un INSERT que enumeraba las
columnas a mano. Esa lista se quedó congelada donde se escribió, así que
cada columna NOT NULL añadida después al modelo —`landing_content_json`,
`share_integrations`, `share_routings`— quedaba fuera. En una base ya
existente no se notaba, porque el tenant ya estaba; en una recién creada
el arranque moría con «null value in column … violates not-null
constraint» y no había forma de instalar.

Estas pruebas fallan si alguien vuelve a insertar esa fila a mano: al
añadir una columna obligatoria al modelo, el INSERT incompleto revienta
también aquí.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session, select

import database
from database import DEFAULT_TENANT_SLUG
from models import Tenant


@pytest.fixture()
def arranque(test_engine, monkeypatch):
    """`_asegurar_tenant_default` apuntando a la base de pruebas."""
    monkeypatch.setattr(database, "engine", test_engine)
    return database._asegurar_tenant_default


def test_crea_el_tenant_por_defecto_en_una_base_vacia(arranque, db_session: Session):
    tenant_id = arranque()

    fila = db_session.exec(
        select(Tenant).where(Tenant.slug == DEFAULT_TENANT_SLUG)
    ).first()
    assert fila is not None, "sin tenant por defecto no se puede entrar"
    assert fila.id == tenant_id


def test_no_deja_columnas_obligatorias_sin_valor(arranque, db_session: Session):
    arranque()
    t = db_session.exec(
        select(Tenant).where(Tenant.slug == DEFAULT_TENANT_SLUG)
    ).first()

    # Las que se quedaban fuera del INSERT escrito a mano.
    assert t.landing_content_json is not None
    assert t.share_integrations is not None
    assert t.share_routings is not None
    assert t.branding_json is not None


def test_llamarlo_dos_veces_no_duplica(arranque, db_session: Session):
    primero = arranque()
    segundo = arranque()

    assert primero == segundo
    cuantos = len(db_session.exec(
        select(Tenant).where(Tenant.slug == DEFAULT_TENANT_SLUG)
    ).all())
    assert cuantos == 1, "el arranque corre en cada despliegue: no puede duplicar"
