// Lectura de Altum: proyectos de la empresa, tareas que ya existen en el proyecto elegido e importación.
// Un repositorio de código = un proyecto de Altum de una empresa (la clave X-API-Key dice cuál empresa).
import { findMark } from './body.mjs';
import { ACTEN_DONE, api, esDeActen, KIND_TO_TYPE, listTasks, projectStates, textoPlano } from './altum.mjs';
import { projectKey } from './altum-clone.mjs';
import { readItems } from './items.mjs';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { writeState } from './store.mjs';

// Un proyecto casi nunca es un solo repositorio (app, web, consola, backend...). Altum lo dice de
// dos formas que conviven: "repo_url" (uno solo, el de siempre) y "repos" (la lista completa).
// Aquí se unifican en "repos"; "repo" queda como el principal, para lo que solo necesita uno.
export function repoList(p) {
  const lista = (p.repos || []).map((r) => ({
    provider: r.provider || 'github',
    name: r.repo || '',
    url: r.url || urlDeRepo(r.provider || 'github', r.repo || ''),
  })).filter((r) => r.url);
  if (p.repo_url && !lista.some((r) => mismaUrl(r.url, p.repo_url))) {
    lista.unshift({ ...identificarRepo(p.repo_url), url: p.repo_url });
  }
  return lista;
}

const mismaUrl = (a, b) => String(a).replace(/\/+$/, '') === String(b).replace(/\/+$/, '');

