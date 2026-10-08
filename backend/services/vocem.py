"""Vocem (Element/Matrix) por empresa: crear la sala de una reunión y compartir el enlace.

La empresa trae su propio servidor Matrix con Element Call. Acten guarda las
credenciales del usuario de servicio cifradas y, cuando alguien pide «Acten
gestiona todo», crea la sala sin cifrado extremo a extremo (el bot tiene que
oír el audio). El servidor está cerrado a sus propias cuentas: la sala es
`public_chat` (quien tenga el enlace entra sin invitación), `visibility:
private` (no sale en el directorio) y no federa (`m.federate: false`), así
que un enlace reenviado fuera no sirve. No hay invitados anónimos.
"""

from __future__ import annotations

import json
import logging
import uuid
import httpx
from urllib.parse import quote
from sqlmodel import Session, select

from models import IntegrationSetting, VocemInvite
from services.cifrado import cifrar, descifrar, enmascarar

logger = logging.getLogger(__name__)

PROVIDER = "vocem"
CAMPOS = ("homeserver", "user_id", "call_base_url")
# Chat web (Element Web) de la empresa: con él se arma el enlace que abre la
# sala en la sesión que la gente ya tiene, con el botón de llamada. Opcional.
OPCIONALES = ("chat_base_url",)


def _fila(db: Session, tenant_id: int) -> IntegrationSetting | None:
    return db.exec(
        select(IntegrationSetting)
        .where(IntegrationSetting.tenant_id == tenant_id)
        .where(IntegrationSetting.provider_name == PROVIDER)
        .where(IntegrationSetting.user_id == None)  # noqa: E711
    ).first()


