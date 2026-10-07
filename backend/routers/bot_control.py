"""Authenticated controls for meeting bots and resumable browser recordings."""

import hashlib
import logging
import json
import re
import secrets
import time
from typing import Literal, Optional
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field as PField,
    SecretStr,
    model_validator,
)
from sqlalchemy.exc import IntegrityError
from sqlmodel import Field, Session, SQLModel, UniqueConstraint, select

from database import get_session
from models import (
    IntegrationSetting,
    MeetingSession,
    Project,
    ProjectContact,
    Tenant,
    User,
)
from routers.auth import get_current_tenant, require_admin, require_session_writer
from services.api_key_auth import IntegrationContext, require_scopes
from services.bot_contract import ActenBotEvent
from services.bot_ingest import BotInbox
from services.billing_catalog import F_OWNED_BOT, F_REALTIME, F_VIDEO
from services.cifrado import cifrar, descifrar
from services.entitlements import require_feature

router = APIRouter(
    prefix="/api/owned-bot",
    tags=["Controles del bot"],
    # El bot propio es una opción del plan: sin ella, 402 en toda la pantalla.
    dependencies=[Depends(get_current_tenant), Depends(require_feature(F_OWNED_BOT))],
)
audio_router = APIRouter(prefix="/api/owned-bot", tags=["Audio del bot"])
# API pública: otras plataformas mandan el bot a una reunión con su X-API-Key.
v1_router = APIRouter(prefix="/api/v1", tags=["Bot propio"])
logger = logging.getLogger(__name__)


