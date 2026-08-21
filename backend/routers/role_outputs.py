"""Sprint 04 — endpoints para Outputs role-específicos.

Las plantillas vienen seedeadas por backend/scripts/seed_dev.py (6 por defecto).
Endpoints:
    GET    /api/output_templates                              — lista plantillas activas
    GET    /api/sessions/{id}/outputs                         — outputs ya generados
    POST   /api/sessions/{id}/outputs?template_id=X           — genera output con LLM
    DELETE /api/outputs/{output_id}                           — borra un output
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from config import settings
from database import get_session
from models import MeetingSession, OutputTemplate, SessionOutput, User
from routers.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Outputs role-específicos"])

from services.llm_keys import clave_groq

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"


class GenerateOutputResponse(BaseModel):
    id: int
    title: str
    body: str
    output_format: str
    created_at: str


@router.get("/api/output_templates")
def list_templates(
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    rows = db.exec(
        select(OutputTemplate).where(OutputTemplate.is_active == True)
        .order_by(OutputTemplate.role_type)
    ).all()
    return [
        {"id": r.id, "name": r.name, "role_type": r.role_type, "output_format": r.output_format}
        for r in rows
    ]


@router.get("/api/sessions/{session_id}/outputs")
def list_outputs(
    session_id: int,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    rows = db.exec(
        select(SessionOutput).where(SessionOutput.session_id == session_id)
        .order_by(SessionOutput.created_at.desc())
    ).all()
    return [
        {"id": r.id, "title": r.title, "body": r.body,
         "output_format": r.output_format, "template_id": r.template_id,
         "created_at": r.created_at}
        for r in rows
    ]


@router.post("/api/sessions/{session_id}/outputs", response_model=GenerateOutputResponse)
async def generate_output(
    session_id: int,
    template_id: int = Query(...),
    provider: str = Query("openai", description="'openai' o 'groq'"),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    sess = db.get(MeetingSession, session_id)
    # Aislamiento: sin comprobar el tenant, cualquiera podía generar un
    # artefacto sobre la transcripción de otra empresa pasando su id.
    if not sess or sess.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Sesión no encontrada.")
    tpl = db.get(OutputTemplate, template_id)
    if not tpl or not tpl.is_active or tpl.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Plantilla no encontrada o inactiva.")
    if not sess.raw_transcript:
        raise HTTPException(400, "La sesión no tiene transcripción para procesar.")

    # Render del prompt
    prompt = (
        tpl.prompt_template
        .replace("{{transcript}}", sess.raw_transcript[:60000])
        .replace("{{date}}", str(sess.date))
        .replace("{{contacts}}", "")
    )

    # Selección de LLM
    if provider == "groq":
        if not clave_groq(current_user.tenant_id):
            raise HTTPException(
                503,
                "Sin llave de Groq. Un administrador la pone en "
                "Configuración → Integraciones → Motor de IA.",
            )
        url = GROQ_URL
        from services.groq_models import MODELO_PRINCIPAL
        model = MODELO_PRINCIPAL
        headers = {"Authorization": f"Bearer {clave_groq(current_user.tenant_id)}",
                   "Content-Type": "application/json"}
    else:
        if not settings.openai_api_key:
            raise HTTPException(503, "OPENAI_API_KEY no configurado.")
        url = OPENAI_URL
        model = "gpt-4o"
        headers = {"Authorization": f"Bearer {settings.openai_api_key}",
                   "Content-Type": "application/json"}

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Eres un escritor experto que genera artefactos corporativos en español. Devuelve EXCLUSIVAMENTE el contenido pedido en formato Markdown, sin meta-comentarios."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(url, json=payload, headers=headers)
        r.raise_for_status()
        body_json = r.json()
        usage = body_json.get("usage") or {}
        if usage:
            logger.info(
                "%s_TOKENS_USED model=%s prompt=%s completion=%s [generate_output template=%s]",
                provider.upper(), model,
                usage.get("prompt_tokens"), usage.get("completion_tokens"),
                tpl.name,
            )
        content = body_json["choices"][0]["message"]["content"].strip()

    out = SessionOutput(
        session_id=session_id,
        template_id=template_id,
        title=f"{tpl.name} — {sess.title or 'Sesión'}",
        body=content,
        output_format=tpl.output_format,
        created_by_user_id=current_user.id,
    )
    db.add(out); db.commit(); db.refresh(out)

    return GenerateOutputResponse(
        id=out.id, title=out.title, body=out.body,
        output_format=out.output_format, created_at=out.created_at,
    )


@router.delete("/api/outputs/{output_id}")
def delete_output(
    output_id: int,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    out = db.get(SessionOutput, output_id)
    if not out:
        raise HTTPException(404, "Output no encontrado.")
    db.delete(out); db.commit()
    return {"status": "deleted", "id": output_id}
