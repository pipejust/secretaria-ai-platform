"""Ask Notiva — chat con RAG sobre el histórico de actas.

Endpoint:
    POST /api/ask  body: {question, project_id?, top_k?}
    →   {answer, citations: [{session_id, kind, snippet, distance}]}

Pipeline:
1. embed_text(question)
2. SELECT top_k FROM embeddingchunk ORDER BY <-> question (cosine).
3. Construir contexto con los chunks. Llamar Groq llama-3.3-70b con
   instrucción de "responde citando session_id".
4. Devolver answer + citations.

Cuando OPENAI_API_KEY o GROQ_API_KEY faltan, devuelve 503.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from config import settings
from database import get_session
from services.embedding_service import search_similar

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ask", tags=["Ask Notiva (RAG)"])

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"


class AskRequest(BaseModel):
    question: str
    project_id: Optional[int] = None
    top_k: int = 8


class Citation(BaseModel):
    session_id: int
    kind: str
    snippet: str
    distance: float


class AskResponse(BaseModel):
    answer: str
    citations: list[Citation]
    model: str
    chunks_used: int


def _build_context(chunks: list[dict]) -> str:
    blocks = []
    for c in chunks:
        snippet = (c["content"] or "")[:1200]
        blocks.append(f"[Sesión #{c['session_id']} · {c['kind']}]\n{snippet}")
    return "\n\n---\n\n".join(blocks)


@router.post("", response_model=AskResponse)
async def ask(payload: AskRequest, db: Session = Depends(get_session)):
    if not settings.openai_api_key:
        raise HTTPException(503, "OPENAI_API_KEY no configurada (necesaria para embeddings).")
    if not settings.groq_api_key:
        raise HTTPException(503, "GROQ_API_KEY no configurada (necesaria para el LLM de respuesta).")

    q = (payload.question or "").strip()
    if len(q) < 3:
        raise HTTPException(400, "La pregunta debe tener al menos 3 caracteres.")

    chunks = await search_similar(
        db, q, top_k=max(min(payload.top_k, 20), 1), project_id=payload.project_id
    )
    if not chunks:
        return AskResponse(
            answer="No encontré actas relevantes en el histórico para responder a esa pregunta.",
            citations=[], model=GROQ_MODEL, chunks_used=0,
        )

    context = _build_context(chunks)
    system = (
        "Eres el asistente de Notiva. Responde EXCLUSIVAMENTE en español. "
        "Cita las sesiones que usaste como evidencia con el formato "
        "(Sesión #ID). NO inventes datos que no estén en el contexto. "
        "Si la información en el contexto es insuficiente, dilo claramente."
    )
    user = (
        f"Pregunta del usuario: {q}\n\n"
        f"Contexto extraído de actas anteriores (top_k={len(chunks)}):\n"
        f"{context}\n\n"
        f"Responde de forma concisa y cita las sesiones."
    )

    payload_llm = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
    }
    headers = {
        "Authorization": f"Bearer {settings.groq_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(GROQ_URL, json=payload_llm, headers=headers)
        r.raise_for_status()
        body = r.json()
        usage = body.get("usage") or {}
        if usage:
            logger.info(
                "GROQ_TOKENS_USED model=%s prompt=%s completion=%s total=%s [/api/ask]",
                GROQ_MODEL, usage.get("prompt_tokens"),
                usage.get("completion_tokens"), usage.get("total_tokens"),
            )
        answer = body["choices"][0]["message"]["content"].strip()

    citations = [
        Citation(
            session_id=c["session_id"], kind=c["kind"],
            snippet=(c["content"] or "")[:280], distance=c["distance"],
        )
        for c in chunks
    ]
    return AskResponse(answer=answer, citations=citations, model=GROQ_MODEL, chunks_used=len(chunks))
