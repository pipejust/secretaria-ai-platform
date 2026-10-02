"""La misma reunión grabada por el bot y por Fireflies: queda una, la del bot."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from models import ActionItem, MeetingSession, Tenant
from services import duplicados

FRASES = [
    "revisamos el cronograma de la obra y los pagos pendientes del contratista",
    "el proveedor entrega los planos corregidos el jueves antes del mediodía",
    "hay que definir quién aprueba el cambio de materiales en la fachada norte",
    "la interventoría pidió el acta de vecindad y las pólizas actualizadas",
    "quedamos en enviar el informe semanal al cliente cada viernes por la tarde",
    "el presupuesto adicional se discute en el comité de la próxima semana",
]
# Dos motores oyendo el mismo audio: casi lo mismo, con alguna palabra distinta.
DEL_BOT = "\n".join(f"[Ana Ruiz] {f.capitalize()}." for f in FRASES * 3)
DE_FIREFLIES = "\n".join(f"[Speaker 1] {f.replace('jueves', 'juebes').replace('pólizas', 'polizas')}" for f in FRASES * 3)
OTRA_REUNION = "\n".join(
    f"[Luis] {f}" for f in [
        "el despliegue de producción se movió para el martes en la madrugada",
        "falta probar la migración de la base con los datos del mes pasado",
        "el cliente quiere ver el tablero de indicadores antes de aprobar",
        "se contrata un tester adicional para las pruebas de regresión",
        "la documentación de la interfaz se entrega junto con el manual",
        "revisamos los permisos de los usuarios administradores del portal",
    ] * 3
)
AHORA = datetime.now(timezone.utc)


def empresa(db):
    t = Tenant(slug=f"dup-{uuid.uuid4().hex[:8]}", name="Duplicados", meeting_source="both")
    db.add(t); db.commit(); db.refresh(t)
    return t


def sesion(db, t, *, bot, texto, minutos=0, status="pending"):
    ident = f"BOT-{uuid.uuid4()}" if bot else f"01M{uuid.uuid4().hex[:20].upper()}"
    s = MeetingSession(tenant_id=t.id, fireflies_id=ident, title="Comité de obra", status=status,
                       date=(AHORA + timedelta(minutes=minutos)).isoformat(), raw_transcript=texto)
    db.add(s); db.commit(); db.refresh(s)
    return s


def test_las_transcripciones_de_la_misma_reunion_se_reconocen():
    assert duplicados.parecido(DEL_BOT, DE_FIREFLIES) > 0.7
    assert duplicados.parecido(DEL_BOT, OTRA_REUNION) < 0.05
    assert duplicados.parecido("Hola, ¿me oyen?", "Hola, ¿me oyen?") == 0.0  # demasiado corta para decidir


def test_llega_fireflies_despues_del_bot_y_queda_archivada(db_session):
    t = empresa(db_session)
    bot = sesion(db_session, t, bot=True, texto=DEL_BOT)
    ff = sesion(db_session, t, bot=False, texto=DE_FIREFLIES, minutos=4)
    assert duplicados.resolver_al_llegar(db_session, ff) is True   # no se analiza
    db_session.refresh(ff); db_session.refresh(bot)
    assert ff.status == "archived" and ff.duplicate_of == bot.id
    assert bot.status == "pending" and bot.duplicate_of is None


def test_llega_el_bot_despues_y_gana_igual(db_session):
    t = empresa(db_session)
    ff = sesion(db_session, t, bot=False, texto=DE_FIREFLIES)
    tareas = [ActionItem(session_id=ff.id, tenant_id=t.id, title=f"T{i}", owner_name="", owner_email="",
                         status=estado) for i, estado in enumerate(("pending", "blocked", "done"))]
    for x in tareas:
        db_session.add(x)
    db_session.commit()
    bot = sesion(db_session, t, bot=True, texto=DEL_BOT, minutos=-3)
    assert duplicados.resolver_al_llegar(db_session, bot) is False  # la del bot sí se analiza
    db_session.refresh(ff)
    assert ff.status == "archived" and ff.duplicate_of == bot.id
    estados = sorted(x.status for x in db_session.exec(select(ActionItem).where(ActionItem.session_id == ff.id)).all())
    assert estados == ["cancelled", "cancelled", "done"]          # lo ya hecho no se toca


def test_no_se_archiva_lo_que_no_es_la_misma_reunion_ni_lo_ya_trabajado(db_session):
    t, otra = empresa(db_session), empresa(db_session)
    bot = sesion(db_session, t, bot=True, texto=DEL_BOT)
    distinta = sesion(db_session, t, bot=False, texto=OTRA_REUNION, minutos=5)
    lejana = sesion(db_session, t, bot=False, texto=DE_FIREFLIES, minutos=600)
    ajena = sesion(db_session, otra, bot=False, texto=DE_FIREFLIES, minutos=2)
    subida = MeetingSession(tenant_id=t.id, fireflies_id=f"manual_{uuid.uuid4()}", title="x",
                            date=AHORA.isoformat(), raw_transcript=DE_FIREFLIES)
    db_session.add(subida); db_session.commit(); db_session.refresh(subida)
    for s in (distinta, lejana, ajena, subida):
        assert duplicados.resolver_al_llegar(db_session, s) is False
        db_session.refresh(s)
        assert s.status != "archived"
    # Una de Fireflies que alguien ya aprobó no se archiva aunque llegue el bot.
    t2 = empresa(db_session)
    aprobada = sesion(db_session, t2, bot=False, texto=DE_FIREFLIES, status="approved")
    tardia = sesion(db_session, t2, bot=True, texto=DEL_BOT, minutos=1)
    assert duplicados.resolver_al_llegar(db_session, tardia) is False
    db_session.refresh(aprobada)
    assert aprobada.status == "approved" and aprobada.duplicate_of is None
    db_session.refresh(bot)
    assert bot.status == "pending"
