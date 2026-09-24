// Conector nativo "altum" — API de tareas de Altum (contrato en references/integraciones.md §7d).
//   Repo -> Altum : crea la tarea (POST /tasks) y luego la actualiza (PATCH /tasks/{id}).
//   Altum -> Repo : ver altum-backlog.mjs (proyectos, tareas existentes, importar).
//   Firma de webhooks de Altum: verifyAltumSignature() para el receptor (n8n u otro).
// Una clave por empresa (X-API-Key); el resto del contrato es igual para todas.
import { execFileSync } from 'node:child_process';
import { createHmac, timingSafeEqual } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { findMark, itemPlainBody } from './body.mjs';
import { setExternalId } from './items.mjs';
import { readState, writeState } from './store.mjs';

const PUSHED_FILE = 'altum-pushed.json';
const SELF_FILE = 'altum-self.json';

// Recuerda cuándo ESTE computador cambió cada tarea y qué id tiene su clave en Altum ("updated_by"),
// para que el vigilante no avise de sus propios cambios.
function recordPush(connector, task) {
  writeState(PUSHED_FILE, { ...readState(PUSHED_FILE, {}), [task.id]: new Date().toISOString() });
  const keyId = task.updated_by?.id;
  if (keyId) writeState(SELF_FILE, { ...readState(SELF_FILE, {}), [connector.name]: keyId });
}

export function lastPush(taskId) {
  return readState(PUSHED_FILE, {})[taskId] || '';
}

export function selfKeyId(connector) {
  return readState(SELF_FILE, {})[connector.name] || '';
}

const DEFAULT_BASE = 'https://servicios.softnexus.io/api/v1/api';
const CLIENTE = (() => {
  try {
    const manifiesto = new URL('../../.claude-plugin/plugin.json', import.meta.url);
    return `softnexus-sdd/${JSON.parse(readFileSync(manifiesto, 'utf8')).version}`;
  } catch {
    return 'softnexus-sdd'; // copia del motor dentro de un repositorio: sin manifiesto al lado
  }
})();
const TIMEOUT_MS = 10000;
const PAGE_LIMIT = 200; // máximo que acepta GET /tasks
const MAX_SIGNATURE_AGE_S = 300;

// Etapa Softnexus -> estado Altum por defecto (sobrescribible con "status_map").
const DEFAULT_STATUS = {
  triaged: 'new', ready: 'new', planning: 'active', plan_written: 'active', plan_approved: 'active',
  building: 'active', built: 'active', verified: 'active', in_review: 'active', merged: 'closed', done: 'closed', discarded: 'removed',
};
// Tipo de ítem -> kind de Altum (epica|feature|historia|requerimiento|tarea|bug|pendiente).
const DEFAULT_KIND = { feature: 'historia', improvement: 'requerimiento', bug: 'bug', incident: 'bug', content: 'tarea', chore: 'tarea' };
export const KIND_TO_TYPE = { epica: 'feature', feature: 'feature', historia: 'feature', requerimiento: 'improvement', tarea: 'chore', bug: 'bug', pendiente: 'chore', 'acten-tarea': 'chore' };

// Tareas que nacen en reuniones gestionadas por Acten: llegan mezcladas en GET /tasks con id "acten:…".
// Se leen igual que las nativas, pero Altum solo deja cambiarles estado, responsable, título y descripción,
// y sus estados son fijos (los de Acten, no los del proyecto). Cualquier otro campo responde 422.
export function esDeActen(task) {
  return task?.source === 'acten' || String(task?.id || '').startsWith('acten:');
}
export const ACTEN_STATES = ['pending', 'blocked', 'done', 'cancelled'];
export const ACTEN_DONE = ['done', 'cancelled'];
const ACTEN_STATUS = {
  triaged: 'pending', ready: 'pending', planning: 'pending', plan_written: 'pending', plan_approved: 'pending',
  building: 'pending', built: 'pending', verified: 'pending', in_review: 'pending', merged: 'done', done: 'done', discarded: 'cancelled',
};

