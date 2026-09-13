import { IDBFactory, IDBKeyRange } from 'fake-indexeddb';
import { vi } from 'vitest';
import { BrowserRecording, RecordingJournal, RecordingMeta } from './browser-recording';

const meta = (): RecordingMeta => ({ id: crypto.randomUUID(), owner: 'tenant-1:user-1',
  title: 'Prueba', mime: 'audio/webm', count: 0, bytes: 0, createdAt: Date.now(), interrupted: false });

describe('Durable browser recording', () => {
  beforeEach(() => {
    vi.stubGlobal('indexedDB', new IDBFactory());
    vi.stubGlobal('IDBKeyRange', IDBKeyRange);
  });
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  it('recovers ordered chunks and isolates the local journal by owner', async () => {
    const journal = new RecordingJournal(), value = meta();
    await journal.save(value);
    await journal.append(value, new Blob(['abcdefghijkl']), 1);
    expect(value.count).toBe(12);
    const reopened = new RecordingJournal();
    expect((await reopened.list(value.owner))[0].count).toBe(12);
    expect(await reopened.list('another-tenant')).toEqual([]);
    for (let sequence = 0; sequence < 12; sequence++) {
      const chunk = (await reopened.next(value.id))!;
      expect(chunk.sequence).toBe(sequence);
      await reopened.acknowledged(chunk.key);
    }
    await reopened.completed(value.id);
    expect(await journal.list(value.owner)).toEqual([]);
  });

  it('keeps unacknowledged audio after a network failure and finalizes once on recovery', async () => {
    let offline = true;
    const uploaded: number[] = [];
    const finish = vi.fn(async () => {});
    const recording = new BrowserRecording(async (_id, seq) => {
      if (offline) throw new Error('offline');
      uploaded.push(seq);
    }, finish, () => {}, () => {});
    const value = meta();
    await recording.journal.save(value);
    await recording.journal.append(value, new Blob(['abcdef']), 2);
    await expect(recording.recover(value)).rejects.toThrow('offline');
    expect(finish).not.toHaveBeenCalled();
    expect((await recording.journal.next(value.id))?.sequence).toBe(0);
    offline = false;
    await recording.recover(value);
    expect(uploaded).toEqual([0, 1, 2]);
    expect(finish).toHaveBeenCalledWith(expect.objectContaining({ count: 3, interrupted: true }));
    expect(await recording.journal.list(value.owner)).toEqual([]);
  });

  it('refuses to mark a journal completed while audio is still pending', async () => {
    const journal = new RecordingJournal(), value = meta();
    await journal.save(value);
    await journal.append(value, new Blob(['audio']));
    await expect(journal.completed(value.id)).rejects.toThrow('fragmentos');
  });

  it('requires shared audio and releases tracks when screen sharing has no audio', async () => {
    const stop = vi.fn();
    vi.stubGlobal('isSecureContext', true);
    vi.stubGlobal('MediaRecorder', {});
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {
      getDisplayMedia: vi.fn(async () => ({ getTracks: () => [{ stop }], getAudioTracks: () => [] })),
    } });
    const recording = new BrowserRecording(async () => {}, async () => {}, () => {}, () => {});
    await expect(recording.prepare(true)).rejects.toThrow('No se compartió audio');
    expect(stop).toHaveBeenCalledOnce();
  });

  it('waits for the final MediaRecorder blob before closing the server recording', async () => {
    class FakeRecorder extends EventTarget {
      state = 'inactive';
      ondataavailable?: (event: { data: Blob }) => void;
      onerror?: () => void;
      start() { this.state = 'recording'; }
      stop() {
        this.state = 'inactive';
        queueMicrotask(() => {
          this.ondataavailable?.({ data: new Blob(['last audio']) });
          this.dispatchEvent(new Event('stop'));
        });
      }
    }
    const finalCounts: number[] = [];
    const recorder = new BrowserRecording(async () => {}, async value => { finalCounts.push(value.count); }, () => {}, () => {});
    // Supply a controlled MediaRecorder to exercise its asynchronous stop contract.
    (recorder as any).recorder = new FakeRecorder();
    await recorder.begin(meta());
    await Promise.all([recorder.stop(), recorder.stop()]);
    expect(finalCounts).toEqual([1]);
    expect(recorder.active).toBe(false);
  });
});
