// Vigilante interno de Altum (sin n8n ni servidor público): mientras la sesión está abierta,
// pregunta a Altum cada ~60 s qué cambió (GET /tasks?updated_since=…) y deja los avisos en una bandeja
// local que el agente lee en el siguiente mensaje. Ignora los cambios hechos desde este computador.
import { execFile } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fechaUtc, lastPush, selfKeyId } from './altum.mjs';
import { readBacklog } from './altum-backlog.mjs';
import { readState, stateFile, writeState } from './store.mjs';

const INBOX = 'inbox.json';
const WATCH_STATE = 'watch-altum.json';
const PID_FILE = 'watch-altum.pid';
const OWN_CHANGE_WINDOW_MS = 15000; // con la misma clave, un cambio hasta 15 s después de nuestro PATCH es nuestro
const MAX_INBOX = 50;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export function readInbox() {
  return readState(INBOX, []);
}

export function clearInbox() {
  writeState(INBOX, []);
}

function notifyDesktop(title, message) {
  if (process.platform !== 'darwin') return;
  const safe = (text) => String(text).replace(/["\\]/g, '');
  execFile('osascript', ['-e', `display notification "${safe(message)}" with title "${safe(title)}"`], () => {});
}

// updated_by = null: alguien lo cambió a mano en Altum. Otra clave: otra persona o integración.
// Misma clave: fue este computador solo si coincide con su último envío (la clave puede ser compartida).
function isOwnChange(connector, entry) {
  const by = entry.task.updated_by;
  if (by === null) return false;
  const self = selfKeyId(connector);
  if (by?.id && self && by.id !== self) return false;
  const pushed = lastPush(entry.altum_id);
  if (!pushed || !entry.task.updated_at) return false;
  return fechaUtc(entry.task.updated_at) - fechaUtc(pushed) <= OWN_CHANGE_WINDOW_MS;
}

// Una ronda: devuelve los avisos nuevos y avanza la marca de tiempo. Cada consulta se solapa
// OVERLAP_MS con la anterior (por si los relojes no coinciden) y "seen" evita repetir avisos.
const OVERLAP_MS = 30000;
const MAX_SEEN = 500;

export async function checkOnce(connector, root = '.') {
  const state = readState(WATCH_STATE, { since: new Date().toISOString(), seen: {} });
  const startedAt = new Date().toISOString();
  const since = new Date(new Date(state.since).getTime() - OVERLAP_MS).toISOString();
  const backlog = await readBacklog(connector, root, since);
  const seen = { ...(state.seen || {}) };
  const fresh = backlog.filter((b) => seen[b.altum_id] !== b.task.updated_at);
  fresh.forEach((b) => { seen[b.altum_id] = b.task.updated_at; });
  const notes = fresh.filter((b) => !isOwnChange(connector, b)).map((b) => ({
    at: startedAt,
    kind: b.deleted ? (b.item ? 'deleted' : 'closed-elsewhere') : b.item ? 'changed' : (b.open ? 'new' : 'closed-elsewhere'),
    number: b.number, title: b.title, state: b.state, priority: b.priority, item: b.item, altum_id: b.altum_id,
  })).filter((n) => n.kind !== 'closed-elsewhere');
  writeState(WATCH_STATE, { since: startedAt, seen: Object.fromEntries(Object.entries(seen).slice(-MAX_SEEN)) });
  if (notes.length) writeState(INBOX, [...readInbox(), ...notes].slice(-MAX_INBOX));
  return notes;
}

export function describe(note) {
  // Las de reuniones (Acten) no tienen número ni prioridad: se nombran por lo que son.
  if (note.kind === 'new' && !note.number) return `Nueva tarea en Altum, nacida en una reunión: ${note.title}. Tráela con /sn.`;
  if (note.kind === 'new') return `Nueva tarea en Altum #${note.number}: ${note.title} (prioridad ${note.priority ?? '—'}). Tráela con /sn.`;
  if (note.kind === 'deleted') return `${note.item}: su tarea #${note.number} se borró en Altum. Pregunta si se descarta el ítem o se vuelve a crear.`;
  return `${note.item} cambió en Altum: estado ${note.state}${note.priority ? `, prioridad ${note.priority}` : ''}.`;
}

// Bucle del vigilante. Un solo vigilante por repositorio (archivo pid). Se detiene al cerrar la sesión,
// al pasar max-minutes, o si otro vigilante tomó su lugar.
export async function watch(connector, { everySeconds = 60, maxMinutes = 480, notify = true } = {}) {
  const pidFile = stateFile(PID_FILE);
  mkdirSync(path.dirname(pidFile), { recursive: true });
  writeFileSync(pidFile, String(process.pid));
  const deadline = Date.now() + maxMinutes * 60000;
  let delay = everySeconds * 1000;
  while (Date.now() < deadline) {
    if (!existsSync(pidFile) || readFileSync(pidFile, 'utf8').trim() !== String(process.pid)) return;
    try {
      const notes = await checkOnce(connector);
      if (notify && notes.length) notifyDesktop('Altum', notes.length === 1 ? describe(notes[0]) : `${notes.length} cambios en Altum`);
      delay = everySeconds * 1000;
    } catch (error) {
      const retryAfter = Number(error.message.match(/reintentar en (\d+)/)?.[1]);
      delay = retryAfter ? retryAfter * 1000 : Math.min(delay * 2, 10 * 60000); // respeta 429 y se aleja si Altum falla
    }
    await sleep(delay + Math.floor(Math.random() * 5000)); // variación para no coincidir con otros computadores
  }
  if (existsSync(pidFile) && readFileSync(pidFile, 'utf8').trim() === String(process.pid)) rmSync(pidFile);
}

export function stopWatch() {
  const pidFile = stateFile(PID_FILE);
  if (!existsSync(pidFile)) return false;
  const pid = Number(readFileSync(pidFile, 'utf8'));
  rmSync(pidFile);
  try {
    process.kill(pid);
  } catch {
    // ya no existía
  }
  return true;
}

export function isWatching() {
  const pidFile = stateFile(PID_FILE);
  if (!existsSync(pidFile)) return false;
  try {
    process.kill(Number(readFileSync(pidFile, 'utf8')), 0);
    return true;
  } catch {
    return false;
  }
}
