// Vistas para personas: lista de ítems y ficha completa de un ítem, en Markdown listo para copiar y pegar
// en cualquier sistema de tareas (no necesita conexión).
import { readFileSync } from 'node:fs';
import { parseFrontmatter } from './items.mjs';
import { STAGES, FLAGS } from './snapshot.mjs';
import { traceOf } from './trace.mjs';

const TYPE_LABEL = {
  feature: 'Funcionalidad', improvement: 'Mejora', bug: 'Bug', incident: 'Incidente', content: 'Contenido', chore: 'Tarea técnica',
};

function cell(text) {
  return String(text ?? '').replace(/\|/g, '\\|').replace(/\n/g, ' ');
}

export function listMarkdown(snapshot) {
  if (!snapshot.items.length) return 'No hay ítems todavía. Se crean solos con /sn.\n';
  const rows = snapshot.items
    .sort((a, b) => (b.created || '').localeCompare(a.created || ''))
    .map((i) => `| ${cell(i.id)} | ${TYPE_LABEL[i.type]} | ${cell(i.title)} | ${STAGES[i.stage]}${i.flag ? ` · ${FLAGS[i.flag]}` : ''} | ${i.risk || '—'} | ${i.commit_count ?? 0} | ${cell(i.file)} |`);
  return `# Ítems de ${snapshot.project}\n\n| ID | Tipo | Título | Etapa | Riesgo | Commits | Archivo |\n|---|---|---|---|---|---|---|\n${rows.join('\n')}\n`;
}

export function itemMarkdown(item) {
  const { body } = parseFrontmatter(readFileSync(item.file, 'utf8'));
  const trace = traceOf(item);
  const stage = `${STAGES[item.stage]}${item.flag ? ` · ${FLAGS[item.flag]}` : ''}`;
  const out = [
    `# ${item.id} · ${item.title}`, '',
    `**Tipo:** ${TYPE_LABEL[item.type]} · **Riesgo:** ${item.risk || '—'} · **Tamaño:** ${item.size || '—'} · **Etapa:** ${stage}`,
    `**Responsable:** ${item.assignee || '—'} · **Creado:** ${item.created || '—'}`, '',
    body.trim(), '',
    '## Trazabilidad', '',
    `- **Archivo del ítem:** \`${item.file}\``,
    `- **Rama:** ${item.branch ? `\`${item.branch}\`` : '—'}`,
    `- **Plano (OpenSpec):** ${trace.change_path ? `\`${trace.change_path}\`` : '—'}`,
    `- **Avance:** ${item.tasks_total ? `${item.tasks_done}/${item.tasks_total} tareas` : '—'}`,
    `- **Evidencia:** ${trace.evidence ? `\`${trace.evidence}\`` : 'aún no'}`,
    `- **PR:** ${item.pr_url || '—'}`,
  ];
  if (trace.validations.length) {
    out.push('', '### Firmas', '', '| Fecha | Decisión | Sello | Quién | Notas |', '|---|---|---|---|---|');
    trace.validations.forEach((v) => out.push(`| ${v.date} | ${v.decision} | ${v.seal} | ${cell(v.by)} | ${cell(v.notes)} |`));
  }
  out.push('', `### Commits (${trace.commits.length})`, '');
  if (trace.commits.length) {
    out.push('| Commit | Fecha | Autor | Mensaje |', '|---|---|---|---|');
    trace.commits.forEach((c) => out.push(`| \`${c.short}\` | ${c.date.slice(0, 10)} | ${cell(c.author)} | ${cell(c.subject)} |`));
  } else out.push('Todavía no hay commits.');
  if (trace.files.length) {
    out.push('', `### Archivos de código tocados (${trace.files.length})`, '', ...trace.files.map((f) => `- \`${f}\``));
  }
  return `${out.join('\n')}\n`;
}
