import { Component, OnInit, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { SettingsService } from '../../services/settings.service';
import { ToastService } from '../../services/toast.service';
import { AuthService } from '../../services/auth.service';
import { environment } from '../../../environments/environment';

interface OAuthCfg {
  client_id: string;
  client_secret: string;
  redirect_uri: string;
  tenant?: string;       // sólo Microsoft
  isActive?: boolean;
}

interface OAuthStatus {
  ready: boolean;
  source: 'tenant' | 'env' | null;
}

/** Preferencias de UX puramente frontend — persistidas en localStorage.
 *  No tocan al backend (no hay modelo de UserPreferences todavía). */
interface UiPrefs {
  dateFormat: string;
  timeZone: string;
  language: string;
  landingPage: string;
  darkMode: boolean;
  workspaceName: string;
  weekStartsOn: 'monday' | 'sunday';
  defaultMeetingDuration: number;
  defaultTaskAssignee: string;
  defaultProjectPrivacy: 'workspace' | 'private' | 'public';
  autoArchiveDays: number;
  defaultCalendar: string;
  defaultFileStorage: string;
  defaultCommunicationChannel: string;
  defaultDocEditor: string;
}

const PREFS_LS_KEY = 'acten:ui-prefs:v1';
const DEFAULT_PREFS: UiPrefs = {
  dateFormat: 'DD/MM/YYYY',
  timeZone: 'America/Bogota',
  language: 'es-CO',
  landingPage: '/admin/dashboard',
  darkMode: false,
  workspaceName: '',
  weekStartsOn: 'monday',
  defaultMeetingDuration: 60,
  defaultTaskAssignee: '',
  defaultProjectPrivacy: 'workspace',
  autoArchiveDays: 90,
  defaultCalendar: 'google',
  defaultFileStorage: 'google-drive',
  defaultCommunicationChannel: '',
  defaultDocEditor: 'google-docs',
};

type SectionKey =
  | 'general' | 'integrations' | 'email' | 'task-sync'
  | 'documents' | 'security' | 'api' | 'audit';

@Component({
  selector: 'app-settings',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterModule],
  templateUrl: './settings.component.html',
  styleUrls: ['./settings.component.css']
})
export class SettingsComponent implements OnInit {
  // ============================================================
  // Settings reales del backend (preservadas tal cual)
  // ============================================================
  smtpSettings = { provider: 'Resend', apiKey: '', senderEmail: '' };
  firefliesSettings: { apiKey: string; webhookUrl: string; webhook_token: string } =
    { apiKey: '', webhookUrl: '', webhook_token: '' };
  trelloSettings = { apiKey: '', apiToken: '', boardId: '', isActive: false };
  jiraSettings = { email: '', apiToken: '', domain: '', isActive: false };
  azureSettings = { organization: '', project: '', pat: '', isActive: false };
  clickupSettings = { apiToken: '', teamId: '', isActive: false };
  autoCurationSettings = { isEnabled: false, timeoutHours: 1 };
  googleCalendarSettings: OAuthCfg = {
    client_id: '', client_secret: '', redirect_uri: '', isActive: true,
  };
  microsoftCalendarSettings: OAuthCfg = {
    client_id: '', client_secret: '', redirect_uri: '', tenant: 'common', isActive: true,
  };
  oauthStatus: { google: OAuthStatus; microsoft: OAuthStatus } = {
    google:    { ready: false, source: null },
    microsoft: { ready: false, source: null },
  };

  // ============================================================
  // Preferencias UX (localStorage)
  // ============================================================
  prefs: UiPrefs = { ...DEFAULT_PREFS };

  // ============================================================
  // Tab actual + workspace toggle visual
  // ============================================================
  activeSection: SectionKey = 'general';
  workspaceActive = true;

  isSaving = false;
  successMessage = '';
  errorMessage = '';

