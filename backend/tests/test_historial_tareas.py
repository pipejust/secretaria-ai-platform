"""El historial de una tarea: quién, qué cambió y desde qué valor.

Lo que se protege: que el evento deje de decir solo «quedó en pending» y
pase a decir «Ana lo movió de done a pending el día tal». Sin el actor, el
historial no puede responder a lo que se le pregunta; sin `from`, no
distingue «lo cambió» de «así estaba».
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlmodel import Session, select

from auth_utils import create_access_token, get_password_hash
from models import ActionItem, MeetingSession, Role, TaskEvent, Tenant, User
from services import task_events


@pytest.fixture()
def tarea(db_session: Session):
    suf = uuid.uuid4().hex[:8]
    t = Tenant(slug=f"hist-{suf}", name="Hist")
    db_session.add(t); db_session.commit(); db_session.refresh(t)
    rol = db_session.exec(select(Role).where(Role.name == "admin")).first()
    if not rol:
        rol = Role(name="admin"); db_session.add(rol); db_session.commit(); db_session.refresh(rol)
    u = User(email=f"ana-{suf}@hist.test", full_name="Ana Gómez",
             hashed_password=get_password_hash("x"), role_id=rol.id,
             tenant_id=t.id, is_active=True)
    db_session.add(u); db_session.commit(); db_session.refresh(u)
    ms = MeetingSession(tenant_id=t.id, fireflies_id=f"ff-{suf}",
                        title="Reunión", date="2026-08-20T10:00:00Z")
    db_session.add(ms); db_session.commit(); db_session.refresh(ms)
    it = ActionItem(tenant_id=t.id, session_id=ms.id, owner_name="Beto",
                    owner_email="beto@hist.test", title="Tarea",
                    status="done", due_date="2026-09-01", priority="media")
    db_session.add(it); db_session.commit(); db_session.refresh(it)
    return t, u, it


def test_registra_quien_y_de_que_valor_a_cual(tarea, db_session, monkeypatch):
    monkeypatch.setattr(task_events, "_mandar", lambda *a, **k: None)
    _, usuario, item = tarea

    antes = task_events.instantanea(item)
    item.status = "pending"
    item.due_date = "2026-09-15"
    db_session.add(item); db_session.commit()

    ev = task_events.registrar(
        db_session, item, antes, task_events.actor_de_usuario(db_session, usuario))

    assert ev is not None
    cambios = json.loads(ev.changes_json)
    assert cambios["status"] == {"from": "done", "to": "pending"}
    assert cambios["due_date"] == {"from": "2026-09-01", "to": "2026-09-15"}
    # Y lo que no se tocó no aparece: si saliera, el historial diría que
    # se cambió el título cada vez que alguien mueve el estado.
    assert "title" not in cambios
    assert ev.actor_kind == "user"
    assert ev.actor_name == "Ana Gómez"
    assert ev.actor_user_id == usuario.id


def test_un_cambio_que_no_cambia_nada_no_se_registra(tarea, db_session, monkeypatch):
    monkeypatch.setattr(task_events, "_mandar", lambda *a, **k: None)
    _, usuario, item = tarea

    antes = task_events.instantanea(item)
    ev = task_events.registrar(
        db_session, item, antes, task_events.actor_de_usuario(db_session, usuario))

    assert ev is None, "un guardado sin cambios llenaría el historial de ruido"


def test_el_actor_de_on_behalf_of_es_la_persona_no_la_clave(tarea, db_session):
    """El atajo que ya existía: quien actúa es el empleado, no la integración."""
    _, usuario, _ = tarea

    class CtxConEmpleado:
        acting_user = usuario
        api_key = type("K", (), {"name": "Altum"})()

    actor = task_events.actor_de_integracion(db_session, CtxConEmpleado())
    assert actor["kind"] == "user"
    assert actor["name"] == "Ana Gómez"
    assert actor["via"] == "integration"


def test_sin_on_behalf_of_el_actor_es_la_integracion(db_session):
    """No se le atribuye a una persona lo que hizo una clave."""
    class CtxSinEmpleado:
        acting_user = None
        api_key = type("K", (), {"name": "Altum"})()

    actor = task_events.actor_de_integracion(db_session, CtxSinEmpleado())
    assert actor["kind"] == "integration"
    assert actor["name"] == "Altum"
    assert actor["id"] is None


def test_el_historial_sobrevive_al_borrado_de_la_tarea(tarea, db_session, monkeypatch):
    monkeypatch.setattr(task_events, "_mandar", lambda *a, **k: None)
    _, usuario, item = tarea
    task_events.registrar(
        db_session, item, None, task_events.actor_de_usuario(db_session, usuario),
        kind="deleted")
    task_id = item.id

    db_session.delete(item)
    db_session.commit()

    quedan = db_session.exec(
        select(TaskEvent).where(TaskEvent.task_id == task_id)).all()
    assert len(quedan) == 1, "sin historial no se sabe quién la borró"
    assert quedan[0].kind == "deleted"


def test_el_webhook_lleva_actor_cambios_y_hora(tarea, db_session, monkeypatch):
    """La forma exacta que consume la otra plataforma."""
    enviados = []
    monkeypatch.setattr(
        "services.webhook_sender.send_event_bg",
        lambda tipo, cuerpo, **k: enviados.append((tipo, cuerpo)))
    _, usuario, item = tarea

    antes = task_events.instantanea(item)
    item.status = "pending"
    db_session.add(item); db_session.commit()
    task_events.registrar(
        db_session, item, antes, task_events.actor_de_usuario(db_session, usuario))

    assert len(enviados) == 1
    tipo, cuerpo = enviados[0]
    assert tipo == "task.updated"
    assert cuerpo["actor"]["name"] == "Ana Gómez"
    assert cuerpo["actor"]["kind"] == "user"
    assert cuerpo["changes"]["status"] == {"from": "done", "to": "pending"}
    assert cuerpo["occurred_at"].startswith("2026-")
    assert cuerpo["event_id"]
    # Se mantiene lo que ya consumían, para no romperles nada.
    assert cuerpo["status"] == "pending"
    assert cuerpo["task_id"] == item.id


# ─────────────────────────────────────────────────────────────────────────────
# El empleado que todavía no tiene cuenta en Acten
# ─────────────────────────────────────────────────────────────────────────────

def test_un_empleado_sin_cuenta_firma_con_su_nombre(db_session, monkeypatch):
    """Cinco de sus once empleados no tienen usuario aquí.

    Cuando uno de ellos mueve una tarea desde la otra plataforma, el
    cambio es suyo. Firmarlo como «la integración» sería perder justo el
    dato que el historial viene a dar.
    """
    task_events._CACHE_EMPLEADOS.clear()
    monkeypatch.setattr(
        task_events, "_empleado",
        lambda eid: {"id": eid, "display_name": "Miguel Campo",
                     "full_name": "Miguel Ángel Campo Díaz"})

    class Ctx:
        acting_user = None
        on_behalf_of = "id-de-miguel"
        api_key = type("K", (), {"name": "Altum"})()

    actor = task_events.actor_de_integracion(db_session, Ctx())
    assert actor["kind"] == "user"
    assert actor["name"] == "Miguel Campo"        # el de pantalla, no el legal
    assert actor["employee_external_id"] == "id-de-miguel"
    assert actor["sin_cuenta_en_acten"] is True
    assert actor["id"] is None, "no se crea cuenta por un evento entrante"


def test_si_el_directorio_no_contesta_no_se_inventa_el_nombre(db_session, monkeypatch):
    task_events._CACHE_EMPLEADOS.clear()
    monkeypatch.setattr(task_events, "_empleado", lambda eid: None)

    class Ctx:
        acting_user = None
        on_behalf_of = "id-desconocido"
        api_key = type("K", (), {"name": "Altum"})()

    actor = task_events.actor_de_integracion(db_session, Ctx())
    assert actor["kind"] == "integration"
    assert actor["name"] == "Altum"
    # El id se conserva igual: sirve para enlazar después.
    assert actor["employee_external_id"] == "id-desconocido"


def test_el_directorio_se_pregunta_una_sola_vez(db_session, monkeypatch):
    """Sin caché, cada movimiento de tarea añadiría una llamada de red."""
    task_events._CACHE_EMPLEADOS.clear()
    llamadas = []

    class ClienteFalso:
        configured = True
        def employee(self, eid, timeout=4.0):
            llamadas.append(eid)
            return {"id": eid, "display_name": "Natalia Gaviria"}

    monkeypatch.setattr("services.servicios_sync.ServiciosClient", ClienteFalso)

    for _ in range(5):
        assert task_events._empleado("id-natalia")["display_name"] == "Natalia Gaviria"
    assert len(llamadas) == 1, f"se preguntó {len(llamadas)} veces"
