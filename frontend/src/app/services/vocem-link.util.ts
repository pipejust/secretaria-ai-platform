/**
 * Detector de enlaces de reunión que acepta el formulario de "Reunión en vivo".
 * Clasifica una URL en una plataforma conocida y produce el enlace canónico
 * cuando se trata de una sala de Vocem/Element.
 *
 * Los tres formatos de Element/Vocem que se aceptan y se normalizan:
 *   matrix.to/#/!sala?via=softnexus.co            → element
 *   call.vocem.softnexus.co/room/#/!sala          → element
 *   call.vocem…/room/#?roomId=%21sala&viaServers= → element (canónico)
 *
 * Reglas duras:
 *  - `room_id` sin `:dominio`: en versión 12 los ids de sala no lo llevan.
 *  - `!` del room_id llega urlencoded (`%21`) o crudo; se urldecode siempre.
 *  - `matrix.to/#/@usuario` apunta a una persona, no a una sala: se rechaza.
 */

export type MeetingPlatform = 'gmeet' | 'teams' | 'zoom' | 'element';

/** Enlace canónico que Acten usa para una sala de Vocem/Element. */
export const VOCEM_CALL_BASE = 'https://call.vocem.softnexus.co';
export const VOCEM_VIA_SERVER = 'softnexus.co';

/** `!abc...` si la URL contiene un room id Matrix; null en cualquier otro caso. */
export function idDeSala(enlace: string | null | undefined): string | null {
  const texto = (enlace ?? '').trim();
  let url: URL;
  try { url = new URL(texto); } catch { return null; }

  // call.vocem…/room/#?roomId=%21abc&viaServers=softnexus.co
  const porParametro = texto.match(/[#?&]roomId=([^&]+)/);
  if (porParametro) {
    try {
      const id = decodeURIComponent(porParametro[1]);
      return id.startsWith('!') ? id : null;
    } catch { return null; }
  }

  // matrix.to/#/!abc?via=…   y   call.vocem…/room/#/!abc
  const fragmento = url.hash.startsWith('#/') ? url.hash.slice(2) : url.hash.slice(1);
  try {
    const primero = decodeURIComponent(fragmento.split('?')[0].split('/')[0]);
    return primero.startsWith('!') ? primero : null;
  } catch { return null; }
}

/** Devuelve la plataforma de la URL, o null si no es de ninguna conocida. */
export function clasificarEnlace(enlace: string | null | undefined): MeetingPlatform | null {
  const texto = (enlace ?? '').trim();
  let host: string;
  try { host = new URL(texto).hostname.toLowerCase(); } catch { return null; }

  if (host === 'meet.google.com') return 'gmeet';
  if (host.endsWith('teams.microsoft.com') || host.endsWith('teams.live.com')) return 'teams';
  if (host === 'zoom.us' || host.endsWith('.zoom.us')) return 'zoom';
  if (idDeSala(texto)) return 'element';
  return null;
}

/** Enlace canónico de Element Call cuando la URL contiene un room_id;
 *  para cualquier otra URL devuelve el texto tal cual (trimmed). */
export function enlaceCanonico(enlace: string): string {
  const id = idDeSala(enlace);
  if (!id) return (enlace ?? '').trim();
  return `${VOCEM_CALL_BASE}/room/#?roomId=${encodeURIComponent(id)}&viaServers=${VOCEM_VIA_SERVER}`;
}

/** `matrix.to/#/@usuario:servidor` → true. Útil para distinguir "persona"
 *  de "sala" y mostrar el mensaje correcto. */
export function esEnlaceDePersona(enlace: string | null | undefined): boolean {
  try {
    const url = new URL((enlace ?? '').trim());
    if (url.hostname.toLowerCase() !== 'matrix.to') return false;
    const fragmento = url.hash.startsWith('#/') ? url.hash.slice(2) : url.hash.slice(1);
    return decodeURIComponent(fragmento).startsWith('@');
  } catch { return false; }
}