// Altum promete UTC, pero las fechas de Acten viajan sin zona ("2026-08-20T17:07:23.569141"):
// sin esto, JavaScript las leería como hora local y se irían varias horas.
export function fechaUtc(value) {
  if (!value) return null;
  const text = String(value);
  return new Date(/[zZ]|[+-]\d{2}:?\d{2}$/.test(text) ? text : `${text}Z`);
}

// Error que no se debe reintentar (reglas de negocio de Altum: 409 bloqueadores o duplicado, 422 dato no válido).
export class NotRetryable extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

function blockersMessage(detail) {
  const blockers = detail?.bloqueadores || [];
  if (!blockers.length) return '';
  return `Altum no deja cerrarla: falta cerrar ${blockers.map((b) => `#${b.number} "${b.title}" (${b.state})`).join(', ')}`;
}

// Una sola variable por empresa (key_env, ej. SN_ALTUM_KEY_SOFTNEXUS). En el computador de cada persona
// guarda SU clave personal (dice quién es, de qué empresa y qué proyectos tiene asignados); en el CI,
// la clave de la empresa. El plugin no distingue: Altum sabe de quién es cada clave (GET /me).
//
// Si la variable no está en el entorno (las apps de escritorio no leen ~/.zshrc ni el perfil de
// PowerShell), se busca donde la dejó el asistente de la clave, cifrada por el sistema operativo:
//   macOS   → Llavero (sn-clave-altum.sh)
//   Windows → archivo cifrado con DPAPI para ese usuario (sn-clave-altum.ps1), en %LOCALAPPDATA%\Softnexus
// El valor se queda en memoria: nunca se imprime ni se guarda en el repositorio.
const keyCache = new Map();

// Cómo se guarda la clave en cada sistema (el asistente la pide sin mostrarla y la deja cifrada).
export const COMO_GUARDARLA = process.platform === 'win32'
  ? '"powershell -ExecutionPolicy Bypass -File scripts\\sn\\sn-clave-altum.ps1"'
  : '"source scripts/sn/sn-clave-altum.sh"';

const ALMACEN_WINDOWS = (name) => path.join(process.env.LOCALAPPDATA || '', 'Softnexus', `${name}.dpapi`);

function delLlavero(name) {
  return execFileSync('security', ['find-generic-password', '-a', os.userInfo().username, '-s', name, '-w'],
    { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], timeout: 5000, windowsHide: true }).trim();
}

// DPAPI: lo que cifró ConvertFrom-SecureString solo lo descifra el mismo usuario en el mismo computador.
function deDpapi(name) {
  const archivo = ALMACEN_WINDOWS(name);
  if (!existsSync(archivo)) return '';
  const guion = '$e = Get-Content -Raw -LiteralPath $env:SN_ARCHIVO_CLAVE;'
    + '$s = ConvertTo-SecureString $e.Trim();'
    + '[Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))';
  return execFileSync('powershell', ['-NoProfile', '-NonInteractive', '-Command', guion],
    { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], timeout: 8000, windowsHide: true,
      env: { ...process.env, SN_ARCHIVO_CLAVE: archivo } }).trim();
}

export function fromKeychain(name) {
  if (keyCache.has(name)) return keyCache.get(name);
  let value = '';
  try {
    if (process.platform === 'darwin') value = delLlavero(name);
    else if (process.platform === 'win32') value = deDpapi(name);
  } catch {
    value = ''; // no hay clave guardada en este computador
  }
  keyCache.set(name, value);
  return value;
}

export function keyName(connector) {
  return connector.key_env || 'SN_ALTUM_KEY';
}

export function hasKey(connector) {
  return Boolean(process.env[keyName(connector)] || fromKeychain(keyName(connector)));
}

function key(connector) {
  const name = keyName(connector);
  const value = process.env[name] || fromKeychain(name);
  if (!value) throw new Error(`falta la clave de Altum (${name}): guárdala con ${COMO_GUARDARLA} (una sola vez, sirve para todos tus proyectos)`);
  return value;
}

