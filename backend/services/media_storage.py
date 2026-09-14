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
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from sqlmodel import Session, select

from models import IntegrationSetting, Tenant
from services.cifrado import cifrar, descifrar

logger = logging.getLogger(__name__)

PROVIDER = "object_storage"
SLUG_DUENIO = "acten"
SEGUNDOS_URL = 900
# Un WebM de 8 h a 1080p ronda los 5 GB; por encima de esto algo no cuadra.
MAX_BYTES = 8 * 1024 ** 3
CONTENT_TYPE_VIDEO = "video/webm"
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


def clave_video(tenant_id: int, session_id: int) -> str:
    return f"tenants/{tenant_id}/sessions/{session_id}/recording.webm"


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


class _Lector:
    """Adapta el streaming de httpx a `read(n)` para `upload_fileobj`, contando bytes."""

    def __init__(self, chunks, limite: int):
        self._chunks = chunks
        self._resto = b""
        self._limite = limite
        self.bytes = 0

    def read(self, size: int = -1) -> bytes:
        while size < 0 or len(self._resto) < size:
            try:
                parte = next(self._chunks)
            except StopIteration:
                break
            self._resto += parte
            self.bytes += len(parte)
            if self.bytes > self._limite:
                raise StorageError("La grabación supera el tamaño máximo admitido")
        if size < 0:
            datos, self._resto = self._resto, b""
        else:
            datos, self._resto = self._resto[:size], self._resto[size:]
        return datos


def _origen_permitido(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise StorageError("La URL de la grabación debe ser HTTPS")
    from services.calendar_ics import IcsError, _destino_permitido

    try:
        _destino_permitido(parsed.hostname)
    except IcsError as exc:
        raise StorageError(str(exc)) from exc


def subir_desde_url(
    db: Session,
    url: str,
    key: str,
    content_type: str = CONTENT_TYPE_VIDEO,
    *,
    transport: httpx.BaseTransport | None = None,
) -> dict:
    """Descarga `url` en streaming y la sube al bucket bajo `key`.

    No se guarda en disco: la grabación puede pesar gigas y el contenedor
    no tiene sitio para eso. `transport` solo lo usan las pruebas.
    """
    _origen_permitido(url)
    client, cfg = _cliente_configurado(db)
    try:
        with httpx.Client(
            timeout=httpx.Timeout(60, connect=15),
            follow_redirects=True,
            transport=transport or _transport_descarga,
        ) as http, http.stream("GET", url) as response:
            if response.status_code != 200:
                raise StorageError(f"La grabación respondió {response.status_code}")
            lector = _Lector(response.iter_bytes(), MAX_BYTES)
            client.upload_fileobj(
                lector, cfg.bucket, key, ExtraArgs={"ContentType": content_type}
            )
            return {"key": key, "bytes": lector.bytes}
    except StorageError:
        raise
    except httpx.HTTPError as exc:
        raise StorageError(f"No se pudo descargar la grabación: {_resumen(exc)}") from exc
    except Exception as exc:  # noqa: BLE001
        raise StorageError(f"No se pudo subir la grabación al bucket: {_resumen(exc)}") from exc


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
