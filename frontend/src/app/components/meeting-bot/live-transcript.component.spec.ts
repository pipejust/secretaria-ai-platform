import { toTurns } from './live-transcript.component';

describe('live transcript turns', () => {
  it('joins consecutive segments of the same voice and keeps voices apart', () => {
    const turns = toTurns([
      { transcript: 'Hola a todos.', start: 1, end: 2, speaker: 0, speaker_name: 'Ana' },
      { transcript: 'Empezamos.', start: 2, end: 3, speaker: 0, speaker_name: 'Ana' },
      { transcript: 'De acuerdo.', start: 4, end: 5, speaker: 1, speaker_name: null },
      { transcript: 'Sigo yo.', start: 6, end: 7, speaker: 0, speaker_name: 'Ana' },
    ]);
    expect(turns).toEqual([
      { speaker: 0, name: 'Ana', start: 1, text: 'Hola a todos. Empezamos.' },
      { speaker: 1, name: null, start: 4, text: 'De acuerdo.' },
      { speaker: 0, name: 'Ana', start: 6, text: 'Sigo yo.' },
    ]);
  });

  it('names a voice retroactively once the provider matches it to a participant', () => {
    const turns = toTurns([
      { transcript: 'Listo, caballero.', start: 6, end: 7, speaker: 1, speaker_name: 'Speaker 1' },
      { transcript: 'Buenas.', start: 8, end: 9, speaker: 2, speaker_name: 'Speaker 2' },
      { transcript: 'Empecemos.', start: 101, end: 102, speaker: 1, speaker_name: 'William Aragón' },
      { transcript: 'Sin emparejar.', start: 110, end: 111, speaker: 3, speaker_name: 'Speaker 3' },
    ]);
    expect(turns.map((t) => [t.speaker, t.name])).toEqual([
      [1, 'William Aragón'], [2, null], [1, 'William Aragón'], [3, null],
    ]);
  });
});
