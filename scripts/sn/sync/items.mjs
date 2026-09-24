// Ítems de trabajo en texto: docs/items/<ID>.md con cabecera "clave: valor" entre líneas ---.
// Son la fuente legible y exportable; el estado (etapa) nunca se escribe aquí, se deriva.
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';

export const ITEMS_DIR = 'docs/items';
export const ITEM_TYPES = ['feature', 'improvement', 'bug', 'incident', 'content', 'chore'];

export function parseFrontmatter(text) {
  const match = text.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/);
  if (!match) return { data: {}, body: text };
  const data = {};
  for (const line of match[1].split('\n')) {
    const kv = line.match(/^([A-Za-z0-9_.-]+):\s*(.*)$/);
    if (!kv) continue;
    const value = kv[2].replace(/\s+#.*$/, '').trim().replace(/^["']|["']$/g, '');
    data[kv[1]] = value;
  }
  return { data, body: match[2] };
}

function section(body, title) {
  const re = new RegExp(`^##\\s+${title}[^\\n]*\\n([\\s\\S]*?)(?=^##\\s|$(?![\\s\\S]))`, 'mi');
  const found = body.match(re);
  return found ? found[1].trim() : '';
}

// Ids externos: claves "ext.<conector>: <id>" (solo si el sistema externo no acepta nuestro id).
function externalIds(data) {
  return Object.fromEntries(Object.entries(data)
    .filter(([k, v]) => k.startsWith('ext.') && v)
    .map(([k, v]) => [k.slice(4), v]));
}

export function readItems(root = '.') {
  const dir = path.join(root, ITEMS_DIR);
  if (!existsSync(dir)) return [];
  return readdirSync(dir)
    .filter((f) => f.endsWith('.md') && !f.startsWith('_'))
    .map((file) => {
      const text = readFileSync(path.join(dir, file), 'utf8');
      const { data, body } = parseFrontmatter(text);
      const criteria = section(body, 'Criterios');
      return {
        id: data.id || path.basename(file, '.md'),
        file: path.join(ITEMS_DIR, file),
        type: ITEM_TYPES.includes(data.type) ? data.type : 'feature',
        title: data.title || '',
        risk: data.risk || '',
        size: data.size || '',
        change: data.change || '',
        branch: data.branch || '',
        assignee: data.assignee || '',
        origin: data.origin || '',
        created: data.created || '',
        parent: data.parent || '',
        // "descartado: <motivo>": se decidió no hacerlo. Queda escrito por qué y la tarea se cancela en Altum.
        discarded: data.descartado || '',
        external: externalIds(data),
        ready: /dado|given/i.test(criteria) && !/\?\s*$/m.test(section(body, 'Preguntas abiertas')),
        story: section(body, 'Historia'),
        criteria,
        body,
      };
    });
}

// Guarda el id que asignó un sistema externo (ej. "ext.altum: <uuid>") en la cabecera del ítem.
export function setExternalId(file, connectorName, externalId) {
  const text = readFileSync(file, 'utf8');
  const key = `ext.${connectorName}`;
  const line = `${key}: ${externalId}`;
  const re = new RegExp(`^${key.replace('.', '\\.')}:.*$`, 'm');
  const updated = re.test(text) ? text.replace(re, line) : text.replace(/^---\n([\s\S]*?)\n---/, (m, head) => `---\n${head}\n${line}\n---`);
  if (updated !== text) writeFileSync(file, updated);
}

// Crea un ítem nuevo a partir de una tarea que nació en un sistema externo.
export function writeImportedItem(root, { id, type, title, story, criteria = '', origin, created, external }) {
  const dir = path.join(root, ITEMS_DIR);
  mkdirSync(dir, { recursive: true });
  const file = path.join(dir, `${id}.md`);
  if (existsSync(file)) return '';
  const ext = Object.entries(external).map(([k, v]) => `ext.${k}: ${v}`).join('\n');
  writeFileSync(file, `---\nid: ${id}\ntype: ${type}\ntitle: ${title.replace(/\n/g, ' ')}\nrisk:\nsize:\nchange:\nbranch:\nassignee:\n`
    + `origin: ${origin}\ncreated: ${created}\n${ext}\n---\n## Historia\n${story || ''}\n\n## Criterios de aceptación\n${criteria || ''}\n\n## Preguntas abiertas\n- Falta refinar: usar /sn con este ítem.\n`);
  return file;
}
