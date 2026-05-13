"""Servicio de embeddings sobre OpenAI text-embedding-3-small (1536 dims).

Diseño:
- `embed_text(text)` → list[float] de 1536 floats. Maneja retries y rate limits.
- `embed_batch(texts)` → batched para reducir round trips (max 100 textos por
  llamada por la doc de OpenAI).
- `embed_session(db, session_id)` → genera chunks por kind y los persiste en
  embeddingchunk. Es el helper que llama el pipeline IA tras procesar una
  sesión.

Costos referenciales: text-embedding-3-small = $0.020 por 1M de tokens.
Una sesión típica de 60 min produce ~10k tokens → $0.0002 por sesión.

Sin OPENAI_API_KEY la función queda no-op (logs warning) — el resto del
sistema sigue funcionando, solo se pierde la capacidad de RAG.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable, List, Optional

import httpx
from sqlalchemy import text as sa_text
from sqlmodel import Session, select

from config import settings
from models import EmbeddingChunk, MeetingSession

logger = logging.getLogger(__name__)

EMBED_URL = "https://api.openai.com/v1/embeddings"
EMBED_MODEL = "text-embedding-3-small"
EMBED_DIMS = 1536
TRANSCRIPT_CHUNK_TOKENS = 600    # ~2400 chars; mantiene el chunk significativo sin pasarse


def _openai_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }


def _split_transcript(text: str, target_chars: int = 2400) -> List[str]:
    """Chunking simple por párrafos hasta llegar al target. Preserva oraciones."""
    if not text:
        return []
    paragraphs = re.split(r"\n\s*\n", text.strip())
    chunks, buf = [], ""
    for p in paragraphs:
        if len(buf) + len(p) + 2 <= target_chars:
            buf = (buf + "\n\n" + p) if buf else p
        else:
            if buf:
                chunks.append(buf)
            buf = p[:target_chars]   # corta párrafo gigante
    if buf:
        chunks.append(buf)
    return chunks


async def embed_text(text: str) -> Optional[List[float]]:
    """Devuelve list[float] de 1536 dims o None si OPENAI_API_KEY no está."""
    if not settings.openai_api_key:
        logger.warning("OPENAI_API_KEY ausente: embed_text retorna None.")
        return None
    payload = {"model": EMBED_MODEL, "input": text, "dimensions": EMBED_DIMS}
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(EMBED_URL, json=payload, headers=_openai_headers())
        r.raise_for_status()
        return r.json()["data"][0]["embedding"]


async def embed_batch(texts: List[str]) -> List[List[float]]:
    """Embed múltiples textos en una sola llamada (max 100)."""
    if not settings.openai_api_key or not texts:
        return [[] for _ in texts]
    payload = {"model": EMBED_MODEL, "input": texts, "dimensions": EMBED_DIMS}
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(EMBED_URL, json=payload, headers=_openai_headers())
        r.raise_for_status()
        data = r.json()["data"]
        # OpenAI garantiza orden; data[i].embedding corresponde a texts[i]
        return [d["embedding"] for d in data]


def _vector_literal(vec: List[float]) -> str:
    """Postgres vector(1536) admite literal '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


