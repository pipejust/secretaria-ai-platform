import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';

/** Estados que devuelve el bot en `BotMeeting.state`. Espejo de
 *  conversacionalbot/store.py (ACTIVE_STATES / RUNNABLE_STATES) más los
 *  terminales; la pantalla traduce los conocidos y muestra el resto tal cual. */
export type MeetingState =
  | 'scheduled' | 'queued' | 'dispatching' | 'joining' | 'recording' | 'capturing' | 'uploading'
  | 'transcribing' | 'audio_assembling' | 'audio_ready' | 'audio_uploading' | 'audio_submitting'
  | 'audio_transcribing' | 'analyzing' | 'delivering' | 'completed' | 'cancelled' | 'failed'
  | 'needs_attention' | 'dispatch_unknown' | 'audio_dispatch_unknown';

export type CaptureKind = 'meeting' | 'browser';
export type CaptureSource = 'meeting' | 'browser';
export type CaptureLanguage = 'es' | 'en' | 'ca';
export type IdentitySource = 'human_confirmed' | 'meeting_metadata' | 'unknown';

/** GET /capabilities — espejo de conversacionalbot `/v1/capabilities`. */
export interface BotCapabilities {
  client_id: string;
  acten_tenant_id: string | null;
  meetings_ready: boolean;
  browser_ready: boolean;
  invitation_email: string | null;
  mail_enabled: boolean;
  max_chunk_bytes: number;
  max_duration_minutes: number;
}

/** GET /config — la clave nunca vuelve al navegador. */
export interface BotConfig {
  service_url: string;
  configured: boolean;
}

/** PUT /config — `client_key` vacío conserva la clave guardada. */
export interface BotConfigInput {
  service_url: string;
  client_key: string;
}

/** Elemento de GET /meetings y respuesta de POST /start/{kind}.
 *  Espejo de conversacionalbot store.py `public_meeting`. */
export interface BotMeeting {
  id: string;
  external_id: string;
  state: MeetingState;
  provider: string;
  title: string;
  join_at: string | null;
  source: CaptureSource;
  provider_id?: string | null;
  provider_status?: string | null;
  profile?: 'quality' | 'economy';
  stop_requested?: boolean;
  error_code: string | null;
  delivery_status?: string | null;
  created_at?: number;
  updated_at?: number;
  result_url?: string;
  /** Lo añade bot_control.py al listar: sesión Acten ya importada, si existe. */
  acten_session_id?: string | null;
}

export interface TranscriptSegment {
  id?: string;
  start: number;
  end: number;
  text: string;
  speaker_id: string | null;
  speaker_name: string | null;
  identity_source?: IdentitySource;
  person_external_id?: string | null;
}

export interface BotSpeaker {
  speaker_id: string;
  speaker_name: string | null;
  identity_source: IdentitySource;
  segment_duration_seconds: number;
  intervals?: [number, number][];
  segment_ids?: string[];
  person_external_id?: string | null;
}

/** Afirmación con evidencia (summary, key_points…); espejo de schemas.py `Claim`. */
export interface ResultClaim {
  text: string;
  evidence_ids?: string[];
}

/** Momento navegable: un Claim con los tiempos de su evidencia. */
export interface KeyMoment extends ResultClaim {
  start: number;
  end: number;
}

/** Capítulo del timeline; espejo de schemas.py `Chapter` + start/end. */
export interface TimelineChapter {
  title?: string;
  topic?: string;
  text?: string;
  description?: string;
  evidence_ids?: string[];
  start: number;
  end: number;
}

export interface SpeakerTurn {
  start: number;
  end: number;
  speaker_id: string | null;
  speaker_name: string | null;
  segment_id: string;
}

/** GET /meetings/{id}/result — espejo de schemas.py `compile_result` más
 *  `acten_session_id` que añade bot_control.py `apply_labels`. */
export interface MeetingResult {
  title?: string;
  summary: ResultClaim[];
  key_points?: ResultClaim[];
  decisions?: ResultClaim[];
  action_items?: ResultClaim[];
  open_questions?: ResultClaim[];
  key_moments: KeyMoment[];
  timeline: TimelineChapter[];
  speakers: BotSpeaker[];
  speaker_timeline?: SpeakerTurn[];
  transcript: TranscriptSegment[];
  timebase?: string;
  recording?: { url?: string };
  warnings?: string[];
  acten_session_id?: string | null;
}

/** Cuerpo de POST /start/{kind}; espejo de bot_control.py `StartCapture`. */
export interface StartCapturePayload {
  external_id: string;
  title: string;
  language?: CaptureLanguage | string;
  meeting_url?: string;
  scheduled_start?: string;
  mime_type?: string;
  max_duration_minutes?: number;
  vocabulary?: string[];
  recording_authorized?: boolean;
}

