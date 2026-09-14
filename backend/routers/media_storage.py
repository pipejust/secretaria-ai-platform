"""Superadministrador: bucket S3 compatible donde se guarda el vídeo de las reuniones."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from database import get_session
from models import User
from routers.auth import require_superadmin
from services import media_storage

router = APIRouter(prefix="/api/media-storage", tags=["Almacenamiento de vídeo"])


class StorageConfigIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint_url: str = Field(min_length=8, max_length=200)
    region: str = Field(default="", max_length=64)
    bucket: str = Field(min_length=1, max_length=128)
    access_key: str = Field(min_length=1, max_length=200)
    # Vacío = conservar la guardada.
    secret_key: Optional[str] = Field(default=None, max_length=200)


@router.get("/config")
def get_config(_u: User = Depends(require_superadmin), db: Session = Depends(get_session)):
    return media_storage.estado_config(db)


@router.put("/config")
def put_config(
    body: StorageConfigIn,
    _u: User = Depends(require_superadmin),
    db: Session = Depends(get_session),
):
    try:
        return media_storage.guardar_config(db, **body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/test")
def test_config(_u: User = Depends(require_superadmin), db: Session = Depends(get_session)):
    try:
        return media_storage.probar(db)
    except media_storage.StorageError as exc:
        raise HTTPException(502, str(exc)) from exc
