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

import base64

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import bindparam as sa_bindparam, text as sa_text
from sqlmodel import Session, select

from config import settings
from database import get_session
from models import ActionItem as ActionItemRow, AskHistory, MeetingSession, Tenant, User
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
    # Si llega, restringimos la búsqueda RAG a esas sesiones únicamente.
    # Útil cuando el usuario "pinnea" una reunión específica desde el
    # menú de adjuntar para no mezclar con otras actas. La validación
    # de pertenencia al tenant ocurre dentro del endpoint.
    session_ids: Optional[list[int]] = None


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

    Secciones:
      · `intro` — párrafo introductorio (1-2 frases).
      · `decisions` — decisiones clave con sus fuentes.
      · `action_items` — tareas pendientes con responsable / fecha límite.
      · `risks` — riesgos identificados con sus fuentes.
      · `agreements` — acuerdos con sus fuentes.
    Si el modelo no encuentra alguna sección, devuelve [] o "".
    """
    intro: str = ""
    decisions: list[Decision] = []
    action_items: list[ActionItemDTO] = []
    risks: list[Decision] = []
    agreements: list[Decision] = []


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


def _normalize_for_match(s: str) -> str:
    """Normaliza un título para comparar (lower, sin signos ni espacios extra)."""
    import re as _re
    s = (s or "").lower()
    s = _re.sub(r"[^\w\s]+", " ", s)
    s = _re.sub(r"\s+", " ", s).strip()
    return s


def _stem_word(w: str) -> str:
    """Stem ligero para español: quita plurales obvios para que 'cuentas'
    matchee 'cuenta', 'sociales' matchee 'social'. Conservador — sólo
    reglas que casi nunca producen falsos positivos."""
    if len(w) >= 6 and w.endswith("es"):
        # 'sociales' → 'social', 'redes' (5 chars, no aplica)
        return w[:-2]
    if len(w) >= 5 and w.endswith("s"):
        # 'cuentas' → 'cuenta', 'tareas' → 'tarea'
        return w[:-1]
    return w


def _significant_words(text: str) -> set:
    """Palabras útiles para matching: >=4 letras + stemmed."""
    return {_stem_word(w) for w in _normalize_for_match(text).split() if len(w) >= 4}


def _best_action_item_match(
    title: str, candidates: list[ActionItemRow]
) -> Optional[ActionItemRow]:
    """Match heurístico: la fila DB cuyo título tenga mayor solapamiento
    Jaccard de palabras significativas (>=4 letras, stemmed) con el
    título propuesto por el LLM. Umbral 0.25 es generoso pero seguro."""
    q_words = _significant_words(title)
    if not q_words:
        return None

    best_score, best_row = 0.0, None
    for row in candidates:
        t_words = _significant_words(row.title or "")
        if not t_words:
            continue
        inter = q_words & t_words
        union = q_words | t_words
        score = len(inter) / max(len(union), 1)
        if score > best_score:
            best_score, best_row = score, row
    return best_row if best_score >= 0.25 else None


def _enrich_action_items_from_db(
    db: Session,
    tenant_id: int,
    items: list,  # list[ActionItemDTO]
) -> None:
    """Para cada action_item devuelto por el LLM, busca el ActionItem
    correspondiente en la DB (por session_id + similitud de título) y
    completa owner/due_date/status si están vacíos en la versión LLM.

    La DB es la fuente de verdad; la del LLM es síntesis.
    Mutación in-place sobre la lista.
    """
    if not items:
        return

    cited_sessions = {sid for it in items for sid in (it.source_sessions or [])}
    if not cited_sessions:
        return

    rows = list(
        db.exec(
            select(ActionItemRow)
            .where(ActionItemRow.tenant_id == tenant_id)
            .where(ActionItemRow.session_id.in_(cited_sessions))
        ).all()
    )
    by_session: dict[int, list[ActionItemRow]] = {}
    for r in rows:
        by_session.setdefault(r.session_id, []).append(r)

    if not by_session:
        return

    for it in items:
        for sid in (it.source_sessions or []):
            candidates = by_session.get(sid, [])
            if not candidates:
                continue
            match = _best_action_item_match(it.title, candidates)
            if not match:
                continue
            # Enriquecer SOLO los campos vacíos: respetamos lo que el LLM ya
            # extrajo (puede ser más sintético/legible) y rellenamos huecos.
            if not (it.owner or "").strip() and (match.owner_name or "").strip():
                it.owner = match.owner_name
            if not (it.due_date or "").strip() and (match.due_date or "").strip():
                it.due_date = match.due_date
            # Status: si LLM dijo 'pending' (default), confiamos en la DB.
            if (it.status or "pending") == "pending" and (match.status or ""):
                it.status = match.status
            break  # primera sesión con match basta


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

    # Si el usuario pinneó sesiones específicas, validamos pertenencia al
    # tenant antes de pasarlas al search (un usuario no puede consultar
    # actas de otra empresa).
    sids_filter: Optional[list[int]] = None
    if payload.session_ids:
        valid = db.exec(
            sa_text(
                "SELECT id FROM meetingsession WHERE tenant_id = :t AND id IN :sids"
            ).bindparams(sa_bindparam("sids", expanding=True))
            .bindparams(t=tenant.id, sids=tuple(payload.session_ids))
        ).all()
        sids_filter = [r[0] for r in valid] or None

    raw_chunks = await search_similar(
        db, q,
        top_k=max(min(payload.top_k, 20), 1),
        project_id=payload.project_id,
        session_ids=sids_filter,
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
        "  ],\n"
        '  "risks": [\n'
        '    {"text": "<riesgo identificado>", "source_sessions": [<id_int>, ...]}\n'
        "  ],\n"
        '  "agreements": [\n'
        '    {"text": "<acuerdo establecido>", "source_sessions": [<id_int>, ...]}\n'
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
            def _parse_decision_list(raw_list) -> list[Decision]:
                """Acepta formato nuevo {text, source_sessions} o string suelto.
                Sanitiza source_sessions contra el set de chunks relevantes."""
                out: list[Decision] = []
                for d in (raw_list or []):
                    if isinstance(d, dict) and d.get("text"):
                        sids = _coerce_int_list(d.get("source_sessions"))
                        sids = [s for s in sids if s in relevant_session_ids]
                        out.append(Decision(text=str(d["text"]).strip(), source_sessions=sids))
                    elif isinstance(d, str) and d.strip():
                        out.append(Decision(text=d.strip(), source_sessions=[]))
                return out

            decisions = _parse_decision_list(parsed.get("decisions"))
            risks = _parse_decision_list(parsed.get("risks"))
            agreements = _parse_decision_list(parsed.get("agreements"))

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

            # Enriquecimiento desde DB: la tabla `actionitem` tiene los
            # owner/due_date/status estructurados que el LLM no siempre
            # encuentra en los chunks de summary/decisions/transcript.
            # La DB es la fuente de verdad; sólo rellenamos campos vacíos.
            _enrich_action_items_from_db(db, tenant.id, action_items)

            structured = StructuredAnswer(
                intro=str(parsed.get("intro") or ""),
                decisions=decisions,
                action_items=action_items,
                risks=risks,
                agreements=agreements,
            )

            # Re-componemos un markdown legible como fallback para el
            # campo `answer` (que es lo que ven los integradores que NO
            # consumen `structured`).
            def _md_decisions(title: str, items: list[Decision]) -> str:
                if not items:
                    return ""
                lines = "\n".join(
                    f"- {d.text}" + (
                        f" _(sesión #{', #'.join(map(str, d.source_sessions))})_"
                        if d.source_sessions else ""
                    )
                    for d in items
                )
                return f"\n\n### {title}\n{lines}"

            answer_md = structured.intro
            answer_md += _md_decisions("Decisiones clave", structured.decisions)
            if structured.action_items:
                answer_md += "\n\n### Tareas pendientes\n" + "\n".join(
                    f"- **{it.title}** — {it.owner or 'Sin asignar'}"
                    + (f" · vence {it.due_date}" if it.due_date else "")
                    + (f" _(sesión #{', #'.join(map(str, it.source_sessions))})_" if it.source_sessions else "")
                    for it in structured.action_items
                )
            answer_md += _md_decisions("Riesgos identificados", structured.risks)
            answer_md += _md_decisions("Acuerdos", structured.agreements)
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


def _entry_to_dto(row: AskHistory, db: Optional[Session] = None) -> AskHistoryEntry:
    """Deserializa los JSON de structured/citations a sus modelos pydantic.

    Si `db` se pasa, re-enriquece los `action_items` contra la tabla DB
    para que las entradas viejas del historial — guardadas antes del fix
    de enriquecimiento — reflejen los datos actuales de owner/due_date/
    status. Es idempotente; sólo rellena los campos vacíos.
    """
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

    # Re-enriquecimiento de entradas históricas con la DB actual.
    if db is not None and structured and structured.action_items:
        try:
            _enrich_action_items_from_db(db, row.tenant_id, structured.action_items)
        except Exception as exc:
            logger.info("ask/history: no se pudo re-enriquecer entry %s: %s", row.id, exc)

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
    return [_entry_to_dto(r, db=db) for r in rows]


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


class ExtractImageResponse(BaseModel):
    text: str
    chars: int


@router.post("/extract-image", response_model=ExtractImageResponse)
async def extract_image(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """OCR ligero vía Groq vision (llama-3.2-90b-vision). El usuario
    sube una imagen (captura de pantalla, foto de pizarra, screenshot
    de email, etc.) y devolvemos el texto extraído para que se inyecte
    como contexto en su próxima pregunta a Acten.

    Límites:
      · Tipos: image/jpeg, image/png, image/webp
      · Tamaño máx: 4 MB
      · El texto devuelto se trunca a 4000 chars (antes de inyectar al
        prompt) — suficiente para el caso de uso, evita explotar tokens.
    """
    if not settings.groq_api_key:
        raise HTTPException(503, "GROQ_API_KEY no configurada (necesaria para OCR).")

    allowed_types = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
    if file.content_type not in allowed_types:
        raise HTTPException(
            400, f"Tipo no soportado ({file.content_type}). Usa JPEG, PNG o WebP."
        )

    raw = await file.read()
    if len(raw) > 4 * 1024 * 1024:
        raise HTTPException(413, "Imagen muy grande (máx 4 MB).")
    if not raw:
        raise HTTPException(400, "Archivo vacío.")

    b64 = base64.b64encode(raw).decode("ascii")
    data_url = f"data:{file.content_type};base64,{b64}"

    payload_llm = {
        # Modelo de visión multimodal de Groq.
        "model": "meta-llama/llama-4-scout-17b-16e-instruct",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Extrae todo el texto visible en esta imagen. "
                            "Si es una captura de chat, email o documento, "
                            "preserva el orden y separa por líneas. "
                            "Devuelve SOLO el texto plano, sin comentarios "
                            "ni explicaciones tuyas."
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "temperature": 0.1,
        "max_tokens": 1500,
    }
    headers = {
        "Authorization": f"Bearer {settings.groq_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            r = await client.post(GROQ_URL, json=payload_llm, headers=headers)
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("ask/extract-image: Groq vision falló: %s — %s",
                           exc.response.status_code, exc.response.text[:300])
            raise HTTPException(502, "El modelo de visión no pudo procesar la imagen.") from exc

        body = r.json()
        text = (body.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
        text = text.strip()

    return ExtractImageResponse(text=text, chars=len(text))


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
