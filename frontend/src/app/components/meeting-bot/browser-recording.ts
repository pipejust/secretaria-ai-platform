/** The server receives a single MediaRecorder stream in ordered, durable chunks. */
export interface RecordingMeta {
  id: string; owner: string; title: string; mime: string; count: number; bytes: number;
  createdAt: number; interrupted: boolean;
}
export interface RecordingChunk { key: string; recording: string; sequence: number; data: Blob; }

export class RecordingJournal {
  private database?: Promise<IDBDatabase>;
  private db(): Promise<IDBDatabase> {
    return this.database ??= new Promise((resolve, reject) => {
      const open = indexedDB.open('acten-audio-journal', 1);
      open.onupgradeneeded = () => {
        open.result.createObjectStore('recordings', { keyPath: 'id' });
        open.result.createObjectStore('chunks', { keyPath: 'key' }).createIndex('recording', 'recording');
      };
      open.onsuccess = () => resolve(open.result);
      open.onerror = () => reject(open.error);
    });
  }
  private async write(stores: string[], action: (tx: IDBTransaction) => void): Promise<void> {
    const db = await this.db();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(stores, 'readwrite');
      tx.oncomplete = () => resolve();
      tx.onabort = () => reject(tx.error ?? new Error('No se pudo conservar el audio local'));
      tx.onerror = () => reject(tx.error);
      try { action(tx); } catch (error) { tx.abort(); reject(error); }
    });
  }
  async save(meta: RecordingMeta): Promise<void> {
    await this.write(['recordings'], tx => tx.objectStore('recordings').put(meta));
  }
  async append(meta: RecordingMeta, blob: Blob, maximum = 1024 * 1024): Promise<void> {
    const pieces: RecordingChunk[] = [];
    const next = { ...meta };
    for (let offset = 0; offset < blob.size; offset += maximum) {
      const sequence = next.count++;
      const data = blob.slice(offset, offset + maximum);
      next.bytes += data.size;
      pieces.push({ key: `${meta.id}:${String(sequence).padStart(8, '0')}`, recording: meta.id, sequence, data });
    }
    await this.write(['recordings', 'chunks'], tx => {
      pieces.forEach(piece => tx.objectStore('chunks').add(piece));
      tx.objectStore('recordings').put(next);
    });
    Object.assign(meta, next);
  }
  async list(owner: string): Promise<RecordingMeta[]> {
    const db = await this.db();
    return new Promise((resolve, reject) => {
      const request = db.transaction('recordings').objectStore('recordings').getAll();
      request.onsuccess = () => resolve(request.result.filter((r: RecordingMeta) => r.owner === owner));
      request.onerror = () => reject(request.error);
    });
  }
  async next(id: string): Promise<RecordingChunk | undefined> {
    const db = await this.db();
    // Cursor reads one blob at a time, even after a long offline session.
    return new Promise((resolve, reject) => {
      const request = db.transaction('chunks').objectStore('chunks')
        .index('recording').openCursor(IDBKeyRange.only(id));
      request.onsuccess = () => {
        const cursor = request.result;
        resolve(cursor?.value);
      };
      request.onerror = () => reject(request.error);
    });
  }
  async acknowledged(key: string): Promise<void> {
    await this.write(['chunks'], tx => tx.objectStore('chunks').delete(key));
  }
  async completed(id: string): Promise<void> {
    if (await this.next(id)) throw new Error('Quedan fragmentos por enviar');
    await this.write(['recordings'], tx => tx.objectStore('recordings').delete(id));
  }
}

export class BrowserRecording {
  readonly journal = new RecordingJournal();
  active = false;
  meta?: RecordingMeta;
  private recorder?: MediaRecorder;
  private tracks: MediaStreamTrack[] = [];
  private context?: AudioContext;
  private persistence: Promise<void> = Promise.resolve();
  private flushing?: Promise<void>;
  private stopping?: Promise<void>;
  private timeout?: ReturnType<typeof setTimeout>;
  private persistenceFailed = false;

  constructor(
    private upload: (id: string, sequence: number, data: Blob) => Promise<unknown>,
    private finish: (meta: RecordingMeta) => Promise<unknown>,
    private changed: () => void,
    private error: (message: string) => void,
  ) {}

