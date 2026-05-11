#!/usr/bin/env node
/**
 * lint-no-hardcode.mjs
 *
 * Recorre frontend/src buscando colores hex literales, font-family no
 * permitidas, y rgb()/hsl() crudos. Excluye:
 *   - tokens.css (es el único lugar donde viven las definiciones).
 *   - imports de Google Fonts (URLs).
 *   - whitelist explícita en scripts/lint-no-hardcode.whitelist.json.
 *
 * Modo `--report` solo lista. Por defecto exit-code 1 si hay hallazgos.
 *
 * NOTA: este linter NO bloquea el build aún — está pensado para CI futuro.
 * Hoy los componentes legacy todavía tienen muchos hex inline. La migración
 * a tokens es incremental.
 */

import { readFileSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execSync } from 'node:child_process';

const __dirname = new URL('.', import.meta.url).pathname;
const ROOT = join(__dirname, '..');
const SCAN_DIR = join(ROOT, 'frontend', 'src');
const TOKENS_FILE = join(SCAN_DIR, 'styles', 'tokens.css');

const WHITELIST_FILE = join(__dirname, 'lint-no-hardcode.whitelist.json');
let WHITELIST = { paths: [], lines: [] };
try {
  WHITELIST = JSON.parse(readFileSync(WHITELIST_FILE, 'utf-8'));
} catch {}

const PATTERNS = [
  { name: 'hex-color',    re: /#[0-9a-fA-F]{3,8}\b/g },
  { name: 'rgb',          re: /\brgb\s*\(/g },
  { name: 'rgba',         re: /\brgba\s*\(/g },
  { name: 'hsl',          re: /\bhsl\s*\(/g },
  { name: 'inter-font',   re: /'Inter'/g },
  { name: 'arial-font',   re: /'Arial'/g },
];

function listFiles() {
  // Sin ripgrep dep: usamos find.
  const cmd = `find "${SCAN_DIR}" -type f \\( -name "*.css" -o -name "*.scss" -o -name "*.ts" -o -name "*.html" \\) -not -path "*/node_modules/*"`;
  return execSync(cmd, { encoding: 'utf-8' }).trim().split('\n').filter(Boolean);
}

function isWhitelisted(file, lineNum, lineText) {
  const rel = relative(ROOT, file);
  if (rel === relative(ROOT, TOKENS_FILE)) return true;
  if (WHITELIST.paths?.some((p) => rel.includes(p))) return true;
  // Líneas explícitamente whitelisted: "path:lineNum"
  if (WHITELIST.lines?.includes(`${rel}:${lineNum}`)) return true;
  // Imports de Google Fonts no son hex.
  if (lineText.includes('fonts.googleapis.com')) return true;
  return false;
}

const findings = [];
for (const file of listFiles()) {
  const content = readFileSync(file, 'utf-8');
  const lines = content.split('\n');
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (isWhitelisted(file, i + 1, line)) continue;
    for (const { name, re } of PATTERNS) {
      const matches = line.match(re);
      if (!matches) continue;
      // Tolerancia: línea que ya referencia var(--brand- o var(--color- ignora warnings.
      if (/var\(--brand-|var\(--color-/.test(line) && name === 'hex-color') continue;
      for (const m of matches) {
        findings.push({ file: relative(ROOT, file), line: i + 1, pattern: name, snippet: line.trim().slice(0, 120), match: m });
      }
    }
  }
}

const isReport = process.argv.includes('--report');

if (findings.length === 0) {
  console.log('✓ lint-no-hardcode: 0 violaciones');
  process.exit(0);
}

console.log(`lint-no-hardcode: ${findings.length} hallazgos`);
const byFile = new Map();
for (const f of findings) {
  if (!byFile.has(f.file)) byFile.set(f.file, []);
  byFile.get(f.file).push(f);
}
let printed = 0;
for (const [file, items] of [...byFile.entries()].sort((a, b) => b[1].length - a[1].length)) {
  console.log(`\n  ${file}  (${items.length})`);
  for (const it of items.slice(0, 5)) {
    console.log(`    ${String(it.line).padStart(4)}  [${it.pattern}]  ${it.match}    ${it.snippet}`);
    printed++;
    if (printed > 50 && !isReport) break;
  }
  if (printed > 50 && !isReport) {
    console.log('\n  (...truncado. Pasá --report para verlo todo.)');
    break;
  }
}

// Sólo fallamos en modo strict. Por defecto solo reportamos para no romper
// el flujo durante la migración incremental.
if (process.argv.includes('--strict')) {
  process.exit(1);
}
process.exit(0);
