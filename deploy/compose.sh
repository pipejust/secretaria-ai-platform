#!/usr/bin/env sh
# Resuelve los build contexts desde la raíz, incluso invocado desde otro directorio.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
variant=${1:-}
case "$variant" in
  prod|supabase) shift ;;
  *) echo "Uso: $0 prod|supabase <comando de docker compose>" >&2; exit 2 ;;
esac
exec docker compose --project-directory "$root" \
  --env-file "${ACTEN_ENV_FILE:-$root/.env.production}" \
  -f "$root/deploy/docker-compose.$variant.yml" "$@"