  /** Catálogo de modelos / opciones para los selects. */
  readonly dateFormats = [
    { value: 'DD/MM/YYYY',         label: '16/05/2026 (DD/MM/YYYY)' },
    { value: 'MM/DD/YYYY',         label: '05/16/2026 (MM/DD/YYYY)' },
    { value: 'YYYY-MM-DD',         label: '2026-05-16 (YYYY-MM-DD)' },
    { value: 'D MMM YYYY',         label: '16 may 2026' },
  ];
  readonly timeZones = [
    { value: 'America/Bogota',     label: '(UTC-05:00) Bogotá / Lima' },
    { value: 'America/Mexico_City',label: '(UTC-06:00) Ciudad de México' },
    { value: 'America/Argentina/Buenos_Aires', label: '(UTC-03:00) Buenos Aires' },
    { value: 'America/Santiago',   label: '(UTC-04:00) Santiago' },
    { value: 'Europe/Madrid',      label: '(UTC+01:00) Madrid' },
    { value: 'America/Los_Angeles',label: '(UTC-08:00) Pacific Time (US & Canada)' },
    { value: 'America/New_York',   label: '(UTC-05:00) Eastern Time (US & Canada)' },
    { value: 'UTC',                label: '(UTC) Coordinated Universal Time' },
  ];
  readonly languages = [
    { value: 'es-CO', label: 'Español (Colombia)' },
    { value: 'es-MX', label: 'Español (México)' },
    { value: 'es-ES', label: 'Español (España)' },
    { value: 'en-US', label: 'English (US)' },
  ];
  readonly landingPages = [
    { value: '/admin/dashboard',   label: 'Resumen' },
    { value: '/admin/meetings',    label: 'Reuniones' },
    { value: '/admin/projects',    label: 'Proyectos' },
    { value: '/admin/pendientes',  label: 'Tareas' },
    { value: '/admin/ask',         label: 'Pregunta a Acten' },
    { value: '/admin/calendar',    label: 'Calendario' },
  ];
  readonly meetingDurations = [
    { value: 15,  label: '15 minutos' },
    { value: 30,  label: '30 minutos' },
    { value: 45,  label: '45 minutos' },
    { value: 60,  label: '60 minutos' },
    { value: 90,  label: '90 minutos' },
    { value: 120, label: '2 horas' },
  ];
  readonly archiveDays = [
    { value: 30,  label: '30 días' },
    { value: 60,  label: '60 días' },
    { value: 90,  label: '90 días' },
    { value: 180, label: '180 días' },
    { value: 365, label: '1 año' },
    { value: 0,   label: 'Nunca' },
  ];
  readonly privacyOptions = [
    { value: 'workspace', label: 'Visible en el workspace' },
    { value: 'private',   label: 'Privado (solo invitados)' },
    { value: 'public',    label: 'Público (link compartido)' },
  ];

  /** Secciones del menú lateral del settings. */
  readonly sections: { key: SectionKey; label: string; icon: string }[] = [
    { key: 'general',      label: 'General',           icon: 'general' },
    { key: 'integrations', label: 'Integraciones',     icon: 'plug' },
    { key: 'email',        label: 'Correo electrónico',icon: 'mail' },
    { key: 'task-sync',    label: 'Sincronización de tareas', icon: 'sync' },
    { key: 'documents',    label: 'Documentos',        icon: 'doc' },
    { key: 'security',     label: 'Seguridad',         icon: 'shield' },
    { key: 'api',          label: 'API y Webhooks',    icon: 'code' },
    { key: 'audit',        label: 'Auditoría',         icon: 'clipboard' },
  ];

  constructor(
    private settingsService: SettingsService,
    private cdr: ChangeDetectorRef,
    private toast: ToastService,
    private http: HttpClient,
    private auth: AuthService,
  ) { }

  get defaultGoogleRedirect(): string {
    return `${environment.apiUrl}/api/calendar/google/callback`;
  }
  get defaultMicrosoftRedirect(): string {
    return `${environment.apiUrl}/api/calendar/microsoft/callback`;
  }

  useDefaultRedirect(provider: 'google' | 'microsoft'): void {
    if (provider === 'google') this.googleCalendarSettings.redirect_uri = this.defaultGoogleRedirect;
    else this.microsoftCalendarSettings.redirect_uri = this.defaultMicrosoftRedirect;
  }

  loadOauthStatus(): void {
    this.http.get<{google: OAuthStatus; microsoft: OAuthStatus}>(
      `${environment.apiUrl}/api/calendar/oauth_config_status`,
      { headers: this.auth.getAuthHeaders() })
      .subscribe({
        next: (s) => { this.oauthStatus = s; this.cdr.detectChanges(); },
        error: () => { /* silencioso */ },
      });
  }

