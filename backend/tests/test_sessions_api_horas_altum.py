"""Tests para `services/sessions_api.py` — helpers de la fase A/B del contrato Altum."""

from __future__ import annotations

import json
import uuid

import pytest

from models import MeetingSession, Project, ProjectContact, Tenant, User
from services.sessions_api import (
    duration_min_from,
    enriquecer_asistentes,
    ilike_pattern_para_correo,
    leer_asistentes_de_sesion,
    resolver_correos_de_participante,
)


# ─── duration_min_from ──────────────────────────────────────────────────

def test_duration_min_from_calcula_minutos_entre_dos_iso():
    assert duration_min_from("2026-11-16T10:00:00+00:00", "2026-11-16T10:45:30+00:00") == 46


def test_duration_min_from_tolera_sufijo_z():
    assert duration_min_from("2026-11-16T10:00:00Z", "2026-11-16T10:30:00Z") == 30


def test_duration_min_from_devuelve_none_si_falta_dato():
    assert duration_min_from(None, "2026-11-16T10:00:00Z") is None
    assert duration_min_from("2026-11-16T10:00:00Z", None) is None
    assert duration_min_from(None, None) is None


def test_duration_min_from_devuelve_none_si_formato_invalido():
    assert duration_min_from("mañana", "pasado") is None


def test_duration_min_from_descarta_rango_negativo():
    # Ended antes de started → None, no un número negativo que confunda al UI.
    assert duration_min_from("2026-11-16T10:30:00Z", "2026-11-16T10:00:00Z") is None


def test_duration_min_from_minimo_1_minuto_para_duraciones_sub_minuto():
    assert duration_min_from("2026-11-16T10:00:00Z", "2026-11-16T10:00:15Z") == 1


# ─── resolver_correos_de_participante ───────────────────────────────────

@pytest.fixture()
def tenant_con_personas(db_session):
    # Slug único por test porque el engine es sesión-scoped (StaticPool).
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", name="Test")
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)

    u = User(
        tenant_id=tenant.id,
        email="ana@acme.co",
        full_name="Ana",
        hashed_password="x",
        external_ref="EMP-ANA",
    )
    db_session.add(u)
    proj = Project(tenant_id=tenant.id, name="Proj A")
    db_session.add(proj)
    db_session.commit()
    db_session.refresh(proj)
    c = ProjectContact(
        project_id=proj.id,
        name="Beto",
        email="beto@acme.co",
        role="cliente",
        external_ref="CONT-BETO",
    )
    db_session.add(c)
    db_session.commit()
    return tenant


def test_resolver_correos_por_email_directo_canoniza(db_session, tenant_con_personas):
    assert resolver_correos_de_participante(
        db_session, tenant_con_personas.id, "Ana@acme.co"
    ) == ["ana@acme.co"]


def test_resolver_correos_por_external_ref_de_user(db_session, tenant_con_personas):
    assert resolver_correos_de_participante(
        db_session, tenant_con_personas.id, "EMP-ANA"
    ) == ["ana@acme.co"]


def test_resolver_correos_por_external_ref_de_contact(db_session, tenant_con_personas):
    assert resolver_correos_de_participante(
        db_session, tenant_con_personas.id, "CONT-BETO"
    ) == ["beto@acme.co"]


def test_resolver_correos_sin_match_devuelve_lista_vacia(db_session, tenant_con_personas):
    assert resolver_correos_de_participante(
        db_session, tenant_con_personas.id, "NO-EXISTE"
    ) == []


def test_resolver_correos_ignora_valor_vacio_o_blanco(db_session, tenant_con_personas):
    assert resolver_correos_de_participante(db_session, tenant_con_personas.id, "") == []
    assert resolver_correos_de_participante(db_session, tenant_con_personas.id, "  ") == []