// De la dirección del repositorio a como lo guarda Altum: proveedor + nombre.
// GitHub/GitLab/Bitbucket usan "organizacion/repositorio"; Azure DevOps mete un nivel más
// ("organizacion/proyecto/repositorio"), porque el proyecto es parte real de su URL.
export function identificarRepo(url, provider = '') {
  const limpia = decodeURIComponent(String(url || '').trim())
    .replace(/^git@([^:]+):/, 'https://$1/').replace(/\.git$/, '').replace(/\/+$/, '');
  const host = limpia.match(/^[a-z+]+:\/\/([^/]+)/i)?.[1]?.toLowerCase() || '';
  const partes = limpia.replace(/^[a-z+]+:\/\/[^/]+\//i, '').split('/').filter(Boolean);
  if (host.includes('dev.azure.com') || host.endsWith('visualstudio.com')) {
    // dev.azure.com/<org>/<proyecto>/_git/<repo>   ·   <org>.visualstudio.com/<proyecto>/_git/<repo>
    const org = host.endsWith('visualstudio.com') ? host.split('.')[0] : partes.shift();
    const git = partes.indexOf('_git');
    const proyecto = git > 0 ? partes.slice(0, git).join('/') : partes[0];
    const repo = git >= 0 ? partes[git + 1] : partes[partes.length - 1];
    return { provider: 'azure_devops', name: [org, proyecto, repo].filter(Boolean).join('/') };
  }
  const porHost = host.includes('gitlab') ? 'gitlab' : host.includes('bitbucket') ? 'bitbucket' : 'github';
  return { provider: provider || porHost, name: partes.slice(-2).join('/') };
}

// Solo para cuando Altum no manda "url" armada (hoy la manda para github y azure_devops).
export function urlDeRepo(provider, repo) {
  const partes = String(repo || '').split('/').filter(Boolean);
  if (!partes.length) return '';
  if (provider === 'azure_devops') {
    const [org, ...resto] = partes;
    const nombre = resto.pop();
    return `https://dev.azure.com/${encodeURIComponent(org)}/${resto.map(encodeURIComponent).join('/')}/_git/${encodeURIComponent(nombre)}`;
  }
  const dominio = { gitlab: 'gitlab.com', bitbucket: 'bitbucket.org' }[provider] || 'github.com';
  return `https://${dominio}/${partes.join('/')}`;
}

// Compatibilidad: el nombre que Altum guarda para esa dirección.
export function nombreDeRepo(url) {
  return identificarRepo(url).name;
}

export function toProject(p) {
  const repos = repoList(p);
  return {
    id: p.id, key: projectKey(p.name), name: p.name || '', client: p.client_name || '',
    status: p.status || '', members: p.members?.length ?? null, repo: repos[0]?.url || '', repos,
  };
}

// Lista de repositorios de un proyecto. Altum todavía puede no tener este endpoint: si responde 404
// se trabaja con lo que traiga "repos"/"repo_url" en el propio proyecto.
export async function listRepos(connector, projectId) {
  try {
    const raw = await api(connector, 'GET', `/projects/${projectId}/repos`);
    return repoList({ repos: Array.isArray(raw) ? raw : raw?.items || [] });
  } catch (error) {
    if (/HTTP 404/.test(error.message)) return null;
    throw error;
  }
}

// Agrega un repositorio a la lista del proyecto (varios por proyecto). Si Altum todavía no tiene
// el endpoint, cae al campo de siempre (repo_url), que solo guarda uno.
export async function addProjectRepo(connector, projectId, url, providerPedido = '') {
  const { provider, name: repo } = identificarRepo(url, providerPedido);
  try {
    await api(connector, 'POST', `/projects/${projectId}/repos`, { provider, repo });
    return { modo: 'lista', repo, provider };
  } catch (error) {
    if (/HTTP 409/.test(error.message)) return { modo: 'ya-estaba', repo, provider };
    if (/HTTP 404|HTTP 405/.test(error.message)) {
      await setProjectRepo(connector, projectId, url);
      return { modo: 'uno-solo', repo, provider };
    }
    throw error;
  }
}

// /projects viene paginado y envuelto en { items, total } (las rutas /config/* no: son listas sueltas).
export async function listProjects(connector) {
  const proyectos = [];
  for (let page = 1; ; page += 1) {
    const raw = await api(connector, 'GET', `/projects?page=${page}&limit=200`);
    const items = Array.isArray(raw) ? raw : raw?.items || [];
    proyectos.push(...items);
    if (!items.length || proyectos.length >= (raw?.total ?? proyectos.length)) break;
  }
  return proyectos.map(toProject);
}

// Registra en Altum de dónde se clona el proyecto. Necesita una clave con "projects:write":
// las claves generadas antes de ese permiso dan 403 hasta que la persona la regenere.
export async function setProjectRepo(connector, projectId, repoUrl) {
  return api(connector, 'PUT', `/projects/${projectId}/repo`, { repo_url: repoUrl || null });
}

// Quitar un repositorio mal registrado. Se identifica por proveedor + nombre, igual que al agregarlo.
// 404 = ese repositorio no estaba en el proyecto (no es un error que deba asustar a nadie).
export async function removeProjectRepo(connector, projectId, repo) {
  try {
    await api(connector, 'DELETE', `/projects/${projectId}/repos`, { provider: repo.provider, repo: repo.name });
    return { quitado: true };
  } catch (error) {
    if (/HTTP 404/.test(error.message)) return { quitado: false, motivo: 'no-estaba' };
    throw error;
  }
}

// ¿Este proyecto ya tiene registrado de dónde se clona? La respuesta se guarda en .sn/state/ para que
// el aviso al abrir la sesión no cueste una petición por mensaje.
const REPO_FILE = 'altum-repo.json';

export async function checkProjectRepo(connector, root = '.') {
  const proyecto = (await listProjects(connector)).find((p) => p.id === connector.project_id);
  const estado = { at: Date.now(), name: proyecto?.name || '', key: proyecto?.key || '', missing: Boolean(proyecto) && !proyecto.repos?.length, repo: proyecto?.repo || '', repos: proyecto?.repos?.length || 0 };
  writeState(REPO_FILE, estado);
  return estado;
}

// Se lee con la raíz explícita porque el hook puede correr desde otra carpeta.
export function repoReminder(root = '.') {
  let estado = null;
  try {
    const file = path.join(root, '.sn/state', REPO_FILE);
    estado = existsSync(file) ? JSON.parse(readFileSync(file, 'utf8')) : null;
  } catch {
    estado = null;
  }
  if (!estado?.missing) return '';
  return `El proyecto "${estado.name}" no tiene registrado en Altum de dónde se clona, así que nadie más puede traerlo por su nombre.`
    + ` Regístralo con: node scripts/sn/sn-sync.mjs set-repo ${estado.key || estado.name}`;
}

// Quién es la clave (GET /me): persona o empresa, a qué empresa pertenece y qué proyectos tiene asignados.
// null si Altum todavía no expone /me (la clave funciona igual).
export async function whoAmI(connector) {
  try {
    return await api(connector, 'GET', '/me');
  } catch (error) {
    if (/HTTP 404/.test(error.message)) return null;
    throw error;
  }
}

export function whoAmIText(me, projectId) {
  if (!me) return 'Altum todavía no dice de quién es la clave (falta GET /me, pedido F). La conexión funciona igual.\n';
  const who = me.user ? `Clave personal de ${me.user.name || ''} <${me.user.email || '?'}>` : `Clave de la empresa (${me.key?.name || 'CI / servidores'})`;
  const projects = me.projects || [];
  const lines = projects.map((p) => `  ${p.name || ''}${p.client_name ? `  · cliente: ${p.client_name}` : ''}${p.is_lead ? '  [líder]' : ''}${p.repo_url ? '' : '  (sin repositorio en Altum)'}`);
  // Las claves anteriores a "projects:write" no pueden registrar el repositorio: se avisa antes del 403.
  const scopes = me.key?.scopes;
  const faltaEscritura = Array.isArray(scopes) && !scopes.includes('projects:write')
    ? '\nTu clave es anterior al permiso "projects:write": no podrás registrar de dónde se clona un proyecto. Regenérala en Altum → Mi perfil → Mis datos y vuelve a guardarla.'
    : '';
  const here = !projectId ? 'Este repositorio todavía no está unido a un proyecto: se une solo con `conectar` (reconoce el proyecto por el repositorio).' : projects.some((p) => p.id === projectId)
    ? `Este repositorio: proyecto ${projectId} — asignado.`
    : `Este repositorio: proyecto ${projectId} — NO estás asignado. Pide al líder del proyecto en Altum que te agregue.`;
  return `${who}\nEmpresa: ${me.company?.name || '?'}${me.company?.slug ? ` (${me.company.slug})` : ''}\n`
    + `${me.user ? 'Proyectos asignados' : 'Proyectos de la empresa'} (${projects.length}):\n${lines.join('\n')}\n${here ? `\n${here}\n` : ''}${faltaEscritura}`;
}

// Quién es el líder técnico del proyecto SEGÚN ALTUM: nadie tiene que escribirlo a mano.
// Altum marca al líder con is_lead en los integrantes y da su nombre, su correo y, cuando está
// registrado, su usuario de GitHub. Si la persona de la clave ES la líder, /me también lo dice.
export async function projectLead(connector, projectId = connector.project_id) {
  const lider = await leerLider(connector, projectId);
  // Copia local: validation-state la usa para no aceptar firmas de quien no es el líder (sin red).
  if (lider?.name) writeState('altum-lider.json', { at: Date.now(), name: lider.name, email: lider.email || '', github: lider.github || '' });
  return lider;
}

async function leerLider(connector, projectId) {
  const [raw, me] = await Promise.all([
    api(connector, 'GET', `/projects?page=1&limit=200`),
    whoAmI(connector).catch(() => null),
  ]);
  const items = Array.isArray(raw) ? raw : raw?.items || [];
  const proyecto = items.find((p) => p.id === projectId);
  if (!proyecto) return null;
  const lead = (proyecto.members || []).find((m) => m.is_lead) || null;
  const yo = (me?.projects || []).find((p) => p.id === projectId);
  if (yo?.is_lead && me?.user) {
    return { project: proyecto.name, role: yo.role || lead?.role || 'Líder técnico', soyYo: true, name: me.user.name || '', email: me.user.email || '', github: lead?.github_username || '', employee_id: lead?.employee_id || '' };
  }
  if (!lead) return { project: proyecto.name, falta: 'sin-lider' };
  return { project: proyecto.name, role: lead.role || 'Líder técnico', soyYo: false, name: lead.name || '', email: lead.email || '', github: lead.github_username || '', employee_id: lead.employee_id || '' };
}

export function leadText(lead) {
  if (!lead) return 'No encuentro el proyecto en Altum: revisa el project_id del conector.\n';
  if (lead.falta === 'sin-lider') return `"${lead.project}" no tiene líder marcado en Altum. Pídele a quien administra Altum que marque al líder del proyecto; mientras tanto, pregúntale a la persona quién firma.\n`;
  if (lead.name || lead.email) {
    // El usuario de GitHub sirve para pedirle la revisión del PR; Altum lo devuelve cuando está registrado.
    return `${lead.role} de "${lead.project}": ${lead.name || '(sin nombre en Altum)'} <${lead.email || '?'}>`
      + `${lead.github ? ` · GitHub @${lead.github}` : ''}${lead.soyYo ? ' — eres tú' : ''}\n`;
  }
  return `${lead.role} de "${lead.project}": está registrado en Altum (employee_id ${lead.employee_id}), pero Altum todavía no devuelve su nombre ni su correo (pedido H).\n`
    + 'Escribe eso mismo en AGENTS.md y sigue: no le preguntes a la persona quién es el líder.\n';
}

function linkedIndex(connector, root) {
  const index = new Map();
  for (const item of readItems(root)) {
    if (item.external?.[connector.name]) index.set(item.external[connector.name], item);
    index.set(item.id, item);
  }
  return index;
}

export { esDeActen };

function importable(connector, task) {
  return {
    id: esDeActen(task) ? `ACT-${String(task.id).replace(/^acten:/, '')}` : `ALT-${task.number ?? task.id.slice(0, 8)}`,
    type: KIND_TO_TYPE[task.kind] || 'feature',
    title: String(task.title || '').replace(/^(\[[^\]]+\]\s*)+/, ''),
    story: task.description || '',
    criteria: textoPlano(task.acceptance_criteria),
    origin: 'altum',
    created: task.created_at || new Date().toISOString(),
    external: { [connector.name]: task.id },
  };
}