export async function api(connector, method, route, body, headers = {}) {
  const base = (connector.base_url || process.env.SN_ALTUM_BASE_URL || DEFAULT_BASE).replace(/\/$/, '');
  const response = await fetch(`${base}${route}`, {
    method,
    // Quién escribe: el plugin, no "alguien". Altum puede mostrarlo en el historial de la tarea (pedido J).
    headers: {
      'X-API-Key': key(connector), 'Content-Type': 'application/json', Accept: 'application/json',
      'User-Agent': CLIENTE, 'X-Client-Name': 'Plugin Softnexus', ...headers,
    },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  if (response.status === 409 || response.status === 422) {
    const text = await response.text();
    let detail = null;
    try { detail = JSON.parse(text); } catch { /* cuerpo no JSON */ }
    const message = blockersMessage(detail)
      || `Altum rechazó el cambio (HTTP ${response.status}${response.status === 422 ? ', estado o campo no válido en ese proyecto' : ''}): ${(detail?.error || text).slice(0, 200)}`;
    throw new NotRetryable(message, response.status);
  }
  if (response.status === 429) throw new Error(`Altum: límite de peticiones (reintentar en ${response.headers.get('retry-after') || '?'} s)`);
  const reasons = {
    401: 'clave de API inválida o revocada: si la regeneraste en Altum, la anterior dejó de servir — vuelve a guardarla con "bash scripts/sn/sn-clave-altum.sh"',
    403: 'la clave no tiene permiso: revisa que tenga tasks:read / tasks:write y, si es personal, que estés asignado a ese proyecto',
    404: 'no existe, o tu clave personal no alcanza ese proyecto: revisa el project_id y que estés asignado a él en Altum',
  };
  if (!response.ok) throw new Error(`Altum HTTP ${response.status}${reasons[response.status] ? ` — ${reasons[response.status]}` : ''} en ${route}`);
  return response.status === 204 ? null : response.json();
}

// Todas las tareas que cumplen el filtro, página por página (GET /tasks trae como máximo 200 por página).
// Con include_deleted=true, Altum devuelve además "deleted": las tareas borradas de verdad.
export async function listTasks(connector, params = {}) {
  const filters = Object.fromEntries(Object.entries(params).filter(([, v]) => v !== undefined && v !== ''));
  const items = [];
  const deleted = [];
  for (let page = 1; ; page += 1) {
    const query = new URLSearchParams({ ...filters, page, limit: PAGE_LIMIT });
    const result = await api(connector, 'GET', `/tasks?${query}`);
    items.push(...(result.items || []));
    if (page === 1) deleted.push(...(result.deleted || []));
    if (!result.items?.length || items.length >= (result.total ?? items.length)) return { items, deleted };
  }
}

// Estados del workflow del proyecto: objetos { key, label, kind } con kind open|in_progress|done|cancelled.
// Los estados cambian muy poco: se guardan STATES_TTL_MS en .sn/state/ para que el vigilante
// haga una sola petición por minuto (solo GET /tasks) y no gaste el límite de la clave.
const STATES_FILE = 'altum-states.json';
const STATES_TTL_MS = 10 * 60000;

export async function projectStates(connector, { fresh = false } = {}) {
  const cached = readState(STATES_FILE, {})[connector.project_id];
  if (!fresh && cached && Date.now() - cached.at < STATES_TTL_MS) return cached.value;
  const value = await fetchProjectStates(connector);
  writeState(STATES_FILE, { ...readState(STATES_FILE, {}), [connector.project_id]: { at: Date.now(), value } });
  return value;
}

async function fetchProjectStates(connector) {
  const raw = await api(connector, 'GET', `/projects/${connector.project_id}/config/estados`);
  const list = Array.isArray(raw) ? raw : raw?.items || [];
  return {
    valid: list.map((s) => s.key),
    done: list.filter((s) => ['done', 'cancelled'].includes(s.kind)).map((s) => s.key),
    list: list.map((s) => ({ key: s.key, kind: s.kind })),
  };
}

// Campos propios del proyecto (GET /config/campos). Se envían solo los que el proyecto definió, de tipo
// texto o lista, y con un valor permitido. Por defecto: riesgo, tamaño y etapa (cambiable con "field_map").
const DEFAULT_FIELDS = { risk: 'riesgo', size: 'tamano', stage_label: 'etapa' };

async function projectFields(connector) {
  try {
    const raw = await api(connector, 'GET', `/projects/${connector.project_id}/config/campos`);
    return Array.isArray(raw) ? raw : raw?.items || [];
  } catch (error) {
    if (/HTTP 404/.test(error.message)) return [];
    throw error;
  }
}

export function customFieldsFor(connector, item, fields) {
  const defs = new Map(fields.map((f) => [f.key, f]));
  const allowed = (def, value) => def && value && (def.field_type === 'text' || (def.field_type === 'select' && def.options?.includes(value)));
  return Object.fromEntries(Object.entries(connector.field_map || DEFAULT_FIELDS)
    .filter(([source, key]) => allowed(defs.get(key), item[source]))
    .map(([source, key]) => [key, item[source]]));
}

// Una lectura por ejecución: estados válidos del proyecto y tareas ya enlazadas.
// El enlace firme es external_ref (= id del ítem); la marca oculta en la descripción queda para tareas viejas.
const runCache = new Map();
async function projectContext(connector) {
  if (runCache.has(connector.name)) return runCache.get(connector.name);
  const [{ valid, list: estados = valid.map((key) => ({ key })) }, fields, { items }] = await Promise.all([
    projectStates(connector, { fresh: true }),
    projectFields(connector),
    listTasks(connector, { project_id: connector.project_id }),
  ]);
  const byRef = new Map(items.map((t) => [t.external_ref || findMark(t.description), t.id]).filter(([id]) => id));
  // En un PATCH, custom_fields REEMPLAZA el objeto: se guarda lo que ya tiene cada tarea para mezclarlo.
  const currentFields = new Map(items.map((t) => [t.id, t.custom_fields || {}]));
  // Cómo está cada tarea hoy en Altum: solo se envía lo que cambió (cada PATCH queda en el historial).
  const current = new Map(items.map((t) => [t.id, t]));
  const context = { validStates: valid, estados, fields, byRef, currentFields, current };
  runCache.set(connector.name, context);
  return context;
}

// "[ID] título" sin repetir el prefijo si el título ya lo trae (p. ej. tareas importadas desde Altum).
export function taskTitle(item) {
  const clean = String(item.title || '').replace(/^(\[[^\]]+\]\s*)+/, '');
  return `[${item.id}] ${clean}`;
}

export function priorityOf(item) {
  if (item.type === 'incident' || item.risk === 'R4') return 1;
  return { R3: 2, R2: 3 }[item.risk] || 4;
}

// Qué "kind" de Altum corresponde a cada etapa, para proyectos con workflow propio.
const STAGE_KIND = {
  triaged: 'open', ready: 'open', planning: 'in_progress', plan_written: 'in_progress', plan_approved: 'in_progress',
  building: 'in_progress', built: 'in_progress', verified: 'in_progress', in_review: 'in_progress', merged: 'done', done: 'done', discarded: 'cancelled',
};

// Estado a enviar: el de status_map o el de siempre si el proyecto lo tiene; si no (workflow propio),
// el equivalente por "kind" — así una tarea terminada SIEMPRE queda en un estado de terminado.
// Al terminar (PR unido o plano archivado) se usa el ÚLTIMO estado "done" del workflow, el más cerrado;
// en los demás, el primero. Unir el PR ya es terminar: la tarea se cierra ahí, sin esperar a nadie.
export function estadoPara(stage, estados, statusMap = {}) {
  const valid = estados.map((s) => s.key);
  const wanted = statusMap[stage] || DEFAULT_STATUS[stage];
  if (valid.includes(wanted)) return wanted;
  const kind = STAGE_KIND[stage];
  let mismos = estados.filter((s) => s.kind === kind);
  // Descartado sin estado de "cancelado" en el workflow: se cierra igual (nunca queda abierta).
  if (!mismos.length && kind === 'cancelled') mismos = estados.filter((s) => s.kind === 'done');
  if (!mismos.length) return null;
  return (['merged', 'done', 'discarded'].includes(stage) ? mismos[mismos.length - 1] : mismos[0]).key;
}

function stateFor(connector, item, estados) {
  return estadoPara(item.stage, estados, connector.status_map);
}

// Criterios de aceptación: en el repositorio son la sección "Criterios de aceptación" del ítem y a
// Altum van como texto plano. Si alguien los edita a mano en Altum, llegan con HTML simple
// (<strong>, <ul>...): para comparar se quitan etiquetas y viñetas, así un cambio solo de formato
// no provoca otro PATCH (ni ensucia el historial).
export function textoPlano(valor) {
  return String(valor || '')
    .replace(/<\s*br\s*\/?>/gi, '\n').replace(/<\/(p|li|div|h\d)>/gi, '\n').replace(/<li[^>]*>/gi, '- ')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'")
    .replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
}

const comparable = (valor) => textoPlano(valor).replace(/^\s*([-*•]|\d+[.)])\s*/gm, '').replace(/\*\*|`/g, '').replace(/\s+/g, ' ').trim().toLowerCase();

function criteriosDe(item) {
  return String(item.criteria || '').replace(/\*\*|`/g, '').trim();
}

