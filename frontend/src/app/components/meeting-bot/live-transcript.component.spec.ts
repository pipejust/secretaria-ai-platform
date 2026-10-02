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
});
