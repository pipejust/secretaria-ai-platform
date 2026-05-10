# shellcheck shell=bash
# Linter: detecta strings hardcodeados visibles que deberían ir a i18n,
# tokens visuales que deberían ir a CSS vars, URLs de API hardcodeadas, etc.
# Output: runs/<RUN_ID>/agents/<sprint>/no_hardcode.json
#
# Notas:
# - Patrones conservadores (false positives son aceptables; rompen el gate
#   y obligan al builder a justificar o mover a config).
# - No se considera hardcoded lo que esté en archivos *.spec.ts, *.test.py,
#   tests/**, seeds/**, ni dentro de comentarios.

no_hardcode::scan() {
  local run_id="$1"
  local sprint_id="$2"
  local out_dir="runs/${run_id}/agents/${sprint_id}"
  mkdir -p "${out_dir}"
  local out="${out_dir}/no_hardcode.json"

  python3 - "${out}" <<'PY'
import json, os, re, sys, pathlib

OUT = sys.argv[1]
ROOT = pathlib.Path('.')

# Globs a inspeccionar.
TARGETS = []
for ext in ('ts','tsx','html','css','py'):
    TARGETS += list(ROOT.rglob(f'frontend/src/**/*.{ext}'))
    TARGETS += list(ROOT.rglob(f'backend/**/*.{ext}'))

def skip(p: pathlib.Path) -> bool:
    s = str(p)
    return (
        '/node_modules/' in s or '/__pycache__/' in s or '/.git/' in s
        or '/dist/' in s or '/build/' in s or '/.angular/' in s
        or '/tests/' in s or s.endswith('.spec.ts') or s.endswith('_test.py')
        or '/seeds/' in s or '/runs/' in s or '/orchestration/' in s
        or '/.worktrees/' in s
    )

# Patrones (regex, motivo, severidad)
PATTERNS = [
    (re.compile(r'https?://[a-z0-9.\-]+\.(?:com|io|app|onrender\.com|vercel\.app|supabase\.co)[^\s\"\']*', re.I),
     'URL hardcoded — mover a env var o config', 'high'),
    (re.compile(r'#[0-9a-fA-F]{3,8}\b'),
     'Color hex inline — mover a CSS var del Design System', 'medium'),
    (re.compile(r'placeholder\s*=\s*"[^"]+"', re.I),
     'placeholder visible inline — i18n', 'medium'),
    (re.compile(r'>[^<]*[a-záéíóúñ]{5,}[^<]*<', re.I),
     'texto visible directo en template — candidato a i18n', 'low'),
]

violations = []
for f in TARGETS:
    if skip(f): continue
    try:
        text = f.read_text(errors='ignore')
    except Exception:
        continue
    for line_num, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith(('#','//','/*','*')):
            continue
        for rx, reason, sev in PATTERNS:
            m = rx.search(line)
            if not m:
                continue
            # ignore .css/.scss for color rule (es propio de tokens)
            if 'CSS var' in reason and f.suffix in ('.css','.scss'):
                # Permitido SOLO en frontend/src/styles/tokens.css
                if 'tokens.css' not in str(f):
                    pass  # toda otra .css = violación intencional, registrar
                else:
                    continue
            # ignore high-severity URLs si están en comentario
            violations.append({
                'file': str(f),
                'line': line_num,
                'severity': sev,
                'reason': reason,
                'snippet': line.strip()[:160],
            })

summary = {
    'total': len(violations),
    'by_severity': {
        s: sum(1 for v in violations if v['severity']==s)
        for s in ('high','medium','low')
    },
    'violations': violations[:1000],
}
pathlib.Path(OUT).write_text(json.dumps(summary, indent=2, ensure_ascii=False))
print(f"no_hardcode.json escrito: total={summary['total']}  high={summary['by_severity']['high']}")
sys.exit(0 if summary['by_severity']['high'] == 0 else 2)
PY
}
