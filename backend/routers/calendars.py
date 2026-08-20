"""El módulo de calendarios: la lista, los permisos, los eventos y las conexiones.

Todo cuelga de `/api/v1/calendars`. La agenda devuelve cada entrada con
el campo `calendar`, que es la clave de a cuál pertenece: la pantalla
apaga y enciende con eso sin volver a preguntar, porque pedir la agenda
otra vez por cada clic haría que el calendario parpadeara.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

from anyio import to_thread
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from database import get_session
from models import (
    Calendar, CalendarAccount, CalendarEntry, CalendarShare, ExternalEvent, User,
)
from routers.auth import get_current_user, require_admin
from services import (
    calendar_check, calendar_config, calendar_google, calendar_ics,
    calendar_microsoft, calendar_sync, calendar_write, calendars as svc,
)
from services.calendar_crypto import cifrar
from services.calendar_providers import (
    PROVEEDORES, conocido, etiqueta, zoho_servidor_valido,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/calendars", tags=["Calendarios"])

CONNECT_TTL_MIN = 10
CONNECT_PURPOSE = "calendar_connect_v2"


# ══════════════════════════════════════════════════════════════════════
# El `state` del viaje de OAuth
# ══════════════════════════════════════════════════════════════════════
# Va firmado y caduca en diez minutos. Sin eso, un enlace de vuelta
# preparado por otro colgaría su cuenta de Google en tu sesión.

def _firmar_state(user: User, provider: str, volver: str) -> str:
    from auth_utils import create_access_token
    return create_access_token(
        {"purpose": CONNECT_PURPOSE, "uid": user.id, "tid": user.tenant_id,
         "provider": provider, "volver": volver},
        expires_delta=timedelta(minutes=CONNECT_TTL_MIN),
    )


def _leer_state(state: str, provider: str) -> dict:
    from jose import JWTError, jwt
    from auth_utils import ALGORITHM, SECRET_KEY
    try:
        datos = jwt.decode(state, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as e:
        raise HTTPException(400, "El enlace de conexión caducó. Vuelve a intentarlo.") from e
    if datos.get("purpose") != CONNECT_PURPOSE or datos.get("provider") != provider:
        raise HTTPException(400, "El enlace de conexión no es de este proveedor.")
    return datos


def _volver_seguro(destino: str) -> str:
    """Solo caminos internos.

    Un destino que se acepta tal como llega es una redirección abierta:
    convierte nuestro dominio en el trampolín que le da credibilidad a
    una página falsa.
    """
    d = (destino or "").strip()
    if not d.startswith("/") or d.startswith("//"):
        return "/admin/calendar"
    if urlparse(d).netloc:
        return "/admin/calendar"
    return d


# ══════════════════════════════════════════════════════════════════════
# La lista
# ══════════════════════════════════════════════════════════════════════

@router.get("")
def listar(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Los calendarios de quien pregunta, con permiso, color y casilla resueltos."""
    svc.asegurar_propio(db, user)
    return {"calendars": svc.lista_para(db, user)}


