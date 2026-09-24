// Conectores de salida. Configuración en .sn/connectors.json (sin secretos: solo NOMBRES de variables de entorno).
//   rest    -> upsert del ítem en una API REST (contrato "Softnexus Task Sync"), idempotente por id
//   webhook -> POST del evento completo, firmado con HMAC-SHA256 (cabecera X-SN-Signature)
//   matrix  -> mensaje de texto a una sala de Matrix/Element (API cliente-servidor)
//   github  -> un issue por ítem (lo usan Orca, GitHub Projects); ver github.mjs
//   altum   -> API de tareas de Altum (POST/PATCH /tasks, X-API-Key por empresa); ver altum.mjs
import { createHmac } from 'node:crypto';
import { deliverAltum } from './altum.mjs';
import { fetchAltumTask } from './altum-backlog.mjs';
import { deliverGithub } from './github.mjs';

const TIMEOUT_MS = 10000;

function secret(name) {
  const value = name ? process.env[name] : '';
  if (name && !value) throw new Error(`falta la variable de entorno ${name}`);
  return value;
}

async function send(url, options) {
  const response = await fetch(url, { ...options, signal: AbortSignal.timeout(TIMEOUT_MS) });
  if (!response.ok) throw new Error(`HTTP ${response.status} en ${new URL(url).host}`);
  return response;
}

function authHeaders(auth = {}) {
  if (auth.type === 'bearer') return { Authorization: `Bearer ${secret(auth.env)}` };
  if (auth.type === 'header') return { [auth.header]: secret(auth.env) };
  return {};
}

function fill(template, values) {
  return template.replace(/\{(\w+)\}/g, (_, key) => encodeURIComponent(values[key] ?? ''));
}

// Cuerpo estándar del contrato Softnexus Task Sync (ver references/integraciones.md).
export function taskBody(connector, evt) {
  const { item } = evt;
  return {
    external_key: item.id,
    project: evt.project,
    type: item.type,
    title: item.title,
    status: connector.status_map?.[item.stage] || item.stage,
    stage: item.stage,
    stage_label: item.stage_label,
    flag: item.flag || null,
    risk: item.risk,
    size: item.size,
    assignee: item.assignee,
    story: item.story,
    progress: item.tasks_total ? { done: item.tasks_done, total: item.tasks_total } : null,
    commits: { count: item.commit_count ?? 0, recent: item.commits || [] },
    links: { file: item.file, branch: item.branch, change: item.change, pr: item.pr_url || null, repo: evt.source },
    updated_at: evt.time,
    event: { id: evt.id, type: evt.type },
  };
}

async function deliverRest(connector, evt) {
  const path = fill(connector.upsert?.path || '/items/{id}', { id: evt.item.id, project: evt.project });
  await send(`${connector.base_url.replace(/\/$/, '')}${path}`, {
    method: connector.upsert?.method || 'PUT',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': evt.id, ...authHeaders(connector.auth) },
    body: JSON.stringify(taskBody(connector, evt)),
  });
}

async function deliverWebhook(connector, evt) {
  const body = JSON.stringify(evt);
  const headers = { 'Content-Type': 'application/json', 'X-SN-Event': evt.type, 'X-SN-Delivery': evt.id };
  if (connector.secret_env) {
    headers['X-SN-Signature'] = `sha256=${createHmac('sha256', secret(connector.secret_env)).update(body).digest('hex')}`;
  }
  await send(connector.url, { method: 'POST', headers, body });
}

export function matrixText(evt) {
  const { item } = evt;
  const head = `[${evt.project}] ${item.id} · ${item.title}`;
  const lines = {
    'sn.item.created': `Nuevo ${item.type} (${item.risk || 'sin riesgo'} · ${item.size || '—'}): ${head}`,
    'sn.item.stage_changed': `${head}\n${evt.previous?.stage || ''} → ${item.stage_label}`,
    'sn.validation.requested': `Validación pedida · ${head}\nEn tu computador: /sn-validate ${item.branch}`,
    'sn.validation.decided': `Validación respondida · ${head}\nPara seguir: git pull y /sn-status`,
  };
  const text = lines[evt.type] || `${head}\n${evt.type.replace('sn.', '')}: ${item.stage_label}`;
  return item.pr_url ? `${text}\nPR: ${item.pr_url}` : text;
}

async function deliverMatrix(connector, evt) {
  const room = encodeURIComponent(connector.room_id);
  const url = `${connector.homeserver.replace(/\/$/, '')}/_matrix/client/v3/rooms/${room}/send/m.room.message/sn-${evt.id}`; // mismo id de transacción = Matrix no duplica en reintentos
  await send(url, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${secret(connector.token_env)}` },
    body: JSON.stringify({ msgtype: 'm.notice', body: matrixText(evt) }),
  });
}

const DELIVER = { rest: deliverRest, webhook: deliverWebhook, matrix: deliverMatrix, github: deliverGithub, altum: deliverAltum };

export async function deliver(connector, evt) {
  const fn = DELIVER[connector.kind];
  if (!fn) throw new Error(`tipo de conector desconocido: ${connector.kind}`);
  await fn(connector, evt);
}

// Trae un ítem desde el sistema externo (opcional: conectores rest con "fetch").
export async function fetchExternal(connector, externalId) {
  if (connector.kind === 'altum') return fetchAltumTask(connector, externalId);
  if (connector.kind !== 'rest' || !connector.fetch?.path) throw new Error(`${connector.name} no tiene "fetch" configurado`);
  const url = `${connector.base_url.replace(/\/$/, '')}${fill(connector.fetch.path, { id: externalId })}`;
  const response = await send(url, { headers: { Accept: 'application/json', ...authHeaders(connector.auth) } });
  return response.json();
}
