#!/usr/bin/env bash
# Fase C — Plan por sprint. Para cada sprint en orchestration/sprints/,
# emite la tarea de planning al rol architect (input.json para que Claude
# Code lo expanda con la skill writing-plans + claude-mem:make-plan).
#
# Cuando todos los .output.json de architect estén en place, esta fase
# es idempotente: detecta los que ya están y no los re-emite.

set -Eeuo pipefail
RUN_ID="${1:?RUN_ID requerido}"
RUN_DIR="runs/${RUN_ID}"
# shellcheck source=lib/log.sh
source "orchestration/scripts/lib/log.sh"
# shellcheck source=lib/agent.sh
source "orchestration/scripts/lib/agent.sh"
log::set_dir "${RUN_DIR}/logs"
log::info "Fase C — planning por sprint."

shopt -s nullglob
sprints=( orchestration/sprints/sprint_*.md )
if (( ${#sprints[@]} == 0 )); then
  log::warn "No hay sprints en orchestration/sprints/. ¿Olvidaste correr el generador?"
fi

for f in "${sprints[@]}"; do
  sprint_id="$(basename "$f" .md | sed 's/^sprint_//')"
  out_dir="${RUN_DIR}/agents/${sprint_id}"
  if [[ -f "${out_dir}/architect.output.json" ]]; then
    log::info "Sprint ${sprint_id}: plan ya existe, skip."
    continue
  fi
  agent::dispatch "${RUN_ID}" "${sprint_id}" architect "$(cat <<EOF
{
  "task": "Genera el Plan técnico del sprint con tareas atómicas (≤1 día) y campos id, title, owner_role, files_touched_glob, dependencies, dod, est_minutes, risk. Marca requires_human_review en pasos sensibles (auth, pagos, datos personales, DDL destructivo).",
  "sprint_md": "$f",
  "stack": "orchestration/config/stack.yaml",
  "gates": "orchestration/config/quality_gates.yaml",
  "skills_to_invoke": ["superpowers:writing-plans","claude-mem:make-plan","engineering:architecture","engineering:system-design"]
}
EOF
)"
done

cat > "${RUN_DIR}/state.json" <<EOF
{ "run_id": "${RUN_ID}", "phase_completed": "C", "next_phase": "D",
  "completed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)" }
EOF
log::ok "Fase C: inputs emitidos. Falta que el LLM genere los architect.output.json para cada sprint."
