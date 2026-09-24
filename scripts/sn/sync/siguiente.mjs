// El siguiente paso EXACTO de un ítem, calculado desde el repositorio (no desde lo que el agente
// recuerde). Lo usan el comando `siguiente`, el aviso que recibe el agente en cada mensaje y el guard.
// Si hay un sello pendiente, ese es el siguiente paso: no se ofrece nada que lo salte.
const nivel = (riesgo) => Number(String(riesgo || 'R0').replace(/\D/g, '') || 0);

export function siguientePaso(item) {
  const r = nivel(item.risk);
  const id = item.id;
  if (item.stage === 'discarded') return { paso: 'Descartado', siguiente: 'nada: está cerrado con su motivo.' };
  if (item.flag === 'awaiting_validation') {
    return { paso: 'Esperando la firma del líder', siguiente: 'esperar. No se construye ni se entrega sobre lo que espera firma. Cuando el líder responda: git pull y /sn-status.', sello: true };
  }
  if (item.flag === 'invalid_signature') {
    return { paso: 'Firmado por alguien que no es el líder', siguiente: 'esa firma no vale. Pedir la firma al líder de Altum: /sn-request.', sello: true };
  }
  if (item.flag === 'changes_requested') return { paso: 'El líder pidió correcciones', siguiente: 'corregir lo que pidió y volver a pedir la firma con /sn-request.' };
  if (item.flag === 'blocked') return { paso: 'Detenido por el líder', siguiente: 'hablar con el líder antes de seguir.' };
  if (item.flag === 'validation_expired') return { paso: 'La firma venció (el plano cambió después)', siguiente: 'volver a pedir la firma: /sn-request.', sello: true };
  switch (item.stage) {
    case 'triaged': return { paso: 'Tarjeta', siguiente: 'completar la historia (sn-story).' };
    case 'ready': return { paso: 'Historia lista', siguiente: 'revisar el código del área y escribir el plano (openspec-propose).' };
    case 'planning': return { paso: 'Plano en curso', siguiente: 'terminar el plano (proposal, escenarios y tareas).' };
    case 'plan_written':
      return r >= 3
        ? { paso: 'Plano escrito', siguiente: 'SELLO 1: la persona lo revisa y, por ser R3+, lo firma el líder: /sn-request (sello plano). No se construye antes.', sello: true }
        : { paso: 'Plano escrito', siguiente: 'SELLO 1: mostrar el plano a la persona y registrar su aprobación en validacion.md. No se construye antes.', sello: true };
    case 'plan_approved': return { paso: 'Plano aprobado', siguiente: `confirmar la tarea en Altum (asegurar ${id}) y empezar a construir.` };
    case 'building': return { paso: `Construyendo (${item.tasks_done}/${item.tasks_total})`, siguiente: 'seguir con la siguiente tarea del plano. Si apareció trabajo fuera del plano: dividir.' };
    case 'built': return { paso: 'Construido', siguiente: 'evidencia completa (sn-evidence). Sin evidencia no hay PR.' };
    case 'verified':
      return r >= 2
        ? { paso: 'Con evidencia', siguiente: 'abrir el PR (sn-ship) y pedir el SELLO de entrega al líder (/sn-request). El PR no se une sin su aprobación.', sello: true }
        : { paso: 'Con evidencia', siguiente: 'abrir el PR (sn-ship).' };
    case 'in_review': return { paso: 'En revisión', siguiente: 'revisión en una sesión NUEVA (/code-review) y prueba humana; el líder aprueba y une el PR.', sello: true };
    case 'merged': return { paso: 'Unido', siguiente: 'cerrar: /sn archiva el plano y confirma la tarea cerrada en Altum.' };
    case 'done': return { paso: 'Terminado', siguiente: 'nada.' };
    default: return { paso: item.stage_label || item.stage, siguiente: '/sn-status' };
  }
}

// ¿Se puede abrir el PR de este ítem? Solo con el plano aprobado (sello 1) y la evidencia hecha.
export function puedeAbrirPr(item) {
  if (!item.change) return { puede: true };
  if (['plan_written', 'planning', 'ready', 'triaged'].includes(item.stage) || ['awaiting_validation', 'invalid_signature', 'validation_expired'].includes(item.flag)) {
    return { puede: false, motivo: `el plano de ${item.id} no tiene su sello 1 (${siguientePaso(item).paso}). Siguiente: ${siguientePaso(item).siguiente}` };
  }
  if (['plan_approved', 'building', 'built'].includes(item.stage)) {
    return { puede: false, motivo: `${item.id} todavía no tiene evidencia completa (${siguientePaso(item).paso}). Siguiente: ${siguientePaso(item).siguiente}` };
  }
  return { puede: true };
}