class BotControlLink(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("tenant_id", "external_id"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    owner_id: int = Field(foreign_key="user.id")
    external_id: str
    meeting_id: Optional[str] = Field(default=None, index=True)
    kind: str
    # Proyecto elegido al pedir la captura; la sesión nace ya en él.
    project_id: Optional[int] = Field(default=None, foreign_key="project.id")


class BotAudioGrant(SQLModel, table=True):
    digest: str = Field(primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id")
    user_id: int = Field(foreign_key="user.id")
    meeting_id: str
    expires_at: float


class BotSpeakerLabel(SQLModel, table=True):
    inbox_id: int = Field(
        foreign_key="botinbox.id", ondelete="CASCADE", primary_key=True
    )
    speaker_id: str = Field(primary_key=True)
    display_name: str
    person_external_id: Optional[str] = None
    updated_by: int = Field(foreign_key="user.id")


DEFAULT_BOT_NAME = "Asistente Acten"


class BotConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_url: str
    client_key: SecretStr = SecretStr("")
    # Nombre con el que el bot aparece en Meet/Teams/Zoom; también se usa
    # en las reuniones que llegan por correo (se copia a la política del bot).
    bot_name: str = PField(default=DEFAULT_BOT_NAME, min_length=1, max_length=50)

    @model_validator(mode="after")
    def clean_name(self):
        self.bot_name = " ".join(self.bot_name.split()) or DEFAULT_BOT_NAME
        return self

    @model_validator(mode="after")
    def url(self):
        parsed = urlsplit(self.service_url)
        local = parsed.scheme == "http" and parsed.hostname in {
            "127.0.0.1",
            "localhost",
        }
        if (
            (parsed.scheme != "https" and not local)
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError(
                "Usa el origen HTTPS del servicio, o localhost para desarrollo"
            )
        return self


def config_row(db, tenant_id):
    return db.exec(
        select(IntegrationSetting).where(
            IntegrationSetting.tenant_id == tenant_id,
            IntegrationSetting.provider_name == "owned_bot",
            IntegrationSetting.user_id.is_(None),
        )
    ).first()


def configuration(db, tenant_id):
    tenant = db.get(Tenant, tenant_id)
    row = config_row(db, tenant_id)
    if not tenant or not tenant.is_active or not row or not row.is_active:
        raise HTTPException(503, "El bot de esta empresa no está configurado")
    cfg = json.loads(row.config_json)
    key = descifrar(cfg.get("client_key", ""))
    if not key:
        raise HTTPException(503, "Falta la clave del bot de esta empresa")
    # Revalidate persisted origins too; caller-controlled URLs never become proxy targets.
    BotConfig(service_url=cfg["service_url"])
    return cfg["service_url"].rstrip("/"), key


async def bot_call(db, tenant_id, method, path, *, body=None, content=None):
    origin, key = configuration(db, tenant_id)
    try:
        async with httpx.AsyncClient(
            timeout=35,
            follow_redirects=False,
            headers={"Authorization": "Bearer " + key},
        ) as client:
            caps = await client.get(origin + "/v1/capabilities")
            if not caps.is_success or caps.json().get("acten_tenant_id") != tenant_id:
                raise HTTPException(
                    503, "El servicio del bot no está vinculado a esta empresa de Acten"
                )
            if path == "/v1/capabilities":
                return caps.json()
            response = await client.request(
                method, origin + path, json=body, content=content
            )
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            502, "No se pudo contactar con el servicio del bot"
        ) from exc
    if not response.is_success:
        code = (
            response.status_code
            if response.status_code in {404, 409, 413, 422, 429, 503}
            else 502
        )
        raise HTTPException(
            code,
            {"message": "El servicio del bot rechazó la operación", "status": code},
        )
    try:
        return response.json()
    except ValueError as exc:
        raise HTTPException(502, "Respuesta del bot inválida") from exc


def is_admin(user):
    return bool(user.role and user.role.name == "admin")


def own(db, user, mid):
    row = db.exec(
        select(BotControlLink).where(
            BotControlLink.tenant_id == user.tenant_id,
            BotControlLink.meeting_id == str(mid),
        )
    ).first()
    # El administrador queda eximido de ser el dueño, NO de que la captura
    # sea de su empresa. Con la condición anterior, `is_admin` cortocircuitaba
    # el `and` y un admin de una empresa podía pedir, parar o reproducir una
    # grabación de otra con solo conocer su UUID.
    if not row or (not is_admin(user) and row.owner_id != user.id):
        raise HTTPException(404, "Grabación no encontrada")
    return row


@router.get("/config")
def get_config(user: User = Depends(require_admin), db: Session = Depends(get_session)):
    row = config_row(db, user.tenant_id)
    cfg = json.loads(row.config_json) if row else {}
    return {
        "service_url": cfg.get("service_url", ""),
        "bot_name": cfg.get("bot_name") or DEFAULT_BOT_NAME,
        "configured": bool(row and row.is_active and cfg.get("client_key")),
    }


def bot_name_of(db, tenant_id):
    row = config_row(db, tenant_id)
    cfg = json.loads(row.config_json) if row else {}
    return cfg.get("bot_name") or DEFAULT_BOT_NAME


AUTH_PLATFORMS = ("gmeet", "teams", "zoom")


def auth_accounts_of(db, tenant_id) -> dict:
    """Cuentas autenticadas de Skribby propias de la empresa, por plataforma.
    Vacío = el bot usa las cuentas de Acten. Las fija el superadmin."""
    row = config_row(db, tenant_id)
    cfg = json.loads(row.config_json) if row else {}
    raw = cfg.get("auth_accounts") or {}
    return {k: v.strip() for k, v in raw.items() if k in AUTH_PLATFORMS and isinstance(v, str) and v.strip()}


def set_auth_accounts(db, tenant_id, cuentas: dict) -> dict:
    row = config_row(db, tenant_id)
    if not row:
        raise HTTPException(409, "La empresa no tiene el bot configurado")
    cfg = json.loads(row.config_json)
    limpias = {k: v.strip() for k, v in cuentas.items() if k in AUTH_PLATFORMS and isinstance(v, str) and v.strip()}
    row.config_json = json.dumps({**cfg, "auth_accounts": limpias})
    db.add(row); db.commit()
    return limpias


async def _copy_name_to_mail_policy(db, tenant_id: int, name: str) -> None:
    """Las invitaciones por correo las programa el bot solo: si ya hay una
    política guardada, se le copia el nombre nuevo. Que el bot no responda no
    invalida el nombre recién guardado."""
    try:
        state = await bot_call(db, tenant_id, "GET", "/v1/mail-policy")
        policy = state.get("policy") if isinstance(state, dict) else None
        if policy:
            await bot_call(
                db,
                tenant_id,
                "PUT",
                "/v1/mail-policy",
                body={
                    "allowed_senders": policy["allowed_senders"],
                    # Sin estos el bot los devuelve a su valor por defecto:
                    # cambiar el nombre apagaba el vídeo.
                    "extra_senders": policy.get("extra_senders", []),
                    "video": policy.get("video", False),
                    **({"realtime": policy["realtime"]} if "realtime" in policy else {}),
                    **({"auth_accounts": policy["auth_accounts"]} if "auth_accounts" in policy else {}),
                    "recording_authorized": policy["recording_authorized"],
                    "timezone": policy["timezone"],
                    "bot_name": name,
                },
            )
    except HTTPException as exc:
        logger.warning(
            "Nombre del bot guardado, pero no se pudo copiar a la política de correo: %s",
            exc.detail,
        )


async def set_bot_name(db, tenant_id: int, name: str) -> str:
    """Cambia solo el nombre con el que el bot aparece en las reuniones."""
    row = config_row(db, tenant_id)
    if not row or not row.is_active:
        raise HTTPException(503, "El bot de esta empresa no está configurado")
    cfg = json.loads(row.config_json)
    anterior = cfg.get("bot_name") or DEFAULT_BOT_NAME
    row.config_json = json.dumps({**cfg, "bot_name": name})
    db.add(row)
    db.commit()
    if anterior != name:
        await _copy_name_to_mail_policy(db, tenant_id, name)
    return name


@router.put("/config")
async def put_config(
    body: BotConfig,
    user: User = Depends(require_admin),
    db: Session = Depends(get_session),
):
    row = config_row(db, user.tenant_id)
    old = json.loads(row.config_json) if row else {}
    # Un origen HTTPS que resuelva a la red interna convertiría el proxy en
    # una puerta a la infraestructura de Acten. Mismo filtro que el de los
    # calendarios .ics: se resuelven TODAS las direcciones del nombre.
    host = urlsplit(body.service_url).hostname or ""
    if host not in {"localhost", "127.0.0.1"}:
        from services.calendar_ics import IcsError, _destino_permitido

        try:
            _destino_permitido(host)
        except IcsError as exc:
            # Lo que no resuelve no puede ser la red interna, y puede ser un
            # DNS que aún no propagó: se deja guardar y fallará con motivo
            # claro en la primera llamada. Lo que resuelve a un rango
            # privado o al servicio de metadatos, no.
            if "apunta a una dirección interna" in str(exc):
                raise HTTPException(422, str(exc)) from exc
            logger.warning("Origen del bot %s no resuelve todavía: %s", host, exc)
    key = body.client_key.get_secret_value()
    if key and len(key) < 32:
        raise HTTPException(422, "La clave requiere al menos 32 caracteres")
    secret = cifrar(key) if key else old.get("client_key", "")
    if not secret:
        raise HTTPException(422, "Indica la clave del servicio del bot")
    if not row:
        row = IntegrationSetting(tenant_id=user.tenant_id, provider_name="owned_bot")
    row.config_json = json.dumps(
        {
            "service_url": body.service_url.rstrip("/"),
            "client_key": secret,
            "bot_name": body.bot_name,
        }
    )
    row.is_active = True
    db.add(row)
    db.commit()
    if old.get("bot_name", DEFAULT_BOT_NAME) != body.bot_name:
        await _copy_name_to_mail_policy(db, user.tenant_id, body.bot_name)
    return {"configured": True}


@router.get("/capabilities")
async def capabilities(
    user: User = Depends(require_session_writer), db: Session = Depends(get_session)
):
    return await bot_call(db, user.tenant_id, "GET", "/v1/capabilities")


class StartCapture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_id: str = PField(min_length=1, max_length=160)
    title: str = PField(default="Reunión", min_length=1, max_length=300)
    language: Literal["es", "en", "ca"] = "es"
    meeting_url: str | None = PField(default=None, max_length=4096)
    scheduled_start: AwareDatetime | None = None
    mime_type: Literal["audio/webm", "audio/ogg", "audio/mp4"] = "audio/webm"
    max_duration_minutes: int = PField(default=480, ge=5, le=720)
    vocabulary: list[str] = PField(default_factory=list, max_length=100)
    recording_authorized: Literal[True]
    # Obsoleto y sin efecto: el vídeo lo decide la suscripción de la empresa
    # (función `meetings.video`), no quien pide la captura. Se sigue
    # aceptando para no romper pantallas abiertas con la versión anterior.
    video: bool = False
    # Proyecto de la sesión; sin él, Acten lo deduce al procesarla.
    project_id: int | None = None


@router.post("/start/{kind}", status_code=202)
async def start(
    kind: Literal["meeting", "browser"],
    body: StartCapture,
    user: User = Depends(require_session_writer),
    db: Session = Depends(get_session),
):
    return await start_capture(db, user.tenant_id, user.id, kind, body)


def graba_video(db, tenant_id: int) -> bool:
    from services import entitlements

    return entitlements.tiene(db, tenant_id, F_VIDEO)


def transcribe_en_vivo(db, tenant_id: int) -> bool:
    from services import entitlements

    return entitlements.tiene(db, tenant_id, F_REALTIME)


async def start_capture(db, tenant_id: int, owner_id: int, kind: str, body: StartCapture):
    """Pide la captura al bot. Lo comparten la pantalla y la API pública."""
    from services import meeting_source

    if not meeting_source.admite(db, tenant_id, meeting_source.OWNED_BOT):
        raise HTTPException(409, meeting_source.mensaje_rechazo(meeting_source.OWNED_BOT))
    if kind == "meeting" and not body.meeting_url:
        raise HTTPException(422, "Indica el enlace de la reunión")
    if body.project_id is not None:
        project = db.get(Project, body.project_id)
        if not project or project.tenant_id != tenant_id:
            raise HTTPException(422, "El proyecto no existe en esta empresa")
    row = db.exec(
        select(BotControlLink).where(
            BotControlLink.tenant_id == tenant_id,
            BotControlLink.external_id == body.external_id,
        )
    ).first()
    if row and (row.owner_id != owner_id or row.kind != kind):
        raise HTTPException(409, "Ese identificador ya pertenece a otra solicitud")
    if not row:
        row = BotControlLink(
            tenant_id=tenant_id,
            owner_id=owner_id,
            external_id=body.external_id,
            kind=kind,
            project_id=body.project_id,
        )
        db.add(row)
        try:
            db.commit()
            db.refresh(row)
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(
                409, "Solicitud simultánea; reintenta con el mismo identificador"
            ) from exc
    payload = body.model_dump(
        mode="json", exclude={"meeting_url", "mime_type", "video", "project_id"}
    )
    payload["analysis_scope"] = "base"
    if kind == "meeting":
        payload.update(
            meeting_url=body.meeting_url,
            profile="quality",
            bot_name=bot_name_of(db, tenant_id),
            # Con vídeo si la suscripción lo incluye; lo configura Acten, no el cliente.
            video=graba_video(db, tenant_id),
        )
        # Reunión programada: el bot entra a esa hora, no ahora.
        if body.scheduled_start:
            payload["join_at"] = payload["scheduled_start"]
        # Transcripción en vivo, también según la suscripción. Solo se manda
        # cuando aplica: un bot anterior a esta opción rechaza la clave.
        if transcribe_en_vivo(db, tenant_id):
            payload["realtime"] = True
    else:
        payload["mime_type"] = body.mime_type
    result = await bot_call(
        db,
        tenant_id,
        "POST",
        "/v1/meetings" if kind == "meeting" else "/v1/recordings",
        body=payload,
    )
    try:
        mid = str(UUID(result["id"]))
        if result["external_id"] != body.external_id:
            raise ValueError()
    except (ValueError, KeyError) as exc:
        raise HTTPException(502, "El bot respondió con otra solicitud") from exc
    row.meeting_id = mid
    db.add(row)
    db.commit()
    return result


@router.get("/meetings")
async def meetings(
    user: User = Depends(require_session_writer), db: Session = Depends(get_session)
):
    result = await bot_call(db, user.tenant_id, "GET", "/v1/meetings?limit=100")
    if not is_admin(user):
        ids = set(
            db.exec(
                select(BotControlLink.meeting_id).where(
                    BotControlLink.tenant_id == user.tenant_id,
                    BotControlLink.owner_id == user.id,
                )
            ).all()
        )
        result["items"] = [item for item in result["items"] if item["id"] in ids]
    imported = {
        row.meeting_id: row.session_id
        for row in db.exec(
            select(BotInbox).where(BotInbox.tenant_id == user.tenant_id)
        ).all()
    }
    for item in result["items"]:
        item["acten_session_id"] = imported.get(item["id"])
    return result


def apply_labels(db, tenant_id, mid, result):
    inbox = db.exec(
        select(BotInbox).where(
            BotInbox.tenant_id == tenant_id, BotInbox.meeting_id == str(mid)
        )
    ).first()
    result["acten_session_id"] = inbox.session_id if inbox else None
    if not inbox:
        return result
    labels = {
        row.speaker_id: row
        for row in db.exec(
            select(BotSpeakerLabel).where(BotSpeakerLabel.inbox_id == inbox.id)
        ).all()
    }
    for key in ("transcript", "speaker_timeline", "speakers"):
        for item in result.get(key, []):
            label = labels.get(item.get("speaker_id"))
            if label:
                item.update(
                    speaker_name=label.display_name,
                    identity_source="human_confirmed",
                    person_external_id=label.person_external_id,
                )
    return result


@router.get("/meetings/{mid}/result")
async def result(
    mid: UUID,
    user: User = Depends(require_session_writer),
    db: Session = Depends(get_session),
):
    own(db, user, mid)
    data = await bot_call(db, user.tenant_id, "GET", f"/v1/meetings/{mid}/result")
    return apply_labels(db, user.tenant_id, mid, data)


@router.post("/meetings/{mid}/stop", status_code=202)
async def stop(
    mid: UUID,
    user: User = Depends(require_session_writer),
    db: Session = Depends(get_session),
):
    own(db, user, mid)
    return await bot_call(db, user.tenant_id, "POST", f"/v1/meetings/{mid}/stop")


@router.get("/recordings/{mid}")
async def recording(
    mid: UUID,
    user: User = Depends(require_session_writer),
    db: Session = Depends(get_session),
):
    own(db, user, mid)
    return await bot_call(db, user.tenant_id, "GET", f"/v1/recordings/{mid}")


@router.delete("/recordings/{mid}")
async def cancel_recording(
    mid: UUID,
    user: User = Depends(require_session_writer),
    db: Session = Depends(get_session),
):
    own(db, user, mid)
    return await bot_call(db, user.tenant_id, "DELETE", f"/v1/recordings/{mid}")


@router.put("/recordings/{mid}/chunks/{sequence}")
async def chunk(
    mid: UUID,
    sequence: int,
    request: Request,
    user: User = Depends(require_session_writer),
    db: Session = Depends(get_session),
):
    own(db, user, mid)
    if not 0 <= sequence < 50000:
        raise HTTPException(422, "Fragmento inválido")
    size, parts = 0, []
    async for part in request.stream():
        size += len(part)
        if size > 4 * 1024 * 1024:
            raise HTTPException(413, "Fragmento demasiado grande")
        parts.append(part)
    return await bot_call(
        db,
        user.tenant_id,
        "PUT",
        f"/v1/recordings/{mid}/chunks/{sequence}",
        content=b"".join(parts),
    )


class Finish(BaseModel):
    chunk_count: int = PField(ge=1, le=50000)
    interrupted: bool = False


@router.post("/recordings/{mid}/finish", status_code=202)
async def finish(
    mid: UUID,
    body: Finish,
    user: User = Depends(require_session_writer),
    db: Session = Depends(get_session),
):
    own(db, user, mid)
    return await bot_call(
        db,
        user.tenant_id,
        "POST",
        f"/v1/recordings/{mid}/finish",
        body=body.model_dump(),
    )


class MailPolicy(BaseModel):
    """Política de invitaciones por correo de la empresa; vive en el bot."""

    model_config = ConfigDict(extra="forbid")
    allowed_senders: list[str] = PField(default_factory=list, max_length=50)
    recording_authorized: bool = False
    timezone: str = PField(default="America/Bogota", max_length=64)
    # Obsoleto y sin efecto: el vídeo lo decide la suscripción (ver `graba_video`).
    video: bool = False

    @model_validator(mode="after")
    def addresses(self):
        from zoneinfo import ZoneInfo

        for item in self.allowed_senders:
            if not re.fullmatch(r"[^@\s]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+", item.strip()):
                raise ValueError(f"Remitente no válido: {item[:80]}")
        try:
            ZoneInfo(self.timezone)
        except Exception as exc:
            raise ValueError("Zona horaria desconocida") from exc
        return self


@router.get("/mail-policy")
async def read_mail_policy(
    user: User = Depends(require_admin), db: Session = Depends(get_session)
):
    return await bot_call(db, user.tenant_id, "GET", "/v1/mail-policy")


@router.put("/mail-policy")
async def write_mail_policy(
    body: MailPolicy,
    user: User = Depends(require_admin),
    db: Session = Depends(get_session),
):
    """Guarda la política en el bot.

    `allowed_senders` que escribe el administrador son remitentes EXTRA:
    los usuarios activos de la empresa se añaden solos (services.
    mail_policy_sync) y se refrescan cada hora y al cambiar el origen.
    """
    from services import mail_policy_sync

    extra = [e.strip().lower() for e in body.allowed_senders if e.strip()]
    return await bot_call(
        db,
        user.tenant_id,
        "PUT",
        "/v1/mail-policy",
        body={
            "allowed_senders": mail_policy_sync.combinar(db, user.tenant_id, extra),
            "extra_senders": sorted(set(extra)),
            "recording_authorized": body.recording_authorized,
            "timezone": body.timezone,
            "bot_name": bot_name_of(db, user.tenant_id),
            "video": graba_video(db, user.tenant_id),
            **({"realtime": True} if transcribe_en_vivo(db, user.tenant_id) else {}),
        },
    )


@router.get("/email-receipts")
async def emails(
    user: User = Depends(require_admin), db: Session = Depends(get_session)
):
    return await bot_call(db, user.tenant_id, "GET", "/v1/email-receipts")


@router.post("/recordings/{mid}/playback")
def playback(
    mid: UUID,
    user: User = Depends(require_session_writer),
    db: Session = Depends(get_session),
):
    own(db, user, mid)
    token = secrets.token_urlsafe(32)
    db.add(
        BotAudioGrant(
            digest=hashlib.sha256(token.encode()).hexdigest(),
            tenant_id=user.tenant_id,
            user_id=user.id,
            meeting_id=str(mid),
            expires_at=time.time() + 1800,
        )
    )
    db.commit()
    return {"path": f"/api/owned-bot/audio/{mid}?grant={token}", "expires_in": 1800}


@audio_router.get("/audio/{mid}")
async def audio(
    mid: UUID, request: Request, grant: str, db: Session = Depends(get_session)
):
    row = db.get(BotAudioGrant, hashlib.sha256(grant.encode()).hexdigest())
    if not row or row.meeting_id != str(mid) or row.expires_at < time.time():
        raise HTTPException(401, "El permiso de reproducción venció")
    user = db.get(User, row.user_id)
    if not user or not user.is_active or user.tenant_id != row.tenant_id:
        raise HTTPException(401, "El permiso de reproducción no está activo")
    require_session_writer(user)
    own(db, user, mid)
    await bot_call(db, row.tenant_id, "GET", "/v1/capabilities")
    origin, key = configuration(db, row.tenant_id)
    headers = {"Authorization": "Bearer " + key, "Accept-Encoding": "identity"}
    if request.headers.get("Range"):
        if not re.fullmatch(r"bytes=\d*-\d*", request.headers["Range"]):
            raise HTTPException(416, "Rango no válido")
        headers["Range"] = request.headers["Range"]
    client = httpx.AsyncClient(timeout=60, follow_redirects=False)
    try:
        upstream = await client.send(
            client.build_request(
                "GET", origin + f"/v1/recordings/{mid}/audio", headers=headers
            ),
            stream=True,
        )
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(502, "No se pudo recuperar el audio") from exc
    if upstream.status_code not in {200, 206}:
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(409, "El audio todavía no está disponible")

    async def stream():
        try:
            async for part in upstream.aiter_raw():
                yield part
        finally:
            await upstream.aclose()
            await client.aclose()

    outgoing = {
        k: v
        for k, v in upstream.headers.items()
        if k.lower()
        in {"content-type", "content-length", "content-range", "accept-ranges"}
    }
    outgoing["Cache-Control"] = "private, no-store"
    return StreamingResponse(
        stream(), status_code=upstream.status_code, headers=outgoing
    )


class SpeakerLabel(BaseModel):
    display_name: str = PField(min_length=1, max_length=200)
    person_external_id: str | None = PField(default=None, max_length=200)


@router.put("/meetings/{mid}/speakers/{speaker_id}")
def label(
    mid: UUID,
    speaker_id: str,
    body: SpeakerLabel,
    user: User = Depends(require_session_writer),
    db: Session = Depends(get_session),
):
    own(db, user, mid)
    inbox = db.exec(
        select(BotInbox).where(
            BotInbox.tenant_id == user.tenant_id, BotInbox.meeting_id == str(mid)
        )
    ).first()
    if not inbox:
        raise HTTPException(409, "Espera a que la sesión llegue a Acten")
    event = ActenBotEvent.model_validate_json(inbox.payload)
    if speaker_id not in {s.speaker_id for s in event.data.transcript}:
        raise HTTPException(404, "Hablante no encontrado")
    name = body.display_name.strip()
    if not name:
        raise HTTPException(422, "Indica el nombre")
    if body.person_external_id:
        person = db.exec(
            select(User).where(
                User.tenant_id == user.tenant_id,
                User.external_ref == body.person_external_id,
            )
        ).first()
        contact = db.exec(
            select(ProjectContact)
            .join(Project)
            .where(
                Project.tenant_id == user.tenant_id,
                ProjectContact.external_ref == body.person_external_id,
            )
        ).first()
        if not person and not contact:
            raise HTTPException(
                422, "La persona no pertenece al directorio de esta empresa"
            )
        name = person.full_name if person else contact.name
    old_labels = {
        r.speaker_id: r.display_name
        for r in db.exec(
            select(BotSpeakerLabel).where(BotSpeakerLabel.inbox_id == inbox.id)
        ).all()
    }
    for segment in event.data.transcript:
        if segment.speaker_id in old_labels:
            segment.speaker_name = old_labels[segment.speaker_id]
    previous_text = event.data.transcript_text()
    row = db.get(BotSpeakerLabel, (inbox.id, speaker_id)) or BotSpeakerLabel(
        inbox_id=inbox.id, speaker_id=speaker_id, display_name=name, updated_by=user.id
    )
    row.display_name, row.person_external_id, row.updated_by = (
        name,
        body.person_external_id,
        user.id,
    )
    db.add(row)
    db.flush()
    labels = {
        r.speaker_id: r.display_name
        for r in db.exec(
            select(BotSpeakerLabel).where(BotSpeakerLabel.inbox_id == inbox.id)
        ).all()
    }
    for segment in event.data.transcript:
        if segment.speaker_id in labels:
            segment.speaker_name = labels[segment.speaker_id]
    meeting = db.get(MeetingSession, inbox.session_id)
    # Do not overwrite Acten curation with a newly rendered source transcript.
    preserved = not meeting or meeting.raw_transcript != previous_text
    if not preserved:
        meeting.raw_transcript = event.data.transcript_text()
        db.add(meeting)
    db.commit()
    return {
        "speaker_id": speaker_id,
        "display_name": name,
        "identity_source": "human_confirmed",
        "transcript_preserved": preserved,
    }


# ─────────────────────── API pública: sesión en vivo ───────────────────────


class LiveMeeting(BaseModel):
    """Cuerpo de POST /api/v1/meetings/live."""

    model_config = ConfigDict(extra="forbid")
    meeting_url: str = PField(min_length=1, max_length=4096)
    title: str = PField(default="Reunión", min_length=1, max_length=300)
    language: Literal["es", "en", "ca"] = "es"
    # Idempotencia: repetir la llamada con el mismo valor no manda otro bot.
    external_id: str | None = PField(default=None, min_length=1, max_length=160)
    project_external_id: str | None = PField(default=None, min_length=1, max_length=200)
    # Reunión programada: fecha y hora de inicio con zona (ISO 8601). El bot
    # entra a esa hora. Ausente = entra ahora.
    scheduled_start: AwareDatetime | None = None
    # Obsoleto y sin efecto (se acepta por compatibilidad): el vídeo lo decide
    # la suscripción de la empresa. Ver GET /api/v1/capabilities.
    video: bool = False
    # Quien llama declara que los asistentes saben que se graba.
    recording_authorized: Literal[True]


def _exigir_funcion(db: Session, tenant_id: int, feature: str) -> None:
    from services import entitlements
    from services.billing_catalog import FEATURES

    if not entitlements.tiene(db, tenant_id, feature):
        raise HTTPException(
            402,
            {
                "message": "Esta opción no está incluida en el plan de tu empresa.",
                "feature": feature,
                "label": FEATURES.get(feature, feature),
            },
        )


@v1_router.post("/meetings/live", status_code=202)
async def live_start(
    body: LiveMeeting,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("sessions:write")),
):
    """Manda el bot a una reunión de Meet, Teams o Zoom que ya está en curso.

    El bot pide entrar de inmediato; alguien de la reunión tiene que
    admitirlo. Al terminar, la sesión aparece en Acten como cualquier otra.
    """
    from services import meeting_source

    tenant_id = ctx.tenant.id
    _exigir_funcion(db, tenant_id, F_OWNED_BOT)
    if not meeting_source.admite(db, tenant_id, meeting_source.OWNED_BOT):
        raise HTTPException(409, "Esta empresa no tiene el bot propio como origen de reuniones.")
    project_id = None
    if body.project_external_id:
        project = db.exec(
            select(Project).where(
                Project.tenant_id == tenant_id,
                Project.external_ref == body.project_external_id,
            )
        ).first()
        visible = ctx.org_wide or not ctx.on_behalf_of or (
            project is not None and project.id in ctx.visible_project_ids
        )
        if not project or not visible:
            raise HTTPException(
                422, f"No existe un proyecto sincronizado con id '{body.project_external_id}'."
            )
        project_id = project.id
    # La captura necesita un dueño en Acten. Si la persona de la otra
    # plataforma aún no tiene cuenta aquí, se atribuye al primer usuario activo.
    owner = ctx.acting_user or db.exec(
        select(User)
        .where(User.tenant_id == tenant_id, User.is_active == True)  # noqa: E712
        .order_by(User.id)
    ).first()
    if owner is None:
        raise HTTPException(503, "La empresa no tiene usuarios activos.")
    capture = StartCapture(
        external_id=body.external_id or f"api-{secrets.token_hex(12)}",
        title=body.title,
        language=body.language,
        meeting_url=body.meeting_url,
        recording_authorized=True,
        project_id=project_id,
        scheduled_start=body.scheduled_start,
    )
    result = await start_capture(db, tenant_id, owner.id, "meeting", capture)
    return _live_public(result, None, body.project_external_id)


# Estados en los que el bot aún no terminó: lo que se enseña como «en vivo».
LIVE_ACTIVE_STATES = {
    "scheduled", "queued", "dispatching", "joining", "recording", "transcribing", "analyzing",
}


@v1_router.get("/meetings/live")
async def live_list(
    status: Literal["active", "all"] = "active",
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("sessions:read")),
):
    """Reuniones del bot de la empresa: en curso (por defecto) o las últimas 100.

    Sirve para pintar una zona «en vivo»: cada elemento trae su estado y, si
    está grabando y la suscripción lo incluye, `realtime_url`. Leyendo como
    empresa salen todas, también las invitadas por calendario; en nombre de
    una persona, solo las que pidió ella o las de sus proyectos.
    """
    result = await bot_call(db, ctx.tenant.id, "GET", "/v1/meetings?limit=100")
    links = {
        row.meeting_id: row
        for row in db.exec(
            select(BotControlLink).where(BotControlLink.tenant_id == ctx.tenant.id)
        ).all()
        if row.meeting_id
    }
    sessions = {
        row.meeting_id: row.session_id
        for row in db.exec(select(BotInbox).where(BotInbox.tenant_id == ctx.tenant.id)).all()
    }
    refs = {
        p.id: p.external_ref
        for p in db.exec(select(Project).where(Project.tenant_id == ctx.tenant.id)).all()
    }
    personal = bool(ctx.on_behalf_of) and not ctx.org_wide
    items = []
    for item in result.get("items", []):
        if item.get("source") == "browser":
            continue  # grabación web: no hay bot en ninguna reunión
        if status == "active" and item.get("state") not in LIVE_ACTIVE_STATES:
            continue
        link = links.get(item["id"])
        if personal:
            mine = link is not None and ctx.acting_user is not None and link.owner_id == ctx.acting_user.id
            if not mine and (link is None or link.project_id not in ctx.visible_project_ids):
                continue
        items.append(
            _live_public(item, sessions.get(item["id"]), refs.get(link.project_id) if link else None)
        )
    return {"items": items}


@v1_router.get("/meetings/live/{meeting_id}")
async def live_status(
    meeting_id: UUID,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("sessions:read")),
):
    """Estado de una captura pedida por la API; `acten_session_id` llega al terminar."""
    link = db.exec(
        select(BotControlLink).where(
            BotControlLink.tenant_id == ctx.tenant.id,
            BotControlLink.meeting_id == str(meeting_id),
        )
    ).first()
    if link and ctx.on_behalf_of and not ctx.org_wide:
        mine = ctx.acting_user is not None and link.owner_id == ctx.acting_user.id
        if not mine and link.project_id not in ctx.visible_project_ids:
            link = None
    if link is None:
        raise HTTPException(404, "Captura no encontrada.")
    result = await bot_call(db, ctx.tenant.id, "GET", f"/v1/meetings/{meeting_id}")
    inbox = db.exec(
        select(BotInbox).where(
            BotInbox.tenant_id == ctx.tenant.id, BotInbox.meeting_id == str(meeting_id)
        )
    ).first()
    project = db.get(Project, link.project_id) if link.project_id else None
    return _live_public(
        result, inbox.session_id if inbox else None, project.external_ref if project else None
    )