// Foto de las tareas del proyecto cruzada con el repositorio: qué está pendiente y si ya está enlazado.
// Con "since" (pull y vigilante) también trae las borradas en Altum (deleted: true) para avisar.
export async function readBacklog(connector, root = '.', since = '') {
  const [{ items, deleted }, { done }] = await Promise.all([
    listTasks(connector, { project_id: connector.project_id, updated_since: since, include_deleted: since ? 'true' : '' }),
    projectStates(connector),
  ]);
  const linked = linkedIndex(connector, root);
  const itemFor = (task) => linked.get(task.id) || linked.get(task.external_ref) || linked.get(findMark(task.description));
  const rows = items.map((task) => ({
    altum_id: task.id, number: task.number, kind: task.kind, title: task.title, state: task.state,
    priority: task.priority, assignee_id: task.assignee_id, target_date: task.target_date || null,
    open: esDeActen(task) ? !ACTEN_DONE.includes(task.state) : !done.includes(task.state),
    deleted: false, external: esDeActen(task), item: itemFor(task)?.id || '', task,
  }));
  const gone = deleted.filter((d) => d.project_id === connector.project_id).map((d) => ({
    altum_id: d.id, number: d.number, kind: '', title: d.title, state: 'borrada', priority: null, assignee_id: null,
    target_date: null, open: false, deleted: true, item: linked.get(d.id)?.id || '',
    task: { ...d, updated_at: d.deleted_at, updated_by: null },
  }));
  return [...rows, ...gone].sort((a, b) => Number(b.open) - Number(a.open) || (a.priority ?? 9) - (b.priority ?? 9));
}

