"""La llave del motor de IA, puesta desde el administrador.

Antes solo salía de `GROQ_API_KEY`: para cambiarla había que entrar al
servidor, editar la variable y reiniciar el contenedor. Y como era una
sola para toda la instalación, el gasto de todas las empresas caía en la
misma factura sin forma de separarlo.

Ahora cada empresa puede poner la suya. La del entorno sigue valiendo
como respaldo, así que quien no ponga nada no nota ningún cambio.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from database import get_session
from models import User
from routers.auth import require_admin
from services import llm_keys
from services.groq_models import MODELO_PRINCIPAL

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/ai", tags=["Motor de IA"])


@router.get("/proveedores")
def listar(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
):
    """Estado de las llaves de esta empresa. Nunca devuelve la llave en claro."""
    return {
        "proveedores": [
            llm_keys.estado(db, admin.tenant_id, p) for p in llm_keys.PROVEEDORES
        ]
    }


class LlaveIn(BaseModel):
    api_key: str = Field(
        default="",
        description="Vacía borra la de la empresa y vuelve a usar la del entorno.",
    )


@router.put("/proveedores/{proveedor}")
def guardar(
    proveedor: str, cuerpo: LlaveIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
):
    if proveedor not in llm_keys.PROVEEDORES:
        raise HTTPException(404, f"Proveedor desconocido: {proveedor}")
    return llm_keys.guardar(db, admin.tenant_id, proveedor, cuerpo.api_key)


@router.post("/proveedores/{proveedor}/comprobar")
async def comprobar(
    proveedor: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
):
    """Pregunta al proveedor si la llave sirve, antes de que falle una reunión.

    Se hace la llamada más barata posible —un mensaje de un token— en vez
    de dar por buena una llave que solo se descubre mala cuando una
    sesión entra y el pipeline se cae.
    """
    if proveedor not in llm_keys.PROVEEDORES:
        raise HTTPException(404, f"Proveedor desconocido: {proveedor}")
    llave = llm_keys.clave(proveedor, admin.tenant_id)
    if not llave:
        return {"ok": False, "detalle": "No hay ninguna llave configurada."}

    try:
        async with httpx.AsyncClient(timeout=20.0) as cli:
            r = await cli.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {llave}",
                         "Content-Type": "application/json"},
                json={"model": MODELO_PRINCIPAL,
                      "messages": [{"role": "user", "content": "ping"}],
                      "max_tokens": 1},
            )
    except httpx.HTTPError as exc:
        return {"ok": False, "detalle": f"No se pudo hablar con Groq: {exc}"}

    if r.status_code < 400:
        return {"ok": True, "detalle": f"La llave funciona. Modelo en uso: {MODELO_PRINCIPAL}."}
    if r.status_code in (401, 403):
        return {"ok": False, "detalle": "Groq rechazó la llave: no es válida o fue revocada."}
    if r.status_code == 429:
        # La llave es buena; lo que se agotó es la cuota. Decir «llave
        # inválida» aquí mandaría a cambiar lo que no está roto.
        return {"ok": True, "detalle": "La llave es válida, pero la cuota está agotada ahora mismo."}
    return {"ok": False, "detalle": f"Groq respondió {r.status_code}: {r.text[:200]}"}