@v1_router.post("/meetings/live/{meeting_id}/stop", status_code=202)
async def live_stop(
    meeting_id: UUID,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("sessions:write")),
):
    """Cancela una reunión programada o saca al bot de una en curso.

    En curso, el bot sale y la reunión se procesa con lo grabado hasta ahí.
    Repetir la llamada no hace daño.
    """
    link = db.exec(
        select(BotControlLink).where(
            BotControlLink.tenant_id == ctx.tenant.id,
            BotControlLink.meeting_id == str(meeting_id),
        )
    ).first()
    if link and ctx.on_behalf_of and not ctx.org_wide:
        mine = ctx.acting_user is not None and link.owner_id == ctx.acting_user.id
        if not mine and link.project_id not in ctx.visible_project_ids:
            link = None
    if link is None:
        raise HTTPException(404, "Captura no encontrada.")
    result = await bot_call(db, ctx.tenant.id, "POST", f"/v1/meetings/{meeting_id}/stop")
    project = db.get(Project, link.project_id) if link.project_id else None
    return _live_public(result, None, project.external_ref if project else None)


def _live_public(result: dict, session_id: int | None, project_external_id: str | None) -> dict:
    """Solo lo que le sirve a otra plataforma; nada interno del proveedor."""
    return {
        "id": result["id"],
        "external_id": result["external_id"],
        "state": result.get("state"),
        "title": result.get("title"),
        # Hora a la que entra el bot si la reunión es programada; si no, null.
        "scheduled_start": result.get("join_at"),
        "error_code": result.get("error_code"),
        # Stream de solo lectura de la transcripción en vivo (WebSocket); solo
        # mientras el bot está en la reunión y si la suscripción lo incluye.
        "realtime_url": result.get("live_url"),
        "project_external_id": project_external_id,
        "acten_session_id": session_id,
    }


