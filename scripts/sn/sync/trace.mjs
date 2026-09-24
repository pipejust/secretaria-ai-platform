// Trazabilidad de un ítem: todo lo que se hizo por él, leído de git y de los archivos del change.
// Un commit pertenece al ítem si menciona su id (Refs: <ID>) o si toca su archivo o la carpeta de su change.
import { execFileSync } from 'node:child_process';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { parseLog } from '../validation-state.mjs';

const CHANGES = 'openspec/changes';
const SEP = '\x1f';
const MAX_COMMITS = 200;
const PROCESS_FILES = /^(docs\/items\/|openspec\/)/;

function git(args) {
  try {
    return execFileSync('git', args, { windowsHide: true, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], maxBuffer: 32 * 1024 * 1024 }).trim();
  } catch {
    return '';
  }
}

export function changeDir(change) {
  if (!change) return '';
  const active = path.join(CHANGES, change);
  if (existsSync(active)) return active;
  const archive = path.join(CHANGES, 'archive');
  const archived = existsSync(archive) ? readdirSync(archive).find((d) => d.replace(/^\d{4}-\d{2}-\d{2}-/, '') === change) : '';
  return archived ? path.join(archive, archived) : '';
}

function logLines(extra) {
  const format = ['%H', '%h', '%an <%ae>', '%ad', '%s'].join(SEP);
  return git(['log', `--max-count=${MAX_COMMITS}`, '--date=iso-strict', `--format=${format}`, ...extra])
    .split('\n').filter(Boolean)
    .map((line) => {
      const [hash, short, author, date, subject] = line.split(SEP);
      return { hash, short, author, date, subject };
    });
}

// Commits de un ítem: los que lo declaran con "Refs: <ID>" (lo que escribe sn-ship) y los que tocan su
// plano. NO cuenta los que solo mencionan el id en el texto ("se registra aparte GDC-…"): esos no
// trabajaron en el ítem.
export function commitsFor(item) {
  const id = String(item.id).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  // (sin \b: git usa expresiones POSIX, que no lo entienden en todos los sistemas)
  const byId = logLines([`--grep=^Refs:(.*[^A-Za-z0-9-])?${id}([^A-Za-z0-9-]|$)`, '--extended-regexp']);
  const paths = [changeDir(item.change)].filter(Boolean);
  const byPath = paths.length ? logLines(['--', ...paths]) : [];
  const seen = new Map([...byId, ...byPath].map((c) => [c.hash, c]));
  return [...seen.values()].sort((a, b) => b.date.localeCompare(a.date));
}

export function filesTouched(commits) {
  const files = new Set();
  for (const { hash } of commits) {
    git(['show', '--name-only', '--format=', hash]).split('\n')
      .filter((f) => f && !PROCESS_FILES.test(f))
      .forEach((f) => files.add(f));
  }
  return [...files].sort();
}

export function validationsFor(item) {
  const dir = changeDir(item.change);
  const file = dir && path.join(dir, 'validacion.md');
  if (!file || !existsSync(file)) return [];
  return parseLog(readFileSync(file, 'utf8')).map((e) => ({
    date: e.date, decision: e.type, seal: e.seal, by: e.fields.Pide || e.fields.Valida || '', notes: e.fields.Notas || '',
  }));
}

export function traceOf(item) {
  const commits = commitsFor(item);
  const dir = changeDir(item.change);
  return {
    change_path: dir,
    evidence: dir && existsSync(path.join(dir, 'evidencia.md')) ? path.join(dir, 'evidencia.md') : '',
    validations: validationsFor(item),
    commits,
    files: filesTouched(commits),
  };
}
