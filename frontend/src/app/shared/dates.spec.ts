import { parseLocalDate } from './dates';

describe('parseLocalDate', () => {
    it('devuelve el día que dice la cadena, no el anterior', () => {
        // `new Date('2026-07-28')` da el 27 en cualquier zona UTC-, que es el
        // bug que esta función existe para evitar: las tareas se pintaban un
        // día antes en todo el calendario y en la lista de pendientes.
        const d = parseLocalDate('2026-07-28')!;
        expect(d.getFullYear()).toBe(2026);
        expect(d.getMonth()).toBe(6);
        expect(d.getDate()).toBe(28);
    });

    it('deja la fecha a medianoche local', () => {
        const d = parseLocalDate('2026-07-28')!;
        expect(d.getHours()).toBe(0);
        expect(d.getMinutes()).toBe(0);
    });

    it('rechaza días que no existen en vez de desbordarlos al mes siguiente', () => {
        expect(parseLocalDate('2026-02-31')).toBeNull();
        expect(parseLocalDate('2026-13-01')).toBeNull();
    });

    it('respeta los años de menos de cuatro cifras', () => {
        // `new Date(99, 0, 1)` daría 1999.
        expect(parseLocalDate('0099-01-01')!.getFullYear()).toBe(99);
    });

    it('devuelve null cuando no hay fecha', () => {
        expect(parseLocalDate(null)).toBeNull();
        expect(parseLocalDate(undefined)).toBeNull();
        expect(parseLocalDate('')).toBeNull();
        expect(parseLocalDate('   ')).toBeNull();
        expect(parseLocalDate('No especificada')).toBeNull();
    });

    it('con hora incluida sí describe un instante y se convierte a local', () => {
        const d = parseLocalDate('2026-07-28T15:30:00Z')!;
        expect(d.getTime()).toBe(Date.UTC(2026, 6, 28, 15, 30));
    });
});
