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

import json as _json_lib
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from config import settings
from database import get_session
from models import AskHistory, Tenant, User
from routers.auth import get_current_tenant, get_current_user
from services.embedding_service import search_similar

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ask", tags=["Ask Notiva (RAG)"])

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

# ──────────────────────────────────────────────────────────────────────────
# Estrategia de filtrado de relevancia (evita contaminación de contexto).
#
# Distancia coseno de pgvector (`<=>`) → 1 - cosine_similarity:
#   - 0.0  = chunk idéntico a la pregunta
#   - 0.6  = relacionado pero no muy similar
#   - 1.0  = ortogonal (no relacionado)
#
# Filtrado dinámico (mejor que un cutoff fijo):
#   1. Anclamos al mejor chunk: aceptamos cualquier chunk cuya distancia
#      esté dentro de RELEVANCE_DELTA del mejor. Así, una pregunta muy
#      específica (best=0.20) sólo trae chunks ≤0.35 (muy estricto), y
#      una pregunta abstracta (best=0.70) trae chunks ≤0.85 (más laxo).
#   2. Cap absoluto en HARD_CUTOFF: nada por encima de 0.95 entra (sería
#      ruido puro).
#   3. Garantizamos un mínimo de MIN_CHUNKS para no dejar al LLM sin
#      contexto cuando la pregunta es legítima pero abstracta. Si el
#      mejor chunk supera HARD_CUTOFF, igual devolvemos respuesta vacía.
#
# El frontend puede override-ar el umbral pasando `min_relevance` como
# cutoff absoluto (modo experto).
# ──────────────────────────────────────────────────────────────────────────
RELEVANCE_DELTA = 0.18   # cuán lejos del mejor chunk permitimos
HARD_CUTOFF     = 0.95   # nada por encima entra, sin importar el mejor
MIN_CHUNKS      = 3      # mínimo para no quedarse sin contexto


def _filter_relevant(raw_chunks: list[dict], override: Optional[float] = None) -> list[dict]:
    """Aplica la estrategia de filtrado descrita arriba.

    Devuelve la sub-lista de chunks que pasan el filtro, ya ordenada por
    distancia ascendente (search_similar ya viene así).
    """
    if not raw_chunks:
        return []

    # Modo experto: override absoluto desde el frontend.
    if override is not None:
        return [c for c in raw_chunks if c["distance"] <= float(override)]

    best = raw_chunks[0]["distance"]
    # Si ni el mejor chunk se acerca, no hay nada útil.
    if best > HARD_CUTOFF:
        return []

    cutoff = min(best + RELEVANCE_DELTA, HARD_CUTOFF)
    filtered = [c for c in raw_chunks if c["distance"] <= cutoff]

    # Garantizar mínimo: si quedamos cortos, usamos los top-MIN_CHUNKS
    # disponibles (siempre que estén bajo HARD_CUTOFF).
    if len(filtered) < MIN_CHUNKS:
        backup = [c for c in raw_chunks if c["distance"] <= HARD_CUTOFF][:MIN_CHUNKS]
        if len(backup) > len(filtered):
            filtered = backup

    return filtered


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