# ─────────────── API pública: ajustes de reuniones de la empresa ───────────────


class MeetingSettingsPatch(BaseModel):
    """Cuerpo de PATCH /api/v1/meetings/settings; solo cambia lo que venga."""

    model_config = ConfigDict(extra="forbid")
    # Nombre con el que el bot aparece en Meet, Teams y Zoom.
    bot_name: str | None = PField(default=None, min_length=1, max_length=50)
    # Por dónde entran las reuniones: Fireflies, el bot propio o ambos.
    meeting_source: Literal["fireflies", "owned_bot", "both"] | None = None


def _meeting_settings(db: Session, tenant_id: int) -> dict:
    from services import meeting_source

    estado = meeting_source.estado(db, tenant_id)
    return {
        "bot_name": bot_name_of(db, tenant_id),
        "meeting_source": estado["source"],
        "fireflies": estado["accepts_fireflies"],
        "owned_bot": estado["accepts_owned_bot"],
        # Qué orígenes deja elegir la suscripción de la empresa.
        "meeting_source_options": [
            {"value": o["value"], "label": o["label"], "available": o["available"]}
            for o in estado["options"]
        ],
    }


@v1_router.get("/meetings/settings")
def get_meeting_settings(
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("integrations:read")),
):
    """Nombre del bot y origen de las reuniones de la empresa."""
    return _meeting_settings(db, ctx.tenant.id)


