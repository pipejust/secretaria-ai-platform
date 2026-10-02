import {
  AfterViewChecked, ChangeDetectionStrategy, ChangeDetectorRef, Component, ElementRef, Input,
  OnChanges, OnDestroy, ViewChild, inject,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { TranslateModule } from '@ngx-translate/core';

/** Segmento del stream en vivo de Skribby (evento `ts` y `connected.transcripts`). */
export interface LiveSegment {
  transcript: string;
  start: number;
  end: number;
  speaker: number | null;
  speaker_name: string | null;
}

/** Varios segmentos seguidos de la misma voz se leen como un solo turno. */
export interface LiveTurn { speaker: number | null; name: string | null; start: number; text: string; }

export type LiveStatus = 'connecting' | 'live' | 'ended' | 'error';

const MAX_RETRIES = 5;
const RETRY_MS = 2000;
const ENDED = new Set(['finished', 'not_admitted', 'processing', 'transcribing']);

export const toTurns = (segments: readonly LiveSegment[]): LiveTurn[] =>
  segments.reduce<LiveTurn[]>((turns, s) => {
    const last = turns[turns.length - 1];
    const same = last && last.speaker === (s.speaker ?? null) && last.name === (s.speaker_name || null);
    return same
      ? [...turns.slice(0, -1), { ...last, text: `${last.text} ${s.transcript}`.trim() }]
      : [...turns, { speaker: s.speaker ?? null, name: s.speaker_name || null, start: s.start, text: s.transcript }];
  }, []);

const isSegment = (v: unknown): v is LiveSegment =>
  !!v && typeof (v as LiveSegment).transcript === 'string' && typeof (v as LiveSegment).start === 'number';

/** Transcripción en vivo de una reunión: se conecta al stream de solo lectura
 *  que entrega el bot (`live_url`) y pinta lo que se va diciendo. */
@Component({
  selector: 'app-live-transcript',
  standalone: true,
  imports: [CommonModule, TranslateModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <p class="lt-state" [attr.data-status]="status" role="status">
      <span class="lt-dot" aria-hidden="true"></span>{{ ('meeting_bot.live_' + status) | translate }}
    </p>
    <div class="lt-scroll" #scroller tabindex="0" [attr.aria-label]="'meeting_bot.live_title' | translate">
      <p class="lt-empty" *ngIf="!turns.length && status === 'live'">{{ 'meeting_bot.live_waiting' | translate }}</p>
      <div class="lt-turn" *ngFor="let turn of turns; trackBy: trackTurn">
        <span class="lt-time">{{ clock(turn.start) }}</span>
        <div>
          <strong>{{ turn.name || ('meeting_bot.live_speaker' | translate:{ n: (turn.speaker ?? 0) + 1 }) }}</strong>
          <p>{{ turn.text }}</p>
        </div>
      </div>
    </div>`,
  styles: [`
    :host { display: block; }
    .lt-state { display: flex; align-items: center; gap: 8px; margin: 0 0 10px; font-size: 12px; font-weight: 600; color: #50736b; }
    .lt-dot { width: 8px; height: 8px; border-radius: 50%; background: #9fb3ac; }
    .lt-state[data-status="live"] { color: #ad342d; }
    .lt-state[data-status="live"] .lt-dot { background: #dc3b30; animation: lt-pulse 1.4s ease-in-out infinite; }
    .lt-state[data-status="error"] { color: #a0342c; }
    .lt-scroll { max-height: 420px; overflow: auto; border: 1px solid #e1e9e4; border-radius: 12px; padding: 4px 14px; background: #fbfdfc; }
    .lt-scroll:focus-visible { outline: 2px solid #62a691; outline-offset: 2px; }
    .lt-turn { display: flex; gap: 14px; padding: 12px 0; border-bottom: 1px solid #edf1ee; }
    .lt-turn:last-child { border-bottom: 0; }
    .lt-time { min-width: 46px; font-size: 12px; font-variant-numeric: tabular-nums; color: #397763; padding-top: 2px; }
    .lt-turn strong { font-size: 12px; color: #17302c; }
    .lt-turn p { margin: 4px 0 0; font-size: 14px; line-height: 1.55; color: #17302c; }
    .lt-empty { margin: 14px 0; font-size: 13px; color: #677d75; }
    @keyframes lt-pulse { 50% { opacity: .35; } }
    @media (prefers-reduced-motion: reduce) { .lt-state[data-status="live"] .lt-dot { animation: none; } }
  `],
})
export class LiveTranscriptComponent implements OnChanges, OnDestroy, AfterViewChecked {
  @Input({ required: true }) url = '';
  @ViewChild('scroller') private scroller?: ElementRef<HTMLElement>;
  private readonly cd = inject(ChangeDetectorRef);
  private socket?: WebSocket;
  private retries = 0;
  private retryTimer?: ReturnType<typeof setTimeout>;
  private segments: LiveSegment[] = [];
  private stickToBottom = true;
  private pendingScroll = false;

  turns: LiveTurn[] = [];
  status: LiveStatus = 'connecting';

  ngOnChanges(): void { this.retries = 0; this.connect(); }

  ngOnDestroy(): void { this.close(); }

  ngAfterViewChecked(): void {
    const el = this.scroller?.nativeElement;
    if (this.pendingScroll && el) { el.scrollTop = el.scrollHeight; this.pendingScroll = false; }
  }

  trackTurn(index: number, turn: LiveTurn): string { return `${index}:${turn.start}`; }

  clock(seconds: number): string {
    const total = Math.max(0, Math.floor(seconds));
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
  }

  private close(): void {
    clearTimeout(this.retryTimer);
    const socket = this.socket;
    this.socket = undefined;
    if (socket) { socket.onclose = null; socket.onmessage = null; socket.onerror = null; socket.close(); }
  }

  private connect(): void {
    this.close();
    // Solo un WebSocket cifrado: la URL viene del servidor, pero no se abre otra cosa.
    if (!this.url.startsWith('wss://')) { this.setStatus('error'); return; }
    this.setStatus('connecting');
    const socket = new WebSocket(this.url);
    this.socket = socket;
    socket.onmessage = (event) => this.onMessage(event.data);
    socket.onclose = () => this.onClose();
  }

  private onClose(): void {
    if (this.status === 'ended') return;
    if (this.retries >= MAX_RETRIES) { this.setStatus('error'); return; }
    this.retries += 1;
    this.setStatus('connecting');
    this.retryTimer = setTimeout(() => this.connect(), RETRY_MS * this.retries);
  }

  private onMessage(raw: unknown): void {
    let message: { type?: string; data?: any };
    try { message = JSON.parse(String(raw)); } catch { return; }
    const data = message.data ?? {};
    if (message.type === 'connected') {
      this.retries = 0;
      this.setSegments(Array.isArray(data.transcripts) ? data.transcripts.filter(isSegment) : []);
      this.setStatus(ENDED.has(data.status) ? 'ended' : 'live');
    } else if (message.type === 'ts' && isSegment(data)) {
      this.setSegments([...this.segments, data]);
    } else if (message.type === 'status-update' && ENDED.has(data.new_status)) {
      this.setStatus('ended');
      this.close();
    }
  }

  private setSegments(segments: LiveSegment[]): void {
    const el = this.scroller?.nativeElement;
    this.stickToBottom = !el || el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    this.segments = segments;
    this.turns = toTurns(segments);
    this.pendingScroll = this.stickToBottom;
    this.cd.markForCheck();
  }

  private setStatus(status: LiveStatus): void { this.status = status; this.cd.markForCheck(); }
}