/** Cuerpo de POST /recordings/{id}/finish; espejo de bot_control.py `Finish`. */
export interface FinishRecordingBody {
  chunk_count: number;
  interrupted: boolean;
}

export interface PlaybackGrant {
  path: string;
  expires_in?: number;
}

export interface SpeakerLabelBody {
  display_name: string;
  person_external_id?: string | null;
}

export interface LabelAnswer {
  speaker_id?: string;
  display_name?: string;
  identity_source?: IdentitySource;
  transcript_preserved: boolean;
}

/** Política de invitaciones por correo; espejo de bot_control.py `MailPolicy`. */
export interface MailPolicy {
  allowed_senders: string[];
  recording_authorized: boolean;
  timezone: string;
}

/** GET/PUT /mail-policy — espejo de conversacionalbot `mail_state`. */
export interface MailPolicyState {
  invitation_email: string | null;
  mail_enabled: boolean;
  policy: MailPolicy | null;
}

export interface EmailReceipt {
  id: string;
  state: string;
  error: string | null;
  created_at: number;
}

interface ItemsPage<T> { items: T[]; }

/** Proxy al bot de reuniones propio (`/api/owned-bot`). Devuelve promesas
 *  porque la pantalla encadena las llamadas con `await` dentro de `action()`. */
@Injectable({ providedIn: 'root' })
export class MeetingBotService {
  private http = inject(HttpClient);
  private base = environment.apiUrl + '/api/owned-bot';

  /** Convierte una ruta relativa del backend (p. ej. el `path` de un
   *  permiso de reproducción) en URL absoluta contra el API. */
  absoluteUrl(path: string): string { return environment.apiUrl + path; }

  getConfig(): Promise<BotConfig> { return this.get<BotConfig>('/config'); }

  saveConfig(body: BotConfigInput): Promise<Pick<BotConfig, 'configured'>> {
    return firstValueFrom(this.http.put<Pick<BotConfig, 'configured'>>(`${this.base}/config`, body));
  }

  getCapabilities(): Promise<BotCapabilities> { return this.get<BotCapabilities>('/capabilities'); }

  async listMeetings(): Promise<BotMeeting[]> {
    return (await this.get<ItemsPage<BotMeeting>>('/meetings')).items;
  }

  getResult(id: string): Promise<MeetingResult> { return this.get<MeetingResult>(`/meetings/${id}/result`); }

  startCapture(kind: CaptureKind, payload: StartCapturePayload): Promise<BotMeeting> {
    return firstValueFrom(this.http.post<BotMeeting>(`${this.base}/start/${kind}`, payload));
  }

  stopMeeting(id: string): Promise<BotMeeting> {
    return firstValueFrom(this.http.post<BotMeeting>(`${this.base}/meetings/${id}/stop`, {}));
  }

  uploadChunk(id: string, seq: number, blob: Blob): Promise<unknown> {
    return firstValueFrom(this.http.put(`${this.base}/recordings/${id}/chunks/${seq}`, blob));
  }

  finishRecording(id: string, body: FinishRecordingBody): Promise<unknown> {
    return firstValueFrom(this.http.post(`${this.base}/recordings/${id}/finish`, body));
  }

  deleteRecording(id: string): Promise<unknown> {
    return firstValueFrom(this.http.delete(`${this.base}/recordings/${id}`));
  }

  playbackGrant(id: string): Promise<PlaybackGrant> {
    return firstValueFrom(this.http.post<PlaybackGrant>(`${this.base}/recordings/${id}/playback`, {}));
  }

  labelSpeaker(meetingId: string, speakerId: string, body: SpeakerLabelBody): Promise<LabelAnswer> {
    return firstValueFrom(this.http.put<LabelAnswer>(
      `${this.base}/meetings/${meetingId}/speakers/${encodeURIComponent(speakerId)}`, body));
  }

  getMailPolicy(): Promise<MailPolicyState> { return this.get<MailPolicyState>('/mail-policy'); }

  saveMailPolicy(body: MailPolicy): Promise<MailPolicyState> {
    return firstValueFrom(this.http.put<MailPolicyState>(`${this.base}/mail-policy`, body));
  }

  async listEmailReceipts(): Promise<EmailReceipt[]> {
    return (await this.get<ItemsPage<EmailReceipt>>('/email-receipts')).items;
  }

  private get<T>(path: string): Promise<T> { return firstValueFrom(this.http.get<T>(this.base + path)); }
}
