// Texto de una tarea externa (issue de GitHub, descripción en Altum): historia + trazabilidad + marca oculta.
// La marca <!-- sn-item:ID --> permite encontrar la tarea sin guardar ids y evita duplicados entre computadores.
export const mark = (id) => `<!-- sn-item:${id} -->`;

export function findMark(text) {
  return (text || '').match(/<!-- sn-item:([^ ]+) -->/)?.[1] || '';
}

export function itemMarkdownBody(evt) {
  const { item } = evt;
  const commits = (item.commits || []).map((c) => `- \`${c.short}\` ${c.subject} — ${c.author} (${(c.date || '').slice(0, 10)})`);
  return [
    `**Etapa:** ${item.stage_label}${item.flag ? ` · ${item.flag}` : ''} · **Riesgo:** ${item.risk || '—'} · **Tamaño:** ${item.size || '—'}`,
    `**Responsable:** ${item.assignee || '—'}`, '',
    '## Historia', item.story || '_(ver archivo del ítem)_', '',
    '## Trazabilidad',
    `- Archivo: \`${item.file}\``,
    `- Rama: ${item.branch ? `\`${item.branch}\`` : '—'} · Plano: ${item.change ? `\`${item.change}\`` : '—'}`,
    `- Avance: ${item.tasks_total ? `${item.tasks_done}/${item.tasks_total} tareas` : '—'} · PR: ${item.pr_url || '—'}`,
    `- Commits (${item.commit_count ?? 0}):`, ...(commits.length ? commits : ['  - todavía no hay']), '',
    '_Actualizado automáticamente por Softnexus Spec Driven. No edites este texto: cambia el ítem en el repositorio._',
    mark(item.id),
  ].join('\n');
}


// Altum: texto plano (no interpreta Markdown) y ESTABLE. Altum guarda en el historial el antes y el
// después completos de cada cambio de descripción, así que aquí solo va lo que casi no cambia:
// la historia, riesgo, tamaño, responsable, rama, plano y PR (cada uno cambia una vez en la vida).
// La etapa ya la dice el estado de la tarea; el avance y los commits viven en el repositorio (/sn-items).
export function itemPlainBody(evt) {
  const { item } = evt;
  const historia = String(item.story || '(ver archivo del ítem)')
    .replace(/^#{2,6}\s+(.*)$/gm, (_, titulo) => titulo.toUpperCase())
    .replace(/\*\*|`/g, '')
    .replace(/(^|\s)_([^_\n]+)_(?=\s|$)/g, '$1$2');
  return [
    ...(item.discarded ? [`DESCARTADO: ${item.discarded}`, ''] : []),
    `Riesgo: ${item.risk || '—'} · Tamaño: ${item.size || '—'}`,
    `Responsable: ${item.assignee || '—'}`, '',
    'HISTORIA', historia, '',
    'TRAZABILIDAD',
    `- Archivo: ${item.file}`,
    `- Rama: ${item.branch || '—'} · Plano: ${item.change || '—'}`,
    `- PR: ${item.pr_url || '—'}`,
    `- Avance, commits y evidencia: en el repositorio (/sn-items ${item.id}).`, '',
    'Actualizado automáticamente por Softnexus Spec Driven. No edites este texto: cambia el ítem en el repositorio.',
  ].join('\n');
}
