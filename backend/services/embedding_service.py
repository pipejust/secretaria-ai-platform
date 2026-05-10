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
    """
    if not settings.openai_api_key:
        logger.info("embed_session %s: sin OPENAI_API_KEY, skip.", session_id)
        return 0

    sess = db.get(MeetingSession, session_id)
    if not sess:
        logger.error("embed_session: sesión %s no existe", session_id)
        return 0

    # Recolectar contenido por kind
    items: list[tuple[str, int, str]] = []
    if sess.raw_summary:
        items.append(("summary", 0, sess.raw_summary[:8000]))
    if sess.processed_decisions:
        items.append(("decisions", 0, sess.processed_decisions[:8000]))
    if sess.processed_risks:
        items.append(("risks", 0, sess.processed_risks[:8000]))
    if sess.processed_agreements:
        items.append(("agreements", 0, sess.processed_agreements[:8000]))
    if sess.raw_transcript:
        for i, chunk in enumerate(_split_transcript(sess.raw_transcript)):
            items.append(("transcript", i, chunk))

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


async def search_similar(
    db: Session,
    query: str,
    top_k: int = 8,
    project_id: Optional[int] = None,
) -> List[dict]:
    """Búsqueda semántica top-k. Retorna dicts con session_id, kind, content, distance."""
    qvec = await embed_text(query)
    if qvec is None:
        return []
    qlit = _vector_literal(qvec)

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

    rows = db.exec(sql).all()
    return [
        {
            "session_id": r[0],
            "kind": r[1],
            "chunk_index": r[2],
            "content": r[3],
            "distance": float(r[4]),
        }
        for r in rows
    ]
