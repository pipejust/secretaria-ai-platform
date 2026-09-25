// Pull requests en GitHub o en Azure DevOps, según de dónde sea el repositorio (remoto "origin").
// Sirve para dos cosas: saber si el PR de un ítem está abierto o unido (etapa y cierre en Altum) y
// comprobar que quien lo aprobó es el líder técnico que dice Altum (y nadie más).
import { execFileSync } from 'node:child_process';
import { identificarRepo } from './altum-backlog.mjs';
import { fromKeychain } from './altum.mjs';

const TIMEOUT_MS = 8000;

function git(args) {
  try {
    return execFileSync('git', args, { windowsHide: true, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
  } catch {
    return '';
  }
}

// github | azure_devops (con org, proyecto y repositorio) | '' si no hay remoto.
export function origenRepo() {
  const url = git(['config', '--get', 'remote.origin.url']);
  if (!url) return { provider: '' };
  const { provider, name } = identificarRepo(url);
  if (provider !== 'azure_devops') return { provider, name };
  const [org, project, repo] = name.split('/');
  return { provider, name, org, project, repo };
}

// ---- GitHub (gh CLI: local con sesión, o en CI con GH_TOKEN) ----
function gh(args) {
  if (process.env.SN_SYNC_NO_GH === '1') return null;
  try {
    return JSON.parse(execFileSync('gh', args, { windowsHide: true, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], timeout: TIMEOUT_MS }));
  } catch {
    return null;
  }
}

// ---- Azure DevOps (API REST con un token personal: SN_AZURE_PAT, o el Llavero en Mac; en
// Azure Pipelines, $(System.AccessToken)). El token va por la entrada estándar de curl, nunca
// en la línea de comandos, para que no se vea en la lista de procesos. ----
function azurePat() {
  return process.env.SN_AZURE_PAT || process.env.AZURE_DEVOPS_EXT_PAT || fromKeychain('SN_AZURE_PAT');
}

function azure(origen, ruta) {
  const pat = azurePat();
  if (!pat || !origen.org) return null;
  const base = (process.env.SN_AZURE_BASE_URL || 'https://dev.azure.com').replace(/\/$/, '');
  const url = `${base}/${encodeURIComponent(origen.org)}/${encodeURIComponent(origen.project)}/_apis/git/repositories/${encodeURIComponent(origen.repo)}${ruta}`;
  try {
    const salida = execFileSync('curl', ['-sS', '--fail', '--max-time', '8', '-K', '-', url], {
      input: `user = ":${pat.replace(/"/g, '')}"\n`, encoding: 'utf8', stdio: ['pipe', 'pipe', 'ignore'], timeout: TIMEOUT_MS + 2000, windowsHide: true,
    });
    return JSON.parse(salida);
  } catch {
    return null;
  }
}

const ESTADO_AZURE = { active: 'OPEN', completed: 'MERGED', abandoned: 'CLOSED' };

// Estado del PR de una rama: { pr_state: OPEN|MERGED|CLOSED, pr_number, pr_url } o {} si no se sabe.
export function prDeRama(branch, origen = origenRepo()) {
  if (!branch) return {};
  if (origen.provider === 'azure_devops') {
    const datos = azure(origen, `/pullrequests?searchCriteria.sourceRefName=${encodeURIComponent(`refs/heads/${branch}`)}&searchCriteria.status=all&api-version=7.1`);
    const pr = (datos?.value || []).sort((a, b) => b.pullRequestId - a.pullRequestId)[0];
    if (!pr) return {};
    return {
      pr_state: ESTADO_AZURE[pr.status] || 'OPEN', pr_number: pr.pullRequestId,
      pr_url: `https://dev.azure.com/${encodeURIComponent(origen.org)}/${encodeURIComponent(origen.project)}/_git/${encodeURIComponent(origen.repo)}/pullrequest/${pr.pullRequestId}`,
    };
  }
  const pr = gh(['pr', 'view', branch, '--json', 'state,number,url']);
  return pr ? { pr_state: pr.state, pr_number: pr.number, pr_url: pr.url } : {};
}

// Quién aprobó el PR. GitHub: la ÚLTIMA revisión de cada persona (si aprobó y luego pidió cambios,
// cuenta lo último). Azure: voto 10 (aprobado) o 5 (aprobado con sugerencias).
// Devuelve { autor, aprobaron: [{ usuario, correo }] } o null si no se pudo leer.
export function aprobacionesPr(numero, origen = origenRepo()) {
  if (origen.provider === 'azure_devops') {
    const pr = azure(origen, `/pullrequests/${numero}?api-version=7.1`);
    if (!pr) return null;
    return {
      autor: { usuario: pr.createdBy?.uniqueName || '', correo: (pr.createdBy?.uniqueName || '').toLowerCase() },
      aprobaron: (pr.reviewers || []).filter((r) => r.vote >= 5)
        .map((r) => ({ usuario: r.uniqueName || r.displayName || '', correo: (r.uniqueName || '').toLowerCase() })),
    };
  }
  const pr = gh(['pr', 'view', String(numero), '--json', 'author,reviews']);
  if (!pr) return null;
  const ultima = new Map();
  for (const r of pr.reviews || []) {
    if (['APPROVED', 'CHANGES_REQUESTED', 'DISMISSED'].includes(r.state)) ultima.set(r.author?.login, r.state);
  }
  return {
    autor: { usuario: pr.author?.login || '', correo: '' },
    aprobaron: [...ultima].filter(([, estado]) => estado === 'APPROVED').map(([usuario]) => ({ usuario, correo: '' })),
  };
}

// ¿La aprobación del PR es válida? Solo si la dio el líder que dice Altum: las aprobaciones de
// cualquier otra persona no cuentan.
// Si el PR lo abrió el propio líder, vale: él es quien decide en este proyecto, así que su trabajo
// no necesita la firma de nadie más (y GitHub no deja aprobar el PR propio, así que nunca habría
// una aprobación que leer). Para todos los demás, sigue haciendo falta la del líder.
export function firmaDelLider(aprob, lider) {
  if (!aprob) return { valida: false, motivo: 'no pude leer las aprobaciones del PR (¿sesión de gh o token de Azure DevOps?).' };
  if (!lider?.name) return { valida: false, motivo: 'Altum no dice quién es el líder de este proyecto.' };
  const esLider = (p) => (lider.github && p.usuario.toLowerCase() === lider.github.toLowerCase())
    || (lider.email && p.correo && p.correo === lider.email.toLowerCase());
  if (esLider(aprob.autor)) return { valida: true, motivo: `el PR lo abrió el propio líder (${lider.name}): su decisión es la que vale en este proyecto.` };
  if (aprob.aprobaron.some(esLider)) return { valida: true, motivo: `aprobado por ${lider.name}, el líder según Altum.` };
  const otros = aprob.aprobaron.map((p) => p.usuario).filter(Boolean);
  return {
    valida: false,
    motivo: otros.length
      ? `lo aprobó ${otros.join(', ')}, pero solo vale la aprobación del líder (${lider.name}${lider.github ? `, @${lider.github}` : ''}).`
      : `todavía no lo aprueba el líder (${lider.name}${lider.github ? `, @${lider.github}` : ''}).`,
  };
}
