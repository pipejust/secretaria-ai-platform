"""Ask Notiva — chat con RAG sobre el histórico de actas.

Endpoint:
    POST /api/ask  body: {question, project_id?, top_k?, min_relevance?}
    →   {answer, structured?, citations: [...], model, chunks_used}

Pipeline:
1. embed_text(question)
2. SELECT top_k FROM embeddingchunk ORDER BY <-> question (cosine).
3. **Filtrar por umbral de distancia (RELEVANCE_THRESHOLD)** — sólo
   pasan al LLM los chunks suficientemente cercanos a la pregunta. Esto
   evita la contaminación de contexto que mezclaba decisiones/tareas de
   reuniones no relacionadas.
4. Construir contexto con los chunks filtrados. Llamar Groq llama-3.3-70b
   pidiendo JSON estricto donde cada decisión/tarea CITA explícitamente
   las sesiones origen (`source_sessions`).
5. Devolver answer + citations (solo de sesiones que pasaron el filtro).

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

# Umbral de distancia coseno (`<=>` de pgvector → 1 - cosine_similarity).
# - 0.0  = chunk idéntico a la pregunta
# - 0.6  = relacionado pero no muy similar
# - 1.0  = ortogonal (no relacionado)
# Empíricamente con text-embedding-3-small, 0.65 deja pasar resultados
# claramente relevantes y descarta ruido. Si una pregunta no tiene
# CHUNK alguno bajo este umbral, devolvemos "no encontré información".
RELEVANCE_THRESHOLD = 0.65


class AskRequest(BaseModel):
    question: str
    project_id: Optional[int] = None
    top_k: int = 8
    # Permitimos override del umbral desde el frontend para experimentación.
    min_relevance: Optional[float] = None


class Citation(BaseModel):
    session_id: int
    kind: str
    snippet: str
    distance: float


class Decision(BaseModel):
    """Cada decisión arrastra el (los) `session_id` de donde proviene
    para que el frontend pueda mostrar un chip 'Sesión #N' verificable."""
    text: str
    source_sessions: list[int] = []


class ActionItemDTO(BaseModel):
    title: str
    owner: str = ""
    due_date: str = ""
    status: str = ""  # "in_progress" | "pending" | "not_started" | "done"
    source_sessions: list[int] = []


class StructuredAnswer(BaseModel):
    """Respuesta estructurada que Groq devuelve cuando le pedimos JSON.

    `intro` es el primer párrafo introductorio.
    `decisions` es la lista de decisiones clave (cada una con sus fuentes).
    `action_items` es la tabla de tareas pendientes que extrajo del
    contexto. Si el modelo no encuentra alguna sección, devuelve [] o "".
    """
    intro: str = ""
    decisions: list[Decision] = []
    action_items: list[ActionItemDTO] = []


class AskResponse(BaseModel):
    answer: str
    structured: Optional[StructuredAnswer] = None
    citations: list[Citation]
    model: str
    chunks_used: int


def _build_context(chunks: list[dict]) -> str:
    blocks = []
    for c in chunks:
        snippet = (c["content"] or "")[:1200]
        blocks.append(f"[Sesión #{c['session_id']} · {c['kind']}]\n{snippet}")
    return "\n\n---\n\n".join(blocks)


def _coerce_int_list(raw) -> list[int]:
    """Acepta int, str numérica o lista de cualquiera de los anteriores y
    devuelve list[int] saneada (descarta lo que no parezca un ID)."""
    if raw is None:
        return []
    if isinstance(raw, (int, str)):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for x in raw:
        try:
            out.append(int(str(x).strip().lstrip('#')))
        except (ValueError, TypeError, AttributeError):
            continue
    return out


@router.post("", response_model=AskResponse)
async def ask(payload: AskRequest, db: Session = Depends(get_session)):
    if not settings.openai_api_key:
        raise HTTPException(503, "OPENAI_API_KEY no configurada (necesaria para embeddings).")
    if not settings.groq_api_key:
        raise HTTPException(503, "GROQ_API_KEY no configurada (necesaria para el LLM de respuesta).")

    q = (payload.question or "").strip()
    if len(q) < 3:
        raise HTTPException(400, "La pregunta debe tener al menos 3 caracteres.")

    raw_chunks = await search_similar(
        db, q, top_k=max(min(payload.top_k, 20), 1), project_id=payload.project_id
    )

    # Filtro por relevancia: descartamos chunks demasiado lejanos para
    # evitar que el LLM mezcle decisiones de reuniones no relacionadas.
    threshold = float(payload.min_relevance) if payload.min_relevance is not None else RELEVANCE_THRESHOLD
    chunks = [c for c in raw_chunks if c["distance"] <= threshold]

    logger.info(
        "ask: project_id=%s top_k=%s chunks_raw=%s chunks_relevant=%s threshold=%.2f",
        payload.project_id, payload.top_k, len(raw_chunks), len(chunks), threshold,
    )

    if not chunks:
        # Mensaje específico según si hubo CERO resultados o si todos
        # quedaron filtrados por baja relevancia (ayuda al usuario a
        # saber si reformular o si la base de actas no contiene el tema).
        msg = (
            "No encontré actas suficientemente relevantes para responder con precisión "
            "a esa pregunta. Intenta usar términos más específicos del contexto "
            "(nombre del proyecto, cliente, fecha aproximada)."
            if raw_chunks
            else "No encontré actas en el histórico relacionadas con esa pregunta."
        )
        return AskResponse(
            answer=msg, citations=[], model=GROQ_MODEL, chunks_used=0,
        )

    # Sólo expondremos como Fuentes las sesiones cuyo chunk pasó el filtro.
    relevant_session_ids = {c["session_id"] for c in chunks}

    context = _build_context(chunks)
    # Pedimos JSON estructurado para que el frontend pueda renderizar
    # secciones (Decisiones / Tareas pendientes / Fuentes) tal como el
    # mockup. Cada decisión y tarea DEBE indicar la(s) sesión(es) origen
    # para hacer verificable el resultado.
    system = (
        "Eres el asistente de Acten. Tu salida DEBE ser un objeto JSON válido "
        "con esta estructura EXACTA:\n"
        "{\n"
        '  "intro": "<resumen introductorio en español, 1-2 frases>",\n'
        '  "decisions": [\n'
        '    {"text": "<decisión textual>", "source_sessions": [<id_int>, ...]}\n'
        "  ],\n"
        '  "action_items": [\n'
        '    {"title": "<tarea>", "owner": "<responsable>", '
        '"due_date": "<fecha YYYY-MM-DD o vacío>", '
        '"status": "<in_progress|pending|not_started|done>", '
        '"source_sessions": [<id_int>, ...]}\n'
        "  ]\n"
        "}\n\n"
        "REGLAS ESTRICTAS — su violación produce respuestas inutilizables:\n"
        "1. Responde EXCLUSIVAMENTE en español.\n"
        "2. Cada decisión y cada tarea DEBE incluir el `source_sessions` con "
        "los IDs numéricos (sin '#') de las sesiones del contexto donde aparece. "
        "El ID es el número que ves entre corchetes (ej: [Sesión #15 · summary] → 15).\n"
        "3. NO incluyas decisiones ni tareas que no estén EXPLÍCITAS en el "
        "contexto. Es preferible una lista vacía a inventar información.\n"
        "4. NO mezcles información entre sesiones: si una decisión proviene "
        "de la sesión #5, su `source_sessions` debe ser [5], no [5, 7] a menos "
        "que la MISMA decisión aparezca también explícitamente en la sesión #7.\n"
        "5. Si la pregunta menciona un cliente/tema concreto y un fragmento "
        "no se relaciona con ese cliente/tema, IGNÓRALO completamente — no "
        "extraigas decisiones ni tareas de él.\n"
        "6. Cada decisión es UNA frase clara, sin viñetas, sin asteriscos.\n"
        "7. `due_date` solo cuando la fecha esté EN el contexto; si no, vacío.\n"
        "8. `status` por defecto 'pending' si no se infiere uno claro.\n"
        "9. Si NO encuentras decisiones/tareas explícitas en el contexto, "
        "devuelve `decisions: []` y `action_items: []` — NUNCA inventes.\n"
        "10. NO reescribas el contenido textual de la decisión cambiando su "
        "significado; cíñete a lo que dice el contexto."
    )
    user = (
        f"Pregunta del usuario: {q}\n\n"
        f"Contexto extraído de actas anteriores ({len(chunks)} fragmentos relevantes, "
        f"filtrados de {len(raw_chunks)} candidatos por umbral de relevancia):\n"
        f"{context}\n\n"
        f"Devuelve la respuesta como JSON estricto siguiendo el esquema y RESPETANDO "
        f"las 10 reglas. Recuerda: cada decisión y tarea DEBE traer su `source_sessions`."
    )

    payload_llm = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.1,  # Bajamos temperatura para reducir confabulación.
        # Modo JSON nativo de Groq (compat con OpenAI). Si Groq no soporta
        # response_format en esta versión del modelo, se ignora silently
        # y validamos parseando manualmente.
        "response_format": {"type": "json_object"},
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
        raw_answer = body["choices"][0]["message"]["content"].strip()

    structured: Optional[StructuredAnswer] = None
    answer_md = raw_answer
    try:
        import json as _json
        parsed = _json.loads(raw_answer)
        if isinstance(parsed, dict):
            # Decisiones: aceptamos formato nuevo {text, source_sessions}
            # o el viejo (string suelto) por si Groq se confunde.
            decisions: list[Decision] = []
            for d in (parsed.get("decisions") or []):
                if isinstance(d, dict) and d.get("text"):
                    sids = _coerce_int_list(d.get("source_sessions"))
                    # Sanidad: descartamos IDs que no estén en el set
                    # filtrado para evitar que el LLM cite sesiones que
                    # nunca le mostramos.
                    sids = [s for s in sids if s in relevant_session_ids]
                    decisions.append(Decision(text=str(d["text"]).strip(), source_sessions=sids))
                elif isinstance(d, str) and d.strip():
                    decisions.append(Decision(text=d.strip(), source_sessions=[]))

            action_items: list[ActionItemDTO] = []
            for it in (parsed.get("action_items") or []):
                if not isinstance(it, dict) or not it.get("title"):
                    continue
                sids = _coerce_int_list(it.get("source_sessions"))
                sids = [s for s in sids if s in relevant_session_ids]
                action_items.append(
                    ActionItemDTO(
                        title=str(it.get("title") or "").strip(),
                        owner=str(it.get("owner") or "").strip(),
                        due_date=str(it.get("due_date") or "").strip(),
                        status=str(it.get("status") or "pending").strip().lower(),
                        source_sessions=sids,
                    )
                )

            structured = StructuredAnswer(
                intro=str(parsed.get("intro") or ""),
                decisions=decisions,
                action_items=action_items,
            )

            # Re-componemos un markdown legible como fallback para el
            # campo `answer` (que es lo que ven los integradores que NO
            # consumen `structured`).
            answer_md = structured.intro
            if structured.decisions:
                answer_md += "\n\n### Decisiones clave\n" + "\n".join(
                    f"- {d.text}" + (f" _(sesión #{', #'.join(map(str, d.source_sessions))})_" if d.source_sessions else "")
                    for d in structured.decisions
                )
            if structured.action_items:
                answer_md += "\n\n### Tareas pendientes\n" + "\n".join(
                    f"- **{it.title}** — {it.owner or 'Sin asignar'}"
                    + (f" · vence {it.due_date}" if it.due_date else "")
                    + (f" _(sesión #{', #'.join(map(str, it.source_sessions))})_" if it.source_sessions else "")
                    for it in structured.action_items
                )
    except (ValueError, TypeError) as exc:
        # Groq devolvió texto libre (no JSON). Lo dejamos como markdown
        # plano y `structured` queda en None.
        logger.info("ask: respuesta no es JSON válido (%s) — fallback a markdown", exc)

    citations = [
        Citation(
            session_id=c["session_id"], kind=c["kind"],
            snippet=(c["content"] or "")[:280], distance=c["distance"],
        )
        for c in chunks
    ]
    return AskResponse(
        answer=answer_md,
        structured=structured,
        citations=citations,
        model=GROQ_MODEL,
        chunks_used=len(chunks),
    )
