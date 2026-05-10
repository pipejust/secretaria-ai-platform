#!/usr/bin/env bash
# Entrypoint del orquestador. Cumple PROTOCOLO ÚNICO.
# Uso:  ./orchestration/scripts/run.sh [--from-phase A|B|C|D|E|F|G] [--sprint NN]
#
# Idempotente. Cada fase escribe su estado en runs/<RUN_ID>/state.json y la
# siguiente corrida puede saltar fases ya completadas con --from-phase.

set -Eeuo pipefail
IFS=$'\n\t'

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# Cargar lib en orden estable.
# shellcheck source=lib/log.sh
source "orchestration/scripts/lib/log.sh"
# shellcheck source=lib/git_safe.sh
source "orchestration/scripts/lib/git_safe.sh"
# shellcheck source=lib/no_hardcode.sh
source "orchestration/scripts/lib/no_hardcode.sh"
# shellcheck source=lib/verify.sh
source "orchestration/scripts/lib/verify.sh"
# shellcheck source=lib/selfheal.sh
source "orchestration/scripts/lib/selfheal.sh"
# shellcheck source=lib/agent.sh
source "orchestration/scripts/lib/agent.sh"

FROM_PHASE="A"
ONLY_SPRINT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --from-phase) FROM_PHASE="$2"; shift 2 ;;
    --sprint) ONLY_SPRINT="$2"; shift 2 ;;
    -h|--help)
      sed -n '1,12p' "$0"; exit 0 ;;
    *) log::warn "Argumento ignorado: $1"; shift ;;
  esac
done

# Resolver RUN_ID. Si ya hay uno cacheado y la fase es A, generar nuevo.
# Si fase != A, exigir un RUN_ID existente.
if [[ "${FROM_PHASE}" == "A" || -z "${RUN_ID:-}" ]]; then
  export RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
fi
RUN_DIR="runs/${RUN_ID}"
mkdir -p "${RUN_DIR}"/{logs,artifacts,agents,ui,dashboard}
log::set_dir "${RUN_DIR}/logs"
log::info "RUN_ID=${RUN_ID}  (fase inicial: ${FROM_PHASE})"

# === Fase A: Bootstrap ===
if [[ "${FROM_PHASE}" == "A" ]]; then
  bash orchestration/scripts/00_bootstrap.sh "${RUN_ID}"
  FROM_PHASE="B"
fi

# === Fase B: Análisis ===
if [[ "${FROM_PHASE}" =~ ^[AB]$ ]]; then
  bash orchestration/scripts/01_analyze.sh "${RUN_ID}"
  FROM_PHASE="C"
fi

# === Fase C: Plan por sprint ===
if [[ "${FROM_PHASE}" =~ ^[ABC]$ ]]; then
  bash orchestration/scripts/02_plan.sh "${RUN_ID}"
  FROM_PHASE="D"
fi

# === Fase D: Build + tests con self-heal por sprint ===
if [[ "${FROM_PHASE}" =~ ^[ABCD]$ ]]; then
  if [[ -n "${ONLY_SPRINT}" ]]; then
    bash orchestration/scripts/03_sprint.sh "${RUN_ID}" "${ONLY_SPRINT}"
  else
    for sprint_file in orchestration/sprints/sprint_*.md; do
      sprint_id="$(basename "$sprint_file" .md | sed 's/^sprint_//')"
      bash orchestration/scripts/03_sprint.sh "${RUN_ID}" "${sprint_id}"
    done
  fi
  FROM_PHASE="E"
fi

# === Fase E: Pruebas transversales ===
if [[ "${FROM_PHASE}" =~ ^[ABCDE]$ ]]; then
  bash orchestration/scripts/04_crosscut_tests.sh "${RUN_ID}" full
  FROM_PHASE="F"
fi

# === Fase F: ya integrada en cada sprint (UX/UI por pantalla) — no-op aquí ===
# Las screens se construyen DENTRO del sprint correspondiente (Fase D),
# delegado al rol uxui. Este placeholder existe por consistencia con el
# protocolo; no requiere script propio.

# === Fase G: Release ===
if [[ "${FROM_PHASE}" =~ ^[ABCDEFG]$ ]]; then
  bash orchestration/scripts/99_release.sh "${RUN_ID}"
fi

log::info "Pipeline completo. Resumen en ${RUN_DIR}/_summary.md"
