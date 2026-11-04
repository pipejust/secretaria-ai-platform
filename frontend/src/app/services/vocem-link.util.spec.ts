import { clasificarEnlace, enlaceCanonico, esEnlaceDePersona, idDeSala } from './vocem-link.util';

const ID = '!5fFLP-0eq0iIaqWaxagXPAwrSVM-veo-0REnCch_oIs';
const CANONICO = `https://call.vocem.softnexus.co/room/#?roomId=${encodeURIComponent(ID)}&viaServers=softnexus.co`;

describe('vocem-link.util', () => {
  describe('clasificarEnlace', () => {
    const casos: [string, ReturnType<typeof clasificarEnlace>][] = [
      [`https://matrix.to/#/${ID}?via=softnexus.co`, 'element'],
      [`https://matrix.to/#/${encodeURIComponent(ID)}?via=softnexus.co`, 'element'],
      [`https://call.vocem.softnexus.co/room/#?roomId=${encodeURIComponent(ID)}&viaServers=softnexus.co`, 'element'],
      [`https://call.vocem.softnexus.co/room/#/${ID}`, 'element'],
      [`https://call.vocem.softnexus.co/room/#/reunion-acme?roomId=${encodeURIComponent(ID)}`, 'element'],
      ['https://matrix.to/#/@felipe:softnexus.co', null],
      ['https://meet.google.com/abc-defg-hij', 'gmeet'],
      ['https://softnexus.zoom.us/j/123', 'zoom'],
      ['https://teams.microsoft.com/l/meetup-join/x', 'teams'],
      ['https://teams.live.com/meet/999', 'teams'],
      ['https://example.com/loquesea', null],
      ['no es una url', null],
      ['', null],
    ];

    for (const [enlace, esperado] of casos) {
      it(`${esperado ?? 'null'} ← ${JSON.stringify(enlace).slice(0, 80)}`, () => {
        expect(clasificarEnlace(enlace)).toBe(esperado);
      });
    }
  });

  describe('idDeSala', () => {
    it('extrae id urldecoded desde ?roomId=%21...', () => {
      expect(idDeSala(`https://call.vocem.softnexus.co/room/#?roomId=${encodeURIComponent(ID)}`)).toBe(ID);
    });

    it('extrae id urldecoded desde el fragmento matrix.to', () => {
      expect(idDeSala(`https://matrix.to/#/${encodeURIComponent(ID)}?via=softnexus.co`)).toBe(ID);
    });

    it('acepta el id crudo (! sin encodear) en el fragmento', () => {
      expect(idDeSala(`https://matrix.to/#/${ID}?via=softnexus.co`)).toBe(ID);
    });

    it('devuelve null para enlace de persona (@)', () => {
      expect(idDeSala('https://matrix.to/#/@felipe:softnexus.co')).toBeNull();
    });

    it('devuelve null para URLs inválidas', () => {
      expect(idDeSala('no es una url')).toBeNull();
      expect(idDeSala('')).toBeNull();
      expect(idDeSala(null)).toBeNull();
    });
  });

  describe('enlaceCanonico', () => {
    it('normaliza matrix.to al canónico', () => {
      expect(enlaceCanonico(`https://matrix.to/#/${ID}?via=softnexus.co`)).toBe(CANONICO);
    });

    it('normaliza call…/room/#/!id al canónico', () => {
      expect(enlaceCanonico(`https://call.vocem.softnexus.co/room/#/${ID}`)).toBe(CANONICO);
    });

    it('idempotente sobre el canónico', () => {
      expect(enlaceCanonico(CANONICO)).toBe(CANONICO);
    });

    it('conserva URLs no-Element sin cambios', () => {
      expect(enlaceCanonico('https://meet.google.com/abc-defg-hij')).toBe('https://meet.google.com/abc-defg-hij');
    });
  });

  describe('esEnlaceDePersona', () => {
    it('detecta matrix.to/#/@usuario', () => {
      expect(esEnlaceDePersona('https://matrix.to/#/@felipe:softnexus.co')).toBe(true);
    });

    it('matrix.to de sala no cuenta como persona', () => {
      expect(esEnlaceDePersona(`https://matrix.to/#/${ID}`)).toBe(false);
    });

    it('otra URL con @ en el path no es persona de matrix.to', () => {
      expect(esEnlaceDePersona('https://example.com/#/@felipe')).toBe(false);
    });
  });
});
