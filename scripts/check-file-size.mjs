#!/usr/bin/env node
// Softnexus: límite de líneas por archivo.
// CLI:   node scripts/check-file-size.mjs                  -> revisa los archivos versionados (git ls-files)
//        node scripts/check-file-size.mjs a.ts b.ts        -> revisa solo esos archivos
//        node scripts/check-file-size.mjs --write-baseline -> registra la deuda existente (solo al adoptar
//                                                             Spec Driven en un proyecto que ya tenía código)
// Proyecto nuevo: no hay línea base; ningún archivo puede pasar el límite.
// Proyecto existente: los archivos que ya pasaban el límite quedan en .sn-size-baseline como deuda;
//   pueden tocarse mientras no crezcan, y se reducen con la skill sn-split.
// Límites: SN_MAX_LINES (por defecto 1000, falla) y SN_WARN_LINES (por defecto 800, aviso).
// Excepciones del repo: archivo .sn-size-ignore (un patrón por línea, estilo glob: dist/**, *.sql).
// También lo importa el hook del plugin softnexus-sdd, así que las reglas viven en un solo lugar.
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const MAX_LINES = Number(process.env.SN_MAX_LINES) || 1000;
export const WARN_LINES = Number(process.env.SN_WARN_LINES) || 800;
const IGNORE_FILE = '.sn-size-ignore';
const BASELINE_FILE = '.sn-size-baseline';

// Archivos generados o de datos: su tamaño no depende de quien escribe código.
const DEFAULT_IGNORES = [
  '**/node_modules/**', '**/dist/**', '**/build/**', '**/.next/**', '**/coverage/**', '**/vendor/**',
  '**/package-lock.json', '**/pnpm-lock.yaml', '**/yarn.lock', '**/*.lock', '**/bun.lockb',
  '**/*.min.js', '**/*.min.css', '**/*.map', '**/*.snap', '**/*.svg', '**/*.csv', '**/*.tsv',
  '**/*.generated.*', '**/*.gen.*', '**/database.types.ts', '**/openspec/changes/archive/**',
];

const TEXT_EXTENSIONS = /\.(m?[jt]sx?|cjs|vue|svelte|astro|css|scss|less|html?|py|rb|go|rs|java|kt|swift|php|cs|sql|md|mdx|ya?ml|json|sh|dart)$/i;

function globToRegExp(glob) {
  // Marcadores temporales para que el reemplazo de "*" no pise el de "**".
  const pattern = glob.trim()
    .replace(/[.+^${}()|[\]\\]/g, '\\$&')
    .replace(/\*\*\//g, '\u0001')
    .replace(/\*\*/g, '\u0002')
    .replace(/\*/g, '[^/]*')
    .replace(/\?/g, '[^/]')
    .replace(/\u0001/g, '(?:.*/)?')
    .replace(/\u0002/g, '.*');
  return new RegExp(`^${glob.includes('/') ? '' : '(?:.*/)?'}${pattern}$`);
}

export function loadIgnores(root = process.cwd()) {
  const file = path.join(root, IGNORE_FILE);
  const custom = existsSync(file)
    ? readFileSync(file, 'utf8').split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith('#'))
    : [];
  return [...DEFAULT_IGNORES, ...custom].map(globToRegExp);
}

// Deuda heredada: "ruta<TAB>líneas" por línea. Solo existe en proyectos que adoptaron Spec Driven con código previo.
export function loadBaseline(root = process.cwd()) {
  const file = path.join(root, BASELINE_FILE);
  if (!existsSync(file)) return new Map();
  return new Map(readFileSync(file, 'utf8').split('\n')
    .map((l) => l.trim()).filter((l) => l && !l.startsWith('#'))
    .map((l) => l.split('\t')).filter(([, n]) => Number(n) > 0)
    .map(([f, n]) => [f, Number(n)]));
}

export function isChecked(relativePath, ignores) {
  const normalized = relativePath.split(path.sep).join('/');
  return TEXT_EXTENSIONS.test(normalized) && !ignores.some((re) => re.test(normalized));
}

export function countLines(text) {
  if (!text) return 0;
  const lines = text.split('\n').length;
  return text.endsWith('\n') ? lines - 1 : lines;
}

export function classify(lines) {
  if (lines > MAX_LINES) return 'error';
  if (lines > WARN_LINES) return 'warn';
  return 'ok';
}

function trackedFiles() {
  try {
    return execFileSync('git', ['ls-files', '-z'], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 })
      .split('\0').filter(Boolean);
  } catch {
    console.error('check-file-size: no es un repositorio git; pasa los archivos como argumentos.');
    process.exit(2);
  }
}

function writeBaseline(results) {
  const debt = results.filter((r) => r.lines > MAX_LINES);
  const body = debt.map((r) => `${r.file}\t${r.lines}`).join('\n');
  writeFileSync(BASELINE_FILE, `# Deuda heredada al adoptar Spec Driven: archivos que ya pasaban ${MAX_LINES} líneas.\n`
    + '# Pueden editarse sin crecer. Se reducen con la skill sn-split; al bajar del límite, se borra la línea.\n'
    + (body ? `${body}\n` : ''));
  console.log(`${BASELINE_FILE}: ${debt.length} archivos registrados como deuda.`);
}

function main(args) {
  const shouldWriteBaseline = args.includes('--write-baseline');
  const explicit = args.filter((a) => !a.startsWith('--'));
  const ignores = loadIgnores();
  const baseline = loadBaseline();
  const files = (explicit.length ? explicit : trackedFiles()).filter((f) => existsSync(f) && isChecked(f, ignores));
  const measured = files.map((file) => ({ file, lines: countLines(readFileSync(file, 'utf8')) }));
  if (shouldWriteBaseline) return writeBaseline(measured);

  const results = measured
    .map((r) => {
      const allowed = baseline.get(r.file);
      const level = classify(r.lines);
      if (level === 'error' && allowed && r.lines <= allowed) return { ...r, level: 'debt', allowed };
      return { ...r, level, allowed };
    })
    .filter((r) => r.level !== 'ok')
    .sort((a, b) => b.lines - a.lines);

  const LABEL = { error: 'ERROR', debt: 'deuda', warn: 'aviso' };
  results.forEach((r) => {
    const note = r.level === 'error' && r.allowed ? `  (creció: la línea base permite ${r.allowed})` : '';
    console.log(`${LABEL[r.level]}  ${String(r.lines).padStart(6)}  ${r.file}${note}`);
  });
  const count = (level) => results.filter((r) => r.level === level).length;
  console.log(`\n${files.length} archivos · ${count('error')} sobre ${MAX_LINES} · ${count('debt')} deuda heredada · ${count('warn')} aviso (>${WARN_LINES})`);
  if (count('error')) {
    console.log('Código nuevo: pon lo que sobra en un módulo aparte. Deuda heredada que creció: redúcela con la skill sn-split.');
    process.exit(1);
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  main(process.argv.slice(2));
}
