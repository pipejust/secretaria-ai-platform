#!/usr/bin/env bash
# Fase D — Build + tests + self-heal por sprint, en worktree aislado.

set -Eeuo pipefail
RUN_ID="${1:?RUN_ID requerido}"
SPRINT_ID="${2:?SPRINT_ID requerido (ej: 00_foundation)}"
RUN_DIR="runs/${RUN_ID}"

# shellcheck source=lib/log.sh
source "orchestration/scripts/lib/log.sh"
# shellcheck source=lib/git_safe.sh
source "orchestration/scripts/lib/git_safe.sh"
# shellcheck source=lib/agent.sh
source "orchestration/scripts/lib/agent.sh"
# shellcheck source=lib/no_hardcode.sh
source "orchestration/scripts/lib/no_hardcode.sh"
# shellcheck source=lib/selfheal.sh
source "orchestration/scripts/lib/selfheal.sh"
# shellcheck source=lib/verify.sh
source "orchestration/scripts/lib/verify.sh"

log::set_dir "${RUN_DIR}/logs"
log::info "Fase D — sprint ${SPRINT_ID}"

mkdir -p "${RUN_DIR}/agents/${SPRINT_ID}"

# 1. Crear worktree (idempotente).
git_safe::create_sprint_worktree "${SPRINT_ID}" || true

# 2. Builder (espera architect.output.json del paso anterior).
agent::dispatch "${RUN_ID}" "${SPRINT_ID}" builder "$(cat <<EOF
{
  "task": "Implementa el plan del architect respetando TDD (test antes que código). Usa worktree .worktrees/sprint-${SPRINT_ID}.",
  "plan_input": "runs/${RUN_ID}/agents/${SPRINT_ID}/architect.output.json",
  "skills_to_invoke": ["superpowers:test-driven-development","superpowers:dispatching-parallel-agents","superpowers:subagent-driven-development"],
  "constraints": ["zero-hardcoding","no protected paths","commits convencionales"]
}
EOF
)"

# 3. Tester (suite específica + transversal aplicable).
agent::dispatch "${RUN_ID}" "${SPRINT_ID}" tester "$(cat <<EOF
{
  "task": "Escribe y corre la suite del sprint ${SPRINT_ID}. Reporta cobertura como JSON.",
  "skills_to_invoke": ["engineering:testing-strategy","python-testing-patterns","javascript-testing-patterns","e2e-testing-patterns"]
}
EOF
)"

# 4. UX/UI agent si el sprint toca pantallas (heurística por nombre)
if [[ "${SPRINT_ID}" =~ (ui|frontend|design|pantalla|mobile) ]]; then
  agent::dispatch "${RUN_ID}" "${SPRINT_ID}" uxui "$(cat <<EOF
{
  "task": "Construye/refina pantallas del sprint con DS, responsive, a11y, microcopy en i18n. Capturas en runs/${RUN_ID}/ui/<screen>/<bp>.png para 360,414,768,1024,1440,1920.",
  "skills_to_invoke": ["ui-ux-pro-max","frontend-design","emil-design-eng","design","accessibility-compliance-accessibility-audit","wcag-audit-patterns"]
}
EOF
)"
fi

# 5. Security
agent::dispatch "${RUN_ID}" "${SPRINT_ID}" security "$(cat <<EOF
{
  "task": "Análisis de seguridad estática (semgrep, bandit, eslint-security) y revisión de dependencias (pip-audit, npm audit). Reporta JSON.",
  "skills_to_invoke": ["security-review","security-auditor","security-scanning-security-sast","security-scanning-security-dependencies"]
}
EOF
)"

# 6. no_hardcode_scan
no_hardcode::scan "${RUN_ID}" "${SPRINT_ID}" || {
  log::warn "no_hardcode_scan reporta violaciones high. Self-heal a invocar."
  selfheal::run "${RUN_ID}" "${SPRINT_ID}" \
    "no_hardcode::scan ${RUN_ID} ${SPRINT_ID}" \
    "${RUN_DIR}/agents/${SPRINT_ID}/no_hardcode.json" || true
}

# 7. Verifier (depende de crosscut.report.json que produce 04_crosscut_tests.sh)
log::info "Verifier corre en Fase E o cuando crosscut.report.json esté listo."
agent::dispatch "${RUN_ID}" "${SPRINT_ID}" verifier "$(cat <<EOF
{
  "task": "Revisar reporte y emitir APPROVE/REJECT respecto a quality_gates.yaml.",
  "report_path": "runs/${RUN_ID}/crosscut.report.json",
  "skills_to_invoke": ["verification-quality","verification-loop","verification-before-completion"]
}
EOF
)"

cat > "${RUN_DIR}/agents/${SPRINT_ID}/state.json" <<EOF
{ "sprint_id": "${SPRINT_ID}", "phase": "D", "status": "agents_dispatched",
  "completed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)" }
EOF
log::ok "Sprint ${SPRINT_ID}: agentes despachados. Esperando outputs."