// Solo los campos que de verdad cambian. Altum guarda en el historial cada PATCH con el antes y el
// después completos, así que mandar lo mismo otra vez solo ensucia ese historial.
function soloCambios(actual = {}, deseado) {
  return Object.fromEntries(Object.entries(deseado).filter(([k, v]) => (k === 'acceptance_criteria'
    ? comparable(actual[k]) !== comparable(v)
    : JSON.stringify(actual[k] ?? null) !== JSON.stringify(v ?? null))));
}

export async function deliverAltum(connector, evt) {
  if (!connector.project_id) throw new NotRetryable('falta "project_id" (UUID del proyecto en Altum) en el conector');
  if (evt.type === 'sn.test') {
    await projectContext(connector);
    return;
  }
  const { item } = evt;
  const { estados, fields, byRef, currentFields, current } = await projectContext(connector);
  const description = itemPlainBody(evt);
  const email = item.assignee.match(/<([^>]+@[^>]+)>/)?.[1] || '';
  const assignee = connector.assignee_map?.[email || item.assignee];
  const ours = customFieldsFor(connector, item, fields);
  let taskId = item.external?.[connector.name] || byRef.get(item.id);
  // Tarea de una reunión (Acten): mismo endpoint, pero solo estado, responsable, título y descripción.
  // Prioridad, campos propios, etiquetas o external_ref responderían 422, así que ni se envían.
  if (esDeActen({ id: taskId })) {
    const estado = connector.acten_status_map?.[item.stage] || ACTEN_STATUS[item.stage];
    const cambios = soloCambios(current.get(taskId), {
      title: taskTitle(item), description,
      ...(ACTEN_STATES.includes(estado) ? { state: estado } : {}), ...(assignee ? { assignee_id: assignee } : {}),
    });
    if (!Object.keys(cambios).length) return;
    const actualizada = await api(connector, 'PATCH', `/tasks/${encodeURIComponent(taskId)}`, cambios);
    current.set(taskId, { ...current.get(taskId), ...cambios, ...actualizada });
    recordPush(connector, { ...actualizada, id: taskId });
    return;
  }
  if (!taskId) {
    const created = await createTask(connector, item, description, { assignee, email, customFields: ours });
    taskId = created.id;
    byRef.set(item.id, taskId);
    currentFields.set(taskId, created.custom_fields || ours);
    // Recién creada con título, descripción y prioridad: el PATCH de abajo solo lleva lo que falte (p. ej. el estado).
    current.set(taskId, created);
    recordPush(connector, created); // también es un cambio nuestro: el vigilante no debe avisarlo
    if (item.file) setExternalId(item.file, connector.name, taskId); // queda en el repo con el siguiente commit
  }
  const state = stateFor(connector, item, estados);
  const customFields = Object.keys(ours).length ? { custom_fields: { ...(currentFields.get(taskId) || {}), ...ours } } : {};
  const criterios = criteriosDe(item);
  const cambios = soloCambios(current.get(taskId), {
    title: taskTitle(item), description, priority: priorityOf(item),
    ...(criterios ? { acceptance_criteria: criterios } : {}),
    ...(state ? { state } : {}), ...(assignee ? { assignee_id: assignee } : {}), ...customFields,
  });
  if (!Object.keys(cambios).length) return; // nada cambió: la tarea no se toca
  const updated = await api(connector, 'PATCH', `/tasks/${taskId}`, cambios);
  if (updated?.custom_fields) currentFields.set(taskId, updated.custom_fields);
  current.set(taskId, { ...current.get(taskId), ...cambios, ...updated });
  recordPush(connector, { ...updated, id: taskId });
}

