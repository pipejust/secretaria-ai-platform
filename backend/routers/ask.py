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
from date_utils import normalize_due_date
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
    # También detectar acrónimos TODO-MAYÚSCULAS (BEPS, API, ONG…)
    # que la regex anterior ignora (requiere minúsculas después de la inicial).
    for tok in _re.findall(r"\b[A-ZÁÉÍÓÚÑ]{2,}\b", q):
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


_HOWTECH_PATTERNS = (
    r"\bc[oó]mo\s+(?:se\s+)?(?:manej|implement|hace|organiz|estructur|gestion|"
    r"funcion|construy|desarroll|integr|conect|configur|despleg|monta|"
    r"orquest|moder|dise\w+|arm|escal|prueb|test|despleg|deploy)\w*\b",
    r"\bc[oó]mo\s+est[aá]\b",
    r"\bqu[eé]\s+estructura\b",
    r"\bqu[eé]\s+arquitectur\w*\b",
    r"\bqu[eé]\s+stack\b",
    r"\bqu[eé]\s+m[oó]dulos?\b",
    r"\bqu[eé]\s+componentes?\b",
    r"\bqu[eé]\s+tecnolog[ií]as?\b",
    r"\bde\s+qu[eé]\s+forma\b",
    r"\bcu[aá]l\s+es\s+(?:la\s+)?(?:estructura|arquitectur|implementaci|stack)\w*\b",
    r"\bflujo\s+de\b",
    r"\bhow\s+(?:do\s+|does\s+|is\s+|are\s+)?(?:we\s+)?(?:handle|implement|"
    r"build|design|organize|manage|structure|deploy|architect|work)\b",
    r"\bwhat\s+(?:is\s+the\s+)?(?:architecture|structure|stack|implementation|"
    r"design|approach|flow|module|component)\w*\b",
)


_YESNO_VERB_PATTERNS = (
    r"\bmaneja\b", r"\bmanejan\b", r"\badministra\b", r"\badministran\b",
    r"\bgestiona\b", r"\bgestionan\b", r"\bcontrola\b", r"\bcontrolan\b",
    r"\boperat?a\b", r"\boperan\b", r"\bhace\b", r"\bhacen\b",
    r"\busa\b", r"\busan\b", r"\butiliza\b", r"\butilizan\b",
    r"\btiene\b", r"\btienen\b", r"\bes\b", r"\bson\b",
    r"\bpuede\b", r"\bpueden\b", r"\bconecta\b", r"\bconectan\b",
    r"\bintegra\b", r"\bintegran\b", r"\bdepende\b", r"\bdependen\b",
    r"\bhandle\b", r"\bmanage\b", r"\bcontrol\b", r"\boperate\b",
    r"\buse\b", r"\buses\b", r"\bhas\b", r"\bhave\b", r"\bis\b", r"\bare\b",
)


def _is_yesno_question(q: str) -> bool:
    """True si la pregunta es relacional/yes-no ("X maneja Y?", "X es Y?",
    "X tiene Y?"). Estas requieren respuesta SI/NO con EVIDENCIA TEXTUAL
    del transcript, no asunción de relación positiva por co-ocurrencia."""
    if not q:
        return False
    import re as _re
    # Termina en ? o tiene verbo relacional + 2 sustantivos.
    if "?" not in q:
        return False
    low = q.lower()
    return any(_re.search(p, low) for p in _YESNO_VERB_PATTERNS)


def _is_howtech_question(q: str) -> bool:
    """True si la pregunta pide DETALLE TÉCNICO sobre el manejo de algo:
    'cómo se manejan los eventos', 'qué arquitectura tiene', 'cómo está
    organizado el módulo'. Diferente de whatis (que define un X); aquí
    el user ya conoce X y quiere el HOW interno. Necesita snippets más
    grandes Y traer también keywords del CONCEPTO técnico, no solo del
    nombre propio del proyecto."""
    import re as _re
    if not q:
        return False
    low = q.lower()
    return any(_re.search(p, low) for p in _HOWTECH_PATTERNS)


# Conceptos técnicos comunes que el LLM debe poder rastrear en
# transcripts cuando la pregunta es howtech. La lista NO es exhaustiva;
# captura los que más aparecen en sesiones de producto/eng.
# Mapa de sinonimos / vocabulario de dominio. Cuando el query menciona
# un term clave, expandimos la busqueda con sus variantes para no
# perder evidencia donde el transcript usa nombres especificos
# (ej. user pregunta "boleteria" pero transcript dice "Tiquetera Mi Boleta",
# "etiquetera", "Mi Boleto", "venta de entradas"). Sin esta expansion,
# las ocurrencias clave NO aparecen en yesno_evidence chunks.
_DOMAIN_SYNONYMS = {
    # Tickets / boletería
    "boletería": ["boleto", "boletas", "tiquetera", "etiquetera", "ticket",
                  "tickets", "mi boleta", "mi boleto", "venta de entradas",
                  "venta de boletos", "boletería"],
    "boleteria": ["boleto", "boletas", "tiquetera", "etiquetera", "ticket",
                  "tickets", "mi boleta", "mi boleto", "venta de entradas",
                  "venta de boletos"],
    "boleto": ["boletos", "tiquetera", "etiquetera", "ticket", "mi boleta",
               "boletería"],
    "boletos": ["boleto", "tiquetera", "etiquetera", "tickets", "mi boleta",
                "boletería"],
    "ticket": ["tickets", "boleto", "boletas", "tiquetera", "etiquetera"],
    "tickets": ["ticket", "boleto", "boletos", "tiquetera", "etiquetera"],
    # ── ALIAS DE UNA MISMA ENTIDAD RENOMBRADA EN EL TIEMPO ──
    # El módulo de boletería de First Class cambió de nombre por sesiones:
    # etiqueta → etiquetera → taquilla/taquillera → tiquetera → «Mi Boleta».
    # TODOS son el MISMO producto. El cluster es bidireccional para que
    # preguntar por cualquier nombre traiga las sesiones de todos los demás
    # (recall), y el LLM pueda INFERIR que son uno solo (ver regla 33).
    "mi boleta": ["mi boleto", "tiquetera", "etiquetera", "etiqueta",
                  "taquilla", "taquillera", "boletería", "admin mb"],
    "mi boleto": ["mi boleta", "tiquetera", "etiquetera", "taquilla"],
    "etiquetera": ["mi boleta", "tiquetera", "etiqueta", "taquilla",
                   "taquillera", "boletería"],
    "etiqueta": ["etiquetera", "mi boleta", "tiquetera", "taquilla"],
    "taquilla": ["taquillera", "etiquetera", "mi boleta", "tiquetera",
                 "boletería"],
    "taquillera": ["taquilla", "etiquetera", "mi boleta", "tiquetera"],
    "tiquetera": ["etiquetera", "mi boleta", "mi boleto", "taquilla",
                  "taquillera", "etiqueta", "boletería", "boletas"],
    # Pagos / cartera
    "pago": ["pagos", "cobro", "cartera", "cuota", "cuotas", "facturación"],
    "pagos": ["pago", "cobro", "cartera", "cuota", "cuotas", "facturación"],
    "cartera": ["cuota", "cuotas", "cobro", "pago"],
    # Programas / fidelización
    "puntos": ["motor de puntos", "fidelización", "fidelizacion", "core",
               "acreditar"],
    "fidelización": ["fidelizacion", "puntos", "motor de puntos"],
    # Productos en venue
    "alimento": ["alimentos", "bebidas", "comida", "snack"],
    "alimentos": ["bebidas", "comida", "snacks", "cafeteria"],
    "bebidas": ["alimentos", "comida"],
    # Eventos
    "evento": ["eventos", "concierto", "función"],
    "eventos": ["evento", "conciertos", "funciones"],
    # Integracion
    "api": ["apis", "endpoint", "webhook", "integración", "integracion"],
    "integración": ["integracion", "api", "webhook"],
    # Legal
    "ley": ["legislación", "legislacion", "legal", "normativa"],
    "legislación": ["ley", "legislacion", "legal", "normativa"],
    # BEPS / Sede electrónica (sistema colombiano de pensiones/beneficios)
    "beps": ["beneficios económicos periódicos", "sede electrónica", "sede",
             "aplicación móvil", "portal", "pensiones", "beneficios"],
    "BEPS": ["beneficios económicos periódicos", "sede electrónica", "sede",
             "aplicación móvil", "portal"],
    "sede": ["sede electrónica", "beps", "portal", "plataforma", "aplicación"],
    "sede electrónica": ["beps", "sede", "portal electrónica", "aplicación móvil"],
    # Parametrización
    "parametri": ["parametrización", "parametrizable", "parametrizar",
                  "configuración", "mensajes", "parámetros"],
    "parametrización": ["parametrizable", "parametrizar", "parametri",
                        "configurar mensajes", "parámetros", "configuración"],
    "parametrizable": ["parametrización", "parametrizar", "configurable"],
    # Móvil / App
    "móvil": ["aplicación móvil", "app móvil", "mobile", "app", "aplicación"],
    "aplicación": ["app", "móvil", "aplicación móvil", "plataforma"],
    # Homologación / "hacerlo igual a la sede, nada nuevo, equivalente".
    # CLAVE: el mismo concepto se dice con MUCHAS palabras distintas en los
    # transcripts (homologar, igual a la sede, como está en sede, equivalente,
    # nada nuevo). Sin esta expansión, una pregunta con UNA de las variantes
    # no encuentra las sesiones donde se dijo con OTRA variante.
    "homologar": ["homologación", "homologa", "igual a la sede",
                  "como está en la sede", "como está en sede",
                  "igual a sede", "equivalente a la sede", "mismo que la sede",
                  "réplica de la sede", "nada nuevo", "no hacer nada nuevo",
                  "de la misma manera", "igual que funciona", "tal como en la sede"],
    "homologación": ["homologar", "homologa", "igual a la sede",
                     "como está en la sede", "equivalente", "nada nuevo",
                     "de la misma manera", "igual a sede"],
    "homologa": ["homologar", "homologación", "igual a la sede"],
    "equivalente": ["homologar", "homologación", "igual a la sede",
                    "como está en sede", "mismo que la sede"],
    # "igual"/"mismo" como verbo de equivalencia funcional con la sede
    "igual": ["homologar", "homologación", "igual a la sede",
              "como está en la sede", "equivalente", "de la misma manera"],
}


QUERY_ANALYZER_MODEL = "llama-3.1-8b-instant"


