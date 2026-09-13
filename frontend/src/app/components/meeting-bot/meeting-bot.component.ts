import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { Component, HostListener, OnDestroy, OnInit, inject, ChangeDetectorRef } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../environments/environment';
import { AuthService } from '../../services/auth.service';
import { BrowserRecording, RecordingMeta } from './browser-recording';

@Component({
  selector: 'app-meeting-bot', standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, TranslateModule],
  templateUrl: './meeting-bot.component.html', styleUrl: './meeting-bot.component.css',
})
export class MeetingBotComponent implements OnInit, OnDestroy {
  private http = inject(HttpClient);
  private auth = inject(AuthService);
  private cd = inject(ChangeDetectorRef);
  private translate = inject(TranslateService);
  private base = environment.apiUrl + '/api/owned-bot';
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
  caps: any = {};
  meetings: any[] = [];
  receipts: any[] = [];
  pending: RecordingMeta[] = [];
  result: any;
  selected = '';
  audioUrl = '';
  audioTime = 0;
  title = '';
  meetingUrl = '';
  vocabulary = '';
  language = 'es';
  authorized = false;
  systemAudio = true;
  busy = false;
  error = '';
  notice = '';
  search = '';
  speakerFilter = '';
  names: Record<string, string> = {};
  serviceUrl = '';
  serviceKey = '';
  policySenders = '';
  policyTimezone = 'America/Bogota';
  policyAuthorized = false;
  elapsed = '00:00';
  recorder = new BrowserRecording(
    (id, seq, blob) => firstValueFrom(this.http.put(`${this.base}/recordings/${id}/chunks/${seq}`, blob)),
    meta => firstValueFrom(this.http.post(`${this.base}/recordings/${meta.id}/finish`, {
      chunk_count: meta.count, interrupted: meta.interrupted,
    })),
    () => { if (!this.destroyed) { void this.loadPending(); this.cd.markForCheck(); } },
    message => { this.error = message; this.cd.markForCheck(); },
  );
  async ngOnInit(): Promise<void> {
    await this.loadPending();
    await this.refresh();
    if (this.isAdmin) {
      try {
        this.serviceUrl = (await this.get<any>('/config')).service_url;
      } catch (e: any) {
        this.error = e?.error?.detail ?? this.translate.instant('meeting_bot.error_load_config');
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
  private get<T>(path: string): Promise<T> { return firstValueFrom(this.http.get<T>(this.base + path)); }
  async refresh(): Promise<void> {
    if (this.refreshing) return;
    this.refreshing = true;
    try {
      this.caps = await this.get('/capabilities');
      this.meetings = (await this.get<any>('/meetings')).items;
    } catch (error) { if (!this.meetings.length) this.error = this.message(error); }
    finally { this.refreshing = false; this.cd.markForCheck(); }
  }
  async loadPending(): Promise<void> {
    try { this.pending = await this.recorder.journal.list(this.owner); }
    catch { this.error = this.translate.instant('meeting_bot.error_local_storage'); }
    this.cd.markForCheck();
  }
  private payload(): any {
    return { external_id: 'web:' + crypto.randomUUID(), title: this.title.trim() || this.translate.instant('meeting_bot.default_title'),
      language: this.language, vocabulary: this.vocabulary.split(',').map(s => s.trim()).filter(Boolean),
      recording_authorized: this.authorized, max_duration_minutes: 480 };
  }
  private async startCapture(kind: 'meeting' | 'browser', payload: any): Promise<any> {
    const key = `acten-bot-start:${this.owner}:${kind}`;
    const { external_id, ...content } = payload;
    const signature = JSON.stringify(content);
    const previous = sessionStorage.getItem(key);
    let saved: any;
    try { saved = previous ? JSON.parse(previous) : null; } catch { saved = null; }
    if (saved?.signature === signature) payload.external_id = saved.external_id;
    sessionStorage.setItem(key, JSON.stringify({ signature, external_id: payload.external_id }));
    const result = await firstValueFrom(this.http.post(`${this.base}/start/${kind}`, payload));
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
      await this.startCapture('meeting', { ...this.payload(), meeting_url: this.meetingUrl });
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
        const remote: any = await this.startCapture('browser', payload);
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
        await firstValueFrom(this.http.delete(`${this.base}/recordings/${meta.id}`));
      } catch (e: any) {
        // El registro local se descarta igual —no hay audio—, pero se dice
        // que el servidor no confirmó, por si hay que limpiarlo a mano.
        this.error = e?.error?.detail ?? this.translate.instant('meeting_bot.error_discard_unconfirmed');
      }
      await this.recorder.journal.completed(meta.id);
      this.notice = this.translate.instant('meeting_bot.notice_discarded');
    });
  }
  async stopBot(id: string): Promise<void> {
    await this.action(async () => { await firstValueFrom(this.http.post(`${this.base}/meetings/${id}/stop`, {})); await this.refresh(); });
  }
  async open(item: any): Promise<void> {
    await this.action(async () => {
      this.result = await this.get<any>(`/meetings/${item.id}/result`);
      this.selected = item.id; this.audioUrl = ''; this.audioTime = 0;
      this.search = ''; this.speakerFilter = ''; this.names = {};
      for (const speaker of this.result.speakers ?? []) this.names[speaker.speaker_id] = speaker.speaker_name || '';
      if (item.source === 'browser') {
        const grant: any = await firstValueFrom(this.http.post(`${this.base}/recordings/${item.id}/playback`, {}));
        this.audioUrl = environment.apiUrl + grant.path;
      } else if (this.result.recording?.url?.startsWith('https://')) this.audioUrl = this.result.recording.url;
    });
  }
  async label(speaker: any): Promise<void> {
    await this.action(async () => {
      const answer: any = await firstValueFrom(this.http.put(`${this.base}/meetings/${this.selected}/speakers/${encodeURIComponent(speaker.speaker_id)}`, { display_name: this.names[speaker.speaker_id] }));
      this.result = await this.get<any>(`/meetings/${this.selected}/result`);
      this.notice = this.translate.instant(answer.transcript_preserved ? 'meeting_bot.notice_name_confirmed_preserved' : 'meeting_bot.notice_name_confirmed');
    });
  }
  async saveConfig(): Promise<void> {
    await this.action(async () => {
      await firstValueFrom(this.http.put(`${this.base}/config`, { service_url: this.serviceUrl, client_key: this.serviceKey }));
      this.serviceKey = ''; this.notice = this.translate.instant('meeting_bot.notice_config_saved'); await this.refresh();
    });
  }
  private async loadMailPolicy(): Promise<void> {
    // Sin conexión al bot no hay política que leer; el error ya se mostró arriba.
    if (!this.serviceUrl) return;
    try {
      const policy = (await this.get<any>('/mail-policy')).policy;
      if (policy) {
        this.policySenders = (policy.allowed_senders ?? []).join('\n');
        this.policyTimezone = policy.timezone || 'America/Bogota';
        this.policyAuthorized = !!policy.recording_authorized;
      }
    } catch (e: any) {
      this.error = e?.error?.detail ?? this.translate.instant('meeting_bot.error_load_mail_policy');
    }
    this.cd.markForCheck();
  }
  async saveMailPolicy(): Promise<void> {
    await this.action(async () => {
      const senders = this.policySenders.split(/[\n,;]+/).map(s => s.trim()).filter(Boolean);
      await firstValueFrom(this.http.put(`${this.base}/mail-policy`, {
        allowed_senders: senders, recording_authorized: this.policyAuthorized,
        timezone: this.policyTimezone.trim() || 'America/Bogota',
      }));
      this.notice = this.translate.instant(this.policyAuthorized
        ? 'meeting_bot.notice_mail_policy_enabled'
        : 'meeting_bot.notice_senders_saved');
      await this.refresh();
    });
  }
  async loadEmails(): Promise<void> {
    await this.action(async () => { this.receipts = (await this.get<any>('/email-receipts')).items; });
  }
  seek(audio: HTMLAudioElement, seconds: number): void {
    if (!this.audioUrl) return;
    audio.currentTime = Math.max(0, seconds);
    void audio.play().catch(() => { this.error = this.translate.instant('meeting_bot.error_play'); });
  }
  audioFailed(): void { this.error = this.translate.instant('meeting_bot.audio_error'); }
  get segments(): any[] {
    return (this.result?.transcript ?? []).filter((s: any) =>
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
  message(error: any): string {
    const detail = error?.error?.detail;
    return typeof detail === 'string' ? detail : error?.status ? this.translate.instant('meeting_bot.error_operation_status', { status: error.status }) : error?.message || this.translate.instant('meeting_bot.error_operation');
  }
}
