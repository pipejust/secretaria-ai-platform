#!/usr/bin/env bash
# Fase E — Pruebas transversales. Detecta qué hay instalado y corre lo que
# se pueda; el resto lo registra como TODO en el reporte.

set -Eeuo pipefail
RUN_ID="${1:?RUN_ID requerido}"
SCOPE="${2:-full}"               # full | quick
RUN_DIR="runs/${RUN_ID}"

# shellcheck source=lib/log.sh
source "orchestration/scripts/lib/log.sh"
log::set_dir "${RUN_DIR}/logs"
log::info "Fase E — pruebas transversales (scope=${SCOPE})."

REPORT="${RUN_DIR}/crosscut.report.json"

python3 - "${RUN_ID}" "${SCOPE}" <<'PY'
import json, sys, pathlib, subprocess, shutil, os, time
run_id, scope = sys.argv[1], sys.argv[2]
report_path = pathlib.Path(f'runs/{run_id}/crosscut.report.json')
report = {
    "run_id": run_id, "scope": scope,
    "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "coverage": {"backend": {}, "frontend": {}},
    "lighthouse": {},
    "a11y": {},
    "visual": {},
    "security": {},
    "dependencies": {},
    "e2e": {},
    "load": {},
    "todo": [],
}

def has(cmd):
    return shutil.which(cmd) is not None

# ---- Backend tests / coverage ----
if has('pytest'):
    try:
        subprocess.run(['pytest','--maxfail=1','-q','--cov=backend','--cov-report=json:cov.json',
                        'backend/tests'], check=False, timeout=300)
        cov_file = pathlib.Path('cov.json')
        if cov_file.exists():
            cov = json.loads(cov_file.read_text())
            report['coverage']['backend']['line'] = cov.get('totals',{}).get('percent_covered',0)/100.0
            cov_file.unlink()
    except Exception as e:
        report['todo'].append(f'backend pytest falló: {e}')
else:
    report['todo'].append('pytest no instalado en runner')

# ---- Frontend tests / coverage ----
if pathlib.Path('frontend/package.json').exists() and has('npm'):
    try:
        # Si no hay script "test", solo registramos
        pkg = json.loads(pathlib.Path('frontend/package.json').read_text())
        if 'test' in pkg.get('scripts', {}):
            subprocess.run(['npm','test','--','--watch=false','--code-coverage'], cwd='frontend',
                           check=False, timeout=600)
            cov_summary = pathlib.Path('frontend/coverage/coverage-summary.json')
            if cov_summary.exists():
                cs = json.loads(cov_summary.read_text())
                report['coverage']['frontend']['line'] = cs.get('total',{}).get('lines',{}).get('pct',0)/100.0
        else:
            report['todo'].append('frontend sin script "test" en package.json')
    except Exception as e:
        report['todo'].append(f'frontend tests fallaron: {e}')
else:
    report['todo'].append('npm no disponible')

# ---- Lighthouse / a11y / visual / load: dependen de servicios externos ----
report['todo'] += [
    'Lighthouse CI (instalar @lhci/cli y configurar lhci-budget.json)',
    'axe-core con Playwright (tests/a11y/*.spec.ts)',
    'Visual regression con Playwright screenshots vs baseline',
    'Load tests con k6 (tests/load/*.js)',
    'Security: semgrep/bandit/eslint-plugin-security (instalar y registrar reglas)',
    'Dependency audit: npm audit + pip-audit',
    'Schemathesis contra OpenAPI publicado',
]

# ---- Sentinels mínimos para que verify.sh tenga algo ----
report['lighthouse'].setdefault('performance', None)
report['lighthouse'].setdefault('accessibility', None)
report['a11y'].setdefault('serious', None)
report['visual'].setdefault('diff_pct_max', None)
report['security'].setdefault('semgrep_high', None)
report['e2e'].setdefault('pass_rate', None)

report['ended_at_utc'] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
print(f"crosscut.report.json escrito en {report_path}")
PY

# Dashboard estático mínimo
cat > "${RUN_DIR}/dashboard/index.html" <<'HTML'
<!doctype html>
<html lang="es"><head><meta charset="utf-8"><title>Notiva — RUN dashboard</title>
<style>body{font-family:system-ui;margin:2rem;max-width:900px;background:#0f172a;color:#e2e8f0}
pre{background:#1e293b;padding:1rem;border-radius:8px;overflow:auto}h1{color:#7dd3fc}</style></head>
<body><h1>Notiva — Crosscut report</h1>
<p>Reporte real en <code>../crosscut.report.json</code>.</p>
<pre id="r">cargando…</pre>
<script>fetch('../crosscut.report.json').then(r=>r.json()).then(j=>{
  document.getElementById('r').textContent=JSON.stringify(j,null,2);
});</script></body></html>
HTML

cat > "${RUN_DIR}/state.json" <<EOF
{ "run_id": "${RUN_ID}", "phase_completed": "E", "next_phase": "F",
  "completed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)" }
EOF
log::ok "Fase E: reporte y dashboard generados."
