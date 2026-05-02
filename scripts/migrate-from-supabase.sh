#!/usr/bin/env bash
# ------------------------------------------------------------------------
# Migrar la base de datos de Supabase → Render Postgres.
#
# Uso:
#   1. Crea el Postgres en Render (manualmente o vía Blueprint con render.yaml).
#   2. En el dashboard de Render → tu Postgres → "Connections", copia
#      la "External Database URL" (la que termina en .render.com).
#   3. En Supabase → Project Settings → Database, copia la "Connection string"
#      en modo "URI" (formato postgres://...).
#   4. Exporta como variables de entorno y corre este script:
#
#      export SUPABASE_URL='postgres://postgres:PASS@db.xxxxx.supabase.co:5432/postgres'
#      export RENDER_URL='postgres://notiva:PASS@dpg-xxxx.oregon-postgres.render.com/notiva'
#      ./scripts/migrate-from-supabase.sh
#
# Requisitos:
#   - postgresql-client 14+ instalado localmente (`brew install postgresql@16`)
#     Asegúrate de que `pg_dump --version` reporte una versión >= la del servidor.
#   - Conectividad de red a ambos hosts (sin VPN bloqueando).
#
# Genera:
#   - backups/supabase-backup-YYYYMMDD-HHMMSS.sql.gz (backup completo)
#   - Carga el dump en Render Postgres.
# ------------------------------------------------------------------------
set -euo pipefail

if [[ -z "${SUPABASE_URL:-}" ]]; then
    echo "ERROR: define SUPABASE_URL antes de ejecutar." >&2
    echo "  export SUPABASE_URL='postgres://postgres:PASS@db.xxxxx.supabase.co:5432/postgres'" >&2
    exit 1
fi

if [[ -z "${RENDER_URL:-}" ]]; then
    echo "ERROR: define RENDER_URL antes de ejecutar." >&2
    echo "  export RENDER_URL='postgres://notiva:PASS@dpg-xxxx.oregon-postgres.render.com/notiva'" >&2
    exit 1
fi

BACKUP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/backups"
mkdir -p "${BACKUP_DIR}"

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
DUMP_FILE="${BACKUP_DIR}/supabase-backup-${TIMESTAMP}.sql"
DUMP_GZ="${DUMP_FILE}.gz"

echo "==> 1/4 Verificando herramientas..."
command -v pg_dump >/dev/null || { echo "ERROR: instala pg_dump (postgresql-client)" >&2; exit 1; }
command -v psql    >/dev/null || { echo "ERROR: instala psql (postgresql-client)" >&2; exit 1; }
command -v gzip    >/dev/null || { echo "ERROR: instala gzip" >&2; exit 1; }
echo "    pg_dump $(pg_dump --version | head -1)"
echo "    psql    $(psql --version | head -1)"

echo "==> 2/4 Dumpeando Supabase a ${DUMP_FILE} ..."
# --no-owner / --no-privileges: el usuario destino en Render es distinto, evitamos GRANT/OWNER inválidos.
# --schema=public: solo lo de la app; ignoramos los esquemas internos de Supabase (auth, storage, realtime, etc.).
# --if-exists --clean: limpia objetos existentes en el destino antes de recrearlos (idempotente).
pg_dump \
    --no-owner \
    --no-privileges \
    --schema=public \
    --if-exists \
    --clean \
    --quote-all-identifiers \
    --format=plain \
    --file="${DUMP_FILE}" \
    "${SUPABASE_URL}"

DUMP_BYTES="$(wc -c < "${DUMP_FILE}" | tr -d ' ')"
echo "    Dump tamaño: ${DUMP_BYTES} bytes"

if [[ "${DUMP_BYTES}" -lt 1024 ]]; then
    echo "ERROR: el dump pesa menos de 1 KB. Aborta y revisa la conexión." >&2
    exit 1
fi

echo "==> 3/4 Comprimiendo a ${DUMP_GZ} ..."
gzip -f "${DUMP_FILE}"

echo "==> 4/4 Restaurando en Render Postgres..."
echo "    Si el destino tiene datos previos, las tablas serán dropeadas (--clean)."
read -r -p "    ¿Continuar? [y/N]: " confirm
if [[ ! "${confirm}" =~ ^[yY]$ ]]; then
    echo "Cancelado por el usuario. El backup queda en ${DUMP_GZ}."
    exit 0
fi

gunzip -c "${DUMP_GZ}" | psql --single-transaction --set ON_ERROR_STOP=on "${RENDER_URL}"

echo ""
echo "==> Listo. Verifica con:"
echo "    psql \"${RENDER_URL}\" -c '\\dt'"
echo ""
echo "Backup conservado en: ${DUMP_GZ}"
