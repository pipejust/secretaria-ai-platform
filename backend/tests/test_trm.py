import httpx
import pytest
from sqlmodel import select

from models import AddOn, Plan
from services import billing_catalog as cat
from services import trm


def _cliente(valor=None, status=200):
    def handler(request: httpx.Request):
        assert "vigenciadesde" in request.url.params["$where"]
        if status != 200:
            return httpx.Response(status)
        return httpx.Response(200, json=[{"valor": valor, "unidad": "COP"}])

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_obtener_trm_lee_el_valor_oficial():
    assert trm.obtener_trm(_cliente("3182.99")) == pytest.approx(3182.99)


@pytest.mark.parametrize("valor,status", [("12", 200), ("abc", 200), ("3000", 503)])
def test_trm_invalida_o_caida_no_se_usa(valor, status):
    with pytest.raises(trm.TrmNoDisponible):
        trm.obtener_trm(_cliente(valor, status))


def test_cop_se_redondea_a_miles_de_pesos():
    # 20 USD × 3 182,99 = 63 659,8 COP → 64 000 COP (6 400 000 centavos)
    assert trm.cop_cents(2000, 3182.99) == 6_400_000
    assert trm.cop_cents(0, 3182.99) == 0


@pytest.fixture()
def catalogo_restaurado(db_session):
    """La base de pruebas se comparte: se devuelven los precios en COP como estaban."""
    cat.sembrar_catalogo(db_session)
    filas = [*db_session.exec(select(Plan)).all(), *db_session.exec(select(AddOn)).all()]
    guardado = [(f, f.price_cop_cents) for f in filas]
    yield
    for fila, cop in guardado:
        fila.price_cop_cents = cop
        db_session.add(fila)
    db_session.commit()


def test_actualizar_precios_cop_usa_la_tasa_y_conserva_si_falla(catalogo_restaurado, db_session, monkeypatch):
    assert trm.actualizar_precios_cop(db_session, 3200.0) == 3200.0
    starter = db_session.exec(select(Plan).where(Plan.key == "starter")).one()
    assert starter.price_cop_cents == trm.cop_cents(starter.price_usd_cents, 3200.0)
    enterprise = db_session.exec(select(Plan).where(Plan.key == "enterprise")).one()
    assert enterprise.price_cop_cents == 0
    antes = db_session.exec(select(AddOn).where(AddOn.key == "owned_bot")).one().price_cop_cents
    monkeypatch.setattr(trm, "obtener_trm", lambda *a, **k: (_ for _ in ()).throw(trm.TrmNoDisponible("caída")))
    assert trm.actualizar_precios_cop(db_session) is None
    assert db_session.exec(select(AddOn).where(AddOn.key == "owned_bot")).one().price_cop_cents == antes