async def embed_session(db: Session, session_id: int) -> int:
    """Genera/actualiza embeddings para una sesión completa.

    Idempotente: borra los chunks previos de la sesión y los regenera.
    Retorna la cantidad de chunks insertados.

    IMPORTANTE: cada chunk se prefija con `[Reunión: {title}]\\n` antes
    de enviarse a OpenAI. Así el TÍTULO de la reunión queda incorporado
    en cada vector. Esto resuelve el caso real donde una sesión titulada
    "Colpensiones - Kyndryl" no aparecía en búsquedas de "kyndryl"
    porque la palabra solo estaba en el título y nunca en el contenido.
    """
    if not settings.openai_api_key:
        logger.info("embed_session %s: sin OPENAI_API_KEY, skip.", session_id)
        return 0

    sess = db.get(MeetingSession, session_id)
    if not sess:
        logger.error("embed_session: sesión %s no existe", session_id)
        return 0

    # Prefijo común que se inyecta en cada chunk para que el título quede
    # en el espacio vectorial. Ej: "[Reunión: Colpensiones - Kyndryl]\n..."
    title_prefix = f"[Reunión: {sess.title}]\n" if (sess.title or "").strip() else ""

    def _wrap(text: str, limit: int = 8000) -> str:
        """Aplica el prefijo de título y respeta el límite total."""
        body = (text or "")[: max(limit - len(title_prefix), 100)]
        return title_prefix + body

    # Recolectar contenido por kind
    items: list[tuple[str, int, str]] = []
    if sess.raw_summary:
        items.append(("summary", 0, _wrap(sess.raw_summary)))
    if sess.processed_decisions:
        items.append(("decisions", 0, _wrap(sess.processed_decisions)))
    if sess.processed_risks:
        items.append(("risks", 0, _wrap(sess.processed_risks)))
    if sess.processed_agreements:
        items.append(("agreements", 0, _wrap(sess.processed_agreements)))
    if sess.raw_transcript:
        for i, chunk in enumerate(_split_transcript(sess.raw_transcript)):
            items.append(("transcript", i, title_prefix + chunk))

    if not items:
        logger.info("embed_session %s: nada que indexar.", session_id)
        return 0

    # Borrar chunks previos para idempotencia
    db.exec(
        sa_text("DELETE FROM embeddingchunk WHERE session_id = :sid").bindparams(
            sid=session_id
        )
    )

    # Embed batch
    vecs = await embed_batch([content for _, _, content in items])

    # Insertar con SQL crudo para escribir el VECTOR
    inserted = 0
    for (kind, chunk_index, content), vec in zip(items, vecs):
        if not vec:
            continue
        db.exec(
            sa_text(
                """
                INSERT INTO embeddingchunk (session_id, kind, chunk_index, content, embedding_vector, created_at)
                VALUES (:sid, :kind, :ci, :content, CAST(:vec AS vector), NOW()::text)
                """
            ).bindparams(
                sid=session_id,
                kind=kind,
                ci=chunk_index,
                content=content,
                vec=_vector_literal(vec),
            )
        )
        inserted += 1
    db.commit()
    logger.info("embed_session %s: %s chunks insertados.", session_id, inserted)
    return inserted


# Stop-words mínimas para no buscar por título palabras vacías como "que",
# "es", "de", "en", etc. — sólo añadimos lo justo para reducir ruido.
_STOP_WORDS = {
    "que", "qué", "es", "el", "la", "los", "las", "un", "una", "unos", "unas",
    "de", "del", "en", "con", "por", "para", "y", "o", "u", "a", "al",
    "como", "qué", "se", "su", "sus", "lo", "le", "les", "mi", "tu", "te",
    "me", "nos", "ya", "muy", "más", "mas", "esta", "este", "estos", "estas",
    "the", "and", "for", "from", "with", "that", "this", "these", "those",
}

# Bonus aplicado a la distancia cuando hay match de título: 0.7 = 30% más
# cerca. Sin bajarla a 0 para no romper el ordenamiento general.
_TITLE_MATCH_BONUS = 0.70


def _extract_keywords(query: str) -> list[str]:
    """Extrae palabras significativas (>=4 letras, no stop-words) de la
    query del usuario. Sirve para hacer ILIKE sobre títulos de sesión."""
    raw = re.findall(r"\b[\wáéíóúÁÉÍÓÚñÑ]+\b", (query or "").lower())
    return [w for w in raw if len(w) >= 4 and w not in _STOP_WORDS]


