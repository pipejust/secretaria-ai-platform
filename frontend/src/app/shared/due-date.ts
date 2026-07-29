/**
 * Criterio de fecha de vencimiento, espejo de `backend/date_utils.py`.
 *
 * `due_date` viaja como texto y la columna también lo es, así que el
 * extractor viejo llegó a guardar frases del LLM («No especificada»). Las
 * filas ya se limpiaron y la escritura está validada, pero el frontend no
 * puede asumirlo: sigue leyendo lo que le manda la API.
 *
 * Hay dos criterios a propósito, igual que en el backend:
 *
 * - `isRealDueDate` tolera un sufijo horario. Es para LEER: mejor rescatar
 *   la fecha que descartar la tarea entera.
 * - `isExactIsoDate` no tolera nada pegado. Es para ESCRIBIR: el backend
 *   responde 422 a cualquier otra cosa, y la hora se manda en `due_time`.
 *
 * Para convertir a `Date` usa `parseLocalDate` de `./dates`.
 */

import { parseLocalDate } from './dates';

const ISO_DATE_PREFIX = /^\d{4}-\d{2}-\d{2}/;

/** `true` si de `value` se puede sacar una fecha real. */
export function isRealDueDate(value: string | null | undefined): boolean {
    return toIsoDateOrNull(value) !== null;
}

/** `true` si `value` es exactamente `YYYY-MM-DD`, sin nada pegado. */
export function isExactIsoDate(value: string | null | undefined): boolean {
    if (!value) return false;
    const s = String(value).trim();
    return s.length === 10 && isRealDueDate(s);
}

/** La fecha sola (`YYYY-MM-DD`), o `null` si no hay ninguna que rescatar. */
export function toIsoDateOrNull(value: string | null | undefined): string | null {
    if (!value) return null;
    const head = String(value).trim().slice(0, 10);
    if (!ISO_DATE_PREFIX.test(head)) return null;
    // parseLocalDate descarta los días que no existen («2026-02-31»).
    return parseLocalDate(head) ? head : null;
}