  async prepare(systemAudio: boolean): Promise<string> {
    if (!window.isSecureContext || !navigator.mediaDevices || !window.MediaRecorder)
      throw new Error('La grabación requiere HTTPS y un navegador compatible');
    try {
      // getDisplayMedia must run directly from the click, before any HTTP request.
      let display: MediaStream | undefined;
      if (systemAudio) {
        display = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
        this.tracks.push(...display.getTracks());
        if (!display.getAudioTracks().length)
          throw new Error('No se compartió audio. Selecciona una pestaña con «Compartir audio», o usa solo micrófono.');
      }
      const mic = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true }, video: false });
      this.tracks.push(...mic.getTracks());
      this.context = new AudioContext();
      await this.context.resume();
      const destination = this.context.createMediaStreamDestination();
      this.context.createMediaStreamSource(mic).connect(destination);
      if (display) this.context.createMediaStreamSource(new MediaStream(display.getAudioTracks())).connect(destination);
      const mime = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4']
        .find(type => MediaRecorder.isTypeSupported(type));
      if (!mime) throw new Error('Este navegador no ofrece un formato de audio compatible');
      this.recorder = new MediaRecorder(destination.stream, { mimeType: mime, audioBitsPerSecond: 64000 });
      for (const track of this.tracks) track.addEventListener('ended', () => {
        if (this.active) void this.stop(true).catch(() => {});
      });
      return mime.split(';')[0];
    } catch (error) { this.release(); throw error; }
  }

  async begin(meta: RecordingMeta): Promise<void> {
    if (!this.recorder || this.tracks.some(track => track.readyState === 'ended'))
      throw new Error('El permiso de audio terminó antes de comenzar');
    await this.journal.save(meta);
    this.meta = meta;
    this.persistenceFailed = false;
    this.stopping = undefined;
    this.persistence = Promise.resolve();
    this.recorder.ondataavailable = event => {
      if (!event.data.size) return;
      this.persistence = this.persistence.then(async () => {
        await this.journal.append(meta, event.data);
        this.changed();
        void this.flush(meta).catch(() => this.error('Sin conexión de subida. El audio pendiente se conserva en este navegador; usa «Recuperar y enviar».'));
        if (meta.bytes > 480 * 1024 * 1024 && this.active) void this.stop(true).catch(() => {});
      }).catch(() => {
        this.persistenceFailed = true;
        this.error('Se agotó o falló el almacenamiento local. Se detuvo la captura; NO se enviará como una grabación completa.');
        if (this.active) void this.stop(true).catch(() => {});
      });
    };
    this.recorder.onerror = () => { void this.stop(true).catch(() => {}); };
    this.active = true;
    this.recorder.start(5000);
    this.timeout = setTimeout(() => { void this.stop().catch(() => {}); }, 480 * 60 * 1000);
    this.changed();
  }

  async flush(meta: RecordingMeta): Promise<void> {
    if (this.flushing) { await this.flushing; return this.flush(meta); }
    this.flushing = (async () => {
      let chunk: RecordingChunk | undefined;
      while ((chunk = await this.journal.next(meta.id))) {
        await this.upload(meta.id, chunk.sequence, chunk.data);
        await this.journal.acknowledged(chunk.key);
      }
    })();
    try { await this.flushing; } finally { this.flushing = undefined; }
  }

  stop(interrupted = false): Promise<void> {
    if (this.stopping) return this.stopping;
    this.stopping = this.finishCapture(interrupted);
    return this.stopping;
  }
  private async finishCapture(interrupted: boolean): Promise<void> {
    const recorder = this.recorder, meta = this.meta;
    if (!recorder || !meta) return;
    this.active = false;
    clearTimeout(this.timeout);
    meta.interrupted ||= interrupted;
    try {
      if (recorder.state !== 'inactive') await new Promise<void>(resolve => {
        recorder.addEventListener('stop', () => resolve(), { once: true });
        recorder.stop();
      });
      await this.persistence;
      meta.interrupted ||= this.persistenceFailed;
      await this.journal.save(meta);
      await this.flush(meta);
      if (!meta.count) throw new Error('No se llegó a capturar audio');
      await this.finish(meta);
      await this.journal.completed(meta.id);
      this.meta = undefined;
    } finally { this.release(); this.changed(); }
  }
  async recover(meta: RecordingMeta): Promise<void> {
    if (this.active) throw new Error('Termina la grabación actual antes de recuperar otra');
    if (!meta.count) throw new Error('Esta captura se interrumpió antes del primer fragmento de audio');
    meta.interrupted = true;
    await this.journal.save(meta);
    await this.flush(meta);
    await this.finish(meta);
    await this.journal.completed(meta.id);
    if (this.meta?.id === meta.id) this.meta = undefined;
    this.changed();
  }
  release(): void {
    this.tracks.forEach(track => track.stop());
    this.tracks = [];
    if (this.context) void this.context.close();
    this.context = undefined;
  }
}
