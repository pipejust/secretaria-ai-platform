// Build tag: 2026-06-10T05-share-i18n (forza rebuild de Coolify cuando el
// webhook de commits empty no dispara). Cambiar este número garantiza
// que el bundle de producción cambie su hash y se note el deploy.
// Esta versión incluye las claves settings.share_* en los 3 idiomas.
import { Component, OnInit, OnDestroy, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { FormsModule } from '@angular/forms';
import { RouterModule, ActivatedRoute } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { Subject } from 'rxjs';
import { takeUntil } from 'rxjs/operators';
import { SettingsService, ShareSettings } from '../../services/settings.service';
import { ToastService } from '../../services/toast.service';
import { AuthService } from '../../services/auth.service';
import { PreferencesService, UiPrefs, DEFAULT_PREFS } from '../../services/preferences.service';
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

/** Las secciones Documents / Security / Audit se removieron por
 *  petición del cliente — la marca/logo vive en /admin/branding,
 *  los usuarios y roles tienen sus propias vistas, y la auditoría
 *  detallada llegará después. */
type SectionKey =
  | 'general' | 'integrations' | 'email' | 'task-sync' | 'api';

@Component({
  selector: 'app-settings',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterModule, TranslateModule],
  templateUrl: './settings.component.html',
  styleUrls: ['./settings.component.css']
})
export class SettingsComponent implements OnInit, OnDestroy {
  private readonly destroy$ = new Subject<void>();
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

  /** Estado del modelo "compartido vs per-user".
   *  null mientras carga; tras ngOnInit lo seteamos siempre — incluso
   *  ante error de red, con defaults seguros (is_owner=false). */
  shareSettings: ShareSettings | null = null;
  isUpdatingShare = false;
  // El campo timeoutMinutes es el "nuevo" (granularidad real). Conservamos
  // timeoutHours para compatibilidad con datos viejos: si el backend trae
  // sólo timeoutHours, lo convertimos a minutos al cargar; y al guardar
  // siempre escribimos AMBOS para que cron/back tradicional siga leyendo.
  autoCurationSettings: { isEnabled: boolean; timeoutMinutes: number; timeoutHours: number } =
    { isEnabled: false, timeoutMinutes: 60, timeoutHours: 1 };
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
  /** True mientras `getSettings()` está cargando la configuración del tenant.
   *  Se gatea el render de la card principal con esto para mostrar un skeleton
   *  shimmer en vez de inputs vacíos con valores por defecto. */
  isLoading = true;
  successMessage = '';
  errorMessage = '';