async def _llm_analyze_query(
    q: str,
    project_names: list[str],
    prior_turns: Optional[list] = None,
) -> dict:
    """ETAPA DE COMPRENSIÓN DE QUERY (query understanding).

    Un LLM rápido y barato analiza la pregunta ANTES del retrieval y
    devuelve:
      · entities       — nombres propios/productos/personas mencionados
      · search_terms   — 5-10 strings cortos (1-3 palabras) en el
                         VOCABULARIO REAL de una reunión, incluyendo
                         sinónimos y conjugaciones que la gente diría
                         hablando (no lenguaje formal). Se usan para
                         ILIKE literal sobre transcripts.
      · reformulated_query — la pregunta reescrita de forma canónica
                         para una 2da búsqueda vectorial.

    Esto reemplaza la dependencia de diccionarios hardcodeados
    (_DOMAIN_SYNONYMS cubre solo temas curados): el modelo genera las
    variantes para CUALQUIER tema. Corre EN PARALELO con el vector
    search (asyncio.gather) → no suma latencia percibida.

    Fail-safe: ante cualquier error/timeout devuelve {} y el pipeline
    sigue con las heurísticas existentes. Cero riesgo de regresión."""
    if not settings.groq_api_key or not q or len(q) < 8:
        return {}
    projs = ", ".join(project_names[:15]) if project_names else "—"
    # Contexto conversacional: si la pregunta es un SEGUIMIENTO ambiguo
    # («¿y quién quedó responsable de eso?», «¿desde cuándo?»), el
    # analizador DEBE resolver las referencias con el hilo previo y
    # producir una reformulated_query AUTÓNOMA — es la que alimenta el
    # retrieval. Sin esto, pgvector busca «eso» y trae basura.
    convo_block = ""
    if prior_turns:
        pieces: list[str] = []
        for t in prior_turns[-2:]:
            tq = (getattr(t, "question", "") or "")[:200]
            ta = (getattr(t, "answer", "") or "")[:350]
            if tq:
                pieces.append(f"Usuario preguntó: {tq}\nSe respondió: {ta}")
        if pieces:
            convo_block = (
                "\nHILO PREVIO DE LA CONVERSACIÓN (para resolver "
                "referencias como 'eso', 'él', 'esa decisión', 'ahí'):\n"
                + "\n---\n".join(pieces)
                + "\nSi la pregunta actual referencia el hilo, "
                "reformulated_query DEBE ser autónoma: sustituye los "
                "pronombres/deícticos por los nombres y temas concretos "
                "del hilo. entities y search_terms también deben salir "
                "del tema RESUELTO, no del pronombre.\n"
            )
    sys_prompt = (
        "Analizas preguntas hechas sobre un archivo de transcripciones de "
        "reuniones de trabajo (español colombiano, mezcla de temas técnicos "
        "y de negocio). Devuelve SOLO un objeto JSON con:\n"
        '{"entities": [<nombres propios, productos, personas, clientes '
        "mencionados o implicados en la pregunta>],\n"
        ' "search_terms": [<5-10 strings de 1-3 palabras que aparecerían '
        "LITERALMENTE en una conversación hablada sobre este tema: "
        "sinónimos coloquiales, conjugaciones verbales, nombres "
        "alternativos. NO palabras genéricas como 'aplicación', 'proyecto', "
        "'plataforma', 'reunión', 'tema'>],\n"
        ' "reformulated_query": "<la pregunta reescrita en forma '
        'declarativa canónica, con los conceptos explícitos>"}\n'
        f"Proyectos/clientes conocidos del workspace: {projs}.\n"
        "Si la pregunta usa un término abstracto o formal, agrega en "
        "search_terms las formas COLOQUIALES en que la gente diría ESE "
        "término hablando en reunión — pero SOLO variantes del tema de "
        "esta pregunta; nunca copies términos de ejemplo ni de otros "
        "temas. Tampoco repitas palabras que ya están en la pregunta si "
        "son genéricas (equipo, sesión, problema, compromiso)."
        f"{convo_block}"
    )
    body = {
        "model": QUERY_ANALYZER_MODEL,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": q},
        ],
        "temperature": 0.2,
        "max_tokens": 400,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {settings.groq_api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(GROQ_URL, json=body, headers=headers)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            data = _json_lib.loads(content)
            if not isinstance(data, dict):
                return {}
            # Sanitizar: solo strings, caps defensivos.
            out = {
                "entities": [
                    str(e).strip() for e in (data.get("entities") or [])
                    if isinstance(e, (str, int)) and len(str(e).strip()) >= 3
                ][:6],
                # min 4 chars: descarta pronombres/deícticos que el modelo
                # a veces copia de la pregunta ('eso', 'ahí', 'él') — con
                # ILIKE matchean todo y son puro ruido.
                "search_terms": [
                    str(t).strip() for t in (data.get("search_terms") or [])
                    if isinstance(t, (str, int)) and 4 <= len(str(t).strip()) <= 40
                    and str(t).strip().lower() not in (
                        "eso", "esa", "este", "esta", "aquello", "ello",
                        "ahí", "alli", "allí", "quedó", "quedo", "dijo",
                        "hablo", "habló", "mencionó", "menciono",
                    )
                ][:10],
                "reformulated_query": str(data.get("reformulated_query") or "").strip()[:300],
            }
            return out
    except Exception as exc:  # noqa: BLE001 — fail-safe deliberado
        logger.warning("ask: query-analyzer falló (%s) — sigo sin él", exc)
        return {}


def _expand_with_synonyms(terms: list[str]) -> list[str]:
    """Para cada termino, anade sinonimos del dominio. Deduplica
    preservando orden. Tope 25 para no inflar SQL."""
    if not terms:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for t in terms:
        low = (t or "").lower().strip()
        if not low:
            continue
        if low not in seen:
            seen.add(low)
            out.append(t)
        for syn in _DOMAIN_SYNONYMS.get(low, []):
            slow = syn.lower()
            if slow not in seen:
                seen.add(slow)
                out.append(syn)
        if len(out) >= 25:
            break
    return out


_TECH_CONCEPTS = {
    "evento": ["evento", "eventos", "event", "events", "webhook", "webhooks"],
    "arquitectura": ["arquitectura", "architecture", "arquitectónic"],
    "estructura": ["estructura", "structure", "organizaci"],
    "módulo": ["módulo", "modulo", "module", "modules"],
    "componente": ["componente", "component", "componentes"],
    "código": ["código", "codigo", "code", "source"],
    "frontend": ["frontend", "front-end", "front end", "angular", "ui"],
    "backend": ["backend", "back-end", "back end", "fastapi", "api"],
    "base de datos": ["base de datos", "database", "db", "postgres", "sql"],
    "infraestructura": ["infraestructura", "infrastructure", "docker", "kubernetes", "coolify"],
    "integración": ["integración", "integration", "integrac"],
    "autenticación": ["autenticación", "autenticacion", "auth", "jwt", "oauth"],
    "tiquetera": ["tiquetera", "ticket", "tickets", "ticketing"],
    "idioma": ["idioma", "lenguaje", "language", "i18n"],
    "seguridad": ["seguridad", "security", "encrypt"],
    "flujo": ["flujo", "flow", "pipeline", "proceso"],
    "evento de calendario": ["calendario", "calendar", "evento"],
    "tarea": ["tarea", "task", "action item", "actionitem"],
    "modelo": ["modelo", "model", "schema"],
    "rol": ["rol", "role", "permiso", "permission"],
    "tenant": ["tenant", "multi-tenant", "multi tenant", "empresa"],
}


def _extract_known_entities(
    db: "Session",
    tenant_id: int,
    q: str,
    *,
    max_results: int = 6,
) -> list[str]:
    """Extrae nombres de proyectos/contactos del tenant mencionados en la
    pregunta — CASE-INSENSITIVE.

    Razón: `_extract_proper_nouns` solo agarra palabras con mayúscula
    inicial. Usuarios escriben "first class" o "firstclass" en
    minúsculas — esos casos se perdían y el deep loader nunca corría.
    Aquí cargamos los nombres reales de project.name + projectcontact
    .name del tenant y buscamos si aparecen en la pregunta (ignorando
    mayúsculas/minúsculas y espacios). Devuelve los nombres en su
    casing original de BD para usarlos en ILIKE downstream.
    """
    if not q:
        return []
    from sqlmodel import select
    from models import Project, ProjectContact

    qlow = q.lower()
    qnorm = "".join(ch for ch in qlow if ch.isalnum() or ch.isspace())
    qnorm = " ".join(qnorm.split())  # espacios colapsados

    candidates: list[str] = []
    seen_lower: set[str] = set()

    # Proyectos del tenant.
    for p in db.exec(
        select(Project).where(Project.tenant_id == tenant_id)
    ).all():
        name = (p.name or "").strip()
        if not name or len(name) < 3:
            continue
        nlow = name.lower()
        if nlow in seen_lower:
            continue
        # Match permisivo: el nombre tal cual O sin espacios
        # (matches "FirstClass" + "first class" → "firstclass").
        ncompact = "".join(ch for ch in nlow if ch.isalnum())
        if nlow in qnorm or (len(ncompact) >= 4 and ncompact in qnorm.replace(" ", "")):
            candidates.append(name)
            seen_lower.add(nlow)
            if len(candidates) >= max_results:
                return candidates

    # Contactos del tenant — match por nombre completo.
    for c in db.exec(
        select(ProjectContact)
        .join(Project, Project.id == ProjectContact.project_id)
        .where(Project.tenant_id == tenant_id)
    ).all():
        name = (c.name or "").strip()
        if not name or len(name) < 3:
            continue
        nlow = name.lower()
        if nlow in seen_lower:
            continue
        if nlow in qnorm:
            candidates.append(name)
            seen_lower.add(nlow)
            if len(candidates) >= max_results:
                return candidates

    return candidates


# Stopwords para filtrar frases candidatas que no son entidades
# (preguntas + nexos + verbos genéricos).
_PHRASE_STOPWORDS = frozenset({
    "que", "qué", "como", "cómo", "cuando", "cuándo", "donde", "dónde",
    "quien", "quién", "cual", "cuál", "por", "para", "con", "sin",
    "los", "las", "del", "una", "uno", "una", "unas", "unos",
    "este", "esta", "esto", "ese", "esa", "eso", "estos", "estas",
    "esos", "esas", "del", "al", "en", "es", "ser", "son", "fue",
    "han", "han", "y", "o", "de", "se", "la", "el", "lo", "le", "les",
    "tu", "tú", "su", "sus", "mi", "mis", "yo", "tu", "él", "ella",
    "ellos", "ellas", "muy", "mas", "más", "menos", "siempre",
    "nunca", "ya", "aún", "aun", "hay", "puede", "pueden", "debe",
    "deben", "tiene", "tienen", "hace", "hacen", "está", "están",
    "the", "and", "or", "of", "for", "with", "without", "this",
    "that", "these", "those", "is", "are", "was", "were", "be",
    "you", "your", "i", "we", "they", "it", "in", "on", "to", "from",
    "evento", "eventos", "estructura", "arquitectura", "código", "codigo",
    "módulo", "modulo", "componente", "frontend", "backend", "base",
    "datos", "manejan", "manejar", "implementa", "implementan",
    "organiza", "organizan", "funciona", "funcionan", "estructur",
})


def _extract_session_title_phrases(
    db: "Session",
    tenant_id: int,
    q: str,
    *,
    max_phrases: int = 5,
) -> list[str]:
    """Extrae frases de 2-4 palabras de la pregunta cuyo match aparezca
    como SUBSTRING en algún session.title del tenant.

    Razón: el caso real "First Class" no es nombre de proyecto en BD —
    es un cliente discutido en sesiones del proyecto Softnexus. Sus
    sesiones llevan títulos como "First Class - Evento - api- tiquetera".
    `_extract_known_entities` (que busca project.name + projectcontact)
    falla. Aquí complementamos: si una frase de la pregunta aparece en
    títulos de sesiones, la tratamos como entidad y el deep loader
    captura esas sesiones vía title ILIKE.
    """
    if not q:
        return []
    import re as _re
    from sqlmodel import select, func
    from models import MeetingSession

    # Tokenizar palabras alfa de longitud ≥3, en minúsculas.
    tokens = [
        t.lower()
        for t in _re.findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñÀÈÌÒÙäëïöü]{3,}", q)
    ]
    if not tokens:
        return []

    # Generar bigramas + trigramas + quadrigramas (preserva orden).
    phrases: list[str] = []
    seen: set[str] = set()
    for n in (4, 3, 2):
        for i in range(len(tokens) - n + 1):
            window = tokens[i:i + n]
            # Skip si TODOS son stopwords (poco específico).
            if all(t in _PHRASE_STOPWORDS for t in window):
                continue
            # Skip si arranca/termina con stopword sola (frase ruidosa).
            if window[0] in _PHRASE_STOPWORDS or window[-1] in _PHRASE_STOPWORDS:
                continue
            phrase = " ".join(window)
            if phrase in seen:
                continue
            seen.add(phrase)
            phrases.append(phrase)

    # Cada frase: contar matches en session.title del tenant. Si ≥1, la
    # phrase es una entidad. Cap total para no abusar SQL.
    found: list[tuple[str, int]] = []
    for phrase in phrases[:30]:  # cap candidates checked
        cnt = db.exec(
            select(func.count())
            .select_from(MeetingSession)
            .where(MeetingSession.tenant_id == tenant_id)
            .where(MeetingSession.title.ilike(f"%{phrase}%"))
        ).first()
        n = int(cnt[0] if isinstance(cnt, tuple) else cnt or 0)
        if n > 0:
            found.append((phrase, n))

    if not found:
        return []
    # Priorizar frases con MÁS sesiones (más representativas).
    found.sort(key=lambda kv: (-kv[1], -len(kv[0])))
    return [phrase for phrase, _ in found[:max_phrases]]


