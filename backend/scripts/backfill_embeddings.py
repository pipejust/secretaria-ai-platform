#!/usr/bin/env python3
"""Backfill de embeddings para sesiones existentes.

Uso:
    cd backend && python scripts/backfill_embeddings.py [--dry-run] [--limit N]

Idempotente: para cada MeetingSession con status in ('pending','processed',
'completed','approved') que NO tenga ya chunks en embeddingchunk, ejecuta
embed_session(). Las sesiones ya indexadas se saltan.

Costo estimado: ~$0.0002 USD por sesión × 46 sesiones = $0.01.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

# Permitir importar el paquete cuando se corre desde backend/
sys.path.insert(0, ".")

from sqlalchemy import text as sa_text
from sqlmodel import Session, select

from database import engine
from models import MeetingSession
from services.embedding_service import embed_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backfill_embeddings")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="0 = sin límite")
    args = parser.parse_args()

    with Session(engine) as db:
        already_indexed = {
            r[0]
            for r in db.exec(
                sa_text("SELECT DISTINCT session_id FROM embeddingchunk")
            ).all()
        }
        sessions = list(
            db.exec(
                select(MeetingSession).where(
                    MeetingSession.status.in_(["pending", "processed", "completed", "approved"])
                )
            ).all()
        )
        pending = [s for s in sessions if s.id not in already_indexed]
        if args.limit > 0:
            pending = pending[: args.limit]

        log.info(
            "Sesiones totales=%d  ya_indexadas=%d  por_indexar=%d",
            len(sessions),
            len(already_indexed),
            len(pending),
        )
        if args.dry_run:
            for s in pending:
                log.info("[DRY-RUN] embed_session %s — '%s'", s.id, (s.title or '')[:60])
            return 0

        ok, fail = 0, 0
        for i, s in enumerate(pending, 1):
            log.info("[%d/%d] embed_session %s — '%s'", i, len(pending), s.id, (s.title or '')[:60])
            try:
                count = await embed_session(db, s.id)
                log.info("  → %d chunks", count)
                ok += 1
            except Exception:
                log.exception("falló embed_session %s", s.id)
                fail += 1

        log.info("Backfill terminado. ok=%d fail=%d", ok, fail)
        return 0 if fail == 0 else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
