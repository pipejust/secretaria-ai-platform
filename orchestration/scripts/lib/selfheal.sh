# shellcheck shell=bash
# Self-heal recursivo. Invoca el rol self_heal del swarm con el log del fallo
# y aplica el patch resultante. Tope MAX_RETRIES=6.

selfheal::run() {
  local run_id="$1"
  local sprint_id="$2"
  local cmd="$3"                    # comando que falló
  local context_file="$4"           # path al log/error
  local max="${MAX_RETRIES:-6}"
  local i=1
  local agent_dir="runs/${run_id}/agents/${sprint_id}/selfheal"
  mkdir -p "${agent_dir}"

  while (( i <= max )); do
    log::warn "Self-heal intento ${i}/${max} para sprint ${sprint_id}"
    local patch_path="${agent_dir}/attempt_${i}.patch"

    # El rol self_heal genera el patch. La integración real con el LLM se
    # documenta en orchestration/prompts/role_self_heal.md. Aquí asumimos
    # que un proceso externo (Claude Code en este mismo loop, o un job)
    # produce el patch en `${patch_path}` con marcadores PATCH/END.
    # En un primer corrida sin LLM, este script solo registra que necesita
    # intervención manual.

    if [[ ! -s "${patch_path}" ]]; then
      cat > "${patch_path}" <<EOF
# REQUIRES_AGENT_INVOCATION
# Comando que falló: ${cmd}
# Contexto: ${context_file}
# Esta corrida arrancó sin LLM en bucle. Invoca manualmente el rol
# self_heal con orchestration/prompts/role_self_heal.md alimentado con
# el contenido de ${context_file}.
EOF
      log::warn "self_heal: requiere invocación manual del agente. Ver ${patch_path}"
      return 99
    fi

    # Verifica que sea aplicable y NO toque rutas protegidas
    if ! python3 - "${patch_path}" <<'PY'
import sys, re
patch = open(sys.argv[1]).read()
PROTECTED = ['backend/auth_utils.py','backend/services/webhook_security.py',
             'backend/database.py','backend/routers/auth.py']
for p in PROTECTED:
    if re.search(rf'^[+-]{{3}}\s+(?:a|b)/{re.escape(p)}\b', patch, re.M):
        print(f"REJECT: patch toca ruta protegida {p}")
        sys.exit(2)
print("PROTECTED OK")
PY
    then
      log::error "Patch viola rutas protegidas. Escalando."
      return 99
    fi

    if git -C ".worktrees/sprint-${sprint_id}" apply --check "${patch_path}" 2>/dev/null; then
      git -C ".worktrees/sprint-${sprint_id}" apply "${patch_path}"
      log::ok "Patch ${i} aplicado. Reintentando comando."
      if eval "${cmd}"; then
        log::ok "Self-heal exitoso en intento ${i}."
        return 0
      fi
    else
      log::warn "Patch ${i} no aplica limpio."
    fi
    (( i++ ))
  done

  log::error "Self-heal agotó ${max} intentos para sprint ${sprint_id}."
  selfheal::escalate "${run_id}" "${sprint_id}" "${cmd}" "${context_file}"
  return 99
}

selfheal::escalate() {
  local run_id="$1" sprint_id="$2" cmd="$3" ctx="$4"
  local file="runs/${run_id}/agents/${sprint_id}/escalation.md"
  cat > "${file}" <<EOF
# Escalación — sprint ${sprint_id}
**Comando fallido:** \`${cmd}\`
**Contexto:** \`${ctx}\`
**Intentos agotados:** ${MAX_RETRIES:-6}
**Decisión:** este sprint queda BLOQUEADO. El pipeline continúa con los
sprints que NO dependen de este. Revisar manualmente.
EOF
  log::error "Escalación escrita: ${file}"
}