def _load_yesno_evidence(
    db: "Session",
    tenant_id: int,
    proper_nouns: list[str],
    extra_terms: list[str],
    *,
    limit_sessions: int = 14,
    window: int = 2000,
    max_occ: int = 6,
) -> list[dict]:
    """Para preguntas yes-no/relacionales: trae snippets del transcript
    donde aparezcan los TÉRMINOS de la pregunta (subject + object) JUNTO
    con marcadores de relación o negación.

    Marcadores buscados:
      Positivos: maneja, administra, gestiona, controla, es, tiene,
                 conecta, integra, usa, hace, opera.
      Negativos: no maneja, no administra, no es, no tiene, sino, pero
                 no, ni, partner, responsable.

    El LLM NO puede inferir la relación correcta solo del resumen IA
    (que a veces invierte el sujeto). Necesita el transcript LITERAL.
    Este loader prioriza sesiones con ambos términos en el mismo
    fragmento y emite chunks distance=0 (bypass relevance)."""
    group_subject = [t for t in (proper_nouns or []) if t and len(t) >= 3]
    group_object = [t for t in (extra_terms or []) if t and len(t) >= 3]
    all_terms = group_subject + group_object
    if not all_terms:
        return []
    from sqlmodel import select, or_
    from models import MeetingSession, Project
    import re as _re

    pattern_filters = [
        MeetingSession.raw_transcript.ilike(f"%{t}%") for t in all_terms
    ]
    sessions = db.exec(
        select(MeetingSession)
        .where(MeetingSession.tenant_id == tenant_id)
        .where(MeetingSession.status != "archived")
        .where(or_(*pattern_filters))
        .order_by(MeetingSession.id.desc())
        .limit(limit_sessions * 3)
    ).all()

    out: list[dict] = []
    proj_cache: dict[int, str] = {}
    for s in sessions:
        if not s.id or not (s.raw_transcript or "").strip():
            continue
        transcript = s.raw_transcript
        tlow = transcript.lower()
        # Relacional REAL: al menos un term del SUBJECT y al menos uno
        # del OBJECT en la transcripcion. Sin esto, una sesion que solo
        # menciona "boleto" sin nada del sujeto se cuela.
        has_subject = any(t.lower() in tlow for t in group_subject) if group_subject else True
        has_object = any(t.lower() in tlow for t in group_object) if group_object else True
        if not (has_subject and has_object):
            continue
        terms = all_terms  # mantener nombre para downstream

        # Snippets centrados en cada término — con ventana grande para
        # capturar el contexto relacional alrededor.
        windows: list[tuple[int, int]] = []
        for t in terms:
            for m in _re.finditer(_re.escape(t.lower()), tlow):
                start = max(0, m.start() - window // 2)
                end = min(len(transcript), m.end() + window // 2)
                if windows and start <= windows[-1][1]:
                    windows[-1] = (windows[-1][0], max(windows[-1][1], end))
                else:
                    windows.append((start, end))
                if len(windows) >= max_occ:
                    break
            if len(windows) >= max_occ:
                break
        if not windows:
            continue

        snippet = "\n…\n".join(
            transcript[a:b].strip() for a, b in windows
        )
        proj_name = ""
        if s.project_id:
            if s.project_id not in proj_cache:
                p = db.get(Project, s.project_id)
                proj_cache[s.project_id] = (p.name if p else "") or ""
            proj_name = proj_cache[s.project_id]

        # Formatear fecha legible para que el LLM pueda citarla.
        date_display = _fmt_session_date(s.date)

        out.append({
            "session_id": s.id,
            "kind": "yesno_evidence",
            "content": (
                f"=== EVIDENCIA TEXTUAL #{s.id} ===\n"
                f"Sesión: «{s.title or 'Sin título'}»\n"
                f"Fecha: {date_display or '—'}\n"
                f"Proyecto: {proj_name or '—'}\n"
                f"Términos co-presentes: {', '.join(terms)}\n"
                f"--- Fragmentos LITERALES del transcript (incluyen "
                f"marcadores [Nombre Speaker]) ---\n"
                f"{snippet}\n"
                f"=== FIN EVIDENCIA #{s.id} ==="
            ),
            "distance": 0.0,
            "session_title": s.title or "",
            "session_date": s.date or "",
            "project_name": proj_name,
        })
        if len(out) >= limit_sessions:
            break
    return out


def _build_concept_index(
    chunks: list[dict],
    concepts: list[str],
) -> str:
    """Construye un mapa concepto → lista de session_ids que mencionan
    ese concepto. Se inyecta al inicio del user_msg cuando howtech está
    activo. Sirve para que el LLM:
      a) Sepa de un vistazo cuántas sesiones cubren cada dimensión.
      b) Pueda CITAR todas las sesiones relevantes en lugar de quedarse
         con la primera que lee.
      c) Identifique gaps (un concepto sin sesiones = "no encontré
         detalle de X").
    """
    if not concepts or not chunks:
        return ""
    import re as _re

    by_concept: dict[str, set[int]] = {c: set() for c in concepts}
    for ch in chunks:
        sid = ch.get("session_id")
        if sid is None:
            continue
        # Buscamos cada concepto en el content del chunk (case-insensitive).
        # El content del chunk ya combina los campos relevantes.
        text = (ch.get("content") or "").lower()
        for concept in concepts:
            if _re.search(_re.escape(concept.lower()), text):
                by_concept[concept].add(int(sid))

    lines: list[str] = ["=== ÍNDICE CONCEPTO → SESIONES ==="]
    lines.append(
        "Mapa de qué sesiones discuten cada concepto. ÚSALO para integrar "
        "información entre sesiones, no responder con una sola. Si un "
        "concepto aparece en 5 sesiones, tu respuesta debe consultar las "
        "5 y citarlas en intro_source_sessions."
    )
    any_hit = False
    for concept, sids in by_concept.items():
        if sids:
            any_hit = True
            ids_str = ", ".join(f"#{i}" for i in sorted(sids))
            lines.append(f"- «{concept}»: {len(sids)} sesiones → {ids_str}")
        else:
            lines.append(f"- «{concept}»: 0 sesiones (sin detalle en el histórico)")
    if not any_hit:
        return ""
    return "\n".join(lines)


def _score_session_for_howtech(
    session_title: str,
    title_match_terms: list[str],
    concept_terms: list[str],
    body_text: str = "",
) -> tuple[int, int]:
    """Score (title_score, body_hits) para priorizar sesiones que de
    verdad discuten los conceptos preguntados.

    - title_score: presencia de proper_nouns + howtech_concepts en TÍTULO
      (alta señal de relevancia).
    - body_hits: nº total de ocurrencias de los howtech_concepts en el
      cuerpo (transcript + resumen). Sesiones con 0 hits son ruido y se
      pueden descartar.
    """
    title_low = (session_title or "").lower()
    body_low = (body_text or "").lower()
    title_score = 0
    for term in title_match_terms or []:
        if term.lower() in title_low:
            title_score += 1
    for c in concept_terms or []:
        if c.lower() in title_low:
            title_score += 3  # peso alto: concepto en título
    body_hits = 0
    for c in concept_terms or []:
        body_hits += body_low.count(c.lower())
    return (title_score, body_hits)


def _load_project_deep_context(
    db: "Session",
    tenant_id: int,
    proper_nouns: list[str],
    limit_sessions: int = 6,
    summary_chars: int = 1800,
    decisions_chars: int = 1200,
    howtech_concepts: Optional[list[str]] = None,
    transcript_snippet_window: int = 2000,
    transcript_max_occ: int = 6,
    require_concept_in_body: bool = True,
) -> list[dict]:
    """Cuando hay intent howtech y un nombre propio matchea un PROYECTO
    del tenant, este loader trae los CUERPOS COMPLETOS (raw_summary,
    processed_decisions, processed_agreements, processed_risks) MÁS
    snippets del raw_transcript alrededor de los conceptos técnicos
    detectados. Material denso para que el LLM construya una respuesta
    arquitectónica real con nombres concretos.

    Fuentes de sesiones (UNION):
      1) sessions WHERE project_id == matched Project.
      2) sessions WHERE title ILIKE %name% (cubre sesiones SIN
         project_id pero con el nombre en el título — caso común
         cuando el auto-match falla).

    Transcript: cuando `howtech_concepts` viene, además del resumen
    estructurado se inyectan hasta `transcript_max_occ` ventanas de
    `transcript_snippet_window` chars centradas en cada concepto.
    El transcript es donde vive el DETALLE real (endpoints, tablas,
    flujos paso-a-paso). Sin esto el LLM solo ve resúmenes y responde
    genérico.

    Cap total: `limit_sessions` para no inflar tokens. Sesiones más
    recientes primero."""
    if not proper_nouns:
        return []
    from sqlmodel import select, or_
    from models import MeetingSession, Project

    # 1. Resolver nombres a project_ids del tenant.
    matched_pids: list[int] = []
    for name in proper_nouns:
        rows = db.exec(
            select(Project)
            .where(Project.tenant_id == tenant_id)
            .where(Project.name.ilike(f"%{name}%"))
            .limit(3)
        ).all()
        for p in rows:
            if p.id not in matched_pids:
                matched_pids.append(p.id)

    # 2. Sesiones candidatas: UNION de las taggeadas al proyecto y las
    #    cuyo TÍTULO contiene el nombre propio (caso untagged).
    title_filters = [MeetingSession.title.ilike(f"%{n}%") for n in proper_nouns]
    q = (
        select(MeetingSession)
        .where(MeetingSession.tenant_id == tenant_id)
        .where(MeetingSession.status != "archived")
    )
    if matched_pids:
        q = q.where(
            or_(
                MeetingSession.project_id.in_(matched_pids),
                *title_filters,
            )
        )
    else:
        # Sin matched_pids igual buscamos por título — útil para sesiones
        # del proyecto que nunca quedaron taggeadas.
        if not title_filters:
            return []
        q = q.where(or_(*title_filters))
    sessions = db.exec(
        q.order_by(MeetingSession.id.desc()).limit(limit_sessions * 6)
    ).all()

    # 3. RANKING — priorizamos sesiones que de verdad discuten los
    # conceptos preguntados, no las que solo comparten el nombre del
    # cliente en el título. Cuando howtech_concepts viene, una sesión
    # SIN ninguna mención del concepto en cuerpo es ruido: dispersa al
    # LLM hacia temas no preguntados (caso reportado: respuesta sobre
    # "eventos" terminaba listando Docker, multi-tenant, tokens — porque
    # el deep dump incluía sesiones de avances generales).
    candidates: list[tuple[tuple[int, int], "MeetingSession"]] = []
    seen_sids: set[int] = set()
    for s in sessions:
        if not s.id or s.id in seen_sids:
            continue
        seen_sids.add(s.id)
        body_text = " ".join([
            s.raw_summary or "",
            s.processed_decisions or "",
            s.processed_agreements or "",
        ])
        score = _score_session_for_howtech(
            s.title or "",
            title_match_terms=proper_nouns,
            concept_terms=howtech_concepts or [],
            body_text=body_text,
        )
        title_score, body_hits = score
        # Filtro: si hay conceptos pero la sesión no menciona ninguno en
        # cuerpo NI en título → descartar. Si NO hay conceptos (no es
        # howtech), aceptar todas.
        if howtech_concepts and require_concept_in_body:
            if body_hits == 0 and title_score == 0:
                continue
            # También descartar si solo machea el nombre del cliente pero
            # NO hay hits del concepto en body — esa sesión no discute
            # el tema preguntado.
            concept_in_title = any(
                c.lower() in (s.title or "").lower() for c in howtech_concepts
            )
            if body_hits == 0 and not concept_in_title:
                continue
        candidates.append((score, s))

    # Ordenar por relevancia: title_score DESC, body_hits DESC, id DESC.
    candidates.sort(key=lambda kv: (-kv[0][0], -kv[0][1], -(kv[1].id or 0)))

    # 4. Ensamblar bloques. Sesiones más altas obtienen ventanas más
    # grandes; las demás solo el resumen breve.
    out: list[dict] = []
    proj_cache: dict[int, str] = {}
    for rank, (score, s) in enumerate(candidates[:limit_sessions]):
        title_score, body_hits = score
        is_top = rank < 2  # top-2 sesiones reciben más bandwidth

        proj_name = ""
        if s.project_id:
            if s.project_id not in proj_cache:
                p = db.get(Project, s.project_id)
                proj_cache[s.project_id] = (p.name if p else "") or ""
            proj_name = proj_cache[s.project_id]

        # Para sesiones top: dossier más rico. Para otras: resumen y
        # solo snippet de conceptos (no decisiones ni acuerdos enteros,
        # para no diluir el foco).
        parts: list[str] = []

        # ORDEN INVERTIDO: transcript PRIMERO. El LLM anclaba en el
        # resumen IA y emitia frases tipo "se discutio funcionalidades"
        # (boilerplate del resumen). Si los snippets del transcript
        # vienen primero, hay mas chance de que cite frases especificas.
        if howtech_concepts and (s.raw_transcript or "").strip():
            transcript = s.raw_transcript or ""
            window = transcript_snippet_window if is_top else transcript_snippet_window // 2
            max_occ = transcript_max_occ if is_top else max(2, transcript_max_occ // 2)
            for concept in howtech_concepts:
                block = _name_snippets_all(
                    transcript, concept,
                    window=window, max_occ=max_occ,
                )
                if block:
                    if len(block) > window * max_occ:
                        block = block[: window * max_occ] + " …"
                    parts.append(
                        f"[Transcripción — fragmentos sobre «{concept}»]\n{block}"
                    )

        # Resumen RECORTADO. Antes era 1800-2200c y dominaba el dossier.
        # Ahora 600-900c para usar solo de contexto, no de fuente
        # principal — la fuente principal es el transcript.
        if is_top:
            if (s.raw_summary or "").strip():
                parts.append(f"[Resumen breve]\n{(s.raw_summary or '')[:900]}")
            if (s.processed_decisions or "").strip():
                parts.append(f"[Decisiones]\n{(s.processed_decisions or '')[:decisions_chars]}")
            if (s.processed_agreements or "").strip():
                parts.append(f"[Acuerdos]\n{(s.processed_agreements or '')[:decisions_chars]}")
        else:
            if (s.raw_summary or "").strip():
                parts.append(f"[Resumen breve]\n{(s.raw_summary or '')[:600]}")

        if not parts:
            continue

        proj_header = f"«{proj_name}»" if proj_name else "(sin proyecto)"
        title_header = s.title or "(sin título)"
        rank_tag = " ★PRIORITARIA★" if is_top else ""
        content = (
            f"[Dossier sesión «{title_header}» — proyecto {proj_header}"
            f" — score=({title_score},{body_hits}){rank_tag}]\n"
            + "\n\n".join(parts)
        )
        out.append({
            "session_id": s.id,
            "kind": "project_deep",
            "content": content,
            "distance": 0.0,  # bypass relevance filter
            "session_title": s.title or "",
            "session_date": s.date or "",
            "project_name": proj_name,
        })
    return out


def _extract_tech_concepts(q: str) -> list[str]:
    """De la pregunta, identifica conceptos técnicos del catálogo. Sirve
    para AMPLIAR la búsqueda literal en transcripts: además de buscar el
    nombre propio del proyecto ("First Class"), buscamos los términos
    técnicos asociados ("eventos", "webhook", "arquitectura") y traemos
    snippets que mencionen AMBOS. Devuelve hasta 4 términos para no
    inflar ILIKE queries."""
    if not q:
        return []
    low = q.lower()
    out: list[str] = []
    seen: set[str] = set()
    for concept, terms in _TECH_CONCEPTS.items():
        for t in terms:
            if t in low and concept not in seen:
                out.append(concept)
                seen.add(concept)
                break
        if len(out) >= 4:
            break
    return out


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
    # Siglas cortas (≤3): match solo como palabra aislada — sin esto la
    # ventana cae dentro de "trabajo"/"tres" y el snippet es ruido.
    if len(name.strip()) <= 3:
        pat = _re.compile(rf"\b{_re.escape(name.strip())}\b", _re.IGNORECASE)
    else:
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
    howtech_concepts: Optional[list[str]] = None,
    scope_project_id: Optional[int] = None,
    scope_title: Optional[str] = None,
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
        # TÉRMINOS CORTOS (≤3 chars: TR, ADM, INF, QA…): ILIKE '%TR%'
        # matchea DENTRO de "trabajo"/"tres"/"otro" → basura en todas las
        # sesiones. Usamos regex con word-boundary (~* '\yTR\y') para que
        # solo matchee la sigla aislada.
        if len(name.strip()) <= 3:
            import re as _re2
            safe = _re2.escape(name.strip())
            pat_re = rf"\y{safe}\y"
            body_filter = (
                MeetingSession.raw_transcript.op("~*")(pat_re)
                | MeetingSession.raw_summary.op("~*")(pat_re)
                | MeetingSession.processed_decisions.op("~*")(pat_re)
                | MeetingSession.processed_agreements.op("~*")(pat_re)
                | MeetingSession.processed_risks.op("~*")(pat_re)
            )
        else:
            # ILIKE con wildcards — buscamos `Camila`, `Camila,`, `con
            # Camila`, etc. Case-insensitive.
            pattern = f"%{name}%"
            body_filter = (
                (MeetingSession.raw_transcript.ilike(pattern))
                | (MeetingSession.raw_summary.ilike(pattern))
                | (MeetingSession.processed_decisions.ilike(pattern))
                | (MeetingSession.processed_agreements.ilike(pattern))
                | (MeetingSession.processed_risks.ilike(pattern))
            )
        q = (
            select(MeetingSession)
            .where(MeetingSession.tenant_id == tenant_id)
            .where(body_filter)
        )
        if project_id:
            q = q.where(MeetingSession.project_id == project_id)
        if scope_project_id or scope_title:
            # SCOPE "X en Y": la pregunta menciona un proyecto/cliente →
            # restringimos a SUS sesiones (por project_id o título). Evita
            # que "TR en ANH" traiga sesiones de First Class.
            from sqlmodel import or_ as _or
            scope_conds = []
            if scope_project_id:
                scope_conds.append(MeetingSession.project_id == scope_project_id)
            if scope_title:
                scope_conds.append(MeetingSession.title.ilike(f"%{scope_title}%"))
            q = q.where(_or(*scope_conds))
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

            # En modo howtech enriquecemos con ventanas sobre los conceptos
            # técnicos detectados en la pregunta (eventos, arquitectura,
            # módulos...). Estos fragmentos son los que el LLM necesita para
            # NO responder genérico. Si el concepto no aparece en una
            # sesión, simplemente no se añade — sin fallback.
            if howtech_concepts:
                extra_blocks: list[str] = []
                for concept in howtech_concepts:
                    for field in (s.raw_transcript, s.raw_summary,
                                  s.processed_decisions, s.processed_agreements):
                        block = _name_snippets_all(field or "", concept, window=900, max_occ=2)
                        if block:
                            extra_blocks.append(f"[Detalle técnico — {concept}]\n{block}")
                            break  # un campo por concepto basta
                if extra_blocks:
                    snippet = snippet + "\n---\n" + "\n---\n".join(extra_blocks)

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
    aparece. Siglas ≤3 chars: solo palabra aislada (word-boundary)."""
    if not text or not name:
        return ""
    if len(name.strip()) <= 3:
        import re as _re
        m = _re.search(rf"\b{_re.escape(name.strip())}\b", text, _re.IGNORECASE)
        if not m:
            return ""
        idx = m.start()
    else:
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


def _parse_summarize_last_n(q: str) -> Optional[int]:
    """Detecta «resume las (últimas) N sesiones (de X)» y devuelve N.
    None si la pregunta no es de ese tipo. Default 3 con «últimas», 5 sin
    número explícito."""
    import re as _re
    low = (q or "").lower()
    if not _re.search(r"(resum|s[ií]ntesis|sintetiz)", low):
        return None
    if not _re.search(r"(sesion|sesión|reunion|reunión|meeting)", low):
        return None
    m = _re.search(r"\b(\d{1,2})\b", low)
    if m:
        return max(1, min(int(m.group(1)), 8))
    words = {"una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6}
    for w, v in words.items():
        if _re.search(rf"\b{w}\b", low):
            return v
    return 3 if _re.search(r"(últim|ultim|recient)", low) else 5


def _load_sessions_digest(
    db: "Session",
    tenant_id: int,
    topic_terms: list[str],
    n: int,
) -> list[dict]:
    """Dossier RICO de las N sesiones más recientes que tocan el tema.

    Selección: sesiones cuyo TÍTULO o PROYECTO matchea algún topic_term
    (o cuyo transcript lo menciona como palabra, para siglas), ordenadas
    por fecha DESC, exactamente N. Cada una produce UN chunk denso con
    resumen + decisiones + acuerdos + riesgos + tareas reales — el
    material que un analista usaría para resumir en detalle, no la frase
    genérica del RAG."""
    from sqlmodel import or_ as _or
    from models import MeetingSession, Project
    import re as _re

    q = (
        select(MeetingSession)
        .where(MeetingSession.tenant_id == tenant_id)
        .where(MeetingSession.status != "archived")
    )
    if topic_terms:
        conds = []
        for t in topic_terms:
            pat = f"%{t}%"
            conds.append(MeetingSession.title.ilike(pat))
            if len(t.strip()) <= 3:
                conds.append(MeetingSession.raw_transcript.op("~*")(rf"\y{_re.escape(t.strip())}\y"))
            else:
                conds.append(MeetingSession.raw_transcript.ilike(pat))
            pids = db.exec(
                select(Project.id)
                .where(Project.tenant_id == tenant_id)
                .where(Project.name.ilike(pat))
            ).all()
            if pids:
                conds.append(MeetingSession.project_id.in_(list(pids)))
        q = q.where(_or(*conds))
    rows = db.exec(q).all()
    # Orden por fecha real DESC (ISO string ordena bien; fallback id).
    rows.sort(key=lambda s: ((s.date or ""), s.id or 0), reverse=True)
    rows = rows[:n]

    out: list[dict] = []
    proj_cache: dict[int, str] = {}
    for s in rows:
        if not s.id:
            continue
        parts: list[str] = []
        if (s.raw_summary or "").strip():
            parts.append(f"[Resumen ejecutivo]\n{s.raw_summary.strip()[:2400]}")
        if (s.processed_decisions or "").strip():
            parts.append(f"[Decisiones]\n{s.processed_decisions.strip()[:1400]}")
        if (s.processed_agreements or "").strip():
            parts.append(f"[Acuerdos]\n{s.processed_agreements.strip()[:1400]}")
        if (s.processed_risks or "").strip():
            parts.append(f"[Riesgos]\n{s.processed_risks.strip()[:800]}")
        tasks = db.exec(
            select(ActionItemRow).where(ActionItemRow.session_id == s.id).limit(15)
        ).all()
        if tasks:
            tl = "\n".join(
                f"· [{t.status or 'pending'}] {t.title} — {t.owner_name or 'sin responsable'}"
                + (f" (vence {t.due_date})" if t.due_date else "")
                for t in tasks
            )
            parts.append(f"[Tareas de esta sesión]\n{tl}")
        if not parts and (s.raw_transcript or "").strip():
            parts.append(f"[Transcripción (inicio)]\n{s.raw_transcript.strip()[:2000]}")
        if not parts:
            continue
        proj_name = ""
        if s.project_id:
            if s.project_id not in proj_cache:
                p = db.get(Project, s.project_id)
                proj_cache[s.project_id] = (p.name if p else "") or ""
            proj_name = proj_cache[s.project_id]
        out.append({
            "session_id": s.id,
            "kind": "session_digest",
            "content": "\n\n".join(parts),
            "distance": 0.0,
            "session_title": s.title or "",
            "session_date": s.date or "",
            "project_name": proj_name,
        })
    return out


def _is_tasks_question(q: str) -> bool:
    """True si la pregunta es sobre tareas/compromisos/pendientes/quién
    debe hacer qué. Estas preguntas necesitan la tabla ActionItem (fuente
    de verdad curada) además de los transcripts."""
    qn = (q or "").lower()
    triggers = (
        "pendiente", "tarea", "compromiso", "responsable", "encargad",
        "asignad", "quién debe", "quien debe", "quién tiene que",
        "quien tiene que", "por hacer", "sin hacer", "sin resolver",
        "action item", "to do", "todo list", "deadline", "fecha límite",
        "fecha limite", "entregable",
    )
    return any(t in qn for t in triggers)


def _load_action_items_context(
    db: "Session", tenant_id: int, project_id: Optional[int], limit: int = 50,
) -> str:
    """Bloque compacto con los ActionItems REALES de la BD (no inferidos
    del transcript). Incluye título, responsable, estado, fecha límite y
    sesión origen. El LLM lo usa como fuente autoritativa para preguntas
    de pendientes/compromisos — los transcripts complementan el contexto
    pero el estado vigente vive aquí."""
    from models import MeetingSession
    q = (
        select(ActionItemRow, MeetingSession)
        .join(MeetingSession, MeetingSession.id == ActionItemRow.session_id)
        .where(ActionItemRow.tenant_id == tenant_id)
    )
    if project_id:
        q = q.where(MeetingSession.project_id == project_id)
    q = q.order_by(ActionItemRow.id.desc()).limit(limit * 2)
    rows = db.exec(q).all()
    if not rows:
        return ""
    # Pendientes/bloqueadas PRIMERO — son las que la pregunta busca. Las
    # done/cancelled van al final y solo si queda cupo (dan contexto de
    # qué ya se cerró).
    _open = [r for r in rows if (r[0].status or "pending") in ("pending", "blocked")]
    _closed = [r for r in rows if r not in _open]
    rows = (_open + _closed)[:limit]
    lines = [
        "=== TAREAS / ACTION ITEMS (tabla curada — fuente autoritativa "
        "del ESTADO ACTUAL; si contradice al transcript, manda esta) ==="
    ]
    for ai, ms in rows:
        status = (ai.status or "pending").strip()
        owner = (ai.owner_name or "sin responsable").strip()
        due = (ai.due_date or "").strip()
        line = (
            f"· [{status}] {ai.title or '(sin título)'} — resp: {owner}"
            + (f" — vence: {due}" if due else "")
            + f" — sesión #{ai.session_id} «{(ms.title or '')[:50]}»"
        )
        lines.append(line)
    return "\n".join(lines)


def _fmt_session_date(raw: object) -> str:
    """Fecha legible ('07 Jun 2026') desde epoch-ms, epoch-s o ISO. El LLM
    COPIA lo que ve en el contexto: si le damos ISO crudo, cita ISO crudo
    en la respuesta. Formateamos en el único choke-point (context builder)
    para que todas las citas salgan bonitas."""
    if not raw:
        return ""
    from datetime import datetime as _dt
    s = str(raw).strip()
    try:
        if s.replace(".", "").isdigit():
            ts = float(s)
            if ts > 10**12:
                ts /= 1000
            return _dt.fromtimestamp(ts).strftime("%d %b %Y")
        return _dt.fromisoformat(s.replace("Z", "+00:00")).strftime("%d %b %Y")
    except Exception:
        return s[:10]


def _build_context(chunks: list[dict]) -> str:
    """Construye el contexto que recibe el LLM. Cada bloque incluye el
    metadato de la sesión (id, título, fecha, proyecto) Y el contenido
    del chunk. Esto es crítico para preguntas "meta" como "qué sitios se
    visitaron" o "qué clientes hubo este mes" — la respuesta vive en los
    TÍTULOS, no en los chunks de summary/decisions."""
    blocks = []
    for c in chunks:
        # session_digest = dossier completo para resúmenes por sesión —
        # necesita mucho más espacio que un chunk RAG normal.
        _cap = 7000 if c.get("kind") == "session_digest" else 1200
        snippet = (c.get("content") or "")[:_cap]
        title = c.get("session_title") or ""
        date = _fmt_session_date(c.get("session_date"))
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
            "date": _fmt_session_date(c.get("session_date")),
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

    # QUERY UNDERSTANDING en paralelo con el vector search: el analizador
    # LLM (8B, ~1s) corre mientras pgvector busca — latencia extra ≈ 0.
    import asyncio as _asyncio
    from models import Project as _Project
    _proj_names = [
        p.name for p in db.exec(
            select(_Project).where(_Project.tenant_id == tenant.id).limit(15)
        ).all() if p.name
    ]
    raw_chunks, query_analysis = await _asyncio.gather(
        search_similar(
            db, q,
            top_k=max(min(payload.top_k, 20), 1),
            project_id=payload.project_id,
            session_ids=sids_filter,
            # CRÍTICO multi-tenant: restringe la búsqueda RAG a las sesiones
            # del tenant del usuario. Sin esto, una pregunta de la empresa A
            # podía recuperar fragmentos de actas de la empresa B (fuga de
            # información entre clientes).
            tenant_id=tenant.id,
        ),
        _llm_analyze_query(q, _proj_names, prior_turns=payload.prior_turns),
    )
    llm_entities: list[str] = (query_analysis or {}).get("entities") or []
    llm_search_terms: list[str] = (query_analysis or {}).get("search_terms") or []
    llm_reformulated: str = (query_analysis or {}).get("reformulated_query") or ""
    if query_analysis:
        logger.info(
            "ask: query-analyzer entities=%s terms=%s reform=%r",
            llm_entities, llm_search_terms, llm_reformulated[:80],
        )

    # 2DA BÚSQUEDA VECTORIAL con la query reformulada — recupera chunks
    # que la redacción original no alcanza (paráfrasis, sinónimos). Merge
    # por (session_id, kind) quedándose con la menor distancia.
    if llm_reformulated and llm_reformulated.lower() != q.lower():
        try:
            reform_chunks = await search_similar(
                db, llm_reformulated,
                top_k=10,
                project_id=payload.project_id,
                session_ids=sids_filter,
                tenant_id=tenant.id,
            )
            seen_rc = {(c.get("session_id"), c.get("kind")): i
                       for i, c in enumerate(raw_chunks)}
            for rc in reform_chunks:
                key = (rc.get("session_id"), rc.get("kind"))
                if key in seen_rc:
                    idx = seen_rc[key]
                    if rc["distance"] < raw_chunks[idx]["distance"]:
                        raw_chunks[idx] = rc
                else:
                    raw_chunks.append(rc)
            raw_chunks.sort(key=lambda c: c.get("distance", 1.0))
        except Exception as exc:  # noqa: BLE001
            logger.warning("ask: reformulated search falló (%s)", exc)

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
    howtech_mode = _is_howtech_question(q)
    # Whatis EXCLUYE yesno: «¿qué es TR?» es definicional, no relacional.
    # Sin esto el patrón verbal «es» + «?» disparaba yesno y la respuesta
    # arrancaba con un «Sí.» sin sentido.
    yesno_mode = _is_yesno_question(q) and not whatis_mode
    howtech_concepts = _extract_tech_concepts(q) if howtech_mode else []

    # _extract_proper_nouns solo agarra palabras con mayúscula inicial.
    # Si el user escribe "first class" en minúsculas se pierde y el
    # deep loader nunca dispara. Como complemento case-insensitive
    # buscamos nombres reales de project/contact del tenant DENTRO de
    # la pregunta y los añadimos como nombres propios.
    known_entities = _extract_known_entities(db, tenant.id, q)
    for entity in known_entities:
        if entity not in proper_nouns:
            proper_nouns.append(entity)
    if known_entities:
        logger.info("ask: known_entities (case-insensitive) → %s", known_entities)

    # Fallback adicional: frases del query que matchean session.title.
    # Cubre el caso CLIENTE no-proyecto: "first class" no es project.name
    # pero está en títulos de 13 sesiones del tenant. Sin esto el deep
    # loader queda vacío y la respuesta cae a genérico.
    title_phrases = _extract_session_title_phrases(db, tenant.id, q)
    for phrase in title_phrases:
        if phrase not in proper_nouns and phrase.title() not in proper_nouns:
            proper_nouns.append(phrase)
    if title_phrases:
        logger.info("ask: session_title_phrases → %s", title_phrases)

    # Entidades detectadas por el query-analyzer LLM — cubren lo que las
    # heurísticas de mayúsculas/títulos no ven (apodos, productos escritos
    # raro, personas implícitas).
    pn_low = {n.lower() for n in proper_nouns}
    for ent in llm_entities:
        if ent.lower() not in pn_low:
            proper_nouns.append(ent)
            pn_low.add(ent.lower())

    # SCOPE "X en <Proyecto>": si la pregunta menciona EXACTAMENTE un
    # proyecto del tenant y no viene project_id del frontend, la búsqueda
    # literal se restringe a las sesiones de ese proyecto (por project_id
    # o por título). Caso reportado: «¿qué es TR en ANH?» traía sesiones
    # de First Class porque TR se buscaba en TODO el tenant.
    scoped_project_id: Optional[int] = None
    scope_title_term: Optional[str] = None
    if not payload.project_id:
        from models import Project as _ScopeProject
        _matched_scope: list[tuple[str, int]] = []
        for ent in (known_entities or []):
            row = db.exec(
                select(_ScopeProject)
                .where(_ScopeProject.tenant_id == tenant.id)
                .where(_ScopeProject.name.ilike(ent))
            ).first()
            if row and row.id:
                _matched_scope.append((ent, row.id))
        if len(_matched_scope) == 1:
            scope_title_term, scoped_project_id = _matched_scope[0]
            logger.info(
                "ask: scope detectado → proyecto «%s» (id=%s)",
                scope_title_term, scoped_project_id,
            )
    if proper_nouns:
        # Modo whatis ("qué es X") trae snippets más grandes y multiples
        # ocurrencias por sesión — necesario para extraer una definición
        # del transcript en lugar de solo enumerar topics.
        # Modo howtech ("cómo se maneja X", "qué arquitectura tiene X")
        # además rastrea conceptos técnicos del catálogo (eventos, módulos,
        # arquitectura, …) en transcripts para que el LLM tenga material
        # CONCRETO, no genérico.
        kw_chunks = _load_keyword_matches(
            db, tenant.id, payload.project_id, proper_nouns,
            limit_per_name=8 if howtech_mode else 4,
            whatis_mode=whatis_mode or howtech_mode,
            howtech_concepts=howtech_concepts,
            scope_project_id=scoped_project_id,
            scope_title=scope_title_term,
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

    # Modo howtech: si el nombre propio matchea un proyecto del tenant,
    # vertimos el dossier completo (resumen + decisiones + acuerdos +
    # riesgos + snippets de transcript sobre conceptos técnicos) de
    # TODAS las sesiones de ese proyecto + las que tengan el nombre
    # en el título. Es lo que necesita el LLM para sintetizar una
    # respuesta arquitectónica real.
    deep_chunks: list[dict] = []
    if howtech_mode and proper_nouns:
        # limit_sessions=6: con filtro por concepto en body, traer 6
        # sesiones realmente relevantes pesa más que 20 sesiones diluidas.
        deep_chunks = _load_project_deep_context(
            db, tenant.id, proper_nouns,
            limit_sessions=6,
            howtech_concepts=howtech_concepts,
        )
        seen = {(c["session_id"], c.get("kind")) for c in chunks}
        for dc in deep_chunks:
            key = (dc["session_id"], dc.get("kind"))
            if key not in seen:
                chunks.append(dc)
                seen.add(key)
        logger.info(
            "ask: howtech project_deep → +%s chunks (sessions únicos: %s)",
            len(deep_chunks),
            len({c["session_id"] for c in deep_chunks}),
        )

    # Modo yes-no: la pregunta es relacional ("X maneja Y?"). El LLM
    # debe responder con EVIDENCIA TEXTUAL del transcript, no asumir
    # relación positiva por co-ocurrencia. Cargamos snippets que
    # contengan AMBOS términos juntos.
    yesno_chunks: list[dict] = []
    if yesno_mode and proper_nouns:
        # Extra terms: cualquier palabra capitalizada o frase específica
        # que no esté en proper_nouns. Para "First Class maneja la
        # boletería de Arena USC?" → proper_nouns=[first class, arena usc]
        # ya cubre subject+object; pero añadimos palabras clave del
        # query como "boletería", "evento", etc.
        import re as _re
        extra = [
            w.lower() for w in _re.findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]{4,}", q)
            if w.lower() not in _PHRASE_STOPWORDS
            and not any(w.lower() in n.lower() for n in proper_nouns)
        ][:6]
        # Expansion por sinonimos del dominio: "boletería" -> tiquetera,
        # etiquetera, mi boleta, ticket, etc. Sin esto perdemos el
        # transcript con los nombres reales.
        extra = _expand_with_synonyms(extra)
        yesno_chunks = _load_yesno_evidence(
            db, tenant.id, proper_nouns, extra,
            limit_sessions=16,
            window=2500,
            max_occ=8,
        )
        seen = {(c["session_id"], c.get("kind")) for c in chunks}
        for yc in yesno_chunks:
            key = (yc["session_id"], yc.get("kind"))
            if key not in seen:
                chunks.append(yc)
                seen.add(key)
        logger.info(
            "ask: yesno_mode terms=%s + %s → +%s evidence chunks",
            proper_nouns, extra, len(yesno_chunks),
        )

    # DETECCIÓN DE TÉRMINOS DE DOMINIO en la pregunta. Si la pregunta usa
    # un concepto del vocabulario (homologar, BEPS, parametrización, sede,
    # boletería…) — aunque esté en minúsculas y NO sea nombre propio —
    # SIEMPRE disparamos la búsqueda literal expandida. Esto permite
    # encontrar "el mismo concepto dicho con otras palabras": el usuario
    # pregunta "homologar" y el transcript dice "igual a la sede" / "como
    # está en sede" / "nada nuevo", todos en el mismo cluster de sinónimos.
    qlow_full = q.lower()
    concept_specific_terms: list[str] = []  # frases-concepto que SÍ discriminan
    domain_seed_terms: list[str] = []
    for _key in _DOMAIN_SYNONYMS.keys():
        if _key.lower() in qlow_full and _key.lower() not in [t.lower() for t in domain_seed_terms]:
            domain_seed_terms.append(_key)

    # CONTENT-WORD FALLBACK: cuando pocas sesiones únicas encontradas
    # (<6) O cuando la pregunta tiene términos de dominio, buscar por
    # palabras de contenido de la pregunta (≥4 chars, no-stopword) que NO
    # estén ya cubiertas por proper_nouns. Cubre términos como
    # "parametrización", "BEPS", "sede", "homologar" que el vector search
    # pierde y proper_noun detection no captura (todo-minúsculas o
    # acrónimos sin equivalente en proyecto/contacto del tenant).
    unique_session_count = len({c["session_id"] for c in chunks})
    if unique_session_count < 6 or domain_seed_terms or llm_search_terms:
        import re as _re
        _content_sw = frozenset([
            "para", "como", "esta", "este", "estos", "estas", "tiene", "hay",
            "cual", "cuales", "cuando", "donde", "quien", "porque", "aunque",
            "algo", "acten", "sobre", "hacer", "hacia", "desde", "maneja",
            "manejo", "dónde", "cómo", "qué", "quién", "cuándo",
        ])
        covered_low = {n.lower() for n in proper_nouns}
        # Tokenizamos pregunta + query resuelta por el analyzer: en
        # seguimientos ambiguos («eso», «ahí») las palabras útiles viven
        # en la reformulación, no en la pregunta cruda.
        _token_src = q + (" " + llm_reformulated if llm_reformulated else "")
        cw_raw = [
            w.lower()
            for w in _re.findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]{4,}", _token_src)
            if w.lower() not in _content_sw
            and w.lower() not in _PHRASE_STOPWORDS
            and w.lower() not in covered_low
        ]
        # Los términos de dominio detectados van PRIMERO (incluye claves
        # multi-palabra como "sede electrónica" que el tokenizador parte).
        cw_raw = [t.lower() for t in domain_seed_terms] + cw_raw
        # Dedup preservando orden
        cw_seen: set[str] = set()
        content_words: list[str] = []
        for w in cw_raw:
            if w not in cw_seen:
                cw_seen.add(w)
                content_words.append(w)
        content_words = content_words[:8]
        if content_words:
            expanded_cw = _expand_with_synonyms(content_words)
            # PRECISIÓN > RECALL. Términos GENÉRICOS (sede, móvil, app,
            # plataforma, BEPS, pensiones…) matchean CASI TODAS las sesiones
            # de un tenant cuyo proyecto entero ES eso → 40 sesiones de
            # boilerplate y la frase específica se diluye. Separamos:
            #   · ESPECÍFICOS = frases multi-palabra (≥2 palabras) o singles
            #     curados (homologar, parametrización) → discriminan.
            #   · GENÉRICOS = se descartan como semilla de búsqueda.
            # Si hay específicos, buscamos SOLO con ellos. Esto reproduce la
            # investigación manual: buscar "igual a la sede"/"homologar",
            # no "sede"/"app".
            _GENERIC_SEARCH_TERMS = frozenset([
                "sede", "móvil", "movil", "app", "aplicación", "aplicacion",
                "plataforma", "portal", "mobile", "beps", "pensiones",
                "beneficios", "mensaje", "mensajes", "configuración",
                "configuracion", "parámetros", "parametros", "trámite",
                "tramite", "trámites", "tramites", "sede electrónica",
                "sede electronica", "aplicación móvil", "aplicacion movil",
                "app móvil", "beneficios económicos periódicos",
                # Vocabulario omnipresente en actas — matchea TODO, no
                # discrimina nada (aparece en casi cualquier reunión):
                "sesión", "sesion", "sesiones", "reunión", "reunion",
                "reuniones", "equipo", "equipos", "compromiso",
                "compromisos", "pendiente", "pendientes", "problema",
                "problemas", "falla", "fallas", "error", "errores",
                "solución", "soluciones", "resolución", "revisar",
                "revisión", "revision", "tarea", "tareas", "tema", "temas",
                "proyecto", "proyectos", "cliente", "clientes", "avance",
                "avances", "seguimiento", "igual a", "lo mismo que",
                "como está en",
            ])
            _SPECIFIC_SINGLES = frozenset([
                "homologar", "homologación", "homologacion", "homologa",
                "parametrización", "parametrizacion", "parametrizable",
                "parametrizar", "parametrizada", "equivalente", "espejo",
                # Alias de la entidad renombrada (boletería First Class):
                "etiquetera", "etiqueta", "taquilla", "taquillera",
                "tiquetera",
            ])
            specific_terms = [
                t for t in expanded_cw
                if t.lower() not in _GENERIC_SEARCH_TERMS
                and (len(t.split()) >= 2 or t.lower() in _SPECIFIC_SINGLES)
            ]
            # Términos del query-analyzer LLM: curados por-pregunta, se
            # tratan como específicos (el prompt ya les prohíbe genéricos;
            # el filtro de abajo es doble seguro).
            st_low = {s.lower() for s in specific_terms}
            for t in llm_search_terms:
                tl = t.lower()
                if tl not in _GENERIC_SEARCH_TERMS and tl not in st_low:
                    specific_terms.append(t)
                    st_low.add(tl)
            non_generic = [
                t for t in expanded_cw if t.lower() not in _GENERIC_SEARCH_TERMS
            ]
            # Preferencia: específicos > no-genéricos > (nada). Nunca solo
            # genéricos.
            search_terms = specific_terms or non_generic
            search_terms = search_terms[:12]
            concept_specific_terms = list(specific_terms)
            if search_terms:
                cw_chunks = _load_keyword_matches(
                    db, tenant.id, payload.project_id, search_terms,
                    limit_per_name=6,
                    # SIEMPRE multi-ocurrencia: una sesión puede mencionar el
                    # concepto varias veces (Lady dice "homologar como está en
                    # sede" y Felipe dice "igual a Sede" en la MISMA sesión).
                    # Con snippet único se pierde la cita más fuerte.
                    whatis_mode=True,
                    howtech_concepts=howtech_concepts,
                    scope_project_id=scoped_project_id,
                    scope_title=scope_title_term,
                )
                # RANKING por especificidad: cuántos términos-búsqueda
                # distintos aparecen en el contenido de cada sesión. Las
                # sesiones que mencionan MÁS de las frases concepto van
                # primero; cap a 8 sesiones para no diluir el contexto.
                by_sess: dict[int, list[dict]] = {}
                for kc in cw_chunks:
                    by_sess.setdefault(kc["session_id"], []).append(kc)
                def _spec_score(sid: int) -> int:
                    blob = " ".join(
                        (c.get("content") or "").lower() for c in by_sess[sid]
                    )
                    return sum(1 for t in search_terms if t.lower() in blob)
                ranked_sids = sorted(
                    by_sess.keys(), key=lambda s: (-_spec_score(s), -s)
                )[:10]
                seen_cw = {(c["session_id"], c.get("kind")) for c in chunks}
                added_cw = 0
                for sid in ranked_sids:
                    for kc in by_sess[sid]:
                        key = (kc["session_id"], kc.get("kind"))
                        if key not in seen_cw:
                            chunks.append(kc)
                            seen_cw.add(key)
                            added_cw += 1
                logger.info(
                    "ask: content_word_fallback specific=%s terms=%s → +%s chunks "
                    "(top %s sessions de %s candidatas)",
                    bool(specific_terms), search_terms, added_cw,
                    len(ranked_sids), len(by_sess),
                )

    # PODA DE RUIDO: cuando la pregunta es sobre un CONCEPTO específico y
    # encontramos evidencia literal de ese concepto, los chunks RAG
    # genéricos (vector search) que NO mencionan ninguna frase-concepto son
    # ruido — el LLM termina listándolos como «se discutió la app y la
    # plataforma». Los descartamos, conservando: evidencia/keyword/deep
    # (distance==0) y cualquier chunk que SÍ contenga una frase-concepto.
    if concept_specific_terms:
        cl_terms = [t.lower() for t in concept_specific_terms]
        pruned = [
            c for c in chunks
            if c.get("distance", 1.0) == 0.0
            or any(t in (c.get("content") or "").lower() for t in cl_terms)
        ]
        # Seguridad: si la poda deja muy poco, conservar original.
        if len({c["session_id"] for c in pruned}) >= 2:
            removed = len(chunks) - len(pruned)
            chunks = pruned
            logger.info("ask: concept-prune removed %s noise chunks", removed)

    # MODO «RESUME LAS ÚLTIMAS N SESIONES (de X)»: selección EXACTA de N
    # sesiones por fecha DESC + dossier rico por sesión (resumen +
    # decisiones + acuerdos + riesgos + tareas). REEMPLAZA el contexto:
    # la respuesta debe hablar SOLO de esas N, con detalle de analista,
    # no de lo que el vector search haya colado.
    summarize_n = _parse_summarize_last_n(q)
    # «EN LA ÚLTIMA REUNIÓN de X, …»: pregunta anclada a LA sesión más
    # reciente del tema. Selección determinística por fecha (no por
    # similitud vectorial, que se equivoca de sesión) + la respuesta se
    # limita a esa única sesión.
    latest_mode = False
    if not summarize_n:
        import re as _re_latest
        if _re_latest.search(
            r"(últim[ao]|ultim[ao]|last|más reciente|mas reciente)\s+"
            r"(reuni[oó]n|sesi[oó]n|meeting)",
            q.lower(),
        ):
            summarize_n = 1
            latest_mode = True
    summarize_mode = False
    if summarize_n:
        _topic = [t for t in proper_nouns if len(t.strip()) >= 2][:4]
        digest_chunks = _load_sessions_digest(db, tenant.id, _topic, summarize_n)
        if digest_chunks:
            chunks = digest_chunks
            summarize_mode = True
            logger.info(
                "ask: summarize_mode n=%s topic=%s → %s sesiones: %s",
                summarize_n, _topic, len(digest_chunks),
                [c["session_id"] for c in digest_chunks],
            )

    # PODA POR SCOPE: la pregunta es sobre UN proyecto concreto («… en
    # ANH») → descartamos chunks de OTROS proyectos que el vector search
    # coló (First Class, etc.). Mantener solo: sesiones del proyecto
    # scope (por nombre de proyecto o título) y evidencia literal.
    if scope_title_term:
        _sc = scope_title_term.lower()
        scoped = [
            c for c in chunks
            if _sc in (c.get("project_name") or "").lower()
            or _sc in (c.get("session_title") or "").lower()
        ]
        if len({c["session_id"] for c in scoped}) >= 2:
            removed_sc = len(chunks) - len(scoped)
            chunks = scoped
            logger.info(
                "ask: scope-prune «%s» removió %s chunks de otros proyectos",
                scope_title_term, removed_sc,
            )

    # RED DE SEGURIDAD anti-dilución: nunca pasar más de MAX_CTX_SESSIONS
    # sesiones distintas al LLM. Demasiadas sesiones → el modelo produce un
    # listado boilerplate ("se discutió la app y la plataforma") y pierde la
    # evidencia específica. Prioridad de retención:
    #   1) chunks distance==0 (evidencia literal / keyword / deep / recency)
    #   2) chunks RAG ordenados por distancia ascendente (más relevantes)
    MAX_CTX_SESSIONS = 16
    if len({c["session_id"] for c in chunks}) > MAX_CTX_SESSIONS:
        prioritized = sorted(
            chunks, key=lambda c: (c.get("distance", 1.0))
        )
        kept: list[dict] = []
        kept_sids: set[int] = set()
        # Primera pasada: garantizar 1 chunk por sesión hasta el cap.
        for c in prioritized:
            sid = c["session_id"]
            if sid in kept_sids:
                continue
            if len(kept_sids) >= MAX_CTX_SESSIONS:
                break
            kept.append(c)
            kept_sids.add(sid)
        # Segunda pasada: re-agregar chunks extra de las sesiones retenidas
        # (un misma sesión puede tener evidencia + resumen + decisiones).
        for c in prioritized:
            if c in kept:
                continue
            if c["session_id"] in kept_sids:
                kept.append(c)
        chunks = kept
        logger.info("ask: context capped to %s sessions (de muchas)", len(kept_sids))

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

    n_evidence = len([
        c for c in chunks
        if c.get("kind") in ("yesno_evidence", "project_deep")
    ])
    # Sesiones únicas desde keyword fallback (no contadas en n_evidence
    # pero igual deben escalar el piso de respuesta).
    n_keyword_sessions = len({
        c["session_id"] for c in chunks
        if (c.get("kind") or "").startswith("keyword:")
    })
    # Para queries temáticas sin intent especial pero con muchas sesiones
    # keyword: escalamos el piso igual que howtech/whatis.
    effective_evidence = n_evidence if (yesno_mode or howtech_mode or whatis_mode) else max(n_evidence, n_keyword_sessions)
    # Pisos de longitud cuando hay material denso. Markdown habilitado
    # para enlaces a sesiones + párrafos justificados.
    if yesno_mode and n_evidence >= 6:
        intro_length_hint = (
            f"Markdown con `\\n\\n` entre párrafos. Respuesta SÍ/NO en "
            f"1ra frase. Luego UN párrafo POR CADA ÁNGULO DISTINTO que "
            f"aporten las {n_evidence} sesiones (regla general / distinción "
            f"/ caso específico / pendiente / riesgo) — típicamente 4-6 "
            f"párrafos. Cada párrafo aporta info NUEVA; PROHIBIDO repetir o "
            f"parafrasear una frase ya dicha. Si no hay 6 ángulos distintos, "
            f"escribe MENOS párrafos — mejor 4 densos que 6 repetidos. NO "
            f"cierres con un «En resumen» que repita lo anterior. Citas "
            f"literales del transcript con atribución «<speaker> en "
            f"<sesión>»."
        )
    elif yesno_mode and n_evidence >= 3:
        intro_length_hint = (
            f"Markdown con `\\n\\n` entre párrafos. SÍ/NO en 1ra frase. "
            f"Luego 4-6 párrafos integrando las {n_evidence} sesiones "
            f"con citas y atribución sesión+fecha."
        )
    elif yesno_mode:
        intro_length_hint = (
            "Markdown. Respuesta SÍ/NO en 1ra frase + 2-4 frases que "
            "contextualicen con sesión+fecha."
        )
    elif (howtech_mode or whatis_mode) and n_evidence >= 6:
        intro_length_hint = (
            f"Markdown con `\\n\\n`. LA PRIMERA FRASE ES LA RESPUESTA "
            f"DIRECTA (la definición/explicación, sin preámbulos tipo "
            f"«las sesiones registradas corresponden a…»). Luego UN "
            f"párrafo por ÁNGULO DISTINTO de las {n_evidence} sesiones "
            f"(típicamente 4-6), con nombres concretos (endpoints, "
            f"tablas, módulos, eventos, jobs) y atribución sesión+fecha. "
            f"Cada párrafo aporta info NUEVA; PROHIBIDO repetir frases o "
            f"cerrar con «En resumen» que reitere."
        )
    elif (howtech_mode or whatis_mode) and n_evidence >= 3:
        intro_length_hint = (
            f"Markdown con `\\n\\n`. LA PRIMERA FRASE ES LA RESPUESTA "
            f"DIRECTA (definición/explicación, sin preámbulos). Luego "
            f"3-5 párrafos integrando las {n_evidence} sesiones con "
            f"nombres concretos y atribución sesión+fecha."
        )
    elif howtech_mode or whatis_mode:
        intro_length_hint = (
            "Markdown. LA PRIMERA FRASE ES LA RESPUESTA DIRECTA (la "
            "definición, sin preámbulos meta sobre las sesiones). Luego "
            "el detalle con nombres concretos (4-10 frases)."
        )
    elif effective_evidence >= 6:
        intro_length_hint = (
            f"Markdown con `\\n\\n` entre párrafos. UN párrafo por ÁNGULO "
            f"DISTINTO de las {effective_evidence} sesiones (típicamente "
            f"4-6), citando sesión+fecha y un detalle concreto NUEVO de "
            f"cada una. PROHIBIDO repetir frases o cerrar con «En resumen» "
            f"que reitere. Mejor 4 párrafos densos que 6 repetidos."
        )
    elif effective_evidence >= 3:
        intro_length_hint = (
            f"Markdown con `\\n\\n`. 3-5 párrafos integrando las "
            f"{effective_evidence} sesiones con atribución sesión+fecha."
        )
    else:
        intro_length_hint = "Markdown. Respuesta breve y directa."
    # OVERRIDE resumen-por-sesión: manda sobre cualquier otro hint.
    if summarize_mode and latest_mode:
        _t0 = chunks[0] if chunks else {}
        intro_length_hint = (
            f"La sesión MÁS RECIENTE sobre el tema es "
            f"«{_t0.get('session_title','')}» "
            f"({_fmt_session_date(_t0.get('session_date'))}) — es LA "
            f"única fuente para responder. Markdown. 1ra frase: "
            f"identifica esa sesión y responde DIRECTO la pregunta. Si "
            f"preguntan qué debe hacer una PERSONA, la lista [Tareas de "
            f"esta sesión] es la fuente AUTORITATIVA: enumera TODAS las "
            f"tareas asignadas a esa persona (título + estado), y "
            f"complementa con el contexto del dossier sobre POR QUÉ se "
            f"le asignaron. PROHIBIDO citar otras sesiones."
        )
    elif summarize_mode:
        n_dig = len({c["session_id"] for c in chunks})
        intro_length_hint = (
            f"Markdown con `\\n\\n`. RESUMEN DETALLADO de EXACTAMENTE "
            f"{n_dig} sesiones, en orden cronológico del más reciente al "
            f"más antiguo. UN BLOQUE POR SESIÓN: primero una línea "
            f"`### «título» (fecha)`, luego MÍNIMO 3 PÁRRAFOS y ~120 "
            f"palabras POR SESIÓN: (1) párrafo de temas centrales con "
            f"los detalles concretos discutidos (nombres, módulos, "
            f"cifras, flujos); (2) párrafo enumerando las decisiones y "
            f"acuerdos — LISTA las 3-5 principales del dossier, no "
            f"resumas en una frase; (3) párrafo de compromisos/tareas "
            f"con responsable POR NOMBRE y riesgos/bloqueos. Exprime "
            f"TODO el material de [Resumen ejecutivo]/[Decisiones]/"
            f"[Acuerdos]/[Tareas] de cada dossier — cada dato concreto "
            f"que esté ahí debe aparecer. NO frases genéricas de una "
            f"línea. PROHIBIDO mencionar sesiones fuera de estas {n_dig} "
            f"y PROHIBIDO repetir la misma frase entre sesiones."
        )
    system = (
        f"Eres el asistente de Acten. Tu salida DEBE ser un objeto JSON válido "
        f"con esta estructura EXACTA:\n"
        "{\n"
        f'  "intro": "<{intro_length_hint} en {out_lang_name}>",\n'
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
        "API. Actualmente está en fase de rediseño de la interfaz web.\"\n"
        "20. PREGUNTAS HOWTECH (\"cómo se manejan los eventos\", \"qué "
        "arquitectura tiene\", \"cómo está organizado el módulo\", \"qué "
        "estructura usa\", \"cómo se implementa\", \"qué stack\", \"cómo "
        "se conectan las APIs\", \"en qué base de datos se almacena\"): "
        "el user ya conoce X y quiere DETALLE TÉCNICO interno. Reglas:\n"
        "  a) PROHIBIDO repetir la definición del proyecto. El user no la "
        "pidió. Si tu respuesta empieza con \"X es una plataforma de…\" "
        "ESTÁS RESPONDIENDO MAL.\n"
        "  b) USA TODO el dossier disponible. Los bloques etiquetados "
        "`[Dossier de proyecto «X» — sesión completa]` traen el RESUMEN, "
        "DECISIONES, ACUERDOS y RIESGOS textuales de cada sesión del "
        "proyecto. Los bloques `[Detalle técnico — <concepto>]` traen "
        "ventanas de transcript sobre conceptos puntuales. LEE TODOS y "
        "extrae datos de TODOS antes de redactar.\n"
        "  c) Tu respuesta DEBE intentar cubrir TODAS las dimensiones "
        "arquitectónicas que aparezcan en el dossier:\n"
        "      • Endpoints / APIs (nombre, método, ruta, qué reciben)\n"
        "      • Base de datos / tablas / esquemas mencionados\n"
        "      • Componentes frontend (vistas, componentes, formularios)\n"
        "      • Componentes backend (servicios, módulos, jobs, colas)\n"
        "      • Flujo concreto paso-a-paso (origen → procesamiento → "
        "destino)\n"
        "      • Integraciones externas (webhooks, OAuth, terceros)\n"
        "      • Multi-tenant / autenticación / permisos mencionados\n"
        "    Para cada dimensión presente: NÓMBRALA explícitamente. Para "
        "cada dimensión NO presente: dilo (\"sobre <X> no encuentro "
        "detalle en las sesiones\"). No omitir = mejor que esconder huecos.\n"
        "  d) Cita TODAS las sesiones que aportaron material en "
        "`intro_source_sessions`, no solo la primera. Cuando uses una "
        "decisión concreta, agrégala como item en `decisions` con su "
        "source_session correspondiente.\n"
        "  e) Si tras leer TODO el dossier no hay material técnico real, "
        "dilo claramente: \"En las N sesiones del proyecto X no se "
        "discutió arquitectura concreta de <concepto>; solo se mencionó "
        "<lo que aparezca>\". Indica QUÉ TEMAS sí se cubrieron. NO "
        "INVENTES endpoints, tablas, ni nombres.\n"
        "  f) Longitud: el `intro` puede tener 4-8 frases cuando hay "
        "material técnico denso. NO te quedes en 2 frases genéricas — "
        "estás respondiendo una pregunta arquitectónica.\n"
        "Ejemplo malo: \"La gestión de eventos en First Class se maneja a "
        "través de una plataforma de gestión de eventos y tiquetera "
        "integrada.\" (tautológico, sin detalle, sin nombres)\n"
        "Ejemplo bueno: \"Los eventos en First Class viven en la tabla "
        "`ticket_event` (Postgres) y se crean por dos vías: (1) desde "
        "el frontend Angular vía POST `/api/events`, que valida el JWT y "
        "persiste con `tenant_id`; (2) desde la integración de tiquetera "
        "externa, que llega como webhook a `/api/webhook/tickets` con "
        "firma HMAC. El backend FastAPI los enriquece con datos del "
        "proyecto, encola un job en el cron service y dispara la "
        "notificación al canal del tenant. No encuentro detalle sobre "
        "el esquema exacto de la tabla ni sobre el formato de la firma. "
        "(Sesiones #61, #312, #401)\"\n"
        "21. SÍNTESIS MULTI-SESIÓN (obligatoria en howtech): consulta "
        "el `ÍNDICE CONCEPTO → SESIONES` al inicio del contexto. Si un "
        "concepto aparece en N>1 sesiones, DEBES leer las N y combinar "
        "lo que dice cada una. Tu respuesta integra perspectivas — una "
        "sesión puede definir el concepto, otra explicar el flujo, otra "
        "mencionar un bug o un pendiente. NO te quedes con la primera. "
        "`intro_source_sessions` debe contener TODAS las sesiones que "
        "aportaron material (no solo 1 o 2). Si dices \"actualmente la "
        "API es unidireccional pero debe ser bidireccional\", cita la "
        "sesión que mencionó la unidireccionalidad. Si dices \"los "
        "eventos pueden tener tiquetera o no\", cita la(s) sesión(es) "
        "que hicieron esa distinción. Cada CLAIM concreto va anclado a "
        "su sesión fuente.\n"
        "22. ENFOQUE EN LA PREGUNTA (anti-tangente): tu respuesta debe "
        "abordar EXACTAMENTE lo que el usuario preguntó — ni más ni "
        "menos. PROHIBIDO:\n"
        "  - Listar temas no preguntados solo porque aparecen en el "
        "contexto (no enumeres Docker, multi-tenant, autenticación si "
        "la pregunta es sobre EVENTOS).\n"
        "  - Resumir el dossier general del proyecto. El dossier es tu "
        "MATERIA PRIMA, no tu respuesta.\n"
        "  - Mezclar respuestas de OTROS clientes/proyectos del dossier "
        "como si fueran del preguntado.\n"
        "Antes de redactar, identifica las 2-3 dimensiones que la "
        "pregunta toca (en \"cómo se manejan los eventos\": creación, "
        "almacenamiento, conexión con otras partes). Cubre SOLO esas. "
        "Las sesiones marcadas ★PRIORITARIA★ son las que más conceptos "
        "preguntados mencionan — son TU fuente principal; las otras solo "
        "complementan si añaden algo específico a esas dimensiones.\n"
        "Estructura sugerida del `intro` para howtech:\n"
        "  (1) Frase de apertura nombrando el concepto y su rol.\n"
        "  (2) Distinciones / variantes / casos (\"con vs sin X\").\n"
        "  (3) Relación con otros módulos directamente relevantes.\n"
        "  (4) Estado actual / pendientes técnicos si aparecen.\n"
        "Si el dossier no cubre una dimensión, DILO explícitamente en "
        "vez de rellenar con temas no preguntados.\n"
        "23. PREGUNTAS YES-NO/RELACIONALES (\"X maneja Y?\", \"X es Y?\", "
        "\"X tiene Y?\", \"X usa Y?\", \"X depende de Y?\"): SIEMPRE "
        "responde con SÍ o NO claro en la primera frase del `intro`, "
        "seguido del por qué con EVIDENCIA TEXTUAL del transcript.\n"
        "  a) Busca los bloques `[EVIDENCIA TEXTUAL — sesión ...]` en el "
        "contexto. Esos son fragmentos del transcript donde aparecen los "
        "términos de la pregunta JUNTOS. Léelos completos.\n"
        "  b) Buscá frases que ESTABLEZCAN la relación: \"X maneja Y\", "
        "\"X administra Y\", \"X es responsable de Y\", \"X integra con "
        "Y\". O que la NIEGUEN: \"X no maneja Y\", \"X no es Y\", \"X "
        "delega a Y\", \"Y es responsabilidad de Z (no de X)\", \"X es "
        "partner de Y\". Cuando aparece negación o partnership, la "
        "respuesta es NO.\n"
        "  c) PROHIBIDO inferir relación positiva por co-ocurrencia. "
        "Que \"First Class\" y \"boletería de Arena USC\" aparezcan en "
        "la misma sesión NO significa que First Class maneje la "
        "boletería de Arena USC. Si el transcript dice \"Arena USC es "
        "partner\" o \"Tiquetera maneja la boletería\", la respuesta es "
        "NO aunque el resumen IA parezca sugerir lo contrario.\n"
        "  d) NUNCA confíes ciegamente en `[Resumen]` o `[Decisiones]` "
        "para contradicciones — esos son IA derivada, pueden estar "
        "invertidos o simplificados. La verdad relacional vive en "
        "`[EVIDENCIA TEXTUAL]` y `[Transcripción — ...]`. Si transcript "
        "y resumen se contradicen, GANA el transcript.\n"
        "  e) Tras responder SÍ/NO, explica con 2-3 oraciones citando "
        "evidencia y `intro_source_sessions` con las sesiones donde "
        "está la evidencia.\n"
        "Ejemplo malo (caso real reportado): \"First Class es una "
        "plataforma de gestión de eventos y boletería que se está "
        "desarrollando para la Arena USC. La plataforma tiene como "
        "objetivo permitir la venta de boletos.\" (asume relación "
        "positiva, ignora que Arena USC es partner)\n"
        "Ejemplo bueno: \"No. First Class NO maneja la boletería de "
        "Arena USC directamente. Según las sesiones, Arena USC es un "
        "partner de First Class y la boletería de Arena USC la "
        "administra la Tiquetera (Mi Boleta). First Class solo "
        "administra el evento en sí (alimentos, productos relacionados). "
        "La distinción es: si al crear el evento se ACTIVA la opción "
        "Tiquetera Mi Boleto, la Tiquetera maneja la venta de entradas "
        "y First Class maneja el resto; si se DESACTIVA, First Class "
        "maneja todo incluyendo la boletería. (Sesiones #N, #M)\"\n"
        "24. ATRIBUCIÓN POR SESIÓN (obligatoria en yesno + howtech): "
        "cada CLAIM del intro debe atribuirse a la sesión específica "
        "que aporta la evidencia. Formato sugerido para citas:\n"
        "  - \"En «<TÍTULO SESIÓN>» (<FECHA>): <claim>.\"\n"
        "  - \"Felipe Cortés en «<TÍTULO>» (<FECHA>) explicó: «<quote "
        "literal del transcript>».\"\n"
        "Los bloques `=== EVIDENCIA TEXTUAL #ID ===` traen Sesión, "
        "Fecha, Speaker (en marcadores [Nombre Speaker]) y fragmentos "
        "LITERALES. USA esos campos. Speaker se extrae del marcador "
        "`[Nombre]` dentro del fragmento — cita el nombre cuando "
        "transcribas una frase.\n"
        "25. INTEGRACIÓN MULTI-SESIÓN (obligatoria en yesno con N>=3): "
        "NO te quedes con una sesión. Si hay 11 sesiones de evidencia, "
        "TU RESPUESTA DEBE INTEGRAR LAS 11 (o las que aporten algo "
        "distinto). Cada sesión aporta un ángulo: una define la regla "
        "general, otra aclara la excepción, otra menciona un caso "
        "específico, otra revela un pendiente o riesgo. Sintetiza los "
        "ángulos. Estructura sugerida del intro para yesno con muchas "
        "fuentes:\n"
        "  ¶1: Respuesta SÍ/NO + frase de contexto.\n"
        "  ¶2: Regla general (citando sesión X + fecha).\n"
        "  ¶3: Distinciones / variantes / casos (citando sesiones Y, Z).\n"
        "  ¶4: Especificidad del caso preguntado (citando sesión W).\n"
        "  ¶5: Pendientes / riesgos / contradicciones si aparecen.\n"
        "intro_source_sessions lista TODAS las sesiones citadas — no "
        "solo las que ‘son la fuente’, también las que matizan.\n"
        "26. FORMATO MARKDOWN del `intro` — el frontend lo renderiza:\n"
        "  - Párrafos separados por línea en blanco (`\\n\\n`).\n"
        "  - **bold** para énfasis en respuestas SÍ/NO, nombres clave, "
        "decisiones críticas.\n"
        "  - Listas con `- ` cuando enumeras casos o pasos.\n"
        "  - Citas de speaker entre comillas, tipo: «<quote literal>».\n"
        "27. ENLACES A SESIONES — OBLIGATORIO cuando cites una sesión: "
        "formato markdown `[«TÍTULO» (FECHA)](/admin/curation/ID)`. "
        "El frontend abre en pestaña nueva. Ejemplo:\n"
        "  `[«First Class - Evento - api- tiquetera» (07 jun 2026)](/admin/curation/369)`\n"
        "Esto reemplaza la cita plana «…». Cada vez que invoques una "
        "sesión como fuente, ÚSALO. Si no recuerdas el ID, mira el "
        "header `Sesión #<id>` del bloque correspondiente del contexto.\n"
        "28. NO ABANDONES decisions/risks/agreements/action_items por "
        "tener un `intro` largo. Las cuatro arrays SIEMPRE se pueblan "
        "cuando hay material EXPLÍCITO en el contexto:\n"
        "  - `decisions`: cualquier frase del tipo «se decide …», «se "
        "acuerda implementar …», «se va a hacer …» de los bloques "
        "`[Decisiones]` o del transcript. Cada item con source_sessions.\n"
        "  - `agreements`: acuerdos formales entre partes — del bloque "
        "`[Acuerdos]` o transcript.\n"
        "  - `risks`: riesgos/bloqueos/pendientes — del bloque `[Riesgos]` "
        "o transcript.\n"
        "  - `action_items`: tareas detectadas con owner si está.\n"
        "Un `intro` rico NO compensa arrays vacías. La UI muestra ambos. "
        "Si el contexto trae 5 decisiones y un riesgo identificado, "
        "DEBEN aparecer en sus arrays — además de mencionarse en `intro`.\n"
        "29. EXTRACCIÓN DE NOMBRES PROPIOS DEL DOMINIO: lee el transcript "
        "buscando los nombres ESPECÍFICOS de productos, plataformas, "
        "leyes, módulos, partners. Tu respuesta DEBE incluir esos "
        "nombres tal cual aparecen en el transcript, NO genéricos. "
        "Ejemplos de nombres a buscar en preguntas de boletería/eventos:\n"
        "  • Plataformas de tiquetera: «Tiquetera Mi Boleta», «Mi "
        "Boleto», «Etiquetera», «Tiquetera externa».\n"
        "  • Distinciones operativas: «evento con tiquetera» vs «evento "
        "sin tiquetera», «activar/desactivar opción Tiquetera Mi Boleto».\n"
        "  • Módulos relacionados: «cartera», «cuotas», «motor de "
        "puntos», «Core», «alimentos y bebidas», «promotores».\n"
        "  • Contexto legal: «legislación colombiana», «plataforma de "
        "eventos no puede generar boletas».\n"
        "  • Partners/casos: «Arena USC», «Cali Santiago de Cali».\n"
        "  • Estado API: «unidireccional», «bidireccional».\n"
        "Si tu respuesta dice solo «la plataforma se conecta con un "
        "tercero» sin nombrar quién, es VAGA. Si dice «la Tiquetera Mi "
        "Boleta administra la boletería por exigencia de la legislación "
        "colombiana», es CONCRETA. Concreta gana.\n"
        "30. ANTI-BOILERPLATE por sesión: cada párrafo que cita una "
        "sesión DEBE aportar UN CLAIM ESPECÍFICO Y DISTINTIVO de "
        "ESA sesión — no una frase genérica que cabría en cualquiera. "
        "Si tu párrafo dice «se discutió X», «se habló de Y», «se "
        "mencionó Z» SIN decir QUÉ se discutió/habló/mencionó "
        "específicamente, está MAL. Reemplaza por:\n"
        "  - Una decisión literal: «se decidió que <X> haga <Y>»\n"
        "  - Una distinción concreta: «si se activa <opción>, entonces "
        "<consecuencia>; si se desactiva, <otra consecuencia>»\n"
        "  - Una cita atribuida: «<Speaker> explicó que «<frase del "
        "transcript>»\n"
        "  - Un dato técnico: «la <tabla/módulo/api> hace <acción>»\n"
        "  - Un riesgo identificado: «<X> está bloqueado porque <Y>»\n"
        "Antes de redactar cada párrafo, pregúntate: «¿esta misma frase "
        "podría servir para CUALQUIER sesión de First Class?». Si la "
        "respuesta es SÍ, está mal — busca en el [Transcripción] de esa "
        "sesión un detalle específico, único.\n"
        "Si dos sesiones tienen el mismo claim, AGRÚPALAS en un solo "
        "párrafo (`En las sesiones X y Y, …`) en lugar de repetir.\n"
        "Si una sesión no aporta nada distintivo más allá del claim "
        "general, NO le dediques un párrafo entero — mencionala en la "
        "lista de fuentes y ya. Cantidad de párrafos = cantidad de "
        "ángulos distintos, no cantidad de sesiones.\n"
        "Ejemplo malo: «En «X» (fecha), se discutió la implementación "
        "de funcionalidades de compra y venta de boletos.»\n"
        "Ejemplo bueno: «En «X» (fecha), Felipe explicó que la "
        "Etiquetera Mi Boleta administra la boletería en el caso de "
        "Arena USC porque la legislación colombiana prohíbe a una "
        "plataforma de eventos vender boletos; First Class se queda con "
        "la venta de alimentos, bebidas y promociones.»\n"
        "31. NO HEDGEAR / NO NEGAR LO QUE SÍ ESTÁ: si en el contexto hay "
        "frases literales que EXPRESAN el concepto preguntado —aunque con "
        "OTRAS PALABRAS— debes AFIRMARLO y CITARLAS textual, nunca decir "
        "«no se encontró una mención/decisión explícita». Conceptos "
        "equivalentes que cuentan como la MISMA afirmación: «homologar», "
        "«homologación», «hacerlo igual a la sede», «como está en la "
        "sede», «que funcione de la misma manera que en la sede», "
        "«equivalente a la sede», «replicar el trámite de la sede», «no "
        "hacer nada nuevo», «desarrollos nuevos igual a Sede» → TODAS "
        "significan: la app móvil debe quedar IGUAL a la sede electrónica "
        "sin funcionalidad nueva. Si ves cualquiera de estas en el "
        "transcript, la respuesta es SÍ: existe esa directriz; cítala con "
        "«<Speaker> en «<sesión>» (fecha): «<frase textual>»». Prohibido "
        "terminar con un párrafo que diga que «no hay decisión explícita» "
        "cuando el contexto contiene estas frases. Solo niega si REALMENTE "
        "no aparece ninguna.\n"
        "32. CERO REPETICIÓN: nunca repitas ni parafrasees una frase/claim "
        "ya escrito en otro párrafo. Está PROHIBIDO un párrafo «En resumen» "
        "o «En conclusión» que reitere lo ya dicho. Cada oración debe "
        "aportar información NUEVA. Si la pregunta es binaria (ej. «¿son "
        "dos X o una?») resuélvela de forma TAJANTE en la 1ra frase con la "
        "distinción real del transcript (qué es cada cosa, quién lo dijo, "
        "en qué sesión), y NO des vueltas: una afirmación clara > cinco "
        "párrafos vagos que repiten «se utiliza para administración de…».\n"
        "33. INFERENCIA DE IDENTIDAD / ENTIDAD RENOMBRADA EN EL TIEMPO: un "
        "MISMO producto, módulo o componente suele aparecer con NOMBRES "
        "DISTINTOS en sesiones de fechas distintas porque se fue renombrando "
        "(ej. en First Class: «etiqueta» → «etiquetera» → «taquilla»/"
        "«taquillera» → «tiquetera» → «Mi Boleta» son TODOS el mismo módulo "
        "de boletería). NO los trates como cosas distintas. Debes:\n"
        "  a) DETECTAR las señales de renombre en el transcript: «se llama "
        "Y», «ahora es Y», «la X, perdón Y», «el proyecto de la X que se "
        "llama Y», o el mismo rol/función descrito con nombres distintos en "
        "sesiones de distinta fecha.\n"
        "  b) UNIFICARLOS como UNA sola entidad y decirlo explícito: «X, Y "
        "y Z son el mismo <componente>; hoy se llama <nombre actual>».\n"
        "  c) Dar la LÍNEA DE TIEMPO del nombre cuando los datos lo "
        "permitan: «en «<sesión>» (fecha) se le decía X; en «<sesión>» "
        "(fecha posterior) ya se llama Y», citando sesión+fecha de cada "
        "cambio.\n"
        "  d) Responder usando el nombre ACTUAL/vigente, aclarando los "
        "alias previos entre paréntesis.\n"
        "Esto es INFERENCIA basada en evidencia (las señales del "
        "transcript), NO invención: solo unifica nombres cuando el contexto "
        "muestra que se refieren a la misma función/proyecto.\n"
        "34. DEFINICIÓN DE SIGLAS/ACRÓNIMOS: cuando pregunten «¿qué es "
        "<sigla>?», busca en los fragmentos la EXPANSIÓN de la sigla — "
        "suele aparecer en aposición justo al lado («la carpeta TR, "
        "términos de referencia»), en una enumeración paralela («la "
        "carpeta DM con lo contractual, la INFE con lo técnico y la TR "
        "con los entregables») o dicha por otro speaker al aclarar. "
        "Responde con la definición CONCRETA + la cita textual y sesión "
        "donde se define. Si distintos speakers dan matices distintos "
        "(uno dice «términos de referencia», otro «transmittal de "
        "entrega»), repórtalos ambos con su atribución. NUNCA digas «la "
        "definición no está claramente establecida» si algún fragmento "
        "contiene la sigla junto a su expansión."
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

    # Hint explícito al LLM cuando la pregunta es howtech, para forzar
    # respuesta técnica concreta en lugar de tautología.
    howtech_hint = ""
    if howtech_mode:
        concepts_str = ", ".join(howtech_concepts) if howtech_concepts else "el detalle técnico"
        deep_session_ids = sorted({
            c["session_id"] for c in chunks if c.get("kind") == "project_deep"
        })
        deep_hint = (
            f" Tienes el dossier completo de {len(deep_session_ids)} sesiones "
            f"(IDs: {deep_session_ids}) marcadas como `[Dossier de proyecto ...]`. "
            f"LÉELOS TODOS antes de responder."
            if deep_session_ids else ""
        )
        howtech_hint = (
            f"\n\nNOTA TÉCNICA: el usuario pide HOW INTERNO sobre {concepts_str}. "
            f"Busca en los bloques `[Dossier de proyecto ...]` y "
            f"`[Detalle técnico — ...]` del contexto y responde con NOMBRES "
            f"CONCRETOS (endpoints/rutas, tablas/esquemas, componentes, "
            f"módulos, librerías, eventos, jobs, integraciones). PROHIBIDO "
            f"repetir la definición del proyecto o decir genéricos como 'a "
            f"través de la plataforma'. Cubre TODAS las dimensiones presentes "
            f"en el dossier (APIs, BD, frontend, backend, flujo, integraciones, "
            f"auth) — declara explícitamente las que NO encuentras.{deep_hint}"
        )

    # Índice concepto→sesiones cuando howtech detectó conceptos. Permite
    # al LLM identificar de un vistazo qué sesiones cubren cada dimensión
    # y EXIGE síntesis multi-sesión en lugar de respuesta basada en una.
    concept_index_block = (
        _build_concept_index(chunks, howtech_concepts)
        if howtech_mode and howtech_concepts else ""
    )

    # Preguntas de tareas/pendientes: inyectar los ActionItems reales de
    # la BD (estado vigente curado) — el transcript dice lo que se DIJO,
    # la tabla dice lo que SIGUE pendiente.
    tasks_block = (
        _load_action_items_context(db, tenant.id, payload.project_id)
        if _is_tasks_question(q) else ""
    )

    user_msg = (
        f"{convo_hint}"
        f"Pregunta del usuario: {q}\n\n"
        + (f"{entity_block}\n\n" if entity_block else "")
        + (f"{tasks_block}\n\n" if tasks_block else "")
        + (f"{concept_index_block}\n\n" if concept_index_block else "")
        + (f"{sessions_inventory}\n\n" if sessions_inventory else "")
        + f"Contexto extraído de actas anteriores ({len(chunks)} fragmentos relevantes, "
          f"filtrados de {len(raw_chunks)} candidatos por umbral de relevancia):\n"
          f"{context}"
          f"{quality_note}"
          f"{howtech_hint}\n\n"
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

    # max_tokens dinámico: yesno/howtech con mucha evidencia necesitan
    # más espacio. Sin cap explícito Groq usa ~4096 por defecto; lo
    # subimos a 6000 para que la respuesta integrada (intro + sections)
    # no se trunque.
    if summarize_mode:
        _max_tokens = 7000  # resumen detallado por sesión — necesita aire
    elif yesno_mode or howtech_mode or whatis_mode:
        _max_tokens = 6000 if n_evidence >= 4 else 3500
    else:
        _max_tokens = 2000

    payload_llm = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system},
            *convo_messages,
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.1,  # Bajamos temperatura para reducir confabulación.
        "max_tokens": _max_tokens,
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
                        # El LLM contesta «No especificada» cuando no hay
                        # fecha; eso no viaja en un campo que el otro lado
                        # puede leer como fecha. Vacío significa sin fecha.
                        due_date=normalize_due_date(it.get("due_date")) or "",
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