// POST con external_ref (enlace firme) e Idempotency-Key (un reintento por corte de red no duplica).
// Responsable: el UUID de assignee_map o, si no está, el correo de git (assignee_email); si ese correo
// no existe en la empresa (404), la tarea se crea sin responsable. Si external_ref ya existe (409), se reutiliza.
// Devuelve la tarea creada (o la existente).
async function createTask(connector, item, description, { assignee, email, customFields }) {
  const body = {
    project_id: connector.project_id,
    title: taskTitle(item),
    kind: connector.kind_map?.[item.type] || DEFAULT_KIND[item.type],
    description,
    ...(criteriosDe(item) ? { acceptance_criteria: criteriosDe(item) } : {}),
    priority: priorityOf(item),
    external_ref: item.id,
    ...(Object.keys(customFields).length ? { custom_fields: customFields } : {}),
    ...(assignee ? { assignee_id: assignee } : email ? { assignee_email: email } : {}),
  };
  // Si la tarea anterior se borró en Altum, la clave de idempotencia tiene que ser otra:
  // con la misma, Altum devolvería la respuesta de la primera vez (la tarea borrada).
  const headers = { 'Idempotency-Key': `sn-${connector.project_id}-${item.id}${item.recrear ? `-re-${String(item.recrear).slice(0, 8)}` : ''}` };
  try {
    return await api(connector, 'POST', '/tasks', body, headers);
  } catch (error) {
    if (error.status === 409) {
      const { items } = await listTasks(connector, { project_id: connector.project_id, external_ref: item.id });
      if (items[0]) return items[0];
    }
    if (body.assignee_email && /HTTP 404/.test(error.message)) {
      const { assignee_email: _, ...withoutAssignee } = body;
      return api(connector, 'POST', '/tasks', withoutAssignee, { 'Idempotency-Key': `${headers['Idempotency-Key']}-sin-responsable` });
    }
    throw error;
  }
}

// Para quien reciba los webhooks de Altum (n8n, función serverless…): firma = HMAC-SHA256(secreto, "{timestamp}." + cuerpo).
export function verifyAltumSignature({ secret, timestamp, signature, rawBody, now = Date.now() / 1000 }) {
  if (!secret || !timestamp || !signature) return false;
  if (Math.abs(now - Number(timestamp)) > MAX_SIGNATURE_AGE_S) return false;
  const expected = createHmac('sha256', secret).update(`${timestamp}.`).update(rawBody).digest('hex');
  const received = String(signature).replace(/^sha256=/, '');
  return expected.length === received.length && timingSafeEqual(Buffer.from(expected), Buffer.from(received));
}
