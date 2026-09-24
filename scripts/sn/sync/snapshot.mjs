// Foto del estado de todos los ítems: combina docs/items, openspec/changes, validaciones y git/PR.
// La etapa se DERIVA de lo que existe (principio P7); nadie la escribe a mano.
import { execFileSync } from 'node:child_process';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { readItems } from './items.mjs';
import { validationFor } from '../validation-state.mjs';
import { commitsFor } from './trace.mjs';
import { prDeRama } from './pr.mjs';

const RECENT_COMMITS = 20;

const CHANGES = 'openspec/changes';

// Etapas estables (claves en inglés para integraciones; etiqueta en español para personas).
export const STAGES = {
  triaged: 'Tarjeta', ready: 'Historia lista', planning: 'Plano en curso', plan_written: 'Plano escrito',
  plan_approved: 'Plano aprobado', building: 'Construyendo', built: 'Construido', verified: 'Con evidencia',
  in_review: 'En revisión', merged: 'Unido', done: 'Terminado', discarded: 'Descartado',
};
export const FLAGS = {
  awaiting_validation: 'Esperando validación', changes_requested: 'Con correcciones', blocked: 'Detenido', validation_expired: 'Validación vencida',
  invalid_signature: 'Firmado por alguien que no es el líder',
};

function git(args) {
  try {
    return execFileSync('git', args, { windowsHide: true, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
  } catch {
    return '';
  }
}

function tasksProgress(dir) {
  const file = path.join(dir, 'tasks.md');
  if (!existsSync(file)) return null;
  const text = readFileSync(file, 'utf8');
  const done = (text.match(/^\s*- \[x\]/gim) || []).length;
  const total = done + (text.match(/^\s*- \[ \]/gm) || []).length;
  return { done, total };
}

function archivedChanges() {
  const dir = path.join(CHANGES, 'archive');
  if (!existsSync(dir)) return [];
  return readdirSync(dir).map((name) => name.replace(/^\d{4}-\d{2}-\d{2}-/, ''));
}

// Estado del PR (GitHub con gh, Azure DevOps con su API). Sin forma de leerlo no se adivina:
// el ítem avanza hasta "Con evidencia" y pasa a "Terminado" al archivar.
function prInfo(branch) {
  return prDeRama(branch);
}

function deriveStage(item, archived, pr) {
  if (item.discarded) return 'discarded';
  const dir = path.join(CHANGES, item.change);
  if (item.change && archived.includes(item.change)) return 'done';
  if (pr.pr_state === 'MERGED') return 'merged';
  if (pr.pr_state === 'OPEN') return 'in_review';
  if (!item.change || !existsSync(dir)) return item.ready ? 'ready' : 'triaged';
  if (existsSync(path.join(dir, 'evidencia.md'))) return 'verified';
  const progress = tasksProgress(dir);
  if (progress && progress.total && progress.done === progress.total) return 'built';
  if (progress && progress.done > 0) return 'building';
  const validation = validationFor(item.change);
  if (validation.seal === 'plano' && validation.status === 'validado') return 'plan_approved';
  if (existsSync(path.join(dir, 'proposal.md')) && progress) return 'plan_written';
  return 'planning';
}

function flagOf(item) {
  if (!item.change) return '';
  const { status } = validationFor(item.change);
  return {
    'esperando validación': 'awaiting_validation', 'con correcciones': 'changes_requested',
    detenido: 'blocked', 'validación vencida': 'validation_expired', 'firma inválida': 'invalid_signature',
  }[status] || '';
}

function lastActor(item) {
  const paths = [item.file, item.change && path.join(CHANGES, item.change)].filter(Boolean);
  return git(['log', '-1', '--format=%an <%ae>', '--', ...paths]);
}

export function takeSnapshot(projectName = '', { solo = null } = {}) {
  const archived = archivedChanges();
  const project = projectName || path.basename(git(['rev-parse', '--show-toplevel']) || process.cwd());
  const source = git(['config', '--get', 'remote.origin.url']) || project;
  const items = readItems().filter((item) => !solo || solo(item)).map((item) => {
    const pr = prInfo(item.branch);
    const stage = deriveStage(item, archived, pr);
    const progress = item.change ? tasksProgress(path.join(CHANGES, item.change)) : null;
    const commits = commitsFor(item);
    const { body, file, ...rest } = item;
    return {
      ...rest, file, stage, stage_label: STAGES[stage], flag: flagOf(item), ...pr,
      tasks_done: progress?.done ?? null, tasks_total: progress?.total ?? null, actor: lastActor(item),
      commit_count: commits.length,
      commits: commits.slice(0, RECENT_COMMITS).map(({ short, date, author, subject }) => ({ short, date, author, subject })),
    };
  });
  return { project, source, taken_at: new Date().toISOString(), items };
}