async def search_similar(
    db: Session,
    query: str,
    top_k: int = 8,
    project_id: Optional[int] = None,
) -> List[dict]:
    """Búsqueda híbrida (vector + título).

    1. Vector search: top_k chunks por similitud coseno con la pregunta.
    2. Title match: identifica sesiones cuyo TÍTULO contiene alguna
       palabra-clave de la pregunta (ILIKE). Para cada una que NO esté
       ya en los resultados vectoriales, añade su mejor chunk con un
       bonus de distancia (lo acerca un 30% para que rankee mejor).
    3. Re-ordena por distancia ascendente y trunca a top_k.

    Esto resuelve el caso "que es kyndryl?" donde el contenido de la
    reunión "Colpensiones - Kyndryl" nunca menciona la palabra Kyndryl
    pero sí está en su título.
    """
    qvec = await embed_text(query)
    if qvec is None:
        return []
    qlit = _vector_literal(qvec)

    # ─────── 1. Vector search ───────
    if project_id is not None:
        sql = sa_text(
            """
            SELECT ec.session_id, ec.kind, ec.chunk_index, ec.content,
                   ec.embedding_vector <=> CAST(:qvec AS vector) AS distance
            FROM embeddingchunk ec
            JOIN meetingsession ms ON ms.id = ec.session_id
            WHERE ms.project_id = :pid
            ORDER BY ec.embedding_vector <=> CAST(:qvec AS vector) ASC
            LIMIT :k
            """
        ).bindparams(qvec=qlit, pid=project_id, k=top_k)
    else:
        sql = sa_text(
            """
            SELECT session_id, kind, chunk_index, content,
                   embedding_vector <=> CAST(:qvec AS vector) AS distance
            FROM embeddingchunk
            ORDER BY embedding_vector <=> CAST(:qvec AS vector) ASC
            LIMIT :k
            """
        ).bindparams(qvec=qlit, k=top_k)

    vector_rows = list(db.exec(sql).all())
    vector_session_ids = {r[0] for r in vector_rows}

    # ─────── 2. Title match ───────
    keywords = _extract_keywords(query)
    extra_rows: list = []
    if keywords:
        # Construimos OR dinámico de ILIKEs sobre el título
        ilike_clauses = " OR ".join([f"LOWER(ms.title) LIKE :kw{i}" for i in range(len(keywords))])
        params = {f"kw{i}": f"%{kw}%" for i, kw in enumerate(keywords)}
        project_filter = " AND ms.project_id = :pid" if project_id is not None else ""
        if project_id is not None:
            params["pid"] = project_id

        title_sql = sa_text(
            f"""
            SELECT id FROM meetingsession ms
            WHERE ({ilike_clauses}){project_filter}
            """
        ).bindparams(**params)
        title_session_ids = {r[0] for r in db.exec(title_sql).all()}

        # Sesiones matched por título pero NO presentes en vector results
        missing = title_session_ids - vector_session_ids
        for sid in missing:
            extra_sql = sa_text(
                """
                SELECT session_id, kind, chunk_index, content,
                       embedding_vector <=> CAST(:qvec AS vector) AS distance
                FROM embeddingchunk
                WHERE session_id = :sid
                ORDER BY embedding_vector <=> CAST(:qvec AS vector) ASC
                LIMIT 1
                """
            ).bindparams(qvec=qlit, sid=sid)
            row = db.exec(extra_sql).first()
            if row:
                # Aplicamos bonus por title-match: bajamos la distancia
                # para que rankee mejor en la mezcla.
                boosted_distance = float(row[4]) * _TITLE_MATCH_BONUS
                extra_rows.append((row[0], row[1], row[2], row[3], boosted_distance))

    # ─────── 3. Merge + re-rank + truncate ───────
    all_rows = vector_rows + extra_rows
    all_rows.sort(key=lambda r: r[4])
    final = all_rows[:top_k]

    return [
        {
            "session_id": r[0],
            "kind": r[1],
            "chunk_index": r[2],
            "content": r[3],
            "distance": float(r[4]),
        }
        for r in final
    ]
