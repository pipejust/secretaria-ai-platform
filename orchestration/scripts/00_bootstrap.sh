#!/usr/bin/env bash
# Fase A — Bootstrap del orquestador.
# - asegura runs/<RUN_ID> y orchestration/
# - re-detecta stack y refresca runs/<RUN_ID>/stack.detected.json
# - copia roadmap a la raíz si no está
# - registra skills no disponibles en runs/<RUN_ID>/missing_skills.md

set -Eeuo pipefail
RUN_ID="${1:?RUN_ID requerido}"
RUN_DIR="runs/${RUN_ID}"

# shellcheck source=lib/log.sh
source "orchestration/scripts/lib/log.sh"
log::set_dir "${RUN_DIR}/logs"
log::info "Fase A — bootstrap arrancando."

mkdir -p "${RUN_DIR}"/{logs,artifacts,agents,ui,dashboard}

# 1. roadmap.md en raíz
if [[ ! -f roadmap.md && -f docs/roadmap.md ]]; then
  cp docs/roadmap.md roadmap.md
  log::ok "roadmap.md copiado a raíz desde docs/roadmap.md"
fi
[[ -f roadmap.md ]] || { log::error "roadmap.md no existe en raíz ni en docs/. Aborta."; exit 1; }

# 2. Re-detección de stack (idempotente)
python3 - "${RUN_ID}" <<'PY'
import json, sys, pathlib, subprocess
run_id = sys.argv[1]
out = pathlib.Path(f'runs/{run_id}/stack.detected.json')
if out.exists():
    print(f"stack.detected.json ya existe; bootstrap no lo sobrescribe.")
    sys.exit(0)
# Re-ejecutar detección mínima (versión reducida del bloque inicial)
backend_req = pathlib.Path('backend/requirements.txt')
node_pkg = pathlib.Path('frontend/package.json')
data = {
    "detected_at_utc": subprocess.check_output(['date','-u','+%Y-%m-%dT%H:%M:%SZ']).decode().strip(),
    "backend_requirements_present": backend_req.exists(),
    "frontend_package_present": node_pkg.exists(),
    "note": "Generado por 00_bootstrap.sh con detección mínima. Para detalle ver el JSON producido por la corrida inicial."
}
out.write_text(json.dumps(data, indent=2))
print(f"escrito: {out}")
PY

# 3. Skills no disponibles
mkdir -p "${RUN_DIR}"
cat > "${RUN_DIR}/missing_skills.md" <<'EOF'
# Skills no disponibles en este entorno

Mapeo de skills mencionadas en PROMPT_CLAUDE_CODE.docx que NO están
instaladas en `~/.claude/skills/`, con su equivalente funcional.

| Skill solicitada                       | Estado       | Equivalente / fallback           |
|----------------------------------------|--------------|----------------------------------|
| `productivity:memory-management`       | unavailable  | `memory-systems` + `claude-mem-knowledge-agent` |
| `engineering:incident-response`        | unavailable  | `incident-response-incident-response`, `incident-runbook-templates` |
| `design:design-system-management`      | unavailable  | `design` (paquete unificado)     |
| `design:design-handoff`                | unavailable  | `design`                         |
| `design:design-critique`               | unavailable  | `design`                         |
| `design:ux-writing`                    | unavailable  | `design`                         |
| `design:accessibility-review`          | unavailable  | `accessibility-compliance-accessibility-audit`, `wcag-audit-patterns` |

El orquestador respeta estos fallbacks automáticamente vía
`orchestration/config/skills.yaml`.
EOF
log::ok "missing_skills.md generado."

# 4. Estado de la fase
cat > "${RUN_DIR}/state.json" <<EOF
{
  "run_id": "${RUN_ID}",
  "phase_completed": "A",
  "next_phase": "B",
  "started_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

log::ok "Fase A completada."
