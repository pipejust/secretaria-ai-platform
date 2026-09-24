// Estado local de la sincronización (NO se versiona: .sn/state/ está en .gitignore).
//   last-snapshot.json : última foto enviada desde este computador
//   outbox.jsonl       : entregas fallidas pendientes de reintento (el sistema externo estaba caído)
//   lock/              : evita dos sincronizaciones a la vez
import { appendFileSync, existsSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import path from 'node:path';

export const SN_DIR = '.sn';
export const CONFIG_FILE = path.join(SN_DIR, 'connectors.json');
const STATE_DIR = path.join(SN_DIR, 'state');
const SNAPSHOT_FILE = path.join(STATE_DIR, 'last-snapshot.json');
const OUTBOX_FILE = path.join(STATE_DIR, 'outbox.jsonl');
const LOCK_DIR = path.join(STATE_DIR, 'lock');
const STALE_LOCK_MS = 120000;
const MAX_ATTEMPTS = 20;

function ensureState() {
  mkdirSync(STATE_DIR, { recursive: true });
}

export function loadConfig() {
  if (!existsSync(CONFIG_FILE)) return null;
  const config = JSON.parse(readFileSync(CONFIG_FILE, 'utf8'));
  return { ...config, connectors: (config.connectors || []).filter((c) => c.enabled !== false) };
}

export function loadSnapshot() {
  return existsSync(SNAPSHOT_FILE) ? JSON.parse(readFileSync(SNAPSHOT_FILE, 'utf8')) : null;
}

export function saveSnapshot(snapshot) {
  ensureState();
  writeFileSync(SNAPSHOT_FILE, `${JSON.stringify(snapshot, null, 2)}\n`);
}

export function readOutbox() {
  if (!existsSync(OUTBOX_FILE)) return [];
  return readFileSync(OUTBOX_FILE, 'utf8').split('\n').filter(Boolean).map((line) => JSON.parse(line));
}

export function writeOutbox(entries) {
  ensureState();
  const kept = entries.filter((e) => e.attempts < MAX_ATTEMPTS);
  writeFileSync(OUTBOX_FILE, kept.map((e) => JSON.stringify(e)).join('\n') + (kept.length ? '\n' : ''));
  return entries.length - kept.length; // descartadas por demasiados intentos
}

export function appendOutbox(entry) {
  ensureState();
  appendFileSync(OUTBOX_FILE, `${JSON.stringify(entry)}\n`);
}

export function acquireLock() {
  ensureState();
  try {
    mkdirSync(LOCK_DIR);
    return true;
  } catch {
    const age = Date.now() - statSync(LOCK_DIR).mtimeMs;
    if (age < STALE_LOCK_MS) return false;
    rmSync(LOCK_DIR, { recursive: true, force: true }); // candado viejo de un proceso que murió
    mkdirSync(LOCK_DIR);
    return true;
  }
}

// Archivos JSON auxiliares en .sn/state/ (último pull, cambios propios, bandeja de avisos).
export function stateFile(name) {
  return path.join(STATE_DIR, name);
}

export function readState(name, fallback) {
  const file = stateFile(name);
  try {
    return existsSync(file) ? JSON.parse(readFileSync(file, 'utf8')) : fallback;
  } catch {
    return fallback; // archivo a medio escribir o dañado: se reconstruye
  }
}

export function writeState(name, value) {
  ensureState();
  writeFileSync(stateFile(name), `${JSON.stringify(value, null, 2)}\n`);
}

export function releaseLock() {
  rmSync(LOCK_DIR, { recursive: true, force: true });
}