def cargar(db: Session, tenant_id: int) -> dict:
    fila = _fila(db, tenant_id)
    if not fila or not fila.is_active:
        return {}
    try:
        cfg = json.loads(fila.config_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    if cfg.get("access_token"):
        cfg["access_token"] = descifrar(cfg["access_token"])
    return cfg


def configurada(db: Session, tenant_id: int) -> bool:
    cfg = cargar(db, tenant_id)
    return all(cfg.get(c) for c in CAMPOS) and bool(cfg.get("access_token"))


def estado(db: Session, tenant_id: int) -> dict:
    """Lo que se enseña en el superadmin: nunca el token en claro."""
    cfg = cargar(db, tenant_id)
    return {
        "configured": configurada(db, tenant_id),
        **{c: cfg.get(c, "") for c in CAMPOS + OPCIONALES},
        "access_token_hint": enmascarar(cfg.get("access_token", "")),
    }


def guardar(db: Session, tenant_id: int, datos: dict) -> dict:
    """Guarda cifrando el token. Un token vacío conserva el anterior."""
    fila = _fila(db, tenant_id)
    actual: dict = {}
    if fila:
        try:
            actual = json.loads(fila.config_json or "{}")
        except (json.JSONDecodeError, TypeError):
            actual = {}
    nuevo = dict(actual)
    for c in CAMPOS + OPCIONALES:
        if datos.get(c) is not None:
            nuevo[c] = str(datos[c]).strip().rstrip("/") if c != "user_id" else str(datos[c]).strip()
    if datos.get("access_token"):
        nuevo["access_token"] = cifrar(str(datos["access_token"]).strip())
    if datos.get("clear"):
        nuevo = {}
    if not fila:
        fila = IntegrationSetting(tenant_id=tenant_id, provider_name=PROVIDER, config_json="{}", is_active=True)
    fila.config_json = json.dumps(nuevo)
    fila.is_active = bool(nuevo)
    db.add(fila); db.commit()
    return estado(db, tenant_id)


class VocemError(Exception):
    pass


def _llamar(cfg: dict, method: str, path: str, body: dict | None = None) -> dict:
    url = cfg["homeserver"].rstrip("/") + path
    try:
        r = httpx.request(method, url, json=body if body is not None else {}, timeout=20,
                          headers={"Authorization": "Bearer " + cfg["access_token"]})
    except httpx.RequestError as exc:
        raise VocemError(f"No se pudo hablar con el servidor de Vocem: {exc.__class__.__name__}") from exc
    if not r.is_success:
        raise VocemError(f"Vocem respondió {r.status_code}: {r.text[:200]}")
    try:
        return r.json()
    except ValueError as exc:
        raise VocemError("Vocem devolvió una respuesta ilegible") from exc


def servidor_de(cfg: dict) -> str:
    """`@integraciones:softnexus.co` → `softnexus.co`."""
    return cfg["user_id"].split(":", 1)[1] if ":" in cfg.get("user_id", "") else ""


def _existe(cfg: dict, user_id: str) -> bool:
    try:
        _llamar(cfg, "GET", f"/_matrix/client/v3/profile/{quote(user_id, safe='')}")
        return True
    except VocemError:
        return False


def _llano(texto: str) -> str:
    import unicodedata

    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return " ".join(t.lower().split())


def _por_nombre(cfg: dict, nombre: str, servidor: str) -> str | None:
    """Busca en el directorio por nombre visible; vale solo si coincide exactamente uno."""
    if not _llano(nombre):
        return None
    try:
        datos = _llamar(cfg, "POST", "/_matrix/client/v3/user_directory/search", {"search_term": nombre, "limit": 10})
    except VocemError:
        return None
    iguales = [r["user_id"] for r in datos.get("results", [])
               if _llano(r.get("display_name", "")) == _llano(nombre) and r["user_id"].endswith(":" + servidor)]
    return iguales[0] if len(iguales) == 1 else None


def cuentas_de_usuarios(db: Session, tenant_id: int, cfg: dict) -> list[str]:
    """Cuentas Matrix de los usuarios activos de la empresa que existen en el servidor.

    Primero por la parte local del correo (`felipe@acme.com` → `@felipe:servidor`);
    si no existe, por el nombre completo en el directorio (`Felipe Cortés` →
    `@felipe:servidor`). Así la sala les aparece en el chat de Vocem con el
    botón de llamada, sin iniciar sesión aparte en Element Call.
    """
    from models import User

    servidor = servidor_de(cfg)
    if not servidor:
        return []
    usuarios = db.exec(select(User).where(User.tenant_id == tenant_id, User.is_active == True)).all()  # noqa: E712
    cuentas: list[str] = []
    for u in usuarios:
        local = (u.email or "").split("@")[0].strip().lower()
        candidato = f"@{local}:{servidor}" if local else None
        cuenta = candidato if candidato and _existe(cfg, candidato) else _por_nombre(cfg, u.full_name or "", servidor)
        if cuenta and cuenta != cfg["user_id"] and cuenta not in cuentas:
            cuentas.append(cuenta)
    return sorted(cuentas)


def crear_sala(db: Session, tenant_id: int, titulo: str, invitados: list[str] | None = None) -> dict:
    """Crea la sala de la reunión y devuelve `{room_id, meeting_url}`.

    Sin `m.room.encryption`: el bot entra como participante y necesita oír.
    Sin `guest_access`: el servidor no admite invitados anónimos. Se invita a
    los usuarios de la empresa con cuenta en el servidor, además de `invitados`.
    """
    cfg = cargar(db, tenant_id)
    if not configurada(db, tenant_id):
        raise VocemError("Esta empresa no tiene Vocem configurado.")
    explicitos = [u for u in (invitados or []) if u.startswith("@") and ":" in u and u != cfg["user_id"]]
    cuerpo = {
        "name": titulo.strip()[:200] or "Reunión",
        "preset": "public_chat",
        "visibility": "private",
        "creation_content": {"m.federate": False},
        # Element Call exige que cada participante publique su propio estado de
        # llamada; con el nivel por defecto (50) entran y se caen («Connection lost»).
        "power_level_content_override": {
            "events": {"m.rtc.member": 0, "org.matrix.msc3401.call.member": 0, "io.element.video.member": 0}
        },
        # Solo invitados explícitos. Invitar a todos los usuarios hacía aparecer la
        # llamada en el chat de cada uno; la gente entra con el enlace y nada más.
        "invite": explicitos,
    }
    datos = _llamar(cfg, "POST", "/_matrix/client/v3/createRoom", cuerpo)
    room_id = datos.get("room_id")
    if not room_id:
        raise VocemError("Vocem no devolvió el id de la sala")
    enlace = enlace_de_sala(cfg, room_id)
    # Un solo enlace para el bot y para las personas: Element Call con `roomId`.
    return {"room_id": room_id, "meeting_url": enlace, "join_url": enlace, "app_url": enlace_app(cfg, room_id)}


def enlace_app(cfg: dict, room_id: str) -> str:
    """Enlace universal matrix.to: el único que abre la app del celular (Element X)."""
    return f"https://matrix.to/#/{quote(room_id, safe='')}?via={servidor_de(cfg)}"


def enlace_de_sala(cfg: dict, room_id: str) -> str:
    """`https://call.…/room/#?roomId=%21id&viaServers=servidor`, el formato que Element Call entiende.

    El id va tal cual lo devuelve el servidor (sin añadirle `:dominio`), urlencoded.
    """
    from urllib.parse import quote as _q

    return f"{cfg['call_base_url'].rstrip('/')}/room/#?roomId={_q(room_id, safe='')}&viaServers={servidor_de(cfg)}"


# ──────────────────────────────────────────────────────────────────────────
# Invitaciones a la hora de la sesión
# ──────────────────────────────────────────────────────────────────────────
# Invitar al crear la sala hacía aparecer la llamada en el chat de todos aunque
# la sesión fuera en tres días. La invitación sale `ANTELACION` antes de la hora
# (o de inmediato si la sesión es ahora); así aparece cuando sirve.

from datetime import datetime, timedelta, timezone  # noqa: E402

ANTELACION = timedelta(minutes=2)


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _nombre_de_sala(cfg: dict, room_id: str) -> str:
    try:
        return _llamar(cfg, "GET", f"/_matrix/client/v3/rooms/{quote(room_id, safe='')}/state/m.room.name/").get("name", "")
    except VocemError:
        return ""


def avisar(cfg: dict, room_id: str) -> None:
    """Mensaje en la sala con el enlace, mencionando a todos (`@room`).

    Es lo que dispara el aviso en quien ya está en la sala: una invitación sola
    solo avisa a quien se invita. Los dos enlaces: Element Call para el
    computador y matrix.to para abrir la app del celular.
    """
    nombre = _nombre_de_sala(cfg, room_id) or "La sesión"
    unirse = enlace_de_sala(cfg, room_id)
    app = enlace_app(cfg, room_id)
    cuerpo = (
        f"@room «{nombre}» empieza en un momento.\n"
        f"Unirse desde el computador: {unirse}\n"
        f"Abrir en la app del celular: {app}"
    )
    _llamar(cfg, "PUT", f"/_matrix/client/v3/rooms/{quote(room_id, safe='')}/send/m.room.message/{uuid.uuid4()}",
            {"msgtype": "m.text", "body": cuerpo, "m.mentions": {"room": True}})


def invitar(db: Session, tenant_id: int, room_id: str, cuentas: list[str] | None = None) -> list[str]:
    """Avisa a la gente de la sesión: invitación (con push) y mensaje `@room` con el enlace.

    `cuentas`: las que indicó quien creó la sesión; sin ellas, los usuarios de la
    empresa con cuenta en el servidor.
    """
    cfg = cargar(db, tenant_id)
    if not configurada(db, tenant_id):
        return []
    cuentas = cuentas if cuentas else cuentas_de_usuarios(db, tenant_id, cfg)
    hechas = []
    for cuenta in cuentas:
        try:
            _llamar(cfg, "POST", f"/_matrix/client/v3/rooms/{quote(room_id, safe='')}/invite", {"user_id": cuenta})
            hechas.append(cuenta)
        except VocemError as exc:
            logger.info("No se pudo invitar a %s a %s: %s", cuenta, room_id, exc)
    try:
        avisar(cfg, room_id)
    except VocemError as exc:
        logger.info("No se pudo publicar el aviso en %s: %s", room_id, exc)
    return hechas


def programar_invitaciones(db: Session, tenant_id: int, room_id: str, inicio: datetime | None,
                           cuentas: list[str] | None = None) -> str:
    """Avisa ya si la sesión es ahora o muy pronto; si no, deja el aviso para antes de la hora."""
    if inicio is None or inicio.tzinfo is None or inicio - _ahora() <= ANTELACION:
        invitar(db, tenant_id, room_id, cuentas)
        return "ahora"
    db.add(VocemInvite(tenant_id=tenant_id, room_id=room_id, invite_at=(inicio - ANTELACION).isoformat(),
                       cuentas=json.dumps(cuentas or [])))
    db.commit()
    return "programada"


def enviar_invitaciones_pendientes(db: Session) -> int:
    """Cron: manda las invitaciones cuya hora llegó. Devuelve cuántas salas se invitaron."""
    limite = _ahora().isoformat()
    filas = db.exec(select(VocemInvite).where(VocemInvite.done_at == None, VocemInvite.invite_at <= limite)).all()  # noqa: E711
    for fila in filas:
        try:
            cuentas = json.loads(fila.cuentas or "[]")
        except (json.JSONDecodeError, TypeError):
            cuentas = []
        invitar(db, fila.tenant_id, fila.room_id, cuentas)
        fila.done_at = _ahora().isoformat()
        db.add(fila)
    if filas:
        db.commit()
    return len(filas)
