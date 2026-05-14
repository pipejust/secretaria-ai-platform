"""Re-fetch del resumen ejecutivo nativo de Fireflies para sesiones que no lo tienen.

Casos típicos que esto repara:
- Sesiones legacy creadas antes de que el pipeline supiera traer summary.
- Sesiones cuyo webhook llegó antes de que Fireflies terminara de generar
  el summary asincrónico (caso muy común: Fireflies tarda 30s-2min en tener
  el summary listo después de que el transcript ya está disponible).
- Sesiones donde el cleanup con Groq se cayó y dejó el summary vacío.

Uso:
    # Backfill de TODAS las sesiones del tenant que no tienen summary.
    docker compose exec backend python -m scripts.backfill_summaries --tenant acten

    # Backfill de TODOS los tenants (uno por uno).
    docker compose exec backend python -m scripts.backfill_summaries --all-tenants

    # Backfill de sesiones específicas por ID interno.
    docker compose exec backend python -m scripts.backfill_summaries 12 14 27

    # Backfill por título (substring case-insensitive).
    docker compose exec backend python -m scripts.backfill_summaries \
        --tenant acten --by-title "Despliegue release"

    # Forzar re-fetch incluso si la sesión ya tiene summary (útil si querés
    # refrescar la versión cacheada porque Fireflies actualizó algo).
    docker compose exec backend python -m scripts.backfill_summaries \
        --tenant acten --force

    # Dry-run: muestra qué se haría sin tocar la DB.
    docker compose exec backend python -m scripts.backfill_summaries \
        --tenant acten --dry-run

Idempotente: nunca sobreescribe un summary existente salvo `--force`.
Rate-limit: espera 1 s entre sesiones para no martillar la API de Fireflies.
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
from services.fireflies_service import (
    get_fireflies_api_key,
    refetch_summary_for_session,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("backfill_summaries")


def _resolve_tenant_id(db: Session, slug: str) -> Optional[int]:
    t = db.exec(select(Tenant).where(Tenant.slug == slug)).first()
    if not t:
        return None
    return t.id


def _list_target_sessions(
    args: argparse.Namespace,
) -> list[tuple[int, int]]:
    """Devuelve [(session_id, tenant_id), ...] de las sesiones a procesar."""
    with Session(engine) as db:
        if args.ids:
            rows = []
            for sid in args.ids:
                s = db.get(MeetingSession, int(sid))
                if not s:
                    logger.warning("Sesión id=%s no existe, salteando.", sid)
                    continue
                rows.append(s)
        else:
            q = select(MeetingSession).order_by(MeetingSession.id.desc())
            if not args.all_tenants:
                tenant_id = _resolve_tenant_id(db, args.tenant)
                if tenant_id is None:
                    raise SystemExit(f"Tenant '{args.tenant}' no existe.")
                q = q.where(MeetingSession.tenant_id == tenant_id)
            rows = db.exec(q).all()

            if args.by_title:
                needle = args.by_title.lower().strip()
                rows = [r for r in rows if needle in (r.title or "").lower()]

            if not args.force:
                rows = [r for r in rows if not (r.raw_summary or "").strip()]

        # Filtramos las que no tienen fireflies_id — sin id no hay nada que pedir.
        rows = [r for r in rows if (r.fireflies_id or "").strip()]
        return [(r.id, r.tenant_id) for r in rows]


async def backfill_one(session_id: int, tenant_id: int, *, force: bool, dry_run: bool) -> str:
    with Session(engine) as db:
        s = db.get(MeetingSession, session_id)
        if not s:
            return f"SKIP id={session_id} no existe"
        if (s.raw_summary or "").strip() and not force:
            return f"SKIP id={session_id} ya tiene summary ({len(s.raw_summary)}c)"
        if not s.fireflies_id:
            return f"SKIP id={session_id} sin fireflies_id"

        api_key = get_fireflies_api_key(db, tenant_id)
        if not api_key:
            return f"ERROR id={session_id} tenant {tenant_id} sin Fireflies API key"

        if dry_run:
            return f"DRY-RUN id={session_id} ff_id={s.fireflies_id} → fetch summary"

        try:
            ok = await refetch_summary_for_session(
                db, s, api_key=api_key, clean_with_groq=True, max_attempts=3,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Backfill falló para id=%s", session_id)
            return f"ERROR id={session_id}: {exc}"

        # Re-leer estado final.
        db.refresh(s)
        if ok:
            return (
                f"OK id={s.id} '{(s.title or '')[:60]}' "
                f"summary={len(s.raw_summary or '')}c"
            )
        return f"EMPTY id={s.id} Fireflies sigue sin summary para {s.fireflies_id}"


async def main_async(targets: list[tuple[int, int]], *, force: bool, dry_run: bool, sleep_between: float) -> int:
    failures = 0
    empties = 0
    skipped = 0
    succeeded = 0
    for sid, tid in targets:
        result = await backfill_one(sid, tid, force=force, dry_run=dry_run)
        logger.info("[id=%s] %s", sid, result)
        if result.startswith("ERROR"):
            failures += 1
        elif result.startswith("EMPTY"):
            empties += 1
        elif result.startswith("SKIP"):
            skipped += 1
        elif result.startswith("OK"):
            succeeded += 1
        if sleep_between > 0 and not dry_run:
            await asyncio.sleep(sleep_between)
    logger.info(
        "=== Resumen: %s OK, %s vacías (Fireflies sin summary), %s salteadas, %s errores. ===",
        succeeded, empties, skipped, failures,
    )
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "ids", nargs="*",
        help="IDs internos de sesiones específicas. Si se pasan, ignora los demás filtros.",
    )
    parser.add_argument(
        "--tenant", default="acten",
        help="Slug del tenant (default: acten). Ignorado con --all-tenants.",
    )
    parser.add_argument(
        "--all-tenants", action="store_true",
        help="Procesa sesiones de TODOS los tenants.",
    )
    parser.add_argument(
        "--by-title", default="",
        help="Filtra por substring case-insensitive en el título.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-fetcha incluso sesiones que ya tienen summary.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Muestra qué se haría sin tocar la DB ni llamar a Fireflies.",
    )
    parser.add_argument(
        "--sleep", type=float, default=1.0,
        help="Segundos a esperar entre sesiones (rate limit). Default: 1.0",
    )
    args = parser.parse_args()

    targets = _list_target_sessions(args)
    if not targets:
        logger.info("No hay sesiones que procesar.")
        return

    logger.info("Procesando %s sesiones%s.", len(targets), " (DRY-RUN)" if args.dry_run else "")
    failures = asyncio.run(
        main_async(targets, force=args.force, dry_run=args.dry_run, sleep_between=args.sleep)
    )
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
