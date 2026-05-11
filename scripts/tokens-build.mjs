#!/usr/bin/env node
/**
 * tokens-build.mjs
 *
 * Lee `frontend/tokens/*.json` y emite `frontend/src/styles/tokens.css`
 * con todas las CSS custom properties del sistema de diseño Acten.
 *
 * Idempotente. Sin dependencias externas. Reemplaza el archivo destino
 * en cada corrida — NO editar tokens.css a mano.
 */

import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..');
const TOKENS_DIR = join(ROOT, 'frontend', 'tokens');
const OUT_DIR = join(ROOT, 'frontend', 'src', 'styles');
const OUT_FILE = join(OUT_DIR, 'tokens.css');

function read(name) {
  return JSON.parse(readFileSync(join(TOKENS_DIR, name), 'utf-8'));
}

function flatten(obj, prefix = '') {
  const out = [];
  for (const [k, v] of Object.entries(obj)) {
    if (k.startsWith('$')) continue;
    const key = prefix ? `${prefix}-${k}` : k;
    if (v && typeof v === 'object') out.push(...flatten(v, key));
    else out.push([key, String(v)]);
  }
  return out;
}

const colors = read('colors.json');
const typo   = read('typography.json');
const space  = read('spacing.json');
const radius = read('radius.json');
const shadow = read('shadow.json');
const motion = read('motion.json');

const sections = [
  ['Brand colors',     flatten(colors.brand,    'brand')],
  ['Semantic colors',  flatten(colors.semantic, 'color')],
  ['Typography family',flatten(typo.family,     'font')],
  ['Typography size',  flatten(typo.size,       'text')],
  ['Typography weight',flatten(typo.weight,     'fw')],
  ['Typography leading',flatten(typo.leading,   'lh')],
  ['Typography tracking',flatten(typo.tracking, 'tracking')],
  ['Spacing',          flatten(space.space,     'space')],
  ['Radius',           flatten(radius.radius,   'radius')],
  ['Shadow',           flatten(shadow.shadow,   'shadow')],
  ['Motion duration',  flatten(motion.duration, 'dur')],
  ['Motion ease',      flatten(motion.ease,     'ease')],
];

let css = `/* ============================================================================
 * tokens.css — Acten Design System
 * GENERADO AUTOMÁTICAMENTE por scripts/tokens-build.mjs.
 * NO editar a mano. Modifica frontend/tokens/*.json y re-corre el script.
 * ============================================================================ */

:root {
`;
for (const [title, vars] of sections) {
  css += `  /* --- ${title} --- */\n`;
  for (const [name, value] of vars) {
    css += `  --${name}: ${value};\n`;
  }
  css += '\n';
}
css += `  /* --- Aliases para retrocompatibilidad con el código viejo --- */
  --bg-main:        var(--color-bg-base);
  --sidebar-bg:     var(--color-bg-sidebar);
  --sidebar-hover:  rgba(255, 255, 255, 0.06);
  --topbar-bg:      var(--color-bg-topbar);
  --panel-bg:       var(--color-bg-panel);
  --text-main:      var(--color-fg-default);
  --text-muted:     var(--color-fg-muted);
  --text-sidebar:   var(--brand-cream-200);
  --text-sidebar-active: var(--brand-cream-50);
  --accent-color:   var(--color-accent);
  --accent-hover:   var(--color-accent-hover);
  --success-color:  var(--color-success);
  --danger-color:   var(--color-danger);
  --border-color:   var(--color-border);
  --transition:     all var(--dur-base) var(--ease-out);

  /* --- Brand white-label (BrandingService los sobreescribe en runtime) --- */
  --brand-primary:   var(--brand-ink-blue-500);
  --brand-secondary: var(--brand-emerald-500);
  --brand-accent:    var(--brand-amber-500);
}
`;

mkdirSync(OUT_DIR, { recursive: true });
writeFileSync(OUT_FILE, css);
const lineCount = css.split('\n').length;
console.log(`✓ Wrote ${OUT_FILE} (${lineCount} lines)`);
