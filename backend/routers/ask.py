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


class PriorTurn(BaseModel):
    """Turno previo del hilo de conversación (pregunta + respuesta del modelo).

    Permite que las preguntas de seguimiento ("Y de eso, ¿qué dijo Juan?")
    tengan contexto del turno anterior sin recargar todo el RAG."""
    question: str
    answer: str


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
    # Turns previos del MISMO hilo de chat — para que el LLM tenga contexto
    # conversacional. La UI envía los últimos N turnos cronológicos.
    prior_turns: Optional[list[PriorTurn]] = None


class Citation(BaseModel):
    session_id: int
    kind: str
    snippet: str
    distance: float
    # Metadatos opcionales — útiles para que el frontend muestre el
    # título de la sesión sin tener que hacer un segundo fetch.
    session_title: Optional[str] = None
    session_date: Optional[str] = None
    project_name: Optional[str] = None


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
      · `intro_source_sessions` — sesión(es) que contienen literalmente la
        evidencia del `intro` (subset de citations). Permite al frontend
        marcar las fuentes PRIMARIAS de la respuesta vs las "otras
        consultadas". Si el intro es meta/agregado, puede venir vacío.
      · `decisions` — decisiones clave con sus fuentes.
      · `action_items` — tareas pendientes con responsable / fecha límite.
      · `risks` — riesgos identificados con sus fuentes.
      · `agreements` — acuerdos con sus fuentes.
    Si el modelo no encuentra alguna sección, devuelve [] o "".
    """
    intro: str = ""
    intro_source_sessions: list[int] = []
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


def _enrich_chunks_with_session_text(
    chunks: list[dict], db: "Session", tenant_id: int,
) -> list[dict]:
    """Sustituye el `content` indexado vacío/escaso por el texto REAL del
    campo correspondiente de la sesión en DB.

    El RAG indexa fragmentos en el momento de procesar la sesión. Si el
    campo `processed_decisions`/`processed_agreements`/`processed_risks` se
    editó después (curador agregó decisiones) sin re-indexar, el chunk
    sigue vacío. Acá lo rellenamos al vuelo desde DB.

    También funciona como red de seguridad cuando un chunk se indexó con
    metadata `kind=decisions` pero contenido vacío.
    """
    from models import MeetingSession
    if not chunks:
        return chunks
    # Cache session_id → (decisions, agreements, risks, summary, title)
    cache: dict[int, dict] = {}
    out: list[dict] = []
    for c in chunks:
        snippet = (c.get("content") or "").strip()
        if len(snippet) >= 40:
            out.append(c)
            continue  # contenido ya suficiente
        sid = c.get("session_id")
        if not sid:
            out.append(c)
            continue
        if sid not in cache:
            ms = db.get(MeetingSession, sid)
            if not ms or ms.tenant_id != tenant_id:
                cache[sid] = {}
            else:
                cache[sid] = {
                    "decisions":  (ms.processed_decisions or "").strip(),
                    "agreements": (ms.processed_agreements or "").strip(),
                    "risks":      (ms.processed_risks or "").strip(),
                    "summary":    (ms.raw_summary or "").strip(),
                    "title":      (ms.title or "").strip(),
                }
        info = cache.get(sid) or {}
        kind = (c.get("kind") or "").lower()
        replacement = ""
        if kind in ("decision", "decisions") and info.get("decisions"):
            replacement = info["decisions"]
        elif kind in ("agreement", "agreements") and info.get("agreements"):
            replacement = info["agreements"]
        elif kind in ("risk", "risks") and info.get("risks"):
            replacement = info["risks"]
        elif kind in ("summary", "overview") and info.get("summary"):
            replacement = info["summary"]
        # Si nada del kind específico, intentamos summary como fallback.
        if not replacement:
            replacement = info.get("summary") or info.get("decisions") or info.get("agreements") or ""
        if replacement:
            c = {**c, "content": replacement[:2000]}
        out.append(c)
    return out


# Palabras vacías comunes para no tomarlas como "nombre propio". Mantengo
# corto y solo lo que confunde al heurístico — el filtro real es que tengan
# longitud ≥3 y empiecen con mayúscula.
_PROPER_NOUN_STOPWORDS = frozenset({
    # ES — comienzo de frase típico
    "Que", "Qué", "Cómo", "Como", "Dónde", "Donde", "Cuándo", "Cuando",
    "Por", "Para", "Sobre", "Con", "Sin", "Quién", "Quien", "Cuáles",
    "Cuales", "Hay", "Han", "Habrá", "Tienes", "Tienen", "Acten",
    # CA
    "Què", "Qui", "Com", "Quan", "On", "Per", "Sense", "Tens",
    # EN
    "What", "Who", "Where", "When", "Why", "How", "Did", "Does", "Has",
    "Have", "Is", "Are", "Will", "Can", "Should",
    # Conectores típicos al inicio
    "Y", "O", "Si", "No", "El", "La", "Los", "Las", "Un", "Una", "Es",
    "The", "A", "An", "Of", "In", "On", "To", "And", "Or",
})


def _extract_proper_nouns(q: str) -> list[str]:
    """Extrae candidatos de nombre propio de la pregunta.

    Heurística simple: palabras de ≥3 letras que arrancan con mayúscula
    y NO son stopwords del set above. Aplica también a palabras como
    "Camila" en mitad de la frase pero ignora "Cómo", "Quién", etc. que
    son palabras-interrogativas que aparecen capitalizadas al inicio.

    Devuelve hasta 6 candidatos para limitar el costo del SQL ILIKE.
    """
    import re as _re
    if not q:
        return []
    candidates: list[str] = []
    seen: set[str] = set()
    # Tokeniza preservando acentos y la ñ.
    for tok in _re.findall(r"[A-ZÁÉÍÓÚÑÀÈÌÒÙÄËÏÖÜ][a-záéíóúñàèìòùäëïöü]{2,}", q):
        if tok in _PROPER_NOUN_STOPWORDS:
            continue
        low = tok.lower()
        if low in seen:
            continue
        seen.add(low)
        candidates.append(tok)
        if len(candidates) >= 6:
            break
    return candidates


_WHATIS_PATTERNS = (
    r"\bqu[eé]\s+es\b", r"\bqu[eé]\s+era\b", r"\bqu[eé]\s+significa\b",
    r"\bde\s+qu[eé]\s+trata\b", r"\bpara\s+qu[eé]\s+sirve\b",
    r"\ben\s+qu[eé]\s+consiste\b", r"\bd[ií]me\s+sobre\b",
    r"\bcu[eé]ntame\s+(?:de|sobre)\b", r"\bdescribe\b", r"\bexplica\b",
    r"\bwhat\s+is\b", r"\bwhat'?s\b", r"\bwhat\s+does\b",
    r"\btell\s+me\s+about\b", r"\bdescribe\b", r"\bexplain\b",
)


def _is_whatis_question(q: str) -> bool:
    """True si la pregunta tiene intent definicional ('qué es X', 'what
    is X', 'cuéntame sobre Y'). Usado para AMPLIAR la ventana de snippet
    y agregar instrucción de síntesis al LLM en lugar de enumeración."""
    import re as _re
    if not q:
        return False
    low = q.lower()
    return any(_re.search(p, low) for p in _WHATIS_PATTERNS)


def _name_snippets_all(text: str, name: str, window: int = 900, max_occ: int = 3) -> str:
    """Devuelve hasta `max_occ` ventanas de `window` chars cada una,
    centradas en distintas ocurrencias del nombre. Útil para preguntas
    definicionales: las menciones tipo 'X es ...', 'X funciona como ...'
    suelen estar dispersas a lo largo del transcript y un solo snippet
    se pierde el contexto. Concatena con separador para que el LLM las
    lea como bloques relacionados de la misma sesión."""
    if not text or not name:
        return ""
    import re as _re
    pat = _re.compile(_re.escape(name), _re.IGNORECASE)
    spans: list[tuple[int, int]] = []
    for m in pat.finditer(text):
        start = max(0, m.start() - window // 2)
        end = min(len(text), m.end() + window // 2)
        if spans and start <= spans[-1][1]:
            # Merge ventana solapada con la anterior.
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
        if len(spans) >= max_occ:
            break
    if not spans:
        return ""
    parts = [text[s:e].strip() for s, e in spans]
    return "\n…\n".join(parts)


def _load_keyword_matches(
    db: "Session",
    tenant_id: int,
    project_id: Optional[int],
    names: list[str],
    limit_per_name: int = 4,
    whatis_mode: bool = False,
) -> list[dict]:
    """Búsqueda LITERAL (SQL ILIKE) por nombres propios en transcript y
    secciones procesadas.

    Razón de ser: la búsqueda vector (RAG) puede pasar por alto menciones
    aisladas de nombres propios en transcripts largos — el embedding del
    chunk se "diluye" por el resto del texto y la pregunta corta
    "¿quién es Camila?" no compite contra texto temático extenso. El
    keyword match garantiza que si una sesión menciona "Camila"
    LITERALMENTE, esa sesión llega al contexto del LLM.

    Para cada `name`, busca hasta `limit_per_name` sesiones distintas
    (ordenadas por id DESC = más recientes primero). Devuelve chunks
    estructurados igual que `_load_recent_session_context` para que el
    merge con los chunks de RAG sea transparente.
    """
    if not names:
        return []
    from sqlmodel import select
    from models import MeetingSession, Project

    out: list[dict] = []
    seen_sids: set[int] = set()
    proj_cache: dict[int, str] = {}

    for name in names:
        # ILIKE con wildcards — buscamos `Camila`, `Camila,`, `con Camila`,
        # etc. Postgres ILIKE es case-insensitive con índices opcionales
        # (no críticos para datasets pequeños del MVP).
        pattern = f"%{name}%"
        q = (
            select(MeetingSession)
            .where(MeetingSession.tenant_id == tenant_id)
            .where(
                (MeetingSession.raw_transcript.ilike(pattern))
                | (MeetingSession.raw_summary.ilike(pattern))
                | (MeetingSession.processed_decisions.ilike(pattern))
                | (MeetingSession.processed_agreements.ilike(pattern))
                | (MeetingSession.processed_risks.ilike(pattern))
            )
        )
        if project_id:
            q = q.where(MeetingSession.project_id == project_id)
        q = q.order_by(MeetingSession.id.desc()).limit(limit_per_name)
        rows = db.exec(q).all()

        for s in rows:
            if not s.id or s.id in seen_sids:
                continue
            seen_sids.add(s.id)

            # Snippet del transcript / resumen / decisiones donde el
            # nombre aparece. En modo whatis (pregunta definicional)
            # tomamos VENTANAS MÁS GRANDES y hasta 3 ocurrencias por
            # campo — las menciones "X es ...", "X funciona como ..."
            # suelen estar dispersas y una sola ventana se las pierde.
            if whatis_mode:
                snippet = _name_snippets_all(s.raw_transcript or "", name, window=1000, max_occ=3) \
                       or _name_snippets_all(s.raw_summary or "", name, window=900, max_occ=2) \
                       or _name_snippets_all(s.processed_decisions or "", name, window=600, max_occ=2) \
                       or _name_snippets_all(s.processed_agreements or "", name, window=600, max_occ=2) \
                       or ""
            else:
                snippet = _name_snippet(s.raw_transcript or "", name) \
                       or _name_snippet(s.raw_summary or "", name) \
                       or _name_snippet(s.processed_decisions or "", name) \
                       or _name_snippet(s.processed_agreements or "", name) \
                       or ""
            if not snippet:
                continue

            proj_name = ""
            if s.project_id:
                if s.project_id not in proj_cache:
                    p = db.get(Project, s.project_id)
                    proj_cache[s.project_id] = (p.name if p else "") or ""
                proj_name = proj_cache[s.project_id]

            out.append({
                "session_id": s.id,
                "kind": f"keyword:{name.lower()}",
                "content": f"[Mención literal de \"{name}\"]\n{snippet}",
                # distance=0 → bypassea el filtro de relevance.
                "distance": 0.0,
                "session_title": s.title or "",
                "session_date": s.date or "",
                "project_name": proj_name,
            })
    return out


def _load_entity_facts(
    db: "Session",
    tenant_id: int,
    project_id: Optional[int],
    names: list[str],
) -> dict:
    """Busca hechos estructurados (no-RAG) sobre personas y proyectos.

    Razón de ser: el RAG saca info SOLO de transcripts/decisiones de
    sesiones. Pero cuando alguien pregunta "¿quién es Felipe Cortés?"
    o "¿qué es First Class?", la base ya tiene info canónica en
    `projectcontact` (rol, empresa, email) y en `project` (nombre,
    descripción, owner). Inyectamos esa info como contexto adicional
    para que el LLM responda con la verdad estructurada, no con
    inferencias del transcript.

    Para cada nombre propio:
      - Personas: contactos de proyectos del tenant cuyo `name` contiene
        el término. Devuelve role/entity/email + lista de proyectos en
        los que aparece.
      - Proyectos: proyectos del tenant cuyo `name` contiene el término.
        Devuelve description + count contactos + número de sesiones.

    Filtra por tenant_id estrictamente. Limita resultados para no
    inflar tokens.
    """
    if not names:
        return {"persons": [], "projects": []}

    from sqlmodel import select
    from models import MeetingSession, Project, ProjectContact

    persons: list[dict] = []
    seen_contact_keys: set[tuple[str, str, int]] = set()  # (name_lower, email, project_id)

    for name in names:
        pattern = f"%{name}%"
        q = (
            select(ProjectContact, Project)
            .join(Project, Project.id == ProjectContact.project_id)
            .where(Project.tenant_id == tenant_id)
            .where(ProjectContact.name.ilike(pattern))
        )
        if project_id:
            q = q.where(Project.id == project_id)
        q = q.limit(20)
        for c, p in db.exec(q).all():
            key = (
                (c.name or "").strip().lower(),
                (c.email or "").strip().lower(),
                p.id,
            )
            if key in seen_contact_keys:
                continue
            seen_contact_keys.add(key)
            persons.append({
                "name": c.name or "",
                "role": (c.role or "").strip(),
                "entity": (c.entity or "").strip(),
                "email": (c.email or "").strip(),
                "project_id": p.id,
                "project_name": p.name or "",
            })

    projects_out: list[dict] = []
    seen_project_ids: set[int] = set()
    for name in names:
        pattern = f"%{name}%"
        rows = db.exec(
            select(Project)
            .where(Project.tenant_id == tenant_id)
            .where(Project.name.ilike(pattern))
            .limit(5)
        ).all()
        for p in rows:
            if p.id in seen_project_ids:
                continue
            seen_project_ids.add(p.id)
            contacts = db.exec(
                select(ProjectContact).where(ProjectContact.project_id == p.id)
            ).all()
            sess_count = db.exec(
                sa_text(
                    "SELECT COUNT(*) FROM meetingsession "
                    "WHERE project_id = :p AND tenant_id = :t"
                ).bindparams(p=p.id, t=tenant_id)
            ).first()
            projects_out.append({
                "id": p.id,
                "name": p.name or "",
                "description": (p.description or "").strip(),
                "contacts": [
                    {
                        "name": c.name or "",
                        "role": (c.role or "").strip(),
                        "entity": (c.entity or "").strip(),
                        "email": (c.email or "").strip(),
                    }
                    for c in contacts[:12]
                ],
                "session_count": int(sess_count[0]) if sess_count else 0,
            })

    return {"persons": persons, "projects": projects_out}


def _format_entity_facts_block(facts: dict) -> str:
    """Formatea el dict de _load_entity_facts a un bloque legible para
    el LLM. Devuelve "" si no hay datos."""
    persons = facts.get("persons") or []
    projects = facts.get("projects") or []
    if not persons and not projects:
        return ""

    lines: list[str] = ["=== DATOS ESTRUCTURADOS DEL DIRECTORIO ==="]
    lines.append(
        "Información canónica proveniente de la base de proyectos y "
        "contactos del tenant (NO de transcripciones). Úsala como fuente "
        "autoritativa para describir personas (rol, empresa, email) y "
        "proyectos (descripción, equipo). Es información REAL — no la "
        "ignores aunque no aparezca en las sesiones."
    )

    if persons:
        # Agrupar por nombre canonical para que un mismo contacto en
        # múltiples proyectos aparezca una sola vez.
        by_name: dict[str, dict] = {}
        for pr in persons:
            key = (pr.get("name") or "").strip().lower()
            if not key:
                continue
            slot = by_name.setdefault(key, {
                "name": pr["name"], "roles": set(), "entities": set(),
                "emails": set(), "projects": [],
            })
            if pr.get("role"): slot["roles"].add(pr["role"])
            if pr.get("entity"): slot["entities"].add(pr["entity"])
            if pr.get("email"): slot["emails"].add(pr["email"])
            slot["projects"].append(pr.get("project_name") or f"#{pr.get('project_id')}")

        lines.append("\nPERSONAS:")
        for slot in by_name.values():
            roles = ", ".join(sorted(slot["roles"])) or "—"
            entities = ", ".join(sorted(slot["entities"])) or "—"
            emails = ", ".join(sorted(slot["emails"])) or "—"
            projs = ", ".join(sorted(set(slot["projects"]))) or "—"
            lines.append(
                f"- {slot['name']}: rol={roles}; empresa={entities}; "
                f"email={emails}; proyectos={projs}"
            )

    if projects:
        lines.append("\nPROYECTOS:")
        for p in projects:
            desc = p["description"] or "(sin descripción)"
            if len(desc) > 600:
                desc = desc[:600] + "…"
            lines.append(
                f"- {p['name']} (id={p['id']}, {p['session_count']} sesiones)"
            )
            lines.append(f"  descripción: {desc}")
            if p["contacts"]:
                team = "; ".join(
                    f"{c['name']}"
                    + (f" ({c['role']})" if c['role'] else "")
                    + (f" — {c['entity']}" if c['entity'] else "")
                    for c in p["contacts"]
                )
                lines.append(f"  equipo: {team}")

    return "\n".join(lines)


def _name_snippet(text: str, name: str, window: int = 600) -> str:
    """Devuelve una ventana de `window` chars centrada en la primera
    ocurrencia (case-insensitive) de `name` dentro de `text`. None si no
    aparece."""
    if not text or not name:
        return ""
    low = text.lower()
    idx = low.find(name.lower())
    if idx == -1:
        return ""
    start = max(0, idx - window // 2)
    end = min(len(text), idx + len(name) + window // 2)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + text[start:end] + suffix


def _is_recency_question(q: str) -> bool:
    """Detecta si la pregunta tiene intent de listar sesiones RECIENTES /
    GENERALES (sin un proyecto/cliente específico). Triggers comunes en
    es-CO: 'recientes', 'últimos', 'última semana', 'este mes', 'todas las
    reuniones', 'qué pasó'."""
    qn = (q or "").lower()
    triggers = [
        "reciente", "recientes", "última", "ultimas", "últimas", "ultimas",
        "últim", "ultim", "esta semana", "este mes", "este día", "este dia",
        "todas las reun", "todas las sesion", "qué pas", "que pas",
        "qué hubo", "que hubo", "general",
    ]
    return any(t in qn for t in triggers)


def _load_recent_session_context(
    db: "Session", tenant_id: int, project_id: Optional[int], limit: int = 8,
) -> list[dict]:
    """Devuelve N sesiones más recientes del tenant (opcionalmente filtradas
    por proyecto) en el formato chunk-like que espera _build_context.

    Cada sesión se devuelve como un chunk con kind=summary y el resumen
    ejecutivo + decisiones + acuerdos concatenados. Cuando el usuario
    pregunta cosas como 'qué decisiones se tomaron recientemente', esto
    asegura que el LLM tiene el texto real de las sesiones del último
    período aunque RAG no las haya seleccionado por similitud."""
    from sqlmodel import select
    from models import MeetingSession, Project

    q = select(MeetingSession).where(MeetingSession.tenant_id == tenant_id)
    if project_id:
        q = q.where(MeetingSession.project_id == project_id)
    # Excluimos archivadas y aún en proceso.
    q = q.where(MeetingSession.status.in_(("pending", "completed", "processed")))
    rows = db.exec(q).all()

    # Ordenamos por DATE descendente (campo `date` = fecha real de la
    # reunión); fallback a id si la fecha viene vacía. Antes solo usábamos
    # id, que falla cuando se sube una sesión vieja (id alto, fecha vieja).
    def _sort_key(s):
        d = (s.date or "").strip()
        return (d, s.id or 0)
    rows.sort(key=_sort_key, reverse=True)
    rows = rows[:limit]

    # Cache de nombre de proyecto.
    proj_cache: dict[int, str] = {}
    out: list[dict] = []
    for s in rows:
        if not s.id:
            continue
        # Componemos un "snippet" con TODO lo curado + porción del transcript
        # para que el LLM pueda responder preguntas que SOLO se contestan
        # leyendo el texto crudo (ej. "¿Raúl llegó a la reunión?" — la
        # asistencia/excusa solo aparece en el transcript, nunca en el
        # resumen estructurado).
        parts: list[str] = []
        if (s.raw_summary or "").strip():
            parts.append("Resumen: " + s.raw_summary.strip())
        if (s.processed_decisions or "").strip():
            parts.append("Decisiones: " + s.processed_decisions.strip())
        if (s.processed_agreements or "").strip():
            parts.append("Acuerdos: " + s.processed_agreements.strip())
        if (s.processed_risks or "").strip():
            parts.append("Riesgos: " + s.processed_risks.strip())
        if (s.raw_transcript or "").strip():
            # Primeros ~1800 chars del transcript: cubre intro/asistencia
            # ("hola Raúl... ah no llegó", "presentes: Camila, María...")
            # sin inflar tokens.
            t = s.raw_transcript.strip()
            parts.append("Transcripción (inicio): " + (t[:1800] + ("…" if len(t) > 1800 else "")))
        snippet = "\n\n".join(parts)
        if not snippet.strip():
            continue

        proj_name = ""
        if s.project_id:
            if s.project_id not in proj_cache:
                p = db.get(Project, s.project_id)
                proj_cache[s.project_id] = (p.name if p else "") or ""
            proj_name = proj_cache[s.project_id]

        out.append({
            "session_id": s.id,
            "kind": "summary",
            "content": snippet[:2500],
            "distance": 0.0,  # placeholder — no participa del relevance filter
            "session_title": s.title or "",
            "session_date": s.date or "",
            "project_name": proj_name,
        })
    return out


def _build_context(chunks: list[dict]) -> str:
    """Construye el contexto que recibe el LLM. Cada bloque incluye el
    metadato de la sesión (id, título, fecha, proyecto) Y el contenido
    del chunk. Esto es crítico para preguntas "meta" como "qué sitios se
    visitaron" o "qué clientes hubo este mes" — la respuesta vive en los
    TÍTULOS, no en los chunks de summary/decisions."""
    blocks = []
    for c in chunks:
        snippet = (c.get("content") or "")[:1200]
        title = c.get("session_title") or ""
        date = c.get("session_date") or ""
        proj = c.get("project_name") or ""
        # Header con todo el metadato para que el LLM pueda razonar sobre
        # las sesiones aunque la pregunta no esté literalmente en el chunk.
        header_parts = [f"Sesión #{c['session_id']}"]
        if title: header_parts.append(f"Título: \"{title}\"")
        if date:  header_parts.append(f"Fecha: {date}")
        if proj:  header_parts.append(f"Proyecto: {proj}")
        header_parts.append(f"Sección: {c['kind']}")
        header = " · ".join(header_parts)
        blocks.append(f"[{header}]\n{snippet}")
    return "\n\n---\n\n".join(blocks)


def _build_sessions_inventory(chunks: list[dict]) -> str:
    """Lista compacta de TODAS las sesiones únicas presentes en los chunks.
    Útil para que el LLM responda preguntas "meta" sobre el conjunto (qué
    sitios, qué clientes, qué proyectos) sin tener que escanear cada chunk."""
    seen: dict[int, dict] = {}
    for c in chunks:
        sid = c.get("session_id")
        if sid is None or sid in seen:
            continue
        seen[sid] = {
            "title": c.get("session_title") or "(sin título)",
            "date": c.get("session_date") or "",
            "project_name": c.get("project_name") or "",
        }
    if not seen:
        return ""
    lines = ["Inventario de sesiones únicas en el contexto:"]
    for sid, meta in seen.items():
        line = f"  · #{sid}  \"{meta['title']}\""
        if meta["date"]:         line += f"  ({meta['date']})"
        if meta["project_name"]: line += f"  — proyecto: {meta['project_name']}"
        lines.append(line)
    return "\n".join(lines)


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
        # CRÍTICO multi-tenant: restringe la búsqueda RAG a las sesiones
        # del tenant del usuario. Sin esto, una pregunta de la empresa A
        # podía recuperar fragmentos de actas de la empresa B (fuga de
        # información entre clientes).
        tenant_id=tenant.id,
    )

    # Filtro de relevancia DINÁMICO (ver `_filter_relevant`).
    chunks = _filter_relevant(raw_chunks, override=payload.min_relevance)

    # Enriquecemos cada chunk con el TEXTO REAL de la sesión cuando el
    # contenido indexado venga vacío o muy corto. Esto pasa cuando el
    # embedding pipeline indexó la metadata (kind=decisions) pero todavía
    # no se había escrito el texto en la sesión, o cuando el campo cambió
    # luego sin reindexarse. Sin esto, el LLM ve `Sección: decisions` y
    # `Snippet: ""` → responde "no hay decisiones registradas" aunque las
    # haya en DB.
    chunks = _enrich_chunks_with_session_text(chunks, db, tenant.id)

    # Después del enriquecimiento, descartamos chunks que siguieron vacíos
    # (la sesión realmente no tiene ese campo poblado). Si tras filtrar
    # quedamos sin nada, conservamos al menos los 3 mejores para que el
    # LLM tenga contexto mínimo aunque sea de baja calidad.
    chunks_with_text = [c for c in chunks if (c.get("content") or "").strip()]
    if chunks_with_text:
        chunks = chunks_with_text
    # Detectar preguntas "generales / recientes / últimos días" y traer
    # cronológicamente las N sesiones más recientes con su resumen +
    # decisiones + acuerdos como contexto adicional. Solo se gatilla cuando
    # la pregunta tiene ese intent — para no inflar tokens en queries
    # específicas.
    if _is_recency_question(q):
        recency_chunks = _load_recent_session_context(db, tenant.id, payload.project_id, limit=8)
        # Agregamos sin duplicar session_ids ya cubiertos.
        seen = {c["session_id"] for c in chunks}
        for rc in recency_chunks:
            if rc["session_id"] not in seen:
                chunks.append(rc)
                seen.add(rc["session_id"])

    # KEYWORD MATCH para nombres propios — fix bug reportado donde el
    # vector search no encontraba menciones aisladas en transcripts largos
    # ("quién es Camila" devolvía 'no sé' aunque Camila estuviera en la
    # transcripción de varias sesiones). El match literal garantiza
    # que esos chunks lleguen al LLM.
    proper_nouns = _extract_proper_nouns(q)
    whatis_mode = _is_whatis_question(q)
    if proper_nouns:
        # Modo whatis ("qué es X") trae snippets más grandes y multiples
        # ocurrencias por sesión — necesario para extraer una definición
        # del transcript en lugar de solo enumerar topics.
        kw_chunks = _load_keyword_matches(
            db, tenant.id, payload.project_id, proper_nouns,
            limit_per_name=4, whatis_mode=whatis_mode,
        )
        seen = {(c["session_id"], c.get("kind")) for c in chunks}
        for kc in kw_chunks:
            key = (kc["session_id"], kc.get("kind"))
            if key not in seen:
                chunks.append(kc)
                seen.add(key)
        logger.info(
            "ask: proper_nouns=%s → +%s keyword chunks (sessions únicos: %s)",
            proper_nouns, len(kw_chunks),
            len({c["session_id"] for c in kw_chunks}),
        )

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
    sessions_inventory = _build_sessions_inventory(chunks)

    # Datos estructurados del directorio (no-RAG): si la pregunta
    # menciona nombres propios, traemos personas (project_contacts) y
    # proyectos cuyos `name` matcheen. Esto da al LLM la verdad
    # canónica (rol, empresa, descripción del proyecto) en lugar de
    # forzarlo a inferir todo del transcript.
    entity_facts = _load_entity_facts(
        db, tenant.id, payload.project_id, proper_nouns,
    )
    entity_block = _format_entity_facts_block(entity_facts)
    # Pedimos JSON estructurado para que el frontend pueda renderizar
    # secciones (Decisiones / Tareas pendientes / Fuentes) tal como el
    # mockup. Cada decisión y tarea DEBE indicar la(s) sesión(es) origen
    # para hacer verificable el resultado.
    # Aviso al sistema sobre el contexto conversacional. Si hay prior_turns,
    # el modelo ya verá esos mensajes en el array `messages` (más abajo) y
    # tratará la nueva pregunta como SEGUIMIENTO del hilo.
    convo_hint = ""
    if payload.prior_turns:
        convo_hint = (
            "\nEsta es una pregunta de SEGUIMIENTO dentro de un hilo "
            "conversacional. Antes te llegarán los turnos previos del mismo "
            "chat (user + assistant). Úsalos para resolver referencias como "
            "'eso', 'lo anterior', 'ella', 'esa decisión'. Si la nueva pregunta "
            "es ambigua sola pero clara con el contexto previo, respóndela "
            "tomando ese hilo. Sigue citando `source_sessions` con IDs reales "
            "de las actas relevantes.\n"
        )

    # Resolvemos el idioma de salida de la respuesta:
    # 1) User.language (preferencia explícita del user logueado)
    # 2) Tenant.default_language (config del workspace)
    # 3) 'es' (fallback final por backwards-compat)
    # El LLM responde mejor cuando le pedimos el idioma EN MAYÚSCULAS y
    # en su propio idioma, así que usamos lang_label() de i18n_pipeline.
    from services.i18n_pipeline import lang_label
    out_lang_code = (getattr(user, "language", None) or tenant.default_language or "es").lower()[:2]
    out_lang_name = lang_label(out_lang_code)

    system = (
        f"Eres el asistente de Acten. Tu salida DEBE ser un objeto JSON válido "
        f"con esta estructura EXACTA:\n"
        "{\n"
        f'  "intro": "<resumen introductorio en {out_lang_name}, 1-2 frases>",\n'
        '  "intro_source_sessions": [<id_int>, ...],\n'
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
        f"1. Responde EXCLUSIVAMENTE en {out_lang_name}. TODOS los campos de "
        f"texto (intro, decisions[].text, action_items[].title/owner, risks[].text, "
        f"agreements[].text) deben estar en {out_lang_name}, sin importar el "
        f"idioma original de las actas del contexto.\n"
        "2. Cada decisión y cada tarea DEBE incluir el `source_sessions` con "
        "los IDs numéricos (sin '#') de las sesiones del contexto donde aparece. "
        "El ID es el número que ves después de `Sesión #` en el header del bloque.\n"
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
        "significado; cíñete a lo que dice el contexto.\n"
        "11. PREGUNTAS META sobre el conjunto de reuniones (ej. \"qué sitios "
        "se visitaron\", \"qué clientes hubo\", \"qué proyectos se trataron\", "
        "\"qué fechas\", \"con quién nos reunimos\"): respóndelas en `intro` "
        "USANDO los TÍTULOS, FECHAS y PROYECTOS de las sesiones del contexto "
        "(están en el header de cada bloque y en el Inventario inicial). Esa "
        "información es parte del contexto — no digas que \"no se encontró "
        "información\" si las sesiones existen en el contexto.\n"
        "12. Cuando el `intro` lista sitios/clientes/proyectos, hazlo concreto "
        "y enuméralos por nombre (ej. \"Las sesiones registradas corresponden "
        "a visitas a Kilómetro Rosso (20 mar), Forma Italia (18 mar)…\").\n"
        "13. PREGUNTAS SOBRE PERSONAS (\"quién es X\", \"qué hace X\", "
        "\"X estuvo en la reunión\"): RECORRE el contexto buscando el nombre "
        "LITERAL. Si encuentras menciones (en transcripts, decisiones, "
        "acuerdos, riesgos o tareas), responde en `intro` con lo que se "
        "diga de esa persona Y cita en `source_sessions` SOLO la(s) "
        "sesión(es) donde aparece literalmente. Si la pregunta menciona "
        "un nombre y NINGÚN bloque del contexto lo contiene, responde "
        "claramente que NO HAY MENCIÓN de esa persona en el histórico — "
        "NO inventes información ni mezcles con otra persona de nombre "
        "parecido. Los bloques de tipo `Sección: keyword:<nombre>` "
        "garantizan que esas sesiones mencionan el nombre LITERALMENTE.\n"
        "14. PREGUNTAS SOBRE ASISTENCIA / NEGACIONES (\"X llegó a la "
        "reunión\", \"X estuvo presente\", \"X confirmó\"): la respuesta "
        "vive en el TRANSCRIPT, no en el resumen. LEE el bloque de tipo "
        "`Sección: transcript` o el bloque `Transcripción (inicio): ...` "
        "del Resumen extendido. Si el transcript dice \"X no pudo asistir\" "
        "o \"X canceló\" o \"hoy nos faltó X\" → la respuesta es NO ASISTIÓ. "
        "Si dice \"hola X\", \"X dijo que…\", o X habla en el transcript → "
        "SÍ ASISTIÓ. NUNCA respondas \"sí asistió\" sin haber visto evidencia "
        "literal en el transcript de esa sesión específica. Cita SOLO la "
        "sesión que contiene la evidencia, no otras del mismo cliente.\n"
        "15. \"ÚLTIMA SESIÓN DE <X>\": en el contexto vienen las sesiones "
        "más recientes (orden cronológico DESC). La \"última sesión\" es la "
        "de FECHA MÁS RECIENTE (mira el `Fecha:` del header) que pertenezca "
        "al proyecto/cliente mencionado en la pregunta. NO cites sesiones "
        "más antiguas como fuentes para una pregunta sobre \"la última\".\n"
        "16. `intro_source_sessions` es CRÍTICO para que el frontend distinga "
        "las fuentes con la respuesta de las \"otras consultadas\". Pon AHÍ "
        "exclusivamente los IDs de las sesiones cuyo CONTENIDO LITERAL "
        "(transcript, decisiones, acuerdos, riesgos o tareas — NO solo el "
        "título) substancia el `intro`. Si el `intro` dice \"Raúl no llegó "
        "a la última sesión\", `intro_source_sessions` debe contener SOLO la "
        "sesión cuyo transcript dice que no llegó, NO las otras sesiones del "
        "mismo cliente. Si el `intro` es un agregado de varias sesiones (ej. "
        "\"se discutieron 4 temas\"), incluye todas las sesiones implicadas. "
        "Si el `intro` es genérico (\"no hay información\"), déjalo vacío [].\n"
        "17. DATOS ESTRUCTURADOS DEL DIRECTORIO: si la pregunta menciona una "
        "PERSONA y aparece en el bloque `DATOS ESTRUCTURADOS DEL DIRECTORIO`, "
        "usa esa info canónica (rol, empresa, email, proyectos en los que "
        "participa) como base para la respuesta en `intro`. NO te limites a "
        "decir \"aparece en las sesiones X, Y\" — describe quién ES la "
        "persona según el directorio Y complementa con lo que dijo o hizo "
        "en las sesiones. Igual para PROYECTOS: si la pregunta menciona un "
        "proyecto y aparece en ese bloque, usa su descripción + equipo como "
        "respuesta principal en `intro`, y luego añade qué se ha avanzado "
        "según las sesiones. El directorio es VERDAD CANÓNICA — sobreescribe "
        "cualquier inferencia que el transcript pudiera sugerir en contra "
        "(ej. rol o empresa de un participante).\n"
        "18. ROUTING DE INTENCIÓN: analiza la pregunta antes de responder.\n"
        "  - \"quién es X\" / \"qué hace X\" → respuesta DESCRIPTIVA sobre "
        "la persona. Empieza por su rol+empresa (del directorio si está), "
        "luego añade su actividad en las sesiones.\n"
        "  - \"qué es X\" / \"de qué trata X\" / \"para qué sirve X\" / "
        "\"explica X\" → respuesta DESCRIPTIVA sobre el proyecto/tema. "
        "Empieza por su propósito (description del directorio si está), "
        "luego añade sesiones donde se ha tratado.\n"
        "  - \"cuándo\" → respuesta TEMPORAL con la(s) fecha(s) concretas.\n"
        "  - \"dónde\" → respuesta de UBICACIÓN.\n"
        "  - meta (\"qué clientes\", \"qué proyectos\") → enumeración.\n"
        "  - analítica (\"cómo va\", \"qué se ha avanzado\") → síntesis "
        "con datos cuantitativos cuando se pueda (nº sesiones, nº "
        "decisiones, etc.).\n"
        "19. PREGUNTAS DEFINICIONALES (\"qué es X\", \"de qué trata X\", "
        "\"para qué sirve X\", \"en qué consiste X\"): PROHIBIDO responder "
        "enumerando topics tratados (\"se ha hablado de A, B y C\"). EXIGE "
        "una DEFINICIÓN clara: qué ES X, qué hace, para quién, qué problema "
        "resuelve. Para construirla:\n"
        "  a) Si el directorio (DATOS ESTRUCTURADOS) tiene description del "
        "proyecto, ÚSALA como base.\n"
        "  b) Busca en los snippets de transcripts/resumen frases que "
        "definan X: patrones tipo \"X es ...\", \"X se trata de ...\", "
        "\"el objetivo de X es ...\", \"X permite ...\", \"X funciona como "
        "...\", \"con X buscamos ...\", \"X resuelve ...\". Sintetiza esas "
        "frases en 2-3 oraciones que respondan QUÉ ES X.\n"
        "  c) Solo DESPUÉS de la definición, si hay espacio, añade en una "
        "frase el estado/avance (\"se ha tratado en N sesiones donde se "
        "discutió ...\"). Pero la definición VA PRIMERO.\n"
        "  d) Si no hay info ni en directorio ni en transcripts para "
        "definir X, decilo explícitamente: \"En el histórico no encuentro "
        "una definición clara de X; solo se ha mencionado en contexto de "
        "<los topics>\". NO inventes una definición.\n"
        "Ejemplo malo: \"First Class es un proyecto dentro de Softnexus "
        "que ha tenido varias sesiones donde se habló de tiquetera, "
        "rediseño web e idiomas.\"\n"
        "Ejemplo bueno: \"First Class es una plataforma de gestión de "
        "tiquetera (mesa de ayuda) que Softnexus está integrando con su "
        "ecosistema. Permite administrar tickets de soporte, soporta "
        "múltiples idiomas y se conecta con los módulos de gestión vía "
        "API. Actualmente está en fase de rediseño de la interfaz web.\""
    )
    quality_note = ""
    if low_quality:
        # Importante: cuando la similitud es baja la pregunta suele ser
        # "meta" (sobre el conjunto). NO le decimos al LLM que diga "no
        # encontré información" — el inventario de sesiones y sus títulos
        # SÍ es información válida que puede resumir en `intro`.
        quality_note = (
            "\n\nNOTA DE CALIDAD: la similitud semántica con la pregunta no es alta. "
            "Probablemente sea una pregunta META sobre el conjunto de reuniones. "
            "En ese caso:\n"
            "  - En `intro` resume usando los TÍTULOS/FECHAS/PROYECTOS de las "
            "sesiones del Inventario (esa información sí está en el contexto).\n"
            "  - Devuelve `decisions: []` y `action_items: []` si no hay decisiones/"
            "tareas literales — pero responde la pregunta en `intro` con la info "
            "META disponible."
        )

    user_msg = (
        f"{convo_hint}"
        f"Pregunta del usuario: {q}\n\n"
        + (f"{entity_block}\n\n" if entity_block else "")
        + (f"{sessions_inventory}\n\n" if sessions_inventory else "")
        + f"Contexto extraído de actas anteriores ({len(chunks)} fragmentos relevantes, "
          f"filtrados de {len(raw_chunks)} candidatos por umbral de relevancia):\n"
          f"{context}"
          f"{quality_note}\n\n"
          f"Devuelve la respuesta como JSON estricto siguiendo el esquema y RESPETANDO "
          f"las reglas. Recuerda: cada decisión y tarea DEBE traer su `source_sessions`."
    )

    # Inyectamos los turnos previos del MISMO hilo (si vienen) para que el
    # LLM tenga contexto conversacional. Cada turno se modela como
    # user + assistant. Limitamos a los últimos 8 turnos para no inflar el
    # token budget; los más recientes son más relevantes para seguimientos.
    convo_messages = []
    if payload.prior_turns:
        for t in payload.prior_turns[-8:]:
            convo_messages.append({"role": "user", "content": t.question})
            convo_messages.append({"role": "assistant", "content": t.answer})

    payload_llm = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system},
            *convo_messages,
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

            # Sanitiza intro_source_sessions del modelo: solo aceptamos IDs
            # que estén en relevant_session_ids (el set que el LLM vio en el
            # contexto). Esto previene que el LLM cite session_ids
            # inventados o de otra empresa.
            intro_sids = _coerce_int_list(parsed.get("intro_source_sessions"))
            intro_sids = [s for s in intro_sids if s in relevant_session_ids]

            structured = StructuredAnswer(
                intro=str(parsed.get("intro") or ""),
                intro_source_sessions=intro_sids,
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

    # Sólo mostramos como FUENTES los chunks con contenido REAL (post-
    # enriquecimiento). Una fuente vacía es engañosa: el usuario ve "hay
    # decisiones aquí" pero el LLM no usó nada porque el texto estaba vacío.
    # Además deduplicamos por session_id+kind para no listar la misma
    # sección dos veces de la misma sesión.
    seen_pairs: set = set()
    citations: list[Citation] = []
    for c in chunks:
        content = (c.get("content") or "").strip()
        if not content:
            continue
        key = (c["session_id"], (c.get("kind") or "").lower())
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        citations.append(Citation(
            session_id=c["session_id"], kind=c["kind"],
            snippet=content[:280], distance=c.get("distance", 0.0),
            session_title=c.get("session_title") or None,
            session_date=c.get("session_date") or None,
            project_name=c.get("project_name") or None,
        ))
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
