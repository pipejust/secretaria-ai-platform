"""Reintenta el pipeline IA sobre una o varias sesiones existentes.

Diseñado para rescatar sesiones que el webhook procesó "a medias" — por
ejemplo, "Despliegue release 19" donde Fireflies sí trajo el transcript y
el summary, pero OpenAI falló silenciosamente extrayendo tareas.

Uso:
    # Reintenta solo el pipeline IA con lo que ya está en DB.
    docker compose exec backend python -m scripts.retry_session_pipeline 123

    # Vuelve a traer transcript + summary nativo desde Fireflies y luego
    # re-ejecuta IA. Útil si el transcript original llegó vacío.
    docker compose exec backend python -m scripts.retry_session_pipeline \
        --rehydrate 123 124 125

    # Reintenta TODAS las sesiones del tenant marcadas como incompletas
    # (processing_error != "" o processing_completed_at == "").
    docker compose exec backend python -m scripts.retry_session_pipeline \
        --tenant acten --all-incomplete

    # Reintenta por título (substring case-insensitive). Cuidado: puede
    # matchear varias.
    docker compose exec backend python -m scripts.retry_session_pipeline \
        --tenant acten --by-title "Despliegue release 19"
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Optional

from sqlmodel import Session, select

from database import engine
from models import MeetingSession, Tenant
from routers.fireflies import process_transcript_background
from services.transcript_pipeline import process_session_with_ai

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("retry_session_pipeline")


def _resolve_tenant_id(db: Session, slug: str) -> int:
    t = db.exec(select(Tenant).where(Tenant.slug == slug)).first()
    if not t:
        raise SystemExit(f"Tenant '{slug}' no existe.")
    return t.id


def _find_session(db: Session, session_id: int) -> Optional[MeetingSession]:
    return db.get(MeetingSession, session_id)


def _is_incomplete(s: MeetingSession) -> bool:
    if (s.processing_error or "").strip():
        return True
    if not (s.processing_completed_at or "").strip():
        return True
    return False


async def retry_one(session_id: int, *, rehydrate: bool) -> str:
    """Reintenta una sesión. Devuelve string con resultado. NO levanta."""
    with Session(engine) as db:
        s = _find_session(db, session_id)
        if not s:
            return f"ERROR id={session_id} no existe"
        title_short = (s.title or "")[:60]
        ff_id = s.fireflies_id

    if rehydrate:
        if not ff_id:
            return f"ERROR id={session_id} '{title_short}' no tiene fireflies_id"
        logger.info("Rehidratando id=%s desde Fireflies (ff_id=%s)", session_id, ff_id)
        try:
            await process_transcript_background(session_id, ff_id, {})
        except Exception as exc:  # noqa: BLE001
            logger.exception("Rehidratación falló para id=%s", session_id)
            return f"ERROR id={session_id}: {exc}"
    else:
        logger.info("Re-ejecutando pipeline IA sobre id=%s ('%s')", session_id, title_short)
        try:
            with Session(engine) as db:
                await process_session_with_ai(db, session_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Pipeline IA falló para id=%s", session_id)
            return f"ERROR id={session_id}: {exc}"

    # Re-leer estado final
    with Session(engine) as db:
        s = _find_session(db, session_id)
        if not s:
            return f"ERROR id={session_id} desapareció tras retry"
        from models import ActionItem
        tasks = db.exec(
            select(ActionItem).where(ActionItem.session_id == session_id)
        ).all()
        return (
            f"OK id={s.id} '{(s.title or '')[:60]}' "
            f"status={s.status} "
            f"transcript={len(s.raw_transcript or '')}c "
            f"summary={len(s.raw_summary or '')}c "
            f"tasks={len(tasks)} "
            f"error='{(s.processing_error or '')[:120]}'"
        )


def _resolve_target_session_ids(
    args: argparse.Namespace,
) -> list[int]:
    if args.ids:
        return [int(i) for i in args.ids]

    if not (args.all_incomplete or args.by_title):
        raise SystemExit(
            "Tenés que pasar IDs, o --all-incomplete, o --by-title."
        )

    with Session(engine) as db:
        tenant_id = _resolve_tenant_id(db, args.tenant)
        q = (
            select(MeetingSession)
            .where(MeetingSession.tenant_id == tenant_id)
            .order_by(MeetingSession.id.desc())
        )
        rows = db.exec(q).all()

    if args.by_title:
        needle = args.by_title.lower().strip()
        rows = [r for r in rows if needle in (r.title or "").lower()]
        if not rows:
            raise SystemExit(f"No matchea ninguna sesión con título '{args.by_title}'.")
        return [r.id for r in rows]

    # --all-incomplete
    return [r.id for r in rows if _is_incomplete(r)]


async def main_async(ids: list[int], rehydrate: bool) -> int:
    failures = 0
    for sid in ids:
        logger.info("=== Reintentando session_id=%s ===", sid)
        result = await retry_one(sid, rehydrate=rehydrate)
        logger.info("[%s] %s", sid, result)
        if result.startswith("ERROR"):
            failures += 1
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "ids", nargs="*",
        help="IDs internos (no fireflies_id) de las sesiones a reintentar.",
    )
    parser.add_argument(
        "--tenant", default="acten",
        help="Slug del tenant para --all-incomplete y --by-title (default: acten).",
    )
    parser.add_argument(
        "--rehydrate", action="store_true",
        help="Vuelve a traer transcript + summary desde Fireflies antes de re-IA.",
    )
    parser.add_argument(
        "--all-incomplete", action="store_true",
        help="Reintenta todas las sesiones del tenant que no completaron OK.",
    )
    parser.add_argument(
        "--by-title", default="",
        help="Reintenta sesiones cuyo título contenga este substring (case-insensitive).",
    )
    args = parser.parse_args()

    target_ids = _resolve_target_session_ids(args)
    if not target_ids:
        logger.info("No hay sesiones que reintentar.")
        return

    logger.info("Sesiones a reintentar: %s", target_ids)
    failures = asyncio.run(main_async(target_ids, args.rehydrate))
    if failures:
        logger.error("%s de %s reintentos fallaron.", failures, len(target_ids))
        sys.exit(1)
    logger.info("Todos los reintentos OK (%s).", len(target_ids))


if __name__ == "__main__":
    main()
