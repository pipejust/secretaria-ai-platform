# shellcheck shell=bash
# Helper para invocar agentes del swarm. Esto es un dispatcher delgado:
# escribe el contexto en runs/<RUN_ID>/agents/<sprint>/<role>.input.json y
# espera que el LLM (Claude Code en este mismo loop, o un cron externo)
# escriba <role>.output.json. La política de timeout/retry está en
# selfheal.sh.
#
# Nota: este orquestador es agnóstico al runtime del agente. La integración
# real con `superpowers:dispatching-parallel-agents` se hace en la capa
# superior (Claude Code) leyendo los .input.json y respondiendo con
# .output.json.

agent::dispatch() {
  local run_id="$1"
  local sprint_id="$2"
  local role="$3"        # architect|builder|tester|uxui|security|self_heal|verifier
  local input_payload="$4"
  local out_dir="runs/${run_id}/agents/${sprint_id}"
  mkdir -p "${out_dir}"
  local in_file="${out_dir}/${role}.input.json"
  local out_file="${out_dir}/${role}.output.json"
  printf '%s\n' "${input_payload}" > "${in_file}"
  log::info "agent::dispatch role=${role} sprint=${sprint_id} input=${in_file}"
  # Marca de "pendiente" para que el orquestador sepa qué pedir al LLM.
  if [[ ! -f "${out_file}" ]]; then
    cat > "${out_file}" <<EOF
{ "status": "pending", "role": "${role}", "input_path": "${in_file}",
  "instructions": "Invoca el rol ${role} con orchestration/prompts/role_${role}.md leyendo ${in_file} y escribe el resultado aquí." }
EOF
  fi
}
