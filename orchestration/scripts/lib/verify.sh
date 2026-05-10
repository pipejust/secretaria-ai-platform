# shellcheck shell=bash
# Verifier: compara crosscut.report.json contra quality_gates.yaml.
# Emite APPROVE o REJECT con razones.

verify::sprint_or_global() {
  local run_id="$1"
  local scope="${2:-sprint}"          # sprint | global
  local sprint_id="${3:-}"
  local report_path="runs/${run_id}/crosscut.report.json"
  local gates="orchestration/config/quality_gates.yaml"
  local out_dir="runs/${run_id}/agents/${sprint_id:-_global}"
  mkdir -p "${out_dir}"
  local out="${out_dir}/verifier.json"

  if [[ ! -f "${report_path}" ]]; then
    cat > "${out}" <<EOF
{ "decision": "REJECT", "reasons": ["crosscut.report.json no existe — el tester no corrió"] }
EOF
    return 2
  fi

  python3 - "${report_path}" "${gates}" "${out}" <<'PY'
import json, sys, pathlib
report_p, gates_p, out_p = sys.argv[1:]
report = json.loads(pathlib.Path(report_p).read_text())
# YAML-lite parser: usamos un mini regex para extraer los campos numéricos
# que usamos. Es suficiente para los gates que controlamos.
import re
gates_text = pathlib.Path(gates_p).read_text()
def num(key, default=None):
    m = re.search(rf'{re.escape(key)}\s*:\s*([0-9.]+)', gates_text)
    return float(m.group(1)) if m else default

reasons = []

def check(name, value, gate, op):
    if value is None or gate is None: return
    ok = (value >= gate) if op == '>=' else (value <= gate)
    if not ok:
        reasons.append(f"{name} = {value} no cumple {op} {gate}")

# Subset crítico (el reporte completo lo expande crosscut_tests.sh)
check('coverage.backend.line', report.get('coverage',{}).get('backend',{}).get('line'),
      num('line_coverage_min'), '>=')
check('coverage.frontend.line', report.get('coverage',{}).get('frontend',{}).get('line'),
      num('line_coverage_min'), '>=')
check('lighthouse.performance', report.get('lighthouse',{}).get('performance'),
      num('performance'), '>=')
check('lighthouse.accessibility', report.get('lighthouse',{}).get('accessibility'),
      num('accessibility'), '>=')
check('a11y.serious', report.get('a11y',{}).get('serious'),
      num('serious_violations_max'), '<=')
check('visual.diff_pct_max', report.get('visual',{}).get('diff_pct_max'),
      num('diff_pct_max'), '<=')
check('security.semgrep_high', report.get('security',{}).get('semgrep_high'),
      num('high_max'), '<=')
check('e2e.pass_rate', report.get('e2e',{}).get('pass_rate'),
      num('required_pass_rate'), '>=')

decision = "APPROVE" if not reasons else "REJECT"
pathlib.Path(out_p).write_text(json.dumps({
    "decision": decision,
    "reasons": reasons,
    "evaluated_at_utc": __import__('datetime').datetime.utcnow().isoformat()+"Z",
}, indent=2))
print(f"VERIFIER → {decision}  ({len(reasons)} razón(es))")
sys.exit(0 if decision == "APPROVE" else 2)
PY
}
