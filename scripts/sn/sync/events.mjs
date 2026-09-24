// Compara dos fotos y produce eventos. El id de cada evento es determinista (mismo cambio -> mismo id),
// así los sistemas externos pueden descartar duplicados si dos computadores reportan lo mismo.
import { createHash } from 'node:crypto';

// story y criteria: cambiar la historia o los criterios de aceptación también se lleva a Altum.
const TRACKED_FIELDS = ['title', 'type', 'risk', 'size', 'assignee', 'branch', 'change', 'pr_url', 'tasks_done', 'tasks_total', 'commit_count', 'story', 'criteria'];

function eventId(parts) {
  return createHash('sha256').update(parts.join('|')).digest('hex').slice(0, 24);
}

function event(type, snapshot, item, extra = {}) {
  const key = [type, item.id, item.stage, item.flag, ...TRACKED_FIELDS.map((f) => item[f] ?? '')];
  return {
    specversion: '1.0',
    id: eventId(key),
    type,
    source: snapshot.source,
    project: snapshot.project,
    time: snapshot.taken_at,
    actor: item.actor || '',
    item,
    ...extra,
  };
}

export function diffSnapshots(previous, current) {
  const before = new Map((previous?.items || []).map((i) => [i.id, i]));
  const events = [];
  for (const item of current.items) {
    const old = before.get(item.id);
    if (!old) {
      events.push(event('sn.item.created', current, item));
      continue;
    }
    if (old.stage !== item.stage) {
      events.push(event('sn.item.stage_changed', current, item, { previous: { stage: old.stage } }));
    }
    if (old.flag !== item.flag) {
      const type = item.flag === 'awaiting_validation' ? 'sn.validation.requested'
        : (old.flag === 'awaiting_validation' ? 'sn.validation.decided' : 'sn.item.flag_changed');
      events.push(event(type, current, item, { previous: { flag: old.flag } }));
    }
    const changed = TRACKED_FIELDS.filter((f) => (old[f] ?? '') !== (item[f] ?? ''));
    if (changed.length) events.push(event('sn.item.updated', current, item, { changed }));
  }
  return events;
}

// Patrones de suscripción: "*", "sn.item.*", "sn.validation.requested", "sn.item.created:incident" (tipo de ítem).
export function matches(patterns = ['*'], evt) {
  return patterns.some((pattern) => {
    const [typePattern, itemType] = pattern.split(':');
    const typeOk = typePattern === '*' || typePattern === evt.type
      || (typePattern.endsWith('.*') && evt.type.startsWith(typePattern.slice(0, -1)));
    return typeOk && (!itemType || evt.item.type === itemType || evt.item.stage === itemType);
  });
}