class AskHistoryEntry(BaseModel):
    """Forma serializada de una entrada del historial. Misma forma que el
    backend devuelve en `ask()` + `id` y `created_at`."""
    id: int
    question: str
    answer: str
    structured: Optional[StructuredAnswer] = None
    citations: list[Citation] = []
    project_id: Optional[int] = None
    model: str = ""
    chunks_used: int = 0
    created_at: str


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
async def ask(
    payload: AskRequest,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
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

    # Filtro de relevancia DINÁMICO (ver `_filter_relevant`).
    chunks = _filter_relevant(raw_chunks, override=payload.min_relevance)

    # Métrica de calidad: distancia del mejor chunk (0 = perfecto).
    best_distance = raw_chunks[0]["distance"] if raw_chunks else None
    low_quality = best_distance is not None and best_distance > 0.55  # señal para el prompt

    logger.info(
        "ask: project=%s top_k=%s raw=%s relevant=%s best_d=%.3f low_quality=%s",
        payload.project_id, payload.top_k, len(raw_chunks), len(chunks),
        best_distance if best_distance is not None else -1, low_quality,
    )

    if not chunks:
        # Sin candidatos válidos. Solo llegamos aquí si:
        #  - no había NINGÚN chunk en la BD (raw=0), o
        #  - el mejor chunk supera HARD_CUTOFF (0.95) → realmente nada relacionado.
        msg = (
            "No encontré actas en el histórico que se relacionen con esa pregunta. "
            "Intenta reformular usando nombres del proyecto, cliente o fecha."
            if not raw_chunks
            else "Las actas indexadas no parecen relacionarse con esa pregunta. "
                 "Intenta usar términos más específicos."
        )
        empty_resp = AskResponse(
            answer=msg, citations=[], model=GROQ_MODEL, chunks_used=0,
        )
        _persist_history(db, tenant.id, user.id, q, empty_resp, payload.project_id)
        return empty_resp

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
    quality_note = ""
    if low_quality:
        quality_note = (
            "\n\nNOTA DE CALIDAD: el sistema filtró los fragmentos pero la similitud "
            "semántica con la pregunta no es alta. Es posible que la respuesta no "
            "esté EXPLÍCITAMENTE en el contexto. Si ese es el caso:\n"
            "  - En `intro` di honestamente que no encontraste información directa y "
            "menciona qué reuniones del contexto tocan temas RELACIONADOS.\n"
            "  - Devuelve `decisions: []` y `action_items: []` antes que inventar.\n"
            "  - NO afirmes hechos que no estén textualmente en el contexto."
        )

    user_msg = (
        f"Pregunta del usuario: {q}\n\n"
        f"Contexto extraído de actas anteriores ({len(chunks)} fragmentos relevantes, "
        f"filtrados de {len(raw_chunks)} candidatos por umbral de relevancia):\n"
        f"{context}"
        f"{quality_note}\n\n"
        f"Devuelve la respuesta como JSON estricto siguiendo el esquema y RESPETANDO "
        f"las 10 reglas. Recuerda: cada decisión y tarea DEBE traer su `source_sessions`."
    )

    payload_llm = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
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
    response = AskResponse(
        answer=answer_md,
        structured=structured,
        citations=citations,
        model=GROQ_MODEL,
        chunks_used=len(chunks),
    )
    _persist_history(db, tenant.id, user.id, q, response, payload.project_id)
    return response


# ============================================================================
# Historial — persistencia y endpoints
# ============================================================================

def _persist_history(
    db: Session,
    tenant_id: int,
    user_id: int,
    question: str,
    resp: AskResponse,
    project_id: Optional[int],
) -> None:
    """Guarda la entrada en `askhistory` (best-effort, swallow errors).

    El historial NO debe tumbar la respuesta al usuario; si falla la
    escritura, lo loggeamos y seguimos.
    """
    try:
        entry = AskHistory(
            tenant_id=tenant_id,
            user_id=user_id,
            question=question[:4000],
            answer=(resp.answer or "")[:20000],
            structured_json=(_json_lib.dumps(resp.structured.model_dump())
                             if resp.structured else None),
            citations_json=_json_lib.dumps([c.model_dump() for c in resp.citations]),
            project_id=project_id,
            model=resp.model or "",
            chunks_used=resp.chunks_used or 0,
        )
        db.add(entry)
        db.commit()
    except Exception as exc:
        logger.warning("ask: no se pudo persistir historial (user=%s): %s", user_id, exc)
        db.rollback()


def _entry_to_dto(row: AskHistory) -> AskHistoryEntry:
    """Deserializa los JSON de structured/citations a sus modelos pydantic."""
    structured: Optional[StructuredAnswer] = None
    if row.structured_json:
        try:
            data = _json_lib.loads(row.structured_json)
            structured = StructuredAnswer(**data) if isinstance(data, dict) else None
        except Exception:
            structured = None

    citations: list[Citation] = []
    if row.citations_json:
        try:
            arr = _json_lib.loads(row.citations_json)
            if isinstance(arr, list):
                for c in arr:
                    if isinstance(c, dict):
                        citations.append(Citation(**c))
        except Exception:
            citations = []

    return AskHistoryEntry(
        id=row.id or 0,
        question=row.question,
        answer=row.answer,
        structured=structured,
        citations=citations,
        project_id=row.project_id,
        model=row.model,
        chunks_used=row.chunks_used,
        created_at=row.created_at,
    )


@router.get("/history", response_model=list[AskHistoryEntry])
def list_history(
    limit: int = 30,
    project_id: Optional[int] = None,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Devuelve las entradas del historial del usuario, ordenadas por
    fecha desc. Filtros opcionales: `project_id` y `limit` (max 100)."""
    limit = max(1, min(int(limit or 30), 100))
    stmt = (
        select(AskHistory)
        .where(AskHistory.tenant_id == tenant.id)
        .where(AskHistory.user_id == user.id)
    )
    if project_id is not None:
        stmt = stmt.where(AskHistory.project_id == project_id)
    stmt = stmt.order_by(AskHistory.id.desc()).limit(limit)
    rows = list(db.exec(stmt).all())
    return [_entry_to_dto(r) for r in rows]


@router.delete("/history/{entry_id}", status_code=204)
def delete_history_entry(
    entry_id: int,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Borra una entrada del historial. Sólo el dueño puede borrar."""
    row = db.get(AskHistory, entry_id)
    if not row or row.tenant_id != tenant.id or row.user_id != user.id:
        raise HTTPException(404, "Entrada no encontrada.")
    db.delete(row)
    db.commit()
    return None


@router.delete("/history", status_code=204)
def clear_history(
    project_id: Optional[int] = None,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Borra TODO el historial del usuario (opcionalmente filtrado por proyecto)."""
    stmt = (
        select(AskHistory)
        .where(AskHistory.tenant_id == tenant.id)
        .where(AskHistory.user_id == user.id)
    )
    if project_id is not None:
        stmt = stmt.where(AskHistory.project_id == project_id)
    rows = list(db.exec(stmt).all())
    for r in rows:
        db.delete(r)
    db.commit()
    return None