class CalendarioNuevo(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    color: str = "#6366f1"
    description: str = ""
    origin: str = Field(default="propio", pattern="^(propio|equipo|proyecto)$")
    project_id: Optional[int] = None
    timezone: str = "America/Bogota"


@router.post("", status_code=201)
def crear(
    cuerpo: CalendarioNuevo,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    if cuerpo.origin in ("equipo", "proyecto") and not (
            user.role and user.role.name == "admin"):
        raise HTTPException(
            403, "Los calendarios de equipo y de proyecto los crea un administrador.")
    cal = Calendar(
        key=uuid.uuid4().hex, tenant_id=user.tenant_id, name=cuerpo.name.strip(),
        description=cuerpo.description, color=cuerpo.color, origin=cuerpo.origin,
        timezone=cuerpo.timezone, project_id=cuerpo.project_id,
        owner_user_id=user.id if cuerpo.origin == "propio" else None,
    )
    db.add(cal); db.commit(); db.refresh(cal)
    if cuerpo.origin in ("equipo", "proyecto"):
        # Un calendario de la empresa nace visible para la empresa; si no,
        # quien lo crea es el único que lo ve y nadie entiende por qué.
        db.add(CalendarShare(calendar_id=cal.id, user_id=None, permission="ver"))
        db.commit()
    return {"calendar": _uno(db, user, cal)}


def _uno(db: Session, user: User, cal: Calendar) -> dict:
    for c in svc.lista_para(db, user):
        if c["key"] == cal.key:
            return c
    return {"key": cal.key, "name": cal.name, "color": cal.color,
            "origin": cal.origin, "permission": svc.permiso_de(db, user, cal)}


def _exigir(db: Session, user: User, clave: str, minimo: str) -> Calendar:
    cal, permiso = svc.por_clave(db, user, clave)
    if clave in svc.DERIVADOS:
        raise HTTPException(
            400, "Los calendarios de sistema (sesiones, tareas, festivos) se "
                 "calculan: no se editan ni se borran. Puedes apagarlos.")
    if not cal:
        raise HTTPException(404, "Ese calendario no existe.")
    if not svc.puede(permiso, minimo):
        raise HTTPException(403, f"Hace falta permiso de «{minimo}» sobre este calendario.")
    return cal


class CalendarioCambio(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = None
    color: Optional[str] = None
    is_default: Optional[bool] = None


@router.patch("/{clave}")
def editar(
    clave: str, cuerpo: CalendarioCambio,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    cal = _exigir(db, user, clave, "gestionar")
    if cuerpo.name is not None:
        cal.name = cuerpo.name.strip()
    if cuerpo.description is not None:
        cal.description = cuerpo.description
    if cuerpo.color is not None:
        cal.color = cuerpo.color
    if cuerpo.is_default is not None and cuerpo.is_default:
        for otro in db.exec(select(Calendar).where(
                Calendar.owner_user_id == user.id)).all():
            otro.is_default = False
            db.add(otro)
        cal.is_default = True
    db.add(cal); db.commit()
    return {"calendar": _uno(db, user, cal)}


@router.delete("/{clave}")
def borrar(
    clave: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Se lleva el calendario. Sus eventos propios quedan sueltos, no se borran.

    Lo traído de fuera sí se va entero: sin su calendario no hay dónde
    pintarlo y nadie podría volver a leerlo.
    """
    cal = _exigir(db, user, clave, "gestionar")
    for x in db.exec(select(ExternalEvent).where(
            ExternalEvent.calendar_id == cal.id)).all():
        db.delete(x)
    for s in db.exec(select(CalendarShare).where(
            CalendarShare.calendar_id == cal.id)).all():
        db.delete(s)
    sueltos = db.exec(select(CalendarEntry).where(
        CalendarEntry.calendar_id == cal.id)).all()
    destino = svc.destino_por_defecto(db, user) if sueltos else None
    for e in sueltos:
        if destino and destino.id != cal.id:
            e.calendar_id = destino.id
            e.external_uid = ""
            db.add(e)
    db.delete(cal); db.commit()
    return {"status": "borrado", "eventos_movidos": len(sueltos)}


class Preferencia(BaseModel):
    visible: Optional[bool] = None
    color: Optional[str] = None
    position: Optional[int] = None


@router.put("/{clave}/preferencia")
def preferencia(
    clave: str, cuerpo: Preferencia,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """La casilla y el color de quien pide. Vale también para `sys:...`."""
    if clave not in svc.DERIVADOS:
        cal, permiso = svc.por_clave(db, user, clave)
        if not cal or not permiso:
            raise HTTPException(404, "Ese calendario no existe o no lo ves.")
    fila = svc.guardar_pref(db, user, clave, cuerpo.visible, cuerpo.color, cuerpo.position)
    return {"key": clave, "visible": fila.visible, "color": fila.color,
            "position": fila.position}


# ══════════════════════════════════════════════════════════════════════
# Compartir
# ══════════════════════════════════════════════════════════════════════

@router.get("/{clave}/compartido")
def ver_compartido(
    clave: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    cal = _exigir(db, user, clave, "gestionar")
    filas = db.exec(select(CalendarShare).where(
        CalendarShare.calendar_id == cal.id)).all()
    nombres = {u.id: (u.full_name or u.email) for u in db.exec(
        select(User).where(User.tenant_id == user.tenant_id)).all()}
    return {"shares": [
        {"user_id": s.user_id, "nombre": nombres.get(s.user_id, "Toda la empresa"),
         "permission": s.permission} for s in filas]}


class Compartir(BaseModel):
    user_id: Optional[int] = None      # en blanco = toda la empresa
    permission: str = Field(default="ver", pattern="^(ocupado|ver|editar|gestionar)$")


@router.post("/{clave}/compartido")
def compartir(
    clave: str, cuerpo: Compartir,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    cal = _exigir(db, user, clave, "gestionar")
    if cuerpo.user_id:
        destinatario = db.get(User, cuerpo.user_id)
        if not destinatario or destinatario.tenant_id != user.tenant_id:
            raise HTTPException(404, "Esa persona no está en la empresa.")
    fila = db.exec(select(CalendarShare).where(
        CalendarShare.calendar_id == cal.id,
        CalendarShare.user_id == cuerpo.user_id)).first()
    if not fila:
        fila = CalendarShare(calendar_id=cal.id, user_id=cuerpo.user_id)
    fila.permission = cuerpo.permission
    db.add(fila); db.commit()
    return {"status": "compartido", "permission": fila.permission}


@router.delete("/{clave}/compartido/{user_id}")
def dejar_de_compartir(
    clave: str, user_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """`user_id = 0` quita el de toda la empresa."""
    cal = _exigir(db, user, clave, "gestionar")
    objetivo = None if user_id == 0 else user_id
    fila = db.exec(select(CalendarShare).where(
        CalendarShare.calendar_id == cal.id,
        CalendarShare.user_id == objetivo)).first()
    if not fila:
        raise HTTPException(404, "No estaba compartido con esa persona.")
    db.delete(fila); db.commit()
    return {"status": "retirado"}


# ══════════════════════════════════════════════════════════════════════
# Suscribirse por dirección .ics
# ══════════════════════════════════════════════════════════════════════

class Suscripcion(BaseModel):
    url: str = Field(min_length=8)
    name: str = ""
    color: str = "#0ea5e9"


@router.post("/suscribir", status_code=201)
async def suscribir(
    cuerpo: Suscripcion,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Añade un calendario de fuera por su dirección `.ics`. Cero credenciales.

    Es lo que publica Google en «Dirección secreta en formato iCal»,
    Apple en «Calendario público» y Outlook en «Publicar calendario». Es
    el camino corto y el único que hay para iCloud, que no tiene OAuth de
    calendario por mucho que exista «Iniciar sesión con Apple».
    """
    try:
        texto, etag = await to_thread.run_sync(calendar_ics.descargar, cuerpo.url)
    except calendar_ics.IcsError as e:
        raise HTTPException(400, str(e))
    eventos = calendar_ics.parsear(texto or "")

    cal = Calendar(
        key=uuid.uuid4().hex, tenant_id=user.tenant_id,
        name=(cuerpo.name or "").strip() or _nombre_del_ics(texto or "") or "Calendario suscrito",
        color=cuerpo.color, origin="suscrito", owner_user_id=user.id,
        ics_url=calendar_ics.normalizar_url(cuerpo.url), sync_token=etag or "",
        read_only=True,   # un .ics es solo lectura, siempre
    )
    db.add(cal); db.commit(); db.refresh(cal)

    await calendar_sync.sincronizar_calendario(db, cal)
    return {"calendar": _uno(db, user, cal), "eventos_leidos": len(eventos)}


def _nombre_del_ics(texto: str) -> str:
    for linea in texto.split("\n")[:60]:
        if linea.upper().startswith("X-WR-CALNAME:"):
            return linea.split(":", 1)[1].strip()
    return ""


@router.post("/{clave}/sincronizar")
async def sincronizar_uno(
    clave: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    cal = _exigir(db, user, clave, "ver")
    try:
        n = await calendar_sync.sincronizar_calendario(db, cal)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"No se pudo releer el calendario: {e}")
    return {"status": "ok", "eventos": n, "last_synced_at": cal.last_synced_at}


@router.post("/sincronizar")
async def sincronizar_todo(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    return await calendar_sync.sincronizar_todo_de(db, user)


# ══════════════════════════════════════════════════════════════════════
# Los eventos
# ══════════════════════════════════════════════════════════════════════

@router.get("/eventos")
def listar_eventos(
    desde: Optional[str] = Query(None, description="ISO. Por defecto, hace 30 días."),
    hasta: Optional[str] = Query(None, description="ISO. Por defecto, dentro de 90."),
    calendarios: Optional[str] = Query(
        None, description="Claves separadas por coma. Sin esto, todos los visibles."),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Todo lo que cae en la ventana, con el calendario al que pertenece."""
    ahora = datetime.now(timezone.utc)
    d0 = _fecha(desde) or ahora - timedelta(days=30)
    d1 = _fecha(hasta) or ahora + timedelta(days=90)
    claves = [c.strip() for c in calendarios.split(",") if c.strip()] if calendarios else None
    return {"events": svc.eventos(db, user, d0, d1, claves),
            "desde": d0.isoformat(), "hasta": d1.isoformat()}


def _fecha(v: Optional[str]) -> Optional[datetime]:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(422, f"Fecha ilegible: «{v}». Se espera ISO 8601.")


class EventoNuevo(BaseModel):
    calendar: Optional[str] = Field(
        default=None, description="Clave del calendario. Sin esto, el de por defecto.")
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    location: str = ""
    start_at: str
    end_at: str = ""
    all_day: bool = False
    meeting_url: str = ""
    project_id: Optional[int] = None


@router.post("/eventos", status_code=201)
async def crear_evento(
    cuerpo: EventoNuevo,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Crea el evento aquí y, si el calendario es de fuera, también allá.

    Elegir calendario se comprueba contra el permiso: sin eso, el
    desplegable sería una forma de meterle cosas en la agenda a quien no
    te lo permitió.
    """
    if cuerpo.calendar:
        cal = _exigir(db, user, cuerpo.calendar, "editar")
    else:
        cal = svc.destino_por_defecto(db, user)
    if cal.read_only:
        raise HTTPException(
            403, "Ese calendario es de solo lectura para tu cuenta: lo dice el "
                 "proveedor, no Acten.")

    entrada = CalendarEntry(
        tenant_id=user.tenant_id, calendar_id=cal.id, title=cuerpo.title.strip(),
        description=cuerpo.description, location=cuerpo.location,
        start_at=cuerpo.start_at, end_at=cuerpo.end_at, all_day=cuerpo.all_day,
        meeting_url=cuerpo.meeting_url, project_id=cuerpo.project_id,
        created_by_user_id=user.id,
    )
    db.add(entrada); db.commit(); db.refresh(entrada)
    await calendar_write.crear(db, cal, entrada)
    return {"event": _evento(entrada, cal)}


class EventoCambio(BaseModel):
    calendar: Optional[str] = None
    title: Optional[str] = Field(default=None, min_length=1, max_length=300)
    description: Optional[str] = None
    location: Optional[str] = None
    start_at: Optional[str] = None
    end_at: Optional[str] = None
    all_day: Optional[bool] = None
    meeting_url: Optional[str] = None
    project_id: Optional[int] = None


@router.patch("/eventos/{evento_id}")
async def editar_evento(
    evento_id: int, cuerpo: EventoCambio,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    entrada = db.get(CalendarEntry, evento_id)
    if not entrada or entrada.tenant_id != user.tenant_id:
        raise HTTPException(404, "Ese evento no existe.")
    cal = db.get(Calendar, entrada.calendar_id)
    if not cal or not svc.puede(svc.permiso_de(db, user, cal), "editar"):
        raise HTTPException(403, "No puedes editar en ese calendario.")

    destino = cal
    if cuerpo.calendar and cuerpo.calendar != cal.key:
        destino = _exigir(db, user, cuerpo.calendar, "editar")

    for campo in ("title", "description", "location", "start_at", "end_at",
                  "all_day", "meeting_url", "project_id"):
        valor = getattr(cuerpo, campo)
        if valor is not None:
            setattr(entrada, campo, valor.strip() if isinstance(valor, str) else valor)
    entrada.updated_at = datetime.now(timezone.utc).isoformat()
    db.add(entrada); db.commit(); db.refresh(entrada)

    if destino.id != cal.id:
        await calendar_write.mover(db, cal, destino, entrada)
    else:
        await calendar_write.actualizar(db, cal, entrada)
    return {"event": _evento(entrada, destino)}


@router.delete("/eventos/{evento_id}")
async def borrar_evento(
    evento_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    entrada = db.get(CalendarEntry, evento_id)
    if not entrada or entrada.tenant_id != user.tenant_id:
        raise HTTPException(404, "Ese evento no existe.")
    cal = db.get(Calendar, entrada.calendar_id)
    if not cal or not svc.puede(svc.permiso_de(db, user, cal), "editar"):
        raise HTTPException(403, "No puedes borrar en ese calendario.")
    await calendar_write.borrar(db, cal, entrada)
    db.delete(entrada); db.commit()
    return {"status": "borrado"}


def _evento(e: CalendarEntry, cal: Calendar) -> dict:
    return {
        "id": e.id, "calendar": cal.key, "title": e.title,
        "description": e.description, "location": e.location,
        "start_at": e.start_at, "end_at": e.end_at, "all_day": e.all_day,
        "meeting_url": e.meeting_url, "project_id": e.project_id,
        "external_uid": e.external_uid, "external_error": e.external_error,
    }


# ══════════════════════════════════════════════════════════════════════
# Conectar cuentas
# ══════════════════════════════════════════════════════════════════════

@router.get("/cuentas")
def cuentas(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    filas = db.exec(select(CalendarAccount).where(
        CalendarAccount.user_id == user.id,
        CalendarAccount.is_active == True)).all()  # noqa: E712
    return {"accounts": [{
        "id": c.id, "provider": c.provider, "etiqueta": etiqueta(c.provider),
        "email": c.account_email,
        # Una cuenta sin correo se conectó antes de que se pidiera el
        # permiso que deja saber de quién es. No se arregla sola: hay que
        # reconectarla, y decirlo evita que parezca un fallo.
        "aviso": ("Reconéctala para ver el correo." if not c.account_email else ""),
        "status": c.status, "last_error": c.last_error,
        "data_center": c.data_center,
        "revocar_manual": PROVEEDORES.get(c.provider, {}).get("revoca_manual", ""),
    } for c in filas]}


@router.get("/{provider}/estado")
def estado_proveedor(
    provider: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Si la app está dada de alta y qué cuentas hay conectadas."""
    if not conocido(provider):
        raise HTTPException(404, f"Proveedor desconocido: {provider}")
    est = calendar_config.estado(db, user.tenant_id, provider)
    mias = db.exec(select(CalendarAccount).where(
        CalendarAccount.user_id == user.id,
        CalendarAccount.provider == provider,
        CalendarAccount.is_active == True)).all()  # noqa: E712
    return {"provider": provider, "etiqueta": est["etiqueta"],
            "configurado": est["configurado"],
            "cuentas": [{"id": c.id, "email": c.account_email, "status": c.status}
                        for c in mias]}


@router.get("/{provider}/conectar")
def conectar(
    provider: str,
    volver: str = Query("/admin/calendar", description="Camino interno al que volver."),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """La dirección a la que mandar el navegador."""
    if not conocido(provider):
        raise HTTPException(404, f"Proveedor desconocido: {provider}")
    cfg = calendar_config.cargar(db, user.tenant_id, provider)
    if not cfg.get("client_id"):
        raise HTTPException(
            424,
            f"{etiqueta(provider)} no está dado de alta. Un administrador tiene "
            "que pegar el ID de cliente y el secreto en Configuración → Calendarios.")
    state = _firmar_state(user, provider, _volver_seguro(volver))
    mod = {"google": calendar_google, "microsoft": calendar_microsoft}.get(provider)
    if provider == "zoho":
        from services import calendar_zoho
        mod = calendar_zoho
    return {"url": mod.url_autorizacion(cfg, state)}


@router.get("/{provider}/callback")
async def callback(
    provider: str,
    request: Request,
    code: Optional[str] = Query(None),
    state: str = Query(...),
    error: Optional[str] = Query(None),
    db: Session = Depends(get_session),
):
    """Donde aterriza el navegador al volver del proveedor.

    Aquí no hay sesión: el viaje vuelve por una puerta sin cookies. Quién
    es y dónde estaba viaja dentro del `state` firmado; si no, aterriza
    en el inicio y parece que no pasó nada.
    """
    datos = _leer_state(state, provider)
    destino = _volver_seguro(datos.get("volver", ""))
    if error or not code:
        return RedirectResponse(f"{destino}?calendario_error={error or 'sin_codigo'}")

    user = db.get(User, datos["uid"])
    if not user or user.tenant_id != datos["tid"]:
        raise HTTPException(400, "El enlace de conexión ya no es válido.")
    cfg = calendar_config.cargar(db, user.tenant_id, provider)

    try:
        if provider == "google":
            tok = await calendar_google.canjear(cfg, code)
        elif provider == "microsoft":
            tok = await calendar_microsoft.canjear(cfg, code)
        else:
            from services import calendar_zoho
            # Zoho dice en el retorno en qué servidor vive esa persona y
            # exige canjear el código ahí. Se valida contra la lista
            # exacta: ahí es donde se manda el secreto de la aplicación.
            crudo = request.query_params.get("accounts-server", "")
            centro = zoho_servidor_valido(crudo) if crudo else None
            if crudo and not centro:
                raise HTTPException(400, f"Zoho devolvió un servidor que no reconozco: {crudo}")
            tok = await calendar_zoho.canjear(cfg, code, centro or cfg.get("data_center", "com"))
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("callback de %s falló", provider)
        return RedirectResponse(f"{destino}?calendario_error={str(e)[:120]}")

    cuenta = _guardar_cuenta(db, user, provider, tok)
    try:
        await calendar_sync.descubrir_calendarios(db, cuenta, user)
        await calendar_sync.sincronizar_todo_de(db, user)
    except Exception as e:  # noqa: BLE001
        logger.warning("Cuenta conectada pero el primer sync falló: %s", e)
    return RedirectResponse(f"{destino}?calendario_conectado={provider}")


def _guardar_cuenta(db: Session, user: User, provider: str, tok: dict) -> CalendarAccount:
    """Guarda la cuenta. Reconectar una que ya estaba la arregla en su sitio.

    Antes se buscaba por `(user, provider)` y la segunda cuenta pisaba a
    la primera. Ahora la llave lleva el correo, así que caben la del
    trabajo y la personal — y reconectar no deja una copia.
    """
    correo = tok.get("email") or ""
    cuenta = db.exec(select(CalendarAccount).where(
        CalendarAccount.user_id == user.id,
        CalendarAccount.provider == provider,
        CalendarAccount.account_email == correo)).first()
    if not cuenta and correo:
        # Una fila anónima de antes: se rellena en vez de duplicar. Si ya
        # hay otra con este correo, la anónima sobra y se va con lo suyo.
        cuenta = db.exec(select(CalendarAccount).where(
            CalendarAccount.user_id == user.id,
            CalendarAccount.provider == provider,
            CalendarAccount.account_email == "")).first()
        if cuenta:
            cuenta.account_email = correo
    if not cuenta:
        cuenta = CalendarAccount(
            tenant_id=user.tenant_id, user_id=user.id, provider=provider,
            account_email=correo)
    cuenta.tenant_id = user.tenant_id
    cuenta.is_active = True
    cuenta.data_center = tok.get("data_center", "") or cuenta.data_center
    db.add(cuenta); db.commit(); db.refresh(cuenta)
    calendar_sync.guardar_tokens(db, cuenta, tok)
    return cuenta


@router.delete("/cuentas/{account_id}")
async def desconectar(
    account_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """Retira el permiso donde se pueda y borra lo traído."""
    cuenta = db.get(CalendarAccount, account_id)
    if not cuenta or cuenta.user_id != user.id:
        raise HTTPException(404, "Esa cuenta no es tuya.")

    from services.calendar_crypto import descifrar
    cfg = calendar_config.cargar(db, user.tenant_id, cuenta.provider)
    revocado = False
    try:
        if cuenta.provider == "google":
            revocado = await calendar_google.revocar(cfg, descifrar(cuenta.refresh_token or ""))
        elif cuenta.provider == "zoho":
            from services import calendar_zoho
            revocado = await calendar_zoho.revocar(
                cfg, descifrar(cuenta.refresh_token or ""), cuenta.data_center)
    except Exception as e:  # noqa: BLE001
        logger.info("No se pudo revocar en %s: %s", cuenta.provider, e)

    borrados = 0
    for cal in db.exec(select(Calendar).where(Calendar.account_id == cuenta.id)).all():
        for x in db.exec(select(ExternalEvent).where(
                ExternalEvent.calendar_id == cal.id)).all():
            db.delete(x); borrados += 1
        db.delete(cal)
    db.delete(cuenta); db.commit()

    return {
        "status": "desconectada", "eventos_borrados": borrados, "revocado": revocado,
        # Microsoft no tiene llamada para revocar: callarlo dejaría a la
        # persona creyendo que ya no tenemos acceso cuando sigue vivo.
        "revocar_manual": PROVEEDORES.get(cuenta.provider, {}).get("revoca_manual", ""),
    }


# ══════════════════════════════════════════════════════════════════════
# Configuración de la integración (administrador)
# ══════════════════════════════════════════════════════════════════════

@router.get("/config/proveedores")
def config_listar(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
):
    """Las tres tarjetas de Configuración → Calendarios. Sin secretos en claro."""
    base = str(request.base_url).rstrip("/")
    salida = []
    for p in PROVEEDORES:
        est = calendar_config.estado(db, admin.tenant_id, p)
        est["redirect_sugerido"] = calendar_config.redireccion_sugerida(p, base)
        est["scopes"] = PROVEEDORES[p]["scopes"]
        salida.append(est)
    return {"proveedores": salida}


class ConfigProveedor(BaseModel):
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    redirect_uri: Optional[str] = None
    tenant: Optional[str] = None        # solo Microsoft
    data_center: Optional[str] = None   # solo Zoho


@router.put("/config/proveedores/{provider}")
def config_guardar(
    provider: str, cuerpo: ConfigProveedor,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
):
    if not conocido(provider):
        raise HTTPException(404, f"Proveedor desconocido: {provider}")
    return calendar_config.guardar(db, admin.tenant_id, provider, cuerpo.model_dump())


@router.post("/config/proveedores/{provider}/comprobar")
async def config_comprobar(
    provider: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
):
    """Dice si el proveedor reconoce la aplicación, sin conectar ninguna cuenta."""
    if not conocido(provider):
        raise HTTPException(404, f"Proveedor desconocido: {provider}")
    cfg = calendar_config.cargar(db, admin.tenant_id, provider)
    return await calendar_check.comprobar(provider, cfg)
