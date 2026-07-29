"""Limpia los `actionitem.due_date` que no son fechas.

`due_date` es una columna de texto y el extractor viejo guardaba ahí la
respuesta literal del LLM. En producción aparecieron 20 filas con cuatro
variantes de «no hay fecha» («No especificada», «No especificado», «No
proporcionado», «No se proporcionó») repartidas en dos tenants.

Desde `date_utils.normalize_due_date` la escritura ya no puede meter texto
libre, pero eso no arregla lo ya guardado: esas filas siguen saliendo
crudas por el detalle de sesión, los PDF/DOCX generados y la búsqueda, y
hacen que editar cualquier otro campo de la tarea reviente con un 422.

La producción de Acten ya se limpió a mano el 2026-07-28. Este script
existe para el resto de entornos (staging, copias locales) y para que la
invariante «o es YYYY-MM-DD o es NULL» valga también hacia atrás.

Es idempotente: correrlo dos veces no cambia nada la segunda vez.

    python -m scripts.backfill_due_date_iso --dry-run   # sólo listar
    python -m scripts.backfill_due_date_iso             # aplicar
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, select     # noqa: E402

from database import engine              # noqa: E402
from date_utils import normalize_due_date  # noqa: E402
from models import ActionItem            # noqa: E402


def encontrar_sucias(db: Session) -> list[ActionItem]:
    """Filas cuyo `due_date` no es NULL y tampoco es una fecha.

    Incluye la cadena vacía. `''` es inofensiva —todo el código la trata
    como «sin fecha»— pero deja la invariante a medias, y a NULL no le
    cuesta nada.
    """
    candidatas = db.exec(
        select(ActionItem).where(ActionItem.due_date.is_not(None))
    ).all()
    return [it for it in candidatas if normalize_due_date(it.due_date) is None]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Lista lo que se limpiaría sin tocar la base de datos.",
    )
    args = parser.parse_args()

    with Session(engine) as db:
        sucias = encontrar_sucias(db)

        if not sucias:
            print("Nada que limpiar: todos los due_date son fecha o NULL.")
            return 0

        print(f"{len(sucias)} fila(s) con due_date que no es fecha:")
        for it in sucias:
            print(f"  id={it.id} tenant={it.tenant_id} "
                  f"due_date={it.due_date!r} title={(it.title or '')[:60]!r}")

        if args.dry_run:
            print("\n--dry-run: no se modificó nada.")
            return 0

        for it in sucias:
            it.due_date = None
            db.add(it)
        db.commit()
        print(f"\nListo: {len(sucias)} fila(s) con due_date = NULL.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
