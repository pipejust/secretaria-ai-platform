"""Almacenamiento S3 compatible (Hetzner Object Storage) para el vídeo de reuniones.

Las credenciales viven cifradas en `IntegrationSetting('object_storage')` de la
empresa dueña de la plataforma (slug `acten`), como las de Wompi; nunca en el
entorno ni en el navegador. El bucket es uno para toda la plataforma y cada
archivo va bajo `tenants/<tenant>/sessions/<sesión>/...`, así el aislamiento
por empresa queda en la clave y no depende de la configuración.

`boto3` se importa dentro de `_cliente` para que el resto de la aplicación (y
las pruebas que simulan el cliente) no dependan de que esté instalado.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from sqlmodel import Session, select

from models import IntegrationSetting, MeetingSession, Tenant
from services.cifrado import cifrar, descifrar

logger = logging.getLogger(__name__)

PROVIDER = "object_storage"
SLUG_DUENIO = "acten"
SEGUNDOS_URL = 900
# Un WebM de 8 h a 1080p ronda los 5 GB; por encima de esto algo no cuadra.
MAX_BYTES = 8 * 1024 ** 3
# Una reunión no es cine: 720p a 15 fps basta para leer una pantalla compartida
# y ver caras, y la voz cabe en mono. CRF más alto = archivo más pequeño.
FFMPEG_ARGS = [
    "-vf", "scale=-2:'min(720,trunc(ih/2)*2)',fps=15",
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-ac", "1", "-b:a", "48k",
    "-movflags", "+faststart",
]
FFMPEG_TIMEOUT = 3 * 3600
# Transporte httpx para la descarga; solo las pruebas lo sustituyen.
_transport_descarga: httpx.BaseTransport | None = None


class StorageError(Exception):
    """Error hablando con el bucket o con la URL de origen; el mensaje es apto para la UI."""


@dataclass
class StorageConfig:
    endpoint_url: str = ""
    region: str = ""
    bucket: str = ""
    access_key: str = ""
    secret_key: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.endpoint_url and self.bucket and self.access_key and self.secret_key)


def clave_video(tenant_id: int, session_id: int, ext: str = "mp4") -> str:
    return f"tenants/{tenant_id}/sessions/{session_id}/recording.{ext}"


def _fila(db: Session):
    duenio = db.exec(select(Tenant).where(Tenant.slug == SLUG_DUENIO)).first()
    if not duenio:
        return None, None
    row = db.exec(
        select(IntegrationSetting).where(
            IntegrationSetting.tenant_id == duenio.id,
            IntegrationSetting.provider_name == PROVIDER,
            IntegrationSetting.user_id.is_(None),
        )
    ).first()
    return duenio, row


def leer_config(db: Session) -> StorageConfig:
    _, row = _fila(db)
    cfg = json.loads(row.config_json) if row and row.config_json else {}
    return StorageConfig(
        endpoint_url=cfg.get("endpoint_url", ""),
        region=cfg.get("region", ""),
        bucket=cfg.get("bucket", ""),
        access_key=cfg.get("access_key", ""),
        secret_key=descifrar(cfg.get("secret_key", "")),
    )


def configurado(db: Session) -> bool:
    return leer_config(db).configured


def _validar_endpoint(endpoint_url: str) -> str:
    parsed = urlsplit(endpoint_url.strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("El endpoint debe ser un origen HTTPS, p. ej. https://fsn1.your-objectstorage.com")
    return endpoint_url.strip().rstrip("/")


def guardar_config(
    db: Session,
    *,
    endpoint_url: str,
    region: str,
    bucket: str,
    access_key: str,
    secret_key: str | None,
) -> dict:
    duenio, row = _fila(db)
    if not duenio:
        raise ValueError("No existe la empresa dueña de la plataforma (slug 'acten')")
    old = json.loads(row.config_json) if row and row.config_json else {}
    bucket = bucket.strip()
    if not bucket or "/" in bucket:
        raise ValueError("Indica el nombre del bucket, sin barras")
    cfg = {
        "endpoint_url": _validar_endpoint(endpoint_url),
        "region": region.strip(),
        "bucket": bucket,
        "access_key": access_key.strip(),
        # Vacío = conservar la guardada.
        "secret_key": cifrar(secret_key.strip()) if secret_key and secret_key.strip() else old.get("secret_key", ""),
    }
    if not cfg["access_key"]:
        raise ValueError("Indica la access key")
    if not cfg["secret_key"]:
        raise ValueError("Indica la secret key")
    if not row:
        row = IntegrationSetting(tenant_id=duenio.id, provider_name=PROVIDER)
    row.config_json = json.dumps(cfg)
    row.is_active = True
    db.add(row)
    db.commit()
    return estado_config(db)


def estado_config(db: Session) -> dict:
    cfg = leer_config(db)
    return {
        "endpoint_url": cfg.endpoint_url,
        "region": cfg.region,
        "bucket": cfg.bucket,
        "access_key": cfg.access_key,
        "has_secret_key": bool(cfg.secret_key),
        "configured": cfg.configured,
    }


def _cliente(cfg: StorageConfig):
    """Cliente S3 sobre el endpoint configurado. Las pruebas lo sustituyen."""
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=cfg.endpoint_url,
        region_name=cfg.region or None,
        aws_access_key_id=cfg.access_key,
        aws_secret_access_key=cfg.secret_key,
        # Path-style vale para cualquier nombre de bucket en Hetzner y en el
        # resto de S3 compatibles; virtual-host falla con puntos en el nombre.
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def _cliente_configurado(db: Session):
    cfg = leer_config(db)
    if not cfg.configured:
        raise StorageError("El almacenamiento de vídeo no está configurado")
    return _cliente(cfg), cfg


def probar(db: Session) -> dict:
    """Valida las credenciales listando el bucket (un objeto basta)."""
    client, cfg = _cliente_configurado(db)
    try:
        client.list_objects_v2(Bucket=cfg.bucket, MaxKeys=1)
    except Exception as exc:  # noqa: BLE001 — botocore lanza decenas de clases
        logger.warning("Prueba del almacenamiento de vídeo fallida: %s", exc)
        raise StorageError(f"No se pudo listar el bucket «{cfg.bucket}»: {_resumen(exc)}") from exc
    return {"ok": True, "bucket": cfg.bucket, "endpoint_url": cfg.endpoint_url}


def _resumen(exc: Exception) -> str:
    texto = str(exc) or type(exc).__name__
    return texto if len(texto) <= 200 else texto[:200] + "…"


def _origen_permitido(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise StorageError("La URL de la grabación debe ser HTTPS")
    from services.calendar_ics import IcsError, _destino_permitido

    try:
        _destino_permitido(parsed.hostname)
    except IcsError as exc:
        raise StorageError(str(exc)) from exc


def _descargar(url: str, destino: Path) -> int:
    try:
        with httpx.Client(
            timeout=httpx.Timeout(60, connect=15),
            follow_redirects=True,
            transport=_transport_descarga,
        ) as http, http.stream("GET", url) as response, destino.open("wb") as salida:
            if response.status_code != 200:
                raise StorageError(f"La grabación respondió {response.status_code}")
            total = 0
            for parte in response.iter_bytes():
                total += len(parte)
                if total > MAX_BYTES:
                    raise StorageError("La grabación supera el tamaño máximo admitido")
                salida.write(parte)
            return total
    except httpx.HTTPError as exc:
        raise StorageError(f"No se pudo descargar la grabación: {_resumen(exc)}") from exc


def _comprimir(origen: Path, destino: Path) -> bool:
    """Recodifica a MP4 compacto. True solo si salió bien y pesa menos que el original."""
    # `-f matroska` (WebM): sin adivinar el formato, un archivo que no sea la
    # grabación (una lista HLS que apunte a otros archivos o URLs) no se abre.
    orden = ["nice", "-n", "19", "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "matroska", "-i", str(origen), *FFMPEG_ARGS, str(destino)]
    try:
        subprocess.run(
            orden, check=True, timeout=FFMPEG_TIMEOUT,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as exc:
        logger.warning("ffmpeg no pudo comprimir el vídeo: %s", exc.stderr[-500:].decode(errors="replace"))
        return False
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("ffmpeg no pudo comprimir el vídeo: %s", _resumen(exc))
        return False
    return 0 < destino.stat().st_size < origen.stat().st_size


def subir_video(cfg: StorageConfig, url: str, tenant_id: int, session_id: int) -> dict:
    """Descarga la grabación, la comprime y la sube al bucket.

    Recibe la configuración y no una sesión de base: esto tarda minutos y no
    debe retener una conexión. Si ffmpeg falla se sube el original tal cual:
    un vídeo grande es mejor que ninguno, y la URL de origen caduca.
    """
    _origen_permitido(url)
    if not cfg.configured:
        raise StorageError("El almacenamiento de vídeo no está configurado")
    client = _cliente(cfg)
    with tempfile.TemporaryDirectory(prefix="acten-video-") as carpeta:
        original, compacto = Path(carpeta) / "original", Path(carpeta) / "compacto.mp4"
        bytes_original = _descargar(url, original)
        ext, archivo = ("mp4", compacto) if _comprimir(original, compacto) else ("webm", original)
        key = clave_video(tenant_id, session_id, ext)
        try:
            with archivo.open("rb") as contenido:
                client.upload_fileobj(
                    contenido, cfg.bucket, key, ExtraArgs={"ContentType": f"video/{ext}"}
                )
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"No se pudo subir la grabación al bucket: {_resumen(exc)}") from exc
        return {"key": key, "bytes": archivo.stat().st_size, "original_bytes": bytes_original}


def url_firmada(db: Session, key: str, segundos: int = SEGUNDOS_URL) -> str:
    client, cfg = _cliente_configurado(db)
    try:
        return client.generate_presigned_url(
            "get_object", Params={"Bucket": cfg.bucket, "Key": key}, ExpiresIn=segundos
        )
    except Exception as exc:  # noqa: BLE001
        raise StorageError(f"No se pudo firmar la URL: {_resumen(exc)}") from exc


def borrar(db: Session, key: str) -> None:
    client, cfg = _cliente_configurado(db)
    try:
        client.delete_object(Bucket=cfg.bucket, Key=key)
    except Exception as exc:  # noqa: BLE001
        raise StorageError(f"No se pudo borrar «{key}»: {_resumen(exc)}") from exc


def caduca_el(db: Session, meeting: MeetingSession) -> datetime | None:
    """Cuándo se borra el vídeo de esta sesión según el plan de su empresa; None = nunca."""
    from services import entitlements

    dias = entitlements.de_empresa(db, meeting.tenant_id).video_retention_days
    if not dias:
        return None
    try:
        return datetime.fromisoformat(meeting.created_at) + timedelta(days=dias)
    except (TypeError, ValueError):
        return None  # sin fecha fiable no se borra nada


def purgar_vencidos(db: Session, ahora: datetime | None = None) -> int:
    """Borra del bucket los vídeos que pasaron la retención de su plan. Devuelve cuántos.

    Solo se va el vídeo: el acta, la transcripción y el audio siguen. Si el
    bucket no responde, la clave se conserva y se reintenta en el siguiente pase.
    """
    ahora = ahora or datetime.now()
    borrados = 0
    con_video = db.exec(
        select(MeetingSession).where(MeetingSession.recording_video_key.is_not(None))
    ).all()
    for meeting in con_video:
        vence = caduca_el(db, meeting)
        if vence is None or vence > ahora:
            continue
        try:
            borrar(db, meeting.recording_video_key)
        except StorageError as exc:
            logger.error("Sesión %s: no se pudo borrar el vídeo vencido: %s", meeting.id, exc)
            continue
        logger.info("Sesión %s: vídeo %s borrado por retención del plan.", meeting.id, meeting.recording_video_key)
        meeting.recording_video_key = None
        db.add(meeting)
        db.commit()
        borrados += 1
    return borrados
