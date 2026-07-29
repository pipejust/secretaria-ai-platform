/**
 * Parseo de fechas que no se desplaza un día.
 *
 * `new Date('2026-07-28')` NO devuelve el 28 a medianoche local: la spec de
 * JS obliga a interpretar un `YYYY-MM-DD` pelado como UTC. En Colombia
 * (UTC-5) eso es el 27 a las 19:00, así que `getDate()` devuelve 27 y la
 * tarea se pinta un día antes. Con hora incluida no pasa: ahí la cadena sí
 * describe un instante y convertirlo a local es lo correcto.
 *
 * `parseLocalDate` distingue los dos casos. Úsalo en vez de `new Date(...)`
 * siempre que el valor pueda venir de un campo de fecha sin hora
 * (`due_date`, fechas de sesión).
 */

const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

/** `Date` en hora local, o `null` si no hay fecha que sacar. */
export function parseLocalDate(value: string | null | undefined): Date | null {
    if (value == null) return null;
    const s = String(value).trim();
    if (!s) return null;

    if (DATE_ONLY.test(s)) {
        const [y, m, day] = s.split('-').map(Number);
        const d = new Date(y, m - 1, day);
        // `new Date(y, ...)` mapea los años 0-99 a 1900+y.
        d.setFullYear(y);
        // Descarta días que no existen («2026-02-31»), que Date desbordaría
        // al mes siguiente en silencio.
        const existe = d.getFullYear() === y && d.getMonth() === m - 1 && d.getDate() === day;
        return existe ? d : null;
    }

    const d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
}