  // Probar envío de correo (POST /api/branding/test_email)
  testEmailTo = '';
  isSendingTest = false;

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
    { key: 'general',      label: 'General',                  icon: 'general' },
    { key: 'integrations', label: 'Integraciones',            icon: 'plug' },
    { key: 'email',        label: 'Correo electrónico',       icon: 'mail' },
    { key: 'task-sync',    label: 'Sincronización de tareas y correos', icon: 'sync' },
    { key: 'api',          label: 'API y Webhooks',           icon: 'code' },
  ];

  /** Estado de visibilidad por campo password — clave libre, valor bool. */
  passwordVisible: Record<string, boolean> = {};
  togglePassword(key: string): void {
    this.passwordVisible[key] = !this.passwordVisible[key];
    this.cdr.detectChanges();
  }
  isPwVisible(key: string): boolean { return !!this.passwordVisible[key]; }

  constructor(
    private settingsService: SettingsService,
    private cdr: ChangeDetectorRef,
    private toast: ToastService,
    private http: HttpClient,
    private auth: AuthService,
    private preferences: PreferencesService,
    private route: ActivatedRoute,
    private translate: TranslateService,
  ) {
    // Deep-link: si entran con `?section=task-sync` (p.ej. desde el modal
    // de Proyectos > Auto-Curación), abrimos esa sección directamente.
    const seg = this.route.snapshot.queryParamMap.get('section');
    const valid: SectionKey[] = ['general', 'integrations', 'email', 'task-sync', 'api'];
    if (seg && (valid as string[]).includes(seg)) {
      this.activeSection = seg as SectionKey;
    }
  }

  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
  }

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

  // -----------------------------------------------------------------
  // "Compartido vs per-user" — switches del owner del tenant
  // -----------------------------------------------------------------

  /** Carga el estado de los 2 switches al entrar a la vista. */
  loadShareSettings(): void {
    this.settingsService.getShareSettings().subscribe({
      next: (s) => { this.shareSettings = s; this.cdr.detectChanges(); },
      error: () => {
        // En error nos quedamos con shareSettings=null que el template
        // interpreta como "todavía cargando o sin datos" → la card de
        // switches no se pinta, y los inputs no quedan read-only.
        this.shareSettings = null;
      }
    });
  }

  /** True cuando el caller no es owner Y share_integrations=ON. La UI
   *  pinta los inputs read-only y muestra una nota explicativa. */
  get integrationsReadOnly(): boolean {
    return !!this.shareSettings &&
           this.shareSettings.share_integrations === true &&
           this.shareSettings.is_owner === false;
  }

  /** True cuando el caller es owner del tenant — gate para mostrar la
   *  card "Quién administra integraciones" con los 2 switches. */
  get canManageShare(): boolean {
    return !!this.shareSettings && this.shareSettings.is_owner === true;
  }

  /** Cambia un switch contra el backend con optimistic update +
   *  rollback si falla. El backend devuelve el nuevo estado completo
   *  que volvemos a aplicar para mantenernos sincronizados. */
  private _updateShare(patch: Partial<Pick<ShareSettings, 'share_integrations' | 'share_routings'>>): void {
    if (!this.shareSettings || !this.shareSettings.is_owner) return;
    const previous = { ...this.shareSettings };
    this.shareSettings = { ...this.shareSettings, ...patch };
    this.isUpdatingShare = true;
    this.cdr.detectChanges();
    this.settingsService.updateShareSettings(patch).subscribe({
      next: (s) => {
        this.shareSettings = s;
        this.isUpdatingShare = false;
        this.toast.success(this.translate.instant('settings.share_updated'));
        this.cdr.detectChanges();
      },
      error: (err) => {
        // Rollback al estado previo si el backend rechazó.
        this.shareSettings = previous;
        this.isUpdatingShare = false;
        const msg = err?.error?.detail || this.translate.instant('settings.share_update_failed');
        this.toast.error(msg);
        this.cdr.detectChanges();
      }
    });
  }

  toggleShareIntegrations(): void {
    if (!this.shareSettings) return;
    this._updateShare({ share_integrations: !this.shareSettings.share_integrations });
  }

  toggleShareRoutings(): void {
    if (!this.shareSettings) return;
    this._updateShare({ share_routings: !this.shareSettings.share_routings });
  }

  copyWebhookUrl(): void {
    const url = this.firefliesSettings.webhookUrl;
    if (!url) return;
    navigator.clipboard.writeText(url).then(
      () => this.toast.success(this.translate.instant('settings.msg_webhook_copied')),
      () => this.toast.error(this.translate.instant('settings.msg_url_copy_failed')),
    );
  }

  // ============================================================
  // Preferencias UX — borrador local. SOLO se aplican al backend / al
  // PreferencesService cuando el usuario pulsa "Guardar cambios".
  // ============================================================

  /** Indica si hay cambios en `prefs` aún no guardados.  */
  get hasUnsavedPrefs(): boolean {
    const saved = this.preferences.get();
    return JSON.stringify(saved) !== JSON.stringify(this.prefs);
  }

  /** Marca que el borrador cambió (no hace I/O). Sirve para forzar CD. */
  markPrefsDirty(): void {
    this.cdr.detectChanges();
  }

  /** Toggle visual del switch — sólo cambia el borrador local.
   *  El efecto en toda la app se aplica al guardar. */
  toggleDarkMode(): void {
    this.prefs.darkMode = !this.prefs.darkMode;
    this.markPrefsDirty();
  }

  /** Confirma TODOS los cambios pendientes del borrador.
   *  Aplica side-effects del servicio (dark mode visible, week-start, lang). */
  commitPrefs(): void {
    this.preferences.setAll(this.prefs);
    this.toast.success(this.translate.instant('settings.msg_prefs_saved'));
  }

  /** Revierte el borrador al estado guardado. */
  discardPrefChanges(): void {
    this.prefs = { ...this.preferences.get() };
    this.cdr.detectChanges();
  }

  setSection(s: SectionKey): void {
    this.activeSection = s;
  }

  // ============================================================
  // Lifecycle
  // ============================================================

  ngOnInit(): void {
    // Cargar prefs desde el servicio y sincronizar al estado local.
    this.prefs = { ...this.preferences.get() };
    // Cualquier cambio en el servicio (otra pestaña, otro componente) se
    // refleja aquí también.
    this.preferences.prefs$.pipe(takeUntil(this.destroy$)).subscribe((p) => {
      this.prefs = { ...p };
      this.cdr.detectChanges();
    });

    this.loadOauthStatus();
    this.loadShareSettings();

    this.settingsService.getSettings().subscribe({
      next: (data) => {
        if (data.smtp) this.smtpSettings = { ...this.smtpSettings, ...data.smtp };
        if (data.fireflies) this.firefliesSettings = { ...this.firefliesSettings, ...data.fireflies };
        if (data.trello) this.trelloSettings = { ...this.trelloSettings, ...data.trello };
        if (data.jira) this.jiraSettings = { ...this.jiraSettings, ...data.jira };
        if (data.azure) this.azureSettings = { ...this.azureSettings, ...data.azure };
        if (data.clickup) this.clickupSettings = { ...this.clickupSettings, ...data.clickup };
        if (data.autoCuration) {
            this.autoCurationSettings = { ...this.autoCurationSettings, ...data.autoCuration };
            // Migración legacy: si vino sólo timeoutHours, derivamos minutos.
            if (!data.autoCuration.timeoutMinutes && data.autoCuration.timeoutHours) {
                this.autoCurationSettings.timeoutMinutes =
                    Math.max(1, Math.round(Number(data.autoCuration.timeoutHours) * 60));
            }
            // Y al revés: si vino sólo minutos, derivamos horas para legacy.
            if (!data.autoCuration.timeoutHours && data.autoCuration.timeoutMinutes) {
                this.autoCurationSettings.timeoutHours =
                    Math.max(1 / 60, Number(data.autoCuration.timeoutMinutes) / 60);
            }
        }
        if (data.google_calendar) {
            this.googleCalendarSettings = { ...this.googleCalendarSettings, ...data.google_calendar };
        }
        if (data.microsoft_calendar) {
            this.microsoftCalendarSettings = { ...this.microsoftCalendarSettings, ...data.microsoft_calendar };
        }
        this.isLoading = false;
        this.cdr.detectChanges();
      },
      error: (err) => {
        console.error('Failed to load settings', err);
        // Aún en error apagamos el skeleton para no dejar la pantalla vacía
        // permanente; los inputs caen a defaults razonables.
        this.isLoading = false;
        this.cdr.detectChanges();
      },
    });
  }

  saveSettings(): void {
    this.isSaving = true;
    this.successMessage = '';
    this.errorMessage = '';

    // 1) Aplicar el borrador de prefs UX → servicio (localStorage + side
    //    effects). Sin toast aquí — el final del saveSettings ya muestra el
    //    success global para evitar toasts duplicados.
    this.preferences.setAll(this.prefs);

    // 2) Sincronizar minutos ↔ horas antes de guardar: el cron lee minutos
    //    si están, pero conservamos `timeoutHours` para compatibilidad con
    //    código viejo y el override per-proyecto que aún usa horas.
    const minutes = Math.max(1, Math.round(Number(this.autoCurationSettings.timeoutMinutes) || 60));
    this.autoCurationSettings.timeoutMinutes = minutes;
    this.autoCurationSettings.timeoutHours = +(minutes / 60).toFixed(4);

    // 3) Guardar settings reales en backend (preserva la API existente).
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
        this.successMessage = this.translate.instant('settings.msg_config_saved');
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
        this.errorMessage = this.translate.instant('settings.msg_config_save_failed');
        this.toast.error(this.errorMessage);
        console.error(err);
        this.cdr.detectChanges();
      }
    });
  }

  /** trackBy helpers para los *ngFor del template. */
  trackBySection(_i: number, s: { key: SectionKey }): SectionKey { return s.key; }

  /**
   * Probar envío de correo: POST /api/branding/test_email.
   * Si el campo `testEmailTo` está vacío el backend envía al admin actual.
   */
  sendTestEmail(): void {
    if (this.isSendingTest) return;
    const target = (this.testEmailTo || '').trim();
    this.isSendingTest = true;
    const headers = this.auth.getAuthHeaders();
    this.http.post<{ status: string; to: string; smtp_configured: boolean }>(
      `${environment.apiUrl}/api/branding/test_email`,
      { to_email: target || null },
      { headers },
    )
      .pipe(takeUntil(this.destroy$))
      .subscribe({
        next: (res) => {
          this.isSendingTest = false;
          if (!res?.smtp_configured) {
            this.toast.warning(
              this.translate.instant('settings.msg_test_email_simulated', { to: res?.to }),
            );
          } else {
            this.toast.success(this.translate.instant('settings.msg_test_email_sent', { to: res.to }));
          }
          this.cdr.detectChanges();
        },
        error: (err) => {
          this.isSendingTest = false;
          this.toast.error(err?.error?.detail || this.translate.instant('settings.msg_test_email_failed'));
          this.cdr.detectChanges();
        },
      });
  }
}
