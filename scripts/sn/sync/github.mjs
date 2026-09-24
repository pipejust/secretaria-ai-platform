// Conector "github": un issue por ítem (lo leen de forma nativa Orca, GitHub Projects y el propio GitHub).
// Encuentra el issue por una marca oculta en el cuerpo (<!-- sn-item:ID -->), así no hace falta guardar ids.
import { execFileSync } from 'node:child_process';
import { itemMarkdownBody, findMark } from './body.mjs';

const TIMEOUT_MS = 10000;
const issueCache = new Map(); // conector -> Map(id -> número), una lectura por ejecución

function token(connector) {
  const fromEnv = process.env[connector.token_env || ''] || process.env.GH_TOKEN || process.env.GITHUB_TOKEN;
  if (fromEnv) return fromEnv;
  try {
    return execFileSync('gh', ['auth', 'token'], { windowsHide: true, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
  } catch {
    throw new Error('sin token de GitHub (inicia sesión con "gh auth login" o define GH_TOKEN)');
  }
}

export function repoFromRemote() {
  try {
    const url = execFileSync('git', ['config', '--get', 'remote.origin.url'], { windowsHide: true, encoding: 'utf8' }).trim();
    const match = url.match(/github\.com[:/]([^/]+\/[^/.]+)(\.git)?$/);
    return match ? match[1] : '';
  } catch {
    return '';
  }
}

async function api(connector, method, route, body) {
  const base = (connector.api_url || 'https://api.github.com').replace(/\/$/, '');
  const response = await fetch(`${base}${route}`, {
    method,
    headers: {
      Authorization: `Bearer ${token(connector)}`, Accept: 'application/vnd.github+json', 'Content-Type': 'application/json',
      'X-GitHub-Api-Version': '2022-11-28',
    },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  if (!response.ok) throw new Error(`GitHub HTTP ${response.status} en ${route}`);
  return response.status === 204 ? null : response.json();
}

async function knownIssues(connector, repo) {
  if (issueCache.has(connector.name)) return issueCache.get(connector.name);
  const found = new Map();
  for (let page = 1; page <= 20; page += 1) {
    const issues = await api(connector, 'GET', `/repos/${repo}/issues?labels=sn-item&state=all&per_page=100&page=${page}`);
    issues.forEach((issue) => {
      const id = findMark(issue.body);
      if (id) found.set(id, issue.number);
    });
    if (issues.length < 100) break;
  }
  issueCache.set(connector.name, found);
  return found;
}

export async function deliverGithub(connector, evt) {
  if (evt.type === 'sn.test') {
    const repo = connector.repo || repoFromRemote();
    await api(connector, 'GET', `/repos/${repo}`);
    return;
  }
  const { item } = evt;
  const repo = connector.repo || repoFromRemote();
  if (!repo) throw new Error('no se pudo deducir el repositorio de GitHub (define "repo": "dueño/nombre")');
  const labels = ['sn-item', `sn:${item.type}`, `sn:etapa:${item.stage}`, item.risk && `sn:riesgo:${item.risk}`, item.flag && `sn:${item.flag}`].filter(Boolean);
  const closed = (connector.close_on || ['done']).includes(item.stage);
  const payload = { title: `[${item.id}] ${item.title}`, body: itemMarkdownBody(evt), labels, state: closed ? 'closed' : 'open' };
  const issues = await knownIssues(connector, repo);
  const number = issues.get(item.id);
  if (number) {
    await api(connector, 'PATCH', `/repos/${repo}/issues/${number}`, payload);
  } else {
    const { state, ...create } = payload;
    const created = await api(connector, 'POST', `/repos/${repo}/issues`, create);
    issues.set(item.id, created.number);
    if (closed) await api(connector, 'PATCH', `/repos/${repo}/issues/${created.number}`, { state: 'closed' });
  }
}