@v1_router.patch("/meetings/settings")
async def patch_meeting_settings(
    body: MeetingSettingsPatch,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("integrations:write")),
):
    """Cambia el nombre del bot y/o el origen de las reuniones.

    Son ajustes de toda la empresa: en nombre de una persona solo los cambia
    un administrador. Lo que la suscripción no incluye no se puede elegir.
    """
    from services import mail_policy_sync, meeting_source

    if ctx.on_behalf_of and not ctx.org_wide:
        if ctx.acting_user is None or not is_admin(ctx.acting_user):
            raise HTTPException(403, "Solo un administrador de la empresa cambia estos ajustes.")
    tenant_id = ctx.tenant.id
    if body.meeting_source:
        try:
            meeting_source.cambiar(db, tenant_id, body.meeting_source)
        except PermissionError as exc:
            raise HTTPException(402, str(exc)) from exc
        # Los remitentes de las invitaciones por correo dependen del origen.
        try:
            await mail_policy_sync.sincronizar(db, tenant_id)
        except Exception as exc:  # noqa: BLE001 — sin bot configurado no hay nada que sincronizar
            logger.info("Sin sincronización del bot para la empresa %s: %s", tenant_id, exc)
    if body.bot_name is not None:
        nombre = " ".join(body.bot_name.split())
        if not nombre:
            raise HTTPException(422, "El nombre del bot no puede quedar vacío.")
        await set_bot_name(db, tenant_id, nombre)
    return _meeting_settings(db, tenant_id)