// Altum -> repo. Solo tareas ABIERTAS sin enlazar se importan (las terminadas son historia, no trabajo).
export async function pullAltum(connector, since, root = '.', { includeClosed = false } = {}) {
  const backlog = await readBacklog(connector, root, since);
  const live = backlog.filter((b) => !b.deleted);
  const created = live.filter((b) => !b.item && (b.open || includeClosed)).map((b) => importable(connector, b.task));
  const changed = live.filter((b) => b.item).map((b) => ({ id: b.item, altum_id: b.altum_id, state: b.state, title: b.title, priority: b.priority }));
  const deActen = live.filter((b) => b.external).length;
  const deleted = backlog.filter((b) => b.deleted && b.item).map((b) => ({ id: b.item, altum_id: b.altum_id, number: b.number, title: b.title }));
  return { created, changed, deleted, deActen, checked: live.length, skippedClosed: live.filter((b) => !b.item && !b.open && !includeClosed).length };
}

export function fetchAltumTask(connector, taskId) {
  return api(connector, 'GET', `/tasks/${encodeURIComponent(taskId)}`);
}

export function backlogMarkdown(backlog, { all = false } = {}) {
  const rows = backlog.filter((b) => all || b.open);
  if (!rows.length) return 'No hay tareas pendientes en el proyecto de Altum.\n';
  const open = backlog.filter((b) => b.open).length;
  const unlinked = backlog.filter((b) => b.open && !b.item).length;
  const lines = rows.map((b) => `| ${b.number ?? '—'} | ${b.kind || (b.external ? 'reunión' : '')} | ${b.title.replace(/\|/g, '\\|')} | ${b.state} | ${b.priority ?? '—'} | ${b.target_date || '—'} | ${b.item || 'sin traer'}${b.external ? ' · de una reunión' : ''} |`);
  return `# Tareas en Altum (${open} pendientes · ${unlinked} sin traer al proyecto)\n\n`
    + '| # | Tipo | Título | Estado | Prioridad | Fecha objetivo | Ítem en el repo |\n|---|---|---|---|---|---|---|\n'
    + `${lines.join('\n')}\n`;
}
