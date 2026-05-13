"""Re-ingesta manual por ID de transcript de Fireflies.

Uso:
    docker compose exec backend python -m scripts.ingest_fireflies_by_id \
        --tenant acten ID1 ID2 ID3 ...

Replica EXACTAMENTE el pipeline del webhook (`routers/fireflies.py`) — útil
cuando una sesión existe en Fireflies pero el webhook nunca disparó (URL
mal configurada, plataforma offline, token cambió, etc.).

Idempotente: si ya hay un MeetingSession con ese fireflies_id en la DB,
lo salta (no duplica). Para forzar reproceso, borrar primero la fila.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from typing import Optional

from sqlmodel import Session, select

from database import engine
from models import MeetingSession, Tenant
from routers.fireflies import process_transcript_background

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ingest_fireflies")


def _resolve_tenant_id(db: Session, slug: str) -> int:
    t = db.exec(select(Tenant).where(Tenant.slug == slug)).first()
    if not t:
        raise SystemExit(f"Tenant '{slug}' no existe.")
    if not t.is_active:
        raise SystemExit(f"Tenant '{slug}' está inactivo.")
    return t.id


def _existing(db: Session, fireflies_id: str, tenant_id: int) -> Optional[MeetingSession]:
    return db.exec(
        select(MeetingSession)
        .where(MeetingSession.fireflies_id == fireflies_id)
        .where(MeetingSession.tenant_id == tenant_id)
    ).first()


async def ingest_one(
    fireflies_id: str,
    tenant_id: int,
    *,
    force: bool = False,
) -> str:
    """Devuelve un string con el resultado de la ingesta. NO levanta excepciones."""
    with Session(engine) as db:
        existing = _existing(db, fireflies_id, tenant_id)
        if existing and not force:
            return f"SKIP existing id={existing.id} status={existing.status}"
        if existing and force:
            db.delete(existing)
            db.commit()

        # Mismo shape que el webhook: status='processing' al inicio, los
        # campos reales se llenan en background. La fecha real llega cuando
        # process_transcript_background lea `dateString` de la API de Fireflies;
        # mientras tanto guardamos un ISO válido (ahora) — nunca un timestamp
        # crudo en ms, para que el frontend pueda parsearlo sin sorpresas.
        from datetime import datetime as _dt, timezone as _tz
        new_session = MeetingSession(
            tenant_id=tenant_id,
            fireflies_id=fireflies_id,
            title="Reunión Procesando…",
            date=_dt.now(_tz.utc).isoformat(),
            raw_transcript="",
            raw_summary="",
            status="processing",
        )
        db.add(new_session)
        db.commit()
        db.refresh(new_session)
        session_id = new_session.id

    # `process_transcript_background` abre su propia sesión SQL; le paso un
    # payload mínimo simulando lo que mandaría el webhook real. La función
    # se encarga de extraer transcript, summary, correr Groq+OpenAI, etc.
    payload_data = {"transcriptId": fireflies_id, "title": "Reunión Procesando…"}
    try:
        await process_transcript_background(session_id, fireflies_id, payload_data)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Pipeline falló para %s", fireflies_id)
        return f"ERROR session_id={session_id}: {exc}"

    # Re-leer estado final
    with Session(engine) as db:
        s = db.get(MeetingSession, session_id)
        if not s:
            return f"ERROR session_id={session_id} desapareció tras pipeline"
        return (
            f"OK session_id={s.id} status={s.status} "
            f"title='{s.title[:60]}' "
            f"transcript_chars={len(s.raw_transcript or '')} "
            f"summary_chars={len(s.raw_summary or '')}"
        )


async def main_async(ids: list[str], tenant_slug: str, force: bool) -> int:
    with Session(engine) as db:
        tenant_id = _resolve_tenant_id(db, tenant_slug)
        logger.info("Tenant '%s' → id=%s", tenant_slug, tenant_id)

    failures = 0
    for fid in ids:
        logger.info("=== Procesando %s ===", fid)
        result = await ingest_one(fid, tenant_id, force=force)
        logger.info("[%s] %s", fid, result)
        if result.startswith("ERROR"):
            failures += 1
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ids", nargs="+", help="Fireflies transcript IDs")
    parser.add_argument(
        "--tenant", default="acten",
        help="Slug del tenant que recibe las sesiones (default: acten)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Si el ID ya existe, lo borra y re-ingesta",
    )
    args = parser.parse_args()
    failures = asyncio.run(main_async(args.ids, args.tenant, args.force))
    if failures:
        logger.error("%s de %s sesiones fallaron.", failures, len(args.ids))
        sys.exit(1)


if __name__ == "__main__":
    main()
