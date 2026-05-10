#!/usr/bin/env bash
# Fase B — Análisis del repositorio (mapa, abstracciones, deuda técnica).
# Genera repo.snapshot.json y analysis.md con datos OBJETIVOS extraídos
# del filesystem. La capa de "decisiones previas" (claude-mem) la registra
# Claude Code como pasos manuales (escribe agent.input.json y espera
# agent.output.json).

set -Eeuo pipefail
RUN_ID="${1:?RUN_ID requerido}"
RUN_DIR="runs/${RUN_ID}"
# shellcheck source=lib/log.sh
source "orchestration/scripts/lib/log.sh"
log::set_dir "${RUN_DIR}/logs"
log::info "Fase B — análisis."

python3 - "${RUN_ID}" <<'PY'
import json, os, sys, pathlib, re
run_id = sys.argv[1]
root = pathlib.Path('.')

def file_loc(p: pathlib.Path) -> int:
    try: return sum(1 for _ in p.read_text(errors='ignore').splitlines())
    except: return 0

def collect(globs):
    out = []
    for g in globs:
        out += [str(p) for p in root.glob(g) if p.is_file()]
    return sorted(out)

snapshot = {
    "run_id": run_id,
    "backend_modules": {
        "routers": collect(['backend/routers/*.py']),
        "services": collect(['backend/services/*.py','backend/services/integrations/*.py']),
        "crud": collect(['backend/crud/*.py']),
        "models_loc": file_loc(pathlib.Path('backend/models.py')),
    },
    "frontend_components": collect([
        'frontend/src/app/components/**/*.component.ts',
        'frontend/src/app/components/**/*.ts',
    ]),
    "frontend_routes": file_loc(pathlib.Path('frontend/src/app/app.routes.ts')),
    "tests_present": {
        "backend": collect(['backend/tests/**/*.py','backend/test_*.py']),
        "frontend": collect(['frontend/src/**/*.spec.ts','frontend/tests/**/*.ts']),
    },
    "ci_workflows": collect(['.github/workflows/*.yml','.github/workflows/*.yaml']),
    "design_tokens_file_present": pathlib.Path('frontend/src/styles/tokens.css').exists(),
    "i18n_dirs_present": [str(p) for p in root.glob('frontend/src/i18n/**') if p.is_dir()],
    "potential_tech_debt": [],
}

# Reportar deuda técnica detectada por reglas simples
DEBT_RULES = [
    ('backend bare exceptions', collect(['backend/**/*.py']), r'^\s*except\s*:\s*$'),
    ('backend print() en código no-test', collect(['backend/**/*.py']), r'print\('),
    ('frontend any tipo', collect(['frontend/src/**/*.ts']), r'\:\s*any\b'),
]
for label, files, pattern in DEBT_RULES:
    rx = re.compile(pattern, re.M)
    matches = []
    for f in files:
        if any(skip in f for skip in ('/__pycache__/','/node_modules/','/.angular/','/dist/','test_','/tests/')):
            continue
        try:
            txt = pathlib.Path(f).read_text(errors='ignore')
        except: continue
        for m in rx.finditer(txt):
            line = txt[:m.start()].count('\n')+1
            matches.append({'file': f, 'line': line})
    snapshot['potential_tech_debt'].append({
        'rule': label,
        'occurrences': len(matches),
        'sample': matches[:5],
    })

pathlib.Path(f'runs/{run_id}/repo.snapshot.json').write_text(json.dumps(snapshot, indent=2, ensure_ascii=False))

# analysis.md de alto nivel
md = [f"# Análisis del repositorio — RUN {run_id}\n"]
md.append(f"## Backend\n")
md.append(f"- {len(snapshot['backend_modules']['routers'])} routers")
md.append(f"- {len(snapshot['backend_modules']['services'])} services")
md.append(f"- models.py: {snapshot['backend_modules']['models_loc']} LOC")
md.append(f"\n## Frontend\n")
md.append(f"- {len(snapshot['frontend_components'])} files de componentes")
md.append(f"- routes: {snapshot['frontend_routes']} LOC")
md.append(f"- design tokens file: {'SÍ' if snapshot['design_tokens_file_present'] else 'NO (crear en sprint 4)'}")
md.append(f"- i18n: {'configurado' if snapshot['i18n_dirs_present'] else 'NO configurado (crear en sprint 4)'}")
md.append(f"\n## Tests\n")
md.append(f"- Backend: {len(snapshot['tests_present']['backend'])} archivos")
md.append(f"- Frontend: {len(snapshot['tests_present']['frontend'])} archivos")
md.append(f"\n## CI\n")
md.append(f"- workflows: {len(snapshot['ci_workflows'])} (CI {'configurado' if snapshot['ci_workflows'] else 'AUSENTE — crear en sprint 0'})")
md.append(f"\n## Deuda técnica detectada\n")
for d in snapshot['potential_tech_debt']:
    md.append(f"- **{d['rule']}**: {d['occurrences']} ocurrencias")

pathlib.Path(f'runs/{run_id}/analysis.md').write_text('\n'.join(md))
print('repo.snapshot.json + analysis.md generados.')
PY

cat > "${RUN_DIR}/state.json" <<EOF
{ "run_id": "${RUN_ID}", "phase_completed": "B", "next_phase": "C",
  "completed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)" }
EOF
log::ok "Fase B completada."