  copyWebhookUrl(): void {
    const url = this.firefliesSettings.webhookUrl;
    if (!url) return;
    navigator.clipboard.writeText(url).then(
      () => this.toast.success('Webhook URL copiada al portapapeles.'),
      () => this.toast.error('No se pudo copiar la URL.'),
    );
  }

  // ============================================================
  // Preferencias UX (localStorage)
  // ============================================================

  private loadPrefs(): void {
    try {
      const raw = localStorage.getItem(PREFS_LS_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === 'object') {
        this.prefs = { ...DEFAULT_PREFS, ...parsed };
      }
    } catch { /* ignore */ }
  }

  savePrefs(): void {
    try {
      localStorage.setItem(PREFS_LS_KEY, JSON.stringify(this.prefs));
    } catch { /* quota o private mode */ }
  }

  /** Toggle de Dark Mode — persiste y aplica una clase global al body. */
  toggleDarkMode(): void {
    this.prefs.darkMode = !this.prefs.darkMode;
    this._applyDarkMode();
    this.savePrefs();
    this.cdr.detectChanges();
  }
  private _applyDarkMode(): void {
    if (typeof document === 'undefined') return;
    document.body.classList.toggle('dark-mode', this.prefs.darkMode);
  }

  setSection(s: SectionKey): void {
    this.activeSection = s;
  }

  // ============================================================
  // Lifecycle
  // ============================================================

  ngOnInit(): void {
    this.loadPrefs();
    this._applyDarkMode();
    this.loadOauthStatus();

    this.settingsService.getSettings().subscribe({
      next: (data) => {
        if (data.smtp) this.smtpSettings = { ...this.smtpSettings, ...data.smtp };
        if (data.fireflies) this.firefliesSettings = { ...this.firefliesSettings, ...data.fireflies };
        if (data.trello) this.trelloSettings = { ...this.trelloSettings, ...data.trello };
        if (data.jira) this.jiraSettings = { ...this.jiraSettings, ...data.jira };
        if (data.azure) this.azureSettings = { ...this.azureSettings, ...data.azure };
        if (data.clickup) this.clickupSettings = { ...this.clickupSettings, ...data.clickup };
        if (data.autoCuration) this.autoCurationSettings = { ...this.autoCurationSettings, ...data.autoCuration };
        if (data.google_calendar) {
            this.googleCalendarSettings = { ...this.googleCalendarSettings, ...data.google_calendar };
        }
        if (data.microsoft_calendar) {
            this.microsoftCalendarSettings = { ...this.microsoftCalendarSettings, ...data.microsoft_calendar };
        }
        this.cdr.detectChanges();
      },
      error: (err) => console.error('Failed to load settings', err)
    });
  }

  saveSettings(): void {
    this.isSaving = true;
    this.successMessage = '';
    this.errorMessage = '';

    // 1) Guardar prefs UX en localStorage (sin red).
    this.savePrefs();

    // 2) Guardar settings reales en backend (preserva la API existente).
    const payload = {
      smtp: this.smtpSettings,
      fireflies: this.firefliesSettings,
      trello: this.trelloSettings,
      jira: this.jiraSettings,
      azure: this.azureSettings,
      clickup: this.clickupSettings,
      autoCuration: this.autoCurationSettings,
      google_calendar: this.googleCalendarSettings,
      microsoft_calendar: this.microsoftCalendarSettings,
    };

    this.settingsService.saveSettings(payload).subscribe({
      next: () => {
        this.isSaving = false;
        this.successMessage = 'Configuración guardada correctamente.';
        this.toast.success(this.successMessage);
        this.loadOauthStatus();
        this.cdr.detectChanges();
        setTimeout(() => {
          this.successMessage = '';
          this.cdr.detectChanges();
        }, 4000);
      },
      error: (err) => {
        this.isSaving = false;
        this.errorMessage = 'Hubo un error guardando la configuración.';
        this.toast.error(this.errorMessage);
        console.error(err);
        this.cdr.detectChanges();
      }
    });
  }

  /** trackBy helpers para los *ngFor del template. */
  trackBySection(_i: number, s: { key: SectionKey }): SectionKey { return s.key; }
}
