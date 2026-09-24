#!/usr/bin/env node
// Estado de las validaciones a distancia (formato en references/validacion.md).
//   node validation-state.mjs            -> estado de cada change activo en la rama actual (JSON)
//   node validation-state.mjs --pending  -> solicitudes sin responder en las ramas remotas (JSON), para sn-validate
// Solo lee git y archivos: el estado nunca se guarda a mano.
import { execFileSync } from 'node:child_process';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';

const CHANGES_DIR = 'openspec/changes';
const PLAN_PATHS = ['proposal.md', 'specs', 'design.md', 'tasks.md'];
const ENTRY = /^##\s+(\S+\s+\S+)\s+·\s+(SOLICITUD|APROBADO|CAMBIOS PEDIDOS|RECHAZADO)\s+·\s+sello:\s*(plano|entrega)/;
const FIELD = /^-\s+(Pide|Valida|Rama|Commit|Commit validado|Riesgo|Qué validar|Notas):\s*(.*)$/;

function git(args) {
  try {
    return execFileSync('git', args, { windowsHide: true, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
  } catch {
    return '';
  }
}

function commitExists(commit) {
  try {
    execFileSync('git', ['cat-file', '-e', `${commit}^{commit}`], { windowsHide: true, stdio: 'ignore' });
    return true;
  } catch {
    return false;
  }
}

export function parseLog(text) {
  const entries = [];
  for (const line of text.split('\n')) {
    const head = line.match(ENTRY);
    if (head) {
      entries.push({ date: head[1], type: head[2], seal: head[3], fields: {}, raw: '' });
      continue;
    }
    if (entries.length) entries[entries.length - 1].raw += `${line}\n`;
    const field = line.match(FIELD);
    if (field && entries.length) entries[entries.length - 1].fields[field[1]] = field[2].trim();
  }
  return entries;
}

function commitOf(entry) {
  // "Commit validado: abc1234" o "Rama: x · Commit: abc1234" (varios campos pueden ir en la misma línea).
  const match = entry.raw.match(/Commit(?: validado)?:\s*([0-9a-f]{7,40})/);
  return match ? match[1] : '';
}

function changedSince(commit, change, seal) {
  if (!commit || !commitExists(commit)) return true; // Sin commit verificable, la validación no se puede dar por vigente.
  const scope = seal === 'plano' ? PLAN_PATHS.map((p) => path.join(CHANGES_DIR, change, p)) : ['.'];
  // Los commits que solo tocan validacion.md no invalidan nada.
  const log = git(['log', '--format=%h', `${commit}..HEAD`, '--', ...scope, `:(exclude)${CHANGES_DIR}/${change}/validacion.md`]);
  return log.length > 0;
}

// El líder según Altum, guardado por "sn-sync lead" / "asegurar" (.sn/state/altum-lider.json).
function liderGuardado(root = '.') {
  try {
    return JSON.parse(readFileSync(path.join(root, '.sn/state/altum-lider.json'), 'utf8'));
  } catch {
    return null;
  }
}

const correoDe = (texto) => String(texto || '').match(/<([^>]+@[^>]+)>/)?.[1]?.toLowerCase() || '';

// Riesgo del ítem que usa este change (docs/items/*.md con "change: <nombre>").
function riesgoDelChange(change, root = '.') {
  const dir = path.join(root, 'docs/items');
  if (!change || !existsSync(dir)) return '';
  for (const f of readdirSync(dir).filter((n) => n.endsWith('.md'))) {
    const texto = readFileSync(path.join(dir, f), 'utf8');
    if (new RegExp(`^change:\\s*${change.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*$`, 'm').test(texto)) {
      return texto.match(/^risk:\s*(R\d)/m)?.[1] || '';
    }
  }
  return '';
}

// ¿Este sello lo tiene que dar el líder? Plano R3–R4 y entrega R2+, o cuando se pidió su firma
// (hay una SOLICITUD). Un plano R0–R2 lo aprueba la propia persona, como dice el proceso.
function pideLider(entries, last, change) {
  if (entries.some((e) => e.type === 'SOLICITUD' && e.seal === last.seal)) return true;
  const nivel = Number((riesgoDelChange(change) || 'R0').slice(1));
  return last.seal === 'plano' ? nivel >= 3 : nivel >= 2;
}

export function statusOf(entries, change, lider = liderGuardado()) {
  if (!entries.length) return { status: 'sin validación' };
  const last = entries[entries.length - 1];
  const base = { seal: last.seal, by: last.fields.Pide || last.fields.Valida || '', date: last.date, notes: last.fields.Notas || '' };
  if (last.type === 'SOLICITUD') return { ...base, status: 'esperando validación', commit: commitOf(last) };
  // Solo el líder que dice Altum puede aprobar, pedir cambios o detener. Una decisión escrita por
  // cualquier otra persona no cuenta: el plano sigue esperando la firma.
  if (lider?.email && pideLider(entries, last, change) && correoDe(last.fields.Valida) && correoDe(last.fields.Valida) !== lider.email.toLowerCase()) {
    return { ...base, status: 'firma inválida', detail: `la firmó ${last.fields.Valida}, pero el líder es ${lider.name} <${lider.email}>` };
  }
  if (last.type === 'CAMBIOS PEDIDOS') return { ...base, status: 'con correcciones' };
  if (last.type === 'RECHAZADO') return { ...base, status: 'detenido' };
  const commit = commitOf(last);
  return { ...base, commit, status: changedSince(commit, change, last.seal) ? 'validación vencida' : 'validado' };
}

function localStates() {
  if (!existsSync(CHANGES_DIR)) return [];
  return readdirSync(CHANGES_DIR, { withFileTypes: true })
    .filter((d) => d.isDirectory() && d.name !== 'archive')
    .map((d) => {
      const file = path.join(CHANGES_DIR, d.name, 'validacion.md');
      const entries = existsSync(file) ? parseLog(readFileSync(file, 'utf8')) : [];
      return { change: d.name, ...statusOf(entries, d.name) };
    });
}

function pendingRemote() {
  git(['fetch', '--quiet', '--prune', 'origin']);
  const branches = git(['for-each-ref', '--format=%(refname:short)', 'refs/remotes/origin'])
    .split('\n').filter((b) => b && b !== 'origin' && !b.endsWith('/HEAD'));
  return branches.flatMap((ref) => {
    const files = git(['ls-tree', '-r', '--name-only', ref, '--', CHANGES_DIR])
      .split('\n').filter((f) => f.endsWith('/validacion.md') && !f.includes('/archive/'));
    return files.map((file) => {
      const entries = parseLog(git(['show', `${ref}:${file}`]));
      const last = entries[entries.length - 1];
      if (!last || last.type !== 'SOLICITUD') return null;
      return {
        branch: ref.replace(/^origin\//, ''), change: file.split('/')[2], seal: last.seal,
        by: last.fields.Pide || '', risk: (last.fields.Riesgo || '').split(' · ')[0], what: last.fields['Qué validar'] || '', date: last.date,
      };
    }).filter(Boolean);
  });
}

export function validationFor(change, root = '.') {
  const file = path.join(root, CHANGES_DIR, change, 'validacion.md');
  return statusOf(existsSync(file) ? parseLog(readFileSync(file, 'utf8')) : [], change);
}

const isMain = process.argv[1] && path.resolve(process.argv[1]) === new URL(import.meta.url).pathname;
if (isMain) {
  const result = process.argv.includes('--pending') ? pendingRemote() : localStates();
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
}
