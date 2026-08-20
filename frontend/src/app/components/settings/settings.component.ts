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
import { CalendarsService, TarjetaProveedor } from '../../services/calendars.service';

interface PairStatus {
  conectada: boolean;
  plataforma: string | null;
  conectada_el: string | null;
  recibe_eventos: boolean;
  codigo_pendiente: boolean;
  codigo_expira_el: string | null;
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

  // ── Emparejamiento con la plataforma de la empresa ──────────────────
  // Todo el intercambio de credenciales se reduce a un código corto: la
  // clave de API la negocian los dos servidores y aquí no se ve nunca.
  pairStatus: PairStatus | null = null;
  pairCode: string | null = null;
  pairSecondsLeft = 0;
  pairCopied = false;
  pairBusy = false;
  private pairTimer: ReturnType<typeof setInterval> | null = null;
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
  // ── Calendarios ────────────────────────────────────────────────────
  // Las credenciales son de la aplicación, no de la persona: se registran
  // una vez y después cada quien conecta su cuenta con dos clics.
  //
  // El secreto se escribe aparte de la tarjeta (`calSecretos`) porque la
  // pantalla NUNCA lo recibe en claro —solo la pista `abcd…wxyz`—, y
  // guardarlo en blanco no debe borrar el que ya hay: quien vuelve aquí a
  // cambiar el ID de cliente no tiene el secreto delante.
  calProviders: TarjetaProveedor[] = [];
  calSecretos: Record<string, string> = {};
  calGuardando = '';
  calProbando = '';
  calResultado: Record<string, { ok: boolean; detalle: string }> = {};

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
    private calendarsApi: CalendarsService,
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
    this.stopPairTimer();
  }

  // ══════════════════════════════════════════════════════════════════
  // Emparejamiento
  // ══════════════════════════════════════════════════════════════════

  loadPairStatus(): void {
    this.http.get<PairStatus>(
      `${environment.apiUrl}/api/integrations/pairing/status`,
      { headers: this.auth.getAuthHeaders() },
    ).pipe(takeUntil(this.destroy$)).subscribe({
      next: (r) => { this.pairStatus = r; this.cdr.detectChanges(); },
      // Un fallo aquí no debe romper la pantalla entera de ajustes.
      error: () => { this.pairStatus = null; },
    });
  }

  createPairingCode(): void {
    if (this.pairBusy) return;
    this.pairBusy = true;
    this.http.post<{ codigo: string; expira_el: string; vigencia_min: number }>(
      `${environment.apiUrl}/api/integrations/pairing`, {},
      { headers: this.auth.getAuthHeaders() },
    ).pipe(takeUntil(this.destroy$)).subscribe({
      next: (r) => {
        this.pairBusy = false;
        this.pairCode = r.codigo;
        this.pairCopied = false;
        this.startPairCountdown(r.expira_el);
      },
      error: (e) => {
        this.pairBusy = false;
        this.toast.error(e?.error?.detail || this.translate.instant('pairing.error'));
      },
    });
  }

  copyPairingCode(): void {
    if (!this.pairCode) return;
    navigator.clipboard?.writeText(this.pairCode).then(
      () => {
        this.pairCopied = true;
        this.cdr.detectChanges();
        setTimeout(() => { this.pairCopied = false; this.cdr.detectChanges(); }, 2500);
      },
      () => this.toast.error(this.translate.instant('pairing.copy_failed')),
    );
  }

  disconnectPlatform(): void {
    if (this.pairBusy) return;
    if (!confirm(this.translate.instant('pairing.disconnect_confirm'))) return;
    this.pairBusy = true;
    this.http.delete<{ status: string }>(
      `${environment.apiUrl}/api/integrations/pairing`,
      { headers: this.auth.getAuthHeaders() },
    ).pipe(takeUntil(this.destroy$)).subscribe({
      next: () => {
        this.pairBusy = false;
        this.pairCode = null;
        this.stopPairTimer();
        this.loadPairStatus();
        this.toast.success(this.translate.instant('pairing.disconnected'));
      },
      error: (e) => {
        this.pairBusy = false;
        this.toast.error(e?.error?.detail || this.translate.instant('pairing.error'));
      },
    });
  }

  /** Cuenta atrás visible. Un código que caduca en silencio deja al que
   *  lo pega mirando un error sin saber por qué. */
  private startPairCountdown(expiraEl: string): void {
    this.stopPairTimer();
    const fin = new Date(expiraEl).getTime();
    const tick = () => {
      this.pairSecondsLeft = Math.max(0, Math.round((fin - Date.now()) / 1000));
      if (this.pairSecondsLeft <= 0) {
        this.pairCode = null;
        this.stopPairTimer();
        this.loadPairStatus();
      }
      this.cdr.detectChanges();
    };
    tick();
    this.pairTimer = setInterval(tick, 1000);
  }

  private stopPairTimer(): void {
    if (this.pairTimer) { clearInterval(this.pairTimer); this.pairTimer = null; }
  }

  get pairCountdown(): string {
    const m = Math.floor(this.pairSecondsLeft / 60);
    const sec = this.pairSecondsLeft % 60;
    return `${m}:${String(sec).padStart(2, '0')}`;
  }

  // ── Calendarios: cargar, guardar y comprobar ──────────────────────

  cargarProveedoresCalendario(): void {
    this.calendarsApi.proveedores().subscribe({
      next: r => {
        this.calProviders = r.proveedores;
        // La dirección de retorno se enseña ya escrita: si se deja
        // adivinar, casi nadie acierta con la barra final.
        for (const p of this.calProviders) {
          if (!p.redirect_uri) { p.redirect_uri = p.redirect_sugerido; }
        }
        this.cdr.detectChanges();
      },
      error: () => { /* la tarjeta se queda vacía y lo dice en pantalla */ },
    });
  }

  guardarProveedor(p: TarjetaProveedor): void {
    this.calGuardando = p.provider;
    const secreto = (this.calSecretos[p.provider] || '').trim();
    this.calendarsApi.guardarProveedor(p.provider, {
      client_id: p.client_id, redirect_uri: p.redirect_uri,
      tenant: p.tenant, data_center: p.data_center,
      ...(secreto ? { client_secret: secreto } : {}),
    }).subscribe({
      next: est => {
        this.calGuardando = '';
        this.calSecretos[p.provider] = '';
        Object.assign(p, est, { redirect_sugerido: p.redirect_sugerido, scopes: p.scopes });
        this.toast.success(`${p.etiqueta}: credenciales guardadas.`);
        this.cdr.detectChanges();
      },
      error: e => {
        this.calGuardando = '';
        this.toast.error(e?.error?.detail ?? 'No se pudieron guardar las credenciales.');
        this.cdr.detectChanges();
      },
    });
  }

  /** Pregunta al proveedor si reconoce la aplicación, sin conectar nada. */
  comprobarProveedor(p: TarjetaProveedor): void {
    this.calProbando = p.provider;
    this.calendarsApi.comprobarProveedor(p.provider).subscribe({
      next: r => {
        this.calProbando = '';
        this.calResultado[p.provider] = r;
        this.cdr.detectChanges();
      },
      error: () => {
        this.calProbando = '';
        this.calResultado[p.provider] = { ok: false, detalle: 'No se pudo comprobar.' };
        this.cdr.detectChanges();
      },
    });
  }

  copiar(texto: string): void {
    navigator.clipboard?.writeText(texto)
      .then(() => this.toast.success('Copiado.'))
      .catch(() => { /* sin portapapeles: el campo se puede seleccionar a mano */ });
  }

  trackByProvider(_i: number, p: TarjetaProveedor): string { return p.provider; }

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

    this.cargarProveedoresCalendario();
    this.loadShareSettings();
    this.loadPairStatus();

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
      // Los calendarios NO van aquí: sus credenciales se guardan por su
      // propio endpoint, que cifra el secreto. Mandarlas en este payload
      // las reescribiría en claro y pisaría lo cifrado.
    };

    this.settingsService.saveSettings(payload).subscribe({
      next: () => {
        this.isSaving = false;
        this.successMessage = this.translate.instant('settings.msg_config_saved');
        this.toast.success(this.successMessage);
        this.cargarProveedoresCalendario();
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
