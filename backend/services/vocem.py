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
import re
import uuid
import httpx
from fastapi import HTTPException
from urllib.parse import quote, unquote, urlparse
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
        "cuentas": cfg.get("cuentas") or {},
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
    if datos.get("cuentas") is not None:
        # correo del usuario en Acten → cuenta de Vocem (`@usuario:servidor`)
        nuevo["cuentas"] = {k.strip().lower(): v.strip() for k, v in datos["cuentas"].items() if k.strip() and v.strip()}
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

    Primero el mapa correo → cuenta de la configuración (`cuentas`), que cubre a quien
    entra con su correo pero tiene otro nombre de usuario; luego la parte local del
    correo (`felipe@acme.com` → `@felipe:servidor`); por último el nombre completo en
    el directorio (`Felipe Cortés` → `@felipe:servidor`). Así la sala les aparece en el chat de Vocem con el
    botón de llamada, sin iniciar sesión aparte en Element Call.
    """
    from models import User

    servidor = servidor_de(cfg)
    if not servidor:
        return []
    usuarios = db.exec(select(User).where(User.tenant_id == tenant_id, User.is_active == True)).all()  # noqa: E712
    mapa = cfg.get("cuentas") or {}
    cuentas: list[str] = []
    for u in usuarios:
        local = (u.email or "").split("@")[0].strip().lower()
        candidato = f"@{local}:{servidor}" if local else None
        cuenta = mapa.get((u.email or "").strip().lower())
        if not cuenta:
            cuenta = candidato if candidato and _existe(cfg, candidato) else _por_nombre(cfg, u.full_name or "", servidor)
        if cuenta and cuenta != cfg["user_id"] and cuenta not in cuentas:
            cuentas.append(cuenta)
    return sorted(cuentas)


def cuenta_del_organizador(db: Session, tenant_id: int, owner_id: int) -> str:
    """Resuelve al actor mediante el vínculo administrado; nombres y prefijos no prueban identidad."""
    from models import User

    usuario = db.get(User, owner_id)
    if not usuario or usuario.tenant_id != tenant_id or not usuario.is_active:
        raise VocemError("No se pudo identificar al organizador de esta empresa.")
    cfg = cargar(db, tenant_id)
    servidor = servidor_de(cfg)
    correo = (usuario.email or "").strip().lower()
    cuenta = (cfg.get("cuentas") or {}).get(correo)
    if not cuenta or not cuenta.endswith(":" + servidor) or cuenta == cfg.get("user_id") or not _existe(cfg, cuenta):
        raise VocemError("El organizador necesita una cuenta Vocem vinculada a su usuario.")
    return cuenta


def crear_sala(db: Session, tenant_id: int, titulo: str, invitados: list[str] | None = None,
               organizador: str | None = None) -> dict:
    """Crea la sala de la reunión y devuelve `{room_id, meeting_url}`.

    Sin `m.room.encryption`: el bot entra como participante y necesita oír.
    Sin `guest_access`: el servidor no admite invitados anónimos. Se invita a
    los usuarios de la empresa con cuenta en el servidor, además de `invitados`.
    """
    cfg = cargar(db, tenant_id)
    if not configurada(db, tenant_id):
        raise VocemError("Esta empresa no tiene Vocem configurado.")
    if not organizador or not organizador.startswith("@") or not organizador.endswith(":" + servidor_de(cfg)) or organizador == cfg["user_id"]:
        raise VocemError("Indica la cuenta Vocem del organizador humano.")
    explicitos = [u for u in (invitados or []) if u.startswith("@") and ":" in u and u != cfg["user_id"]]
    cuerpo = {
        "name": titulo.strip()[:200] or "Reunión",
        "preset": "public_chat",
        "visibility": "private",
        "room_version": "12",
        "creation_content": {"m.federate": False},
        # Element Call exige que cada participante publique su propio estado de
        # llamada; con el nivel por defecto (50) entran y se caen («Connection lost»).
        "power_level_content_override": {
            "users": {organizador: 50},
            "users_default": 0, "kick": 50, "ban": 50,
            "events": {"m.rtc.member": 0, "org.matrix.msc3401.call.member": 0,
                       "io.element.video.member": 0, "co.vocem.session": 100}
        },
        "initial_state": [{"type": "co.vocem.session", "state_key": "",
                           "content": {"organizer": organizador}}],
        # Solo invitados explícitos. Invitar a todos los usuarios hacía aparecer la
        # llamada en el chat de cada uno; la gente entra con el enlace y nada más.
        "invite": sorted(set(explicitos + [organizador])),
    }
    datos = _llamar(cfg, "POST", "/_matrix/client/v3/createRoom", cuerpo)
    room_id = datos.get("room_id")
    if not room_id:
        raise VocemError("Vocem no devolvió el id de la sala")
    enlace = enlace_de_sala(cfg, room_id)
    _llamar(cfg, "PUT", f"/_matrix/client/v3/rooms/{quote(room_id, safe='')}/send/m.room.message/{uuid.uuid4()}",
            {"msgtype": "m.text", "body": f"Enlace de la sesión: {enlace}\nAbrir en la app: {enlace_app(cfg, room_id)}"})
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


# ──────────────────────────────────────────────────────────────────────────
# Detector de enlaces y acceso del bot a una sala existente
# ──────────────────────────────────────────────────────────────────────────
# Element entrega tres formatos distintos de enlace para la misma sala. Los
# tres se clasifican como "element" y se normalizan al canónico antes de pedir
# captura. Si el enlace apunta a una persona (`matrix.to/#/@...`) se rechaza
# con `enlace_es_usuario` para que la UI lo diferencie de un enlace basura.

GMEET_HOST = "meet.google.com"
TEAMS_HOSTS = ("teams.microsoft.com", "teams.live.com")
ZOOM_HOST = "zoom.us"

_ROOM_ID_PARAM = re.compile(r"[#?&]roomId=([^&]+)")


def id_de_sala(enlace: str | None) -> str | None:
    """`!abc` si la URL contiene un room id Matrix; None en cualquier otro caso."""
    texto = (enlace or "").strip()
    if not texto:
        return None
    try:
        url = urlparse(texto)
    except ValueError:
        return None
    if not url.scheme or not url.netloc:
        return None
    m = _ROOM_ID_PARAM.search(texto)
    if m:
        try:
            ident = unquote(m.group(1))
        except Exception:  # noqa: BLE001 — unquote es defensivo
            return None
        return ident if ident.startswith("!") else None
    frag = url.fragment
    if frag.startswith("/"):
        frag = frag[1:]
    try:
        primero = unquote(frag.split("?", 1)[0].split("/", 1)[0])
    except Exception:  # noqa: BLE001
        return None
    return primero if primero.startswith("!") else None


def clasificar_enlace(enlace: str | None) -> str | None:
    """Plataforma: `gmeet`, `teams`, `zoom`, `element`, o None si no se reconoce."""
    texto = (enlace or "").strip()
    try:
        host = urlparse(texto).hostname or ""
    except ValueError:
        return None
    host = host.lower()
    if not host:
        return None
    if host == GMEET_HOST:
        return "gmeet"
    if any(host == h or host.endswith("." + h) for h in TEAMS_HOSTS):
        return "teams"
    if host == ZOOM_HOST or host.endswith("." + ZOOM_HOST):
        return "zoom"
    if id_de_sala(texto):
        return "element"
    return None


def es_enlace_de_persona(enlace: str | None) -> bool:
    """`https://matrix.to/#/@usuario:servidor` → True."""
    texto = (enlace or "").strip()
    try:
        url = urlparse(texto)
    except ValueError:
        return False
    if (url.hostname or "").lower() != "matrix.to":
        return False
    frag = url.fragment
    if frag.startswith("/"):
        frag = frag[1:]
    try:
        return unquote(frag).startswith("@")
    except Exception:  # noqa: BLE001
        return False


def enlace_canonico_de(cfg: dict, enlace: str) -> str:
    """Si la URL es de Element, devuelve el canónico de Element Call;
    cualquier otra URL se devuelve sin cambios (trimmed)."""
    rid = id_de_sala(enlace)
    if not rid:
        return (enlace or "").strip()
    return (
        f"{cfg['call_base_url'].rstrip('/')}/room/#?roomId={quote(rid, safe='')}"
        f"&viaServers={servidor_de(cfg)}"
    )


# ──────────────────────────────────────────────────────────────────────────
# Códigos de resultado de `unir_bot_a_sala`. Mapean 1 a 1 a los `error_code`
# que la API expone a Altum y a la pantalla de Acten.
# ──────────────────────────────────────────────────────────────────────────
JOIN_OK = "ok"
JOIN_FALTA_INVITAR = "sala_falta_invitar"
JOIN_SALA_CIFRADA = "sala_cifrada"
JOIN_SALA_NO_EXISTE = "sala_no_existe"


# Mensajes expuestos en la respuesta. Claves estables para que Altum y la
# pantalla tengan un contrato predecible y los puedan mapear a su propia UI.
MENSAJES_ERROR = {
    "enlace_vacio": "Indica el enlace de la reunión",
    "enlace_no_reconocido": "No reconocemos ese enlace. Pega uno de Google Meet, Teams, Zoom, o el de una sala de Vocem.",
    "enlace_es_usuario": "Ese enlace es de una persona, no de una sala.",
    JOIN_FALTA_INVITAR: "Falta invitar al bot a la sala. /invite @integraciones:softnexus.co y vuelve a mandar el bot.",
    JOIN_SALA_CIFRADA: "Esa sala está cifrada y el bot no puede leerla.",
    JOIN_SALA_NO_EXISTE: "No existe ninguna sala con ese enlace. Vuelve a copiarlo desde Element sin editarlo.",
}


class EnlaceReunionError(HTTPException):
    """Preflight del `meeting_url` falló. Se expone con `error_code` estable
    y mensaje accionable, para que Altum y la pantalla mapeen a su UI.

    ponytail: hereda HTTPException para que un single-line call-site en el
    router no tenga que crecer a 4 líneas de try/except (bot_control.py ya
    está al tope de tamaño). Upgrade path: cuando se parta ese router, mover
    a Exception puro y convertirlo en el router.
    """

    def __init__(self, code: str):
        self.code = code
        self.message = MENSAJES_ERROR.get(code, code)
        super().__init__(status_code=422, detail={"error_code": code, "message": self.message})


def preparar_enlace_reunion(db: Session, tenant_id: int, meeting_url: str | None) -> str:
    """Clasifica, normaliza y —si es Element— une al bot a la sala.

    Devuelve el `meeting_url` listo para pasar al bot upstream. Lanza
    `EnlaceReunionError` (= HTTPException 422) con `error_code` estable si el
    enlace no es aceptable. Enlaces de Meet/Teams/Zoom se dejan como están.
    """
    if not (meeting_url or "").strip():
        raise EnlaceReunionError("enlace_vacio")
    plataforma = clasificar_enlace(meeting_url)
    if plataforma is None:
        if es_enlace_de_persona(meeting_url):
            raise EnlaceReunionError("enlace_es_usuario")
        raise EnlaceReunionError("enlace_no_reconocido")
    if plataforma != "element":
        return (meeting_url or "").strip()
    cfg = cargar(db, tenant_id)
    canonico = enlace_canonico_de(cfg, meeting_url)
    room_id = id_de_sala(meeting_url)
    if room_id is None:
        # Defensa: `plataforma == 'element'` ya implica room_id, pero una URL
        # ambigua podría pasar el detector; mejor rechazar que seguir.
        raise EnlaceReunionError("enlace_no_reconocido")
    codigo = unir_bot_a_sala(db, tenant_id, room_id)
    if codigo in (JOIN_FALTA_INVITAR, JOIN_SALA_CIFRADA, JOIN_SALA_NO_EXISTE):
        raise EnlaceReunionError(codigo)
    return canonico


def unir_bot_a_sala(db: Session, tenant_id: int, room_id: str) -> str:
    """Mete al bot en la sala y comprueba que no esté cifrada.

    Devuelve uno de los códigos `JOIN_*`. Es idempotente: si el bot ya está
    dentro, el `join` responde 200 y se sigue con la comprobación de cifrado.
    Si la sala está cifrada, sale (`leave`) para no quedar dentro de una sala
    que no puede transcribir.
    """
    cfg = cargar(db, tenant_id)
    if not configurada(db, tenant_id):
        raise VocemError("Esta empresa no tiene Vocem (Element) configurado.")
    rid = quote(room_id, safe="")
    url = cfg["homeserver"].rstrip("/") + f"/_matrix/client/v3/join/{rid}?server_name={servidor_de(cfg)}"
    headers = {"Authorization": "Bearer " + cfg["access_token"]}
    try:
        r = httpx.post(url, headers=headers, json={}, timeout=20)
    except httpx.RequestError as exc:
        raise VocemError(
            f"No se pudo hablar con el servidor de Vocem: {exc.__class__.__name__}"
        ) from exc
    if r.status_code == 403:
        return JOIN_FALTA_INVITAR
    if r.status_code == 404:
        return JOIN_SALA_NO_EXISTE
    if not r.is_success:
        raise VocemError(f"Vocem respondió {r.status_code} al unirse: {r.text[:200]}")
    # Sala con `m.room.encryption` configurado = cifrada. El bot sale y reporta.
    try:
        cipher = _llamar(cfg, "GET", f"/_matrix/client/v3/rooms/{rid}/state/m.room.encryption")
    except VocemError as exc:
        # 404 significa que no está cifrada — es el caso bueno.
        if "404" in str(exc):
            return JOIN_OK
        raise
    if cipher.get("algorithm"):
        try:
            _llamar(cfg, "POST", f"/_matrix/client/v3/rooms/{rid}/leave", {})
        except VocemError as exc:
            logger.info("No se pudo salir de la sala cifrada %s: %s", room_id, exc)
        return JOIN_SALA_CIFRADA
    return JOIN_OK
