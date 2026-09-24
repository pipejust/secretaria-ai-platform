// El mensaje que la persona le copia y le pega al líder para pedirle una validación.
// Mientras no haya mensajería conectada, este texto ES el canal: tiene que bastarse solo,
// aunque el líder no haya visto nada del proyecto ni tenga el repositorio en su computador.

// Texto puro (sin red ni git): así se puede probar y no cambia de una vez a otra.
export function mensajeValidacion({
  proyecto = '', sello = 'plano', riesgo = '', pide = '', titulo = '', queValidar = '',
  rama = '', commit = '', pr = '', lider = null, clonar = '',
} = {}) {
  const para = lider?.name ? `Para: ${lider.name}${lider.email ? ` <${lider.email}>` : ''}\n` : '';
  const traer = clonar ? `  1. Si todavía no tienes el proyecto: abre Claude Code en una carpeta vacía y escribe:\n       /sn clóname el proyecto ${clonar}\n  2. Ya dentro del proyecto, escribe:\n       /sn-validate ${rama}\n` : `  /sn-validate ${rama}\n`;
  return `[Validación Softnexus] ${proyecto || '(proyecto)'} · sello de ${sello}${riesgo ? ` · riesgo ${riesgo}` : ''}\n`
    + para
    + `${pide || 'Alguien del equipo'} necesita tu validación: ${titulo || '(sin título)'}\n`
    + `${queValidar ? `Qué validar: ${queValidar}\n` : ''}`
    + `\nEn tu computador:\n${traer}`
    + `Eso trae la rama y todo el contexto: no necesitas nada de mi sesión ni que yo te explique nada.\n`
    + `Si quieres ver primero en qué va, escribe /sn-status.\n`
    + `\nRama: ${rama || '(sin rama)'}${commit ? ` · Commit: ${commit}` : ''}\n`
    + `${pr ? `PR: ${pr}\n` : ''}`;
}