def test_resolver_no_cruza_tenants(db_session):
    # Dos tenants, misma ref: cada uno solo ve la suya.
    t1 = Tenant(slug=f"a-{uuid.uuid4().hex[:8]}", name="A")
    t2 = Tenant(slug=f"b-{uuid.uuid4().hex[:8]}", name="B")
    db_session.add_all([t1, t2]); db_session.commit(); db_session.refresh(t1); db_session.refresh(t2)
    u1 = User(tenant_id=t1.id, email="x@a.co", full_name="X", hashed_password="x", external_ref="SHARED")
    u2 = User(tenant_id=t2.id, email="x@b.co", full_name="X", hashed_password="x", external_ref="SHARED")
    db_session.add_all([u1, u2]); db_session.commit()
    assert resolver_correos_de_participante(db_session, t1.id, "SHARED") == ["x@a.co"]
    assert resolver_correos_de_participante(db_session, t2.id, "SHARED") == ["x@b.co"]


# ─── enriquecer_asistentes ──────────────────────────────────────────────

def test_enriquecer_asistentes_llena_employee_external_id_desde_user(
    db_session, tenant_con_personas
):
    asis = [{"name": "Ana", "email": "ana@acme.co", "role": "—", "entity": "—"}]
    out = enriquecer_asistentes(db_session, tenant_con_personas.id, asis)
    assert out == [
        {"name": "Ana", "email": "ana@acme.co", "role": "—", "entity": "—",
         "employee_external_id": "EMP-ANA"}
    ]


def test_enriquecer_asistentes_llena_desde_contact_si_no_hay_user(
    db_session, tenant_con_personas
):
    asis = [{"name": "Beto", "email": "beto@acme.co"}]
    out = enriquecer_asistentes(db_session, tenant_con_personas.id, asis)
    assert out[0]["employee_external_id"] == "CONT-BETO"


def test_enriquecer_asistentes_deja_null_si_no_hay_match(db_session, tenant_con_personas):
    asis = [{"name": "Desconocido", "email": "nadie@fuera.com"}]
    out = enriquecer_asistentes(db_session, tenant_con_personas.id, asis)
    assert out[0]["employee_external_id"] is None


def test_enriquecer_asistentes_no_muta_originales(db_session, tenant_con_personas):
    asis = [{"name": "Ana", "email": "ana@acme.co"}]
    _ = enriquecer_asistentes(db_session, tenant_con_personas.id, asis)
    assert "employee_external_id" not in asis[0]


def test_enriquecer_asistentes_canoniza_email_mayus(db_session, tenant_con_personas):
    asis = [{"name": "Ana", "email": "ANA@ACME.CO"}]
    out = enriquecer_asistentes(db_session, tenant_con_personas.id, asis)
    assert out[0]["employee_external_id"] == "EMP-ANA"


# ─── leer_asistentes_de_sesion ──────────────────────────────────────────

def test_leer_asistentes_de_sesion_desde_json_list():
    s = MeetingSession(
        tenant_id=1, fireflies_id="x", title="t", date="2026-11-16",
        processed_attendees=json.dumps([{"name": "A"}, {"name": "B"}])
    )
    assert leer_asistentes_de_sesion(s) == [{"name": "A"}, {"name": "B"}]


def test_leer_asistentes_de_sesion_tolera_vacio_o_invalido():
    for raw in ("", "   ", "no es json", "null", "[]"):
        s = MeetingSession(
            tenant_id=1, fireflies_id="x", title="t", date="2026-11-16",
            processed_attendees=raw,
        )
        assert leer_asistentes_de_sesion(s) == []


def test_leer_asistentes_de_sesion_desde_json_dict_lo_envuelve_en_lista():
    s = MeetingSession(
        tenant_id=1, fireflies_id="x", title="t", date="2026-11-16",
        processed_attendees=json.dumps({"name": "solo"}),
    )
    assert leer_asistentes_de_sesion(s) == [{"name": "solo"}]


# ─── ilike_pattern_para_correo ──────────────────────────────────────────

def test_ilike_pattern_produce_patron_correcto():
    assert ilike_pattern_para_correo("ana@acme.co") == '%"email": "ana@acme.co"%'


def test_ilike_pattern_escapa_wildcards_sql():
    # Un correo con % o _ no debe convertirse en wildcard sin controlar.
    pat = ilike_pattern_para_correo("pepe%admin@acme.co")
    assert pat == '%"email": "pepe\\%admin@acme.co"%'
