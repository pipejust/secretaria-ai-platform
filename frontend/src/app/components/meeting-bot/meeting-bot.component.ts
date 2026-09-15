import { CommonModule } from '@angular/common';
import { Component, HostListener, OnDestroy, OnInit, inject, ChangeDetectorRef } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { AuthService } from '../../services/auth.service';
import {
  BotCapabilities, BotMeeting, BotSpeaker, CaptureKind, EmailReceipt, MeetingBotService, MeetingResult,
  StartCapturePayload, TranscriptSegment,
} from '../../services/meeting-bot.service';
import { BrowserRecording, RecordingMeta } from './browser-recording';

/** Forma mínima de un error HTTP (HttpErrorResponse) o de un Error normal
 *  para extraer un mensaje sin asumir la clase concreta. */
interface ErrorLike {
  error?: { detail?: unknown } | null;
  status?: number;
  message?: string;
}
function isErrorLike(value: unknown): value is ErrorLike {
  return typeof value === 'object' && value !== null;
}

/** Identidad de la última solicitud de captura guardada en sessionStorage. */
interface SavedCaptureIdentity {
  signature?: string;
  external_id?: string;
}

@Component({
  selector: 'app-meeting-bot', standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, TranslateModule],
  templateUrl: './meeting-bot.component.html', styleUrl: './meeting-bot.component.css',
})
export class MeetingBotComponent implements OnInit, OnDestroy {
  private api = inject(MeetingBotService);
  private auth = inject(AuthService);
  private cd = inject(ChangeDetectorRef);
  private translate = inject(TranslateService);
  private timer?: ReturnType<typeof setInterval>;
  private refreshing = false;
  private destroyed = false;
  // Getters, no campos: el perfil llega por /auth/me de forma asíncrona y
  // al recargar la página aún no está cuando el componente se construye.
  // Como campo, un administrador quedaba sin panel de configuración y el
  // dueño de las grabaciones locales era «undefined:undefined».
  private get owner(): string {
    const u = this.auth.currentUserValue;
    return `${u?.tenant?.id ?? u?.tenant_id}:${u?.id}`;
  }
  get isAdmin(): boolean {
    const rol = this.auth.currentUserValue?.role;
    return rol?.name === 'admin' || rol === 'admin';
  }
  /** La grabación de vídeo la decide el plan: `/auth/me` trae
   *  `tenant.entitlements.features`; sin `meetings.video` la casilla no se enseña. */
  get canRecordVideo(): boolean {
    const features: unknown = this.auth.currentUserValue?.tenant?.entitlements?.features;
    return Array.isArray(features) && features.includes('meetings.video');
  }
  caps: BotCapabilities | null = null;
  meetings: BotMeeting[] = [];
  receipts: EmailReceipt[] = [];
  pending: RecordingMeta[] = [];
  result: MeetingResult | null = null;
  selected = '';
  audioUrl = '';
  audioTime = 0;
  title = '';
  meetingUrl = '';
  vocabulary = '';
  language = 'es';
  authorized = false;
  video = false;
  systemAudio = true;
  busy = false;
  error = '';
  notice = '';
  search = '';
  speakerFilter = '';
  names: Record<string, string> = {};
  serviceUrl = '';
  botName = 'Asistente Acten';
  serviceKey = '';
  policySenders = '';
  policyTimezone = 'America/Bogota';
  policyAuthorized = false;
  elapsed = '00:00';
  recorder = new BrowserRecording(
    (id, seq, blob) => this.api.uploadChunk(id, seq, blob),
    meta => this.api.finishRecording(meta.id, { chunk_count: meta.count, interrupted: meta.interrupted }),
    () => { if (!this.destroyed) { void this.loadPending(); this.cd.markForCheck(); } },
    message => { this.error = message; this.cd.markForCheck(); },
  );
  async ngOnInit(): Promise<void> {
    await this.loadPending();
    await this.refresh();
    if (this.isAdmin) {
      try {
        const config = await this.api.getConfig();
        this.serviceUrl = config.service_url;
        this.botName = config.bot_name || this.botName;
      } catch (e) {
        this.error = this.detail(e) ?? this.translate.instant('meeting_bot.error_load_config');
      }
      await this.loadMailPolicy();
    }
    this.timer = setInterval(() => {
      if (this.recorder.active && this.recorder.meta)
        this.elapsed = this.time((Date.now() - this.recorder.meta.createdAt) / 1000);
      void this.refresh();
    }, 5000);
  }
  ngOnDestroy(): void { this.destroyed = true; clearInterval(this.timer); this.recorder.release(); }
  @HostListener('window:beforeunload', ['$event']) beforeUnload(event: BeforeUnloadEvent): void {
    if (this.recorder.active || this.busy || this.pending.length) { event.preventDefault(); event.returnValue = ''; }
  }
  canLeave(): boolean {
    if (this.recorder.active || this.busy) { this.error = this.translate.instant('meeting_bot.error_leave_recording'); return false; }
    return true;
  }
  async refresh(): Promise<void> {
    if (this.refreshing) return;
    this.refreshing = true;
    try {
      this.caps = await this.api.getCapabilities();
      this.meetings = await this.api.listMeetings();
    } catch (error) { if (!this.meetings.length) this.error = this.message(error); }
    finally { this.refreshing = false; this.cd.markForCheck(); }
  }
  async loadPending(): Promise<void> {
    try { this.pending = await this.recorder.journal.list(this.owner); }
    catch { this.error = this.translate.instant('meeting_bot.error_local_storage'); }
    this.cd.markForCheck();
  }
  private payload(): StartCapturePayload {
    return { external_id: 'web:' + crypto.randomUUID(), title: this.title.trim() || this.translate.instant('meeting_bot.default_title'),
      language: this.language, vocabulary: this.vocabulary.split(',').map(s => s.trim()).filter(Boolean),
      recording_authorized: this.authorized, max_duration_minutes: 480 };
  }
  private async startCapture(kind: CaptureKind, payload: StartCapturePayload): Promise<BotMeeting> {
    const key = `acten-bot-start:${this.owner}:${kind}`;
    const { external_id, ...content } = payload;
    const signature = JSON.stringify(content);
    const previous = sessionStorage.getItem(key);
    let saved: SavedCaptureIdentity | null;
    try { saved = previous ? JSON.parse(previous) : null; } catch { saved = null; }
    if (saved?.signature === signature && saved.external_id) payload.external_id = saved.external_id;
    sessionStorage.setItem(key, JSON.stringify({ signature, external_id: payload.external_id }));
    const result = await this.api.startCapture(kind, payload);
    sessionStorage.removeItem(key);
    return result;
  }
  private async action(work: () => Promise<void>): Promise<void> {
    this.busy = true; this.error = ''; this.notice = '';
    try { await work(); } catch (error) { this.error = this.message(error); }
    finally { this.busy = false; await this.loadPending(); this.cd.markForCheck(); }
  }
  async join(): Promise<void> {
    if (!this.authorized) { this.error = this.translate.instant('meeting_bot.error_confirm_authorization'); return; }
    await this.action(async () => {
      await this.startCapture('meeting', { ...this.payload(), meeting_url: this.meetingUrl, video: this.canRecordVideo && this.video });
      this.notice = this.translate.instant('meeting_bot.notice_join_requested');
      await this.refresh();
    });
  }
  async record(): Promise<void> {
    if (!this.authorized) { this.error = this.translate.instant('meeting_bot.error_confirm_authorization'); return; }
    await this.action(async () => {
      try {
        const mime = await this.recorder.prepare(this.systemAudio);
        const payload = { ...this.payload(), mime_type: mime };
        const remote = await this.startCapture('browser', payload);
        await this.recorder.begin({ id: remote.id, owner: this.owner, title: payload.title,
          mime, count: 0, bytes: 0, createdAt: Date.now(), interrupted: false });
        this.notice = this.translate.instant(this.systemAudio ? 'meeting_bot.notice_recording_system' : 'meeting_bot.notice_recording_mic');
      } catch (error) { this.recorder.release(); throw error; }
    });
  }
  async stopRecording(): Promise<void> {
    await this.action(async () => { await this.recorder.stop(); this.notice = this.translate.instant('meeting_bot.notice_audio_saved'); await this.refresh(); });
  }
  async recover(meta: RecordingMeta): Promise<void> {
    await this.action(async () => { await this.recorder.recover(meta); this.notice = this.translate.instant('meeting_bot.notice_recovered'); await this.refresh(); });
  }
  async discardEmpty(meta: RecordingMeta): Promise<void> {
    if (meta.count || this.recorder.active) return;
    await this.action(async () => {
      // No audio exists locally. An unreachable empty server job expires on its own.
      try {
        await this.api.deleteRecording(meta.id);
      } catch (e) {
        // El registro local se descarta igual —no hay audio—, pero se dice
        // que el servidor no confirmó, por si hay que limpiarlo a mano.
        this.error = this.detail(e) ?? this.translate.instant('meeting_bot.error_discard_unconfirmed');
      }
      await this.recorder.journal.completed(meta.id);
      this.notice = this.translate.instant('meeting_bot.notice_discarded');
    });
  }
  async stopBot(id: string): Promise<void> {
    await this.action(async () => { await this.api.stopMeeting(id); await this.refresh(); });
  }
  async open(item: BotMeeting): Promise<void> {
    await this.action(async () => {
      const result = await this.api.getResult(item.id);
      this.result = result;
      this.selected = item.id; this.audioUrl = ''; this.audioTime = 0;
      this.search = ''; this.speakerFilter = ''; this.names = {};
      for (const speaker of result.speakers ?? []) this.names[speaker.speaker_id] = speaker.speaker_name || '';
      if (item.source === 'browser') {
        const grant = await this.api.playbackGrant(item.id);
        this.audioUrl = this.api.absoluteUrl(grant.path);
      } else if (result.recording?.url?.startsWith('https://')) this.audioUrl = result.recording.url;
    });
  }
  async label(speaker: BotSpeaker): Promise<void> {
    await this.action(async () => {
      const answer = await this.api.labelSpeaker(this.selected, speaker.speaker_id, { display_name: this.names[speaker.speaker_id] });
      this.result = await this.api.getResult(this.selected);
      this.notice = this.translate.instant(answer.transcript_preserved ? 'meeting_bot.notice_name_confirmed_preserved' : 'meeting_bot.notice_name_confirmed');
    });
  }
  async saveConfig(): Promise<void> {
    await this.action(async () => {
      await this.api.saveConfig({ service_url: this.serviceUrl, client_key: this.serviceKey, bot_name: this.botName.trim() || 'Asistente Acten' });
      this.serviceKey = ''; this.notice = this.translate.instant('meeting_bot.notice_config_saved'); await this.refresh();
    });
  }
  private async loadMailPolicy(): Promise<void> {
    // Sin conexión al bot no hay política que leer; el error ya se mostró arriba.
    if (!this.serviceUrl) return;
    try {
      const policy = (await this.api.getMailPolicy()).policy;
      if (policy) {
        this.policySenders = (policy.extra_senders ?? []).join('\n');
        this.policyTimezone = policy.timezone || 'America/Bogota';
        this.policyAuthorized = !!policy.recording_authorized;
      }
    } catch (e) {
      this.error = this.detail(e) ?? this.translate.instant('meeting_bot.error_load_mail_policy');
    }
    this.cd.markForCheck();
  }
  async saveMailPolicy(): Promise<void> {
    await this.action(async () => {
      const senders = this.policySenders.split(/[\n,;]+/).map(s => s.trim()).filter(Boolean);
      await this.api.saveMailPolicy({
        allowed_senders: senders, recording_authorized: this.policyAuthorized,
        timezone: this.policyTimezone.trim() || 'America/Bogota',
      });
      this.notice = this.translate.instant(this.policyAuthorized
        ? 'meeting_bot.notice_mail_policy_enabled'
        : 'meeting_bot.notice_senders_saved');
      await this.refresh();
    });
  }
  async loadEmails(): Promise<void> {
    await this.action(async () => { this.receipts = await this.api.listEmailReceipts(); });
  }
  seek(audio: HTMLAudioElement, seconds: number): void {
    if (!this.audioUrl) return;
    audio.currentTime = Math.max(0, seconds);
    void audio.play().catch(() => { this.error = this.translate.instant('meeting_bot.error_play'); });
  }
  audioFailed(): void { this.error = this.translate.instant('meeting_bot.audio_error'); }
  get segments(): TranscriptSegment[] {
    return (this.result?.transcript ?? []).filter(s =>
      (!this.speakerFilter || s.speaker_id === this.speakerFilter) &&
      (!this.search || s.text.toLocaleLowerCase().includes(this.search.toLocaleLowerCase())));
  }
  time(seconds: number): string {
    const value = Math.max(0, Math.floor(seconds));
    return (value >= 3600 ? Math.floor(value / 3600) + ':' : '') +
      String(Math.floor(value / 60) % 60).padStart(2, '0') + ':' + String(value % 60).padStart(2, '0');
  }
  state(value: string): string {
    const known = ['scheduled', 'queued', 'dispatching', 'recording', 'capturing', 'uploading', 'transcribing',
      'audio_assembling', 'audio_ready', 'audio_uploading', 'audio_submitting', 'audio_transcribing', 'analyzing',
      'delivering', 'completed', 'cancelled', 'failed', 'needs_attention', 'dispatch_unknown', 'audio_dispatch_unknown'];
    return known.includes(value) ? this.translate.instant('meeting_bot.state_' + value) : value;
  }
  /** `detail` de un error HTTP del backend, si es un texto; si no, nada. */
  private detail(error: unknown): string | undefined {
    const detail = isErrorLike(error) ? error.error?.detail : undefined;
    return typeof detail === 'string' ? detail : undefined;
  }
  message(error: unknown): string {
    const info: ErrorLike = isErrorLike(error) ? error : {};
    const detail = info.error?.detail;
    return typeof detail === 'string' ? detail : info.status ? this.translate.instant('meeting_bot.error_operation_status', { status: info.status }) : info.message || this.translate.instant('meeting_bot.error_operation');
  }
}
