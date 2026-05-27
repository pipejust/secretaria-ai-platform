import { Component, OnInit, OnDestroy, ChangeDetectorRef, ViewChild, ElementRef, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { TranslateModule } from '@ngx-translate/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { Subject } from 'rxjs';
import { takeUntil } from 'rxjs/operators';
import { AuthService } from '../../services/auth.service';
import { ToastService } from '../../services/toast.service';
import { NotificationService } from '../../services/notification.service';
import { PreferencesService } from '../../services/preferences.service';
import { PasswordInputComponent } from '../shared/password-input/password-input.component';
import { LanguageSelectorComponent } from '../shared/language-selector/language-selector.component';
import { environment } from '../../../environments/environment';

interface ActivityEntry {
    id: number;
    action: string;
    label: string;
    icon: string;
    resource_type: string | null;
    resource_id: string | null;
    created_at: string;
    ip: string | null;
}

interface NotifPrefs {
    email_enabled: boolean;
    push_enabled: boolean;
    meeting_reminders: boolean;
    task_assigned: boolean;
    session_processed: boolean;
    weekly_report: boolean;
    security_alerts: boolean;
}

const DEFAULT_NOTIF_PREFS: NotifPrefs = {
    email_enabled:     true,
    push_enabled:      true,
    meeting_reminders: true,
    task_assigned:     true,
    session_processed: true,
    weekly_report:     false,
    security_alerts:   true,
};

type TwoFactorModalStep = 'idle' | 'enable-code' | 'disable-password';

@Component({
    selector: 'app-profile',
    standalone: true,
    imports: [CommonModule, FormsModule, PasswordInputComponent, LanguageSelectorComponent, TranslateModule],
    templateUrl: './profile.component.html',
    styleUrls: ['./profile.component.css'],
})
export class ProfileComponent implements OnInit, OnDestroy {
    private readonly authService = inject(AuthService);
    private readonly toast = inject(ToastService);
    private readonly notifications = inject(NotificationService);
    private readonly preferences = inject(PreferencesService);
    private readonly router = inject(Router);
    private readonly cdr = inject(ChangeDetectorRef);

    @ViewChild('avatarInput') avatarInput?: ElementRef<HTMLInputElement>;

    user: any = null;

    profile = {
        full_name: '',
        email: '',
        phone: '',
        position: '',
        department: '',
        location: '',
        bio: '',
    };
    private pristine = { ...this.profile };

    pwd = { current: '', new: '', confirm: '' };
    showPwd = { current: false, new: false, confirm: false };

    msg = '';
    isError = false;
    isSavingProfile = false;
    isChangingPassword = false;
    isSavingNotif = false;
    isLoadingActivity = true;
    isUploadingAvatar = false;

    uiPrefs = this.preferences.get();
    notif: NotifPrefs = { ...DEFAULT_NOTIF_PREFS };

    unreadCount = 0;
    activity: ActivityEntry[] = [];

    // ── 2FA modal state ─────────────────────────────────────────
    twoFactorModalStep: TwoFactorModalStep = 'idle';
    twoFactorCode = '';
    twoFactorPassword = '';
    twoFactorError = '';
    twoFactorEmailSent = '';
    isProcessing2FA = false;

    private readonly destroy$ = new Subject<void>();

    ngOnInit(): void {
        this.authService.currentUser$
            .pipe(takeUntil(this.destroy$))
            .subscribe((u) => {
                this.user = u;
                if (u) this.applyUserToForm(u);
                this.cdr.detectChanges();
            });
        this.authService.loadUserProfile();

        this.notifications.unreadCount$
            .pipe(takeUntil(this.destroy$))
            .subscribe((n) => { this.unreadCount = n; this.cdr.detectChanges(); });
        this.notifications.refreshUnreadCount();

        this.refreshActivity();

        this.preferences.prefs$
            .pipe(takeUntil(this.destroy$))
            .subscribe((p) => { this.uiPrefs = p; this.cdr.detectChanges(); });
    }

    ngOnDestroy(): void { this.destroy$.next(); this.destroy$.complete(); }

    private applyUserToForm(u: any): void {
        this.profile = {
            full_name:  u.full_name || '',
            email:      u.email || '',
            phone:      u.phone || '',
            position:   u.position || u.role || '',
            department: u.department || '',
            location:   u.location || '',
            bio:        u.bio || '',
        };
        this.pristine = { ...this.profile };
        if (u.notifications) {
            this.notif = { ...DEFAULT_NOTIF_PREFS, ...u.notifications };
        }
    }

    get isDirty(): boolean {
        return (
            this.profile.full_name !== this.pristine.full_name ||
            this.profile.phone     !== this.pristine.phone     ||
            this.profile.position  !== this.pristine.position  ||
            this.profile.department !== this.pristine.department ||
            this.profile.location  !== this.pristine.location  ||
            this.profile.bio       !== this.pristine.bio
        );
    }

    // ===== Avatar URL builder (con apiUrl) =====
    get avatarSrc(): string | null {
        const raw = this.user?.avatar_url;
        if (!raw) return null;
        if (raw.startsWith('http')) return raw;
        return `${environment.apiUrl}${raw}`;
    }

    // ===== 2FA helpers =====
    get is2FAEnabled(): boolean {
        return !!this.user?.two_factor?.enabled;
    }

    // ===== Helpers visuales =====
    getInitials(name: string): string {
        if (!name) return 'U';
        const words = name.trim().split(/\s+/);
        if (words.length >= 2) return (words[0].charAt(0) + words[1].charAt(0)).toUpperCase();
        return name.substring(0, 2).toUpperCase();
    }

    get profileCompletePct(): number {
        const fields = [
            this.profile.full_name,
            this.profile.email,
            this.profile.phone,
            this.profile.position,
            this.profile.department,
            this.profile.location,
            this.profile.bio,
        ];
        const filled = fields.filter((f) => !!(f || '').toString().trim()).length;
        return Math.round((filled / fields.length) * 100);
    }

    get profileCompleteLabel(): string {
        const pct = this.profileCompletePct;
        if (pct >= 90) return 'Excelente, tu perfil está completo.';
        if (pct >= 70) return 'Buen trabajo, tu perfil está casi completo.';
        if (pct >= 40) return 'Tu perfil está en camino. Completa los datos restantes.';
        return 'Completa tu perfil para que tu equipo te identifique mejor.';
    }

    get profileMissingHint(): string {
        const missing: string[] = [];
        if (!this.profile.phone)      missing.push('teléfono');
        if (!this.profile.department) missing.push('departamento');
        if (!this.profile.location)   missing.push('ubicación');
        if (!this.profile.bio)        missing.push('biografía');
        if (!missing.length) return 'Todos tus datos están al día.';
        return `Faltan: ${missing.join(', ')}.`;
    }

    get securityLevelLabel(): string {
        if (this.is2FAEnabled) return 'Excelente';
        return 'Alta';
    }

    get securityHintLabel(): string {
        if (this.is2FAEnabled) return 'Autenticación en dos pasos activa';
        return 'Activa la autenticación en dos pasos';
    }

    get activeNotifCount(): number {
        return Object.values(this.notif).filter((v) => !!v).length;
    }
    get totalNotifCount(): number {
        return Object.keys(this.notif).length;
    }

    get lastLoginLabel(): string {
        const raw = this.user?.last_login_at;
        if (!raw) return 'Sin registrar';
        const d = new Date(raw);
        if (isNaN(d.getTime())) return 'Sin registrar';
        return d.toLocaleString('es-CO', {
            day: '2-digit', month: 'long', year: 'numeric',
            hour: '2-digit', minute: '2-digit',
        });
    }

    get lastLoginAgo(): string {
        const raw = this.user?.last_login_at;
        if (!raw) return '—';
        const d = new Date(raw);
        if (isNaN(d.getTime())) return '—';
        const diff = Date.now() - d.getTime();
        const hours = Math.floor(diff / 3_600_000);
        if (hours < 1) return 'Hace menos de una hora';
        if (hours < 24) return `Hace ${hours} h`;
        const days = Math.floor(hours / 24);
        if (days < 30) return `Hace ${days} día${days === 1 ? '' : 's'}`;
        const months = Math.floor(days / 30);
        return `Hace ${months} mes${months === 1 ? '' : 'es'}`;
    }

    get createdAtLabel(): string {
        const raw = this.user?.created_at;
        if (!raw) return '—';
        const d = new Date(raw);
        if (isNaN(d.getTime())) return '—';
        return d.toLocaleDateString('es-CO', { day: '2-digit', month: 'long', year: 'numeric' });
    }

    get updatedAtLabel(): string {
        const raw = this.user?.updated_at || this.user?.created_at;
        if (!raw) return '—';
        const d = new Date(raw);
        if (isNaN(d.getTime())) return '—';
        return d.toLocaleDateString('es-CO', { day: '2-digit', month: 'long', year: 'numeric' });
    }

    formatActivityDate(raw: string): string {
        if (!raw) return '—';
        const d = new Date(raw);
        if (isNaN(d.getTime())) return raw;
        return d.toLocaleString('es-CO', {
            day: '2-digit', month: 'short', year: 'numeric',
            hour: '2-digit', minute: '2-digit',
        });
    }

    // ============================================================
    // Cancelar / Guardar perfil
    // ============================================================
    cancelChanges(): void {
        this.profile = { ...this.pristine };
        this.toast.info('Cambios descartados.');
        this.cdr.detectChanges();
    }

    saveProfile(): void {
        if (!this.isDirty) {
            this.toast.info('No hay cambios pendientes.');
            return;
        }
        if (!this.profile.full_name.trim()) {
            this.toast.error('El nombre no puede estar vacío.');
            return;
        }
        this.isSavingProfile = true;
        this.authService.updateProfile({
            full_name:  this.profile.full_name.trim(),
            phone:      this.profile.phone.trim(),
            position:   this.profile.position.trim(),
            department: this.profile.department.trim(),
            location:   this.profile.location.trim(),
            bio:        this.profile.bio.trim(),
        })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (updated) => {
                    this.applyUserToForm(updated);
                    this.isSavingProfile = false;
                    this.toast.success('Información personal guardada.');
                    this.refreshActivity();
                    this.cdr.detectChanges();
                },
                error: (err) => {
                    this.isSavingProfile = false;
                    const detail = err?.error?.detail || 'No se pudo guardar el perfil.';
                    this.toast.error(detail);
                    this.cdr.detectChanges();
                },
            });
    }

    // ============================================================
    // Avatar upload
    // ============================================================
    openAvatarPicker(): void {
        this.avatarInput?.nativeElement.click();
    }

    onAvatarSelected(event: Event): void {
        const input = event.target as HTMLInputElement;
        const file = input.files?.[0];
        // Limpia el input para que seleccionar el mismo archivo dos veces dispare el change.
        if (input) input.value = '';
        if (!file) return;
        if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) {
            this.toast.error('Formato no permitido. Usa PNG, JPG o WebP.');
            return;
        }
        if (file.size > 2 * 1024 * 1024) {
            this.toast.error('La imagen pesa más de 2 MB.');
            return;
        }
        this.isUploadingAvatar = true;
        this.authService.uploadAvatar(file)
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: () => {
                    this.isUploadingAvatar = false;
                    this.toast.success('Foto de perfil actualizada.');
                    this.refreshActivity();
                    this.cdr.detectChanges();
                },
                error: (err) => {
                    this.isUploadingAvatar = false;
                    this.toast.error(err?.error?.detail || 'No se pudo subir la imagen.');
                    this.cdr.detectChanges();
                },
            });
    }

    removeAvatar(): void {
        if (!this.user?.avatar_url) return;
        this.authService.deleteAvatar()
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: () => this.toast.success('Foto de perfil eliminada.'),
                error: () => this.toast.error('No se pudo eliminar la foto.'),
            });
    }

    // ============================================================
    // Cambio de contraseña (con ojo show/hide)
    // ============================================================
    toggleShowPwd(field: 'current' | 'new' | 'confirm'): void {
        this.showPwd = { ...this.showPwd, [field]: !this.showPwd[field] };
    }

    changePassword(): void {
        this.msg = '';
        this.isError = false;

        if (!this.pwd.current || !this.pwd.new || !this.pwd.confirm) {
            this.isError = true;
            this.msg = 'Por favor completa todos los campos.';
            return;
        }
        if (this.pwd.new.length < 6) {
            this.isError = true;
            this.msg = 'La nueva contraseña debe tener al menos 6 caracteres.';
            return;
        }
        if (this.pwd.new !== this.pwd.confirm) {
            this.isError = true;
            this.msg = 'Las nuevas contraseñas no coinciden.';
            return;
        }
        this.isChangingPassword = true;
        this.authService.changePassword(this.pwd.current, this.pwd.new)
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (res) => {
                    this.msg = res?.msg || 'Contraseña actualizada correctamente.';
                    this.isError = false;
                    this.pwd = { current: '', new: '', confirm: '' };
                    this.isChangingPassword = false;
                    this.toast.success('Contraseña actualizada.');
                    this.refreshActivity();
                    this.cdr.detectChanges();
                },
                error: (err) => {
                    this.isError = true;
                    this.msg = err?.error?.detail || 'Error al actualizar contraseña.';
                    this.isChangingPassword = false;
                    this.cdr.detectChanges();
                },
            });
    }

    // ============================================================
    // 2FA — modal de activación / desactivación
    // ============================================================
    openTwoFactorModal(): void {
        this.twoFactorCode = '';
        this.twoFactorPassword = '';
        this.twoFactorError = '';
        if (this.is2FAEnabled) {
            this.twoFactorModalStep = 'disable-password';
        } else {
            this.isProcessing2FA = true;
            this.authService.init2FA()
                .pipe(takeUntil(this.destroy$))
                .subscribe({
                    next: (r) => {
                        this.isProcessing2FA = false;
                        this.twoFactorEmailSent = r?.email || this.user?.email || '';
                        this.twoFactorModalStep = 'enable-code';
                        this.toast.success('Te enviamos un código a tu correo.');
                        this.cdr.detectChanges();
                    },
                    error: (err) => {
                        this.isProcessing2FA = false;
                        this.toast.error(err?.error?.detail || 'No se pudo iniciar la activación 2FA.');
                        this.cdr.detectChanges();
                    },
                });
        }
    }

    closeTwoFactorModal(): void {
        this.twoFactorModalStep = 'idle';
        this.twoFactorCode = '';
        this.twoFactorPassword = '';
        this.twoFactorError = '';
        this.isProcessing2FA = false;
    }

    confirmTwoFactor(): void {
        if (!this.twoFactorCode || this.twoFactorCode.trim().length < 6) {
            this.twoFactorError = 'Introduce el código de 6 dígitos.';
            return;
        }
        this.isProcessing2FA = true;
        this.twoFactorError = '';
        this.authService.confirm2FA(this.twoFactorCode.trim())
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: () => {
                    this.isProcessing2FA = false;
                    this.toast.success('Autenticación en dos pasos activada.');
                    this.refreshActivity();
                    this.closeTwoFactorModal();
                },
                error: (err) => {
                    this.isProcessing2FA = false;
                    this.twoFactorError = err?.error?.detail || 'Código incorrecto.';
                    this.cdr.detectChanges();
                },
            });
    }

    disableTwoFactor(): void {
        if (!this.twoFactorPassword) {
            this.twoFactorError = 'Introduce tu contraseña para desactivar 2FA.';
            return;
        }
        this.isProcessing2FA = true;
        this.twoFactorError = '';
        this.authService.disable2FA(this.twoFactorPassword)
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: () => {
                    this.isProcessing2FA = false;
                    this.toast.success('Autenticación en dos pasos desactivada.');
                    this.refreshActivity();
                    this.closeTwoFactorModal();
                },
                error: (err) => {
                    this.isProcessing2FA = false;
                    this.twoFactorError = err?.error?.detail || 'No se pudo desactivar 2FA.';
                    this.cdr.detectChanges();
                },
            });
    }

    resend2FACode(): void {
        this.isProcessing2FA = true;
        this.authService.init2FA()
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: () => {
                    this.isProcessing2FA = false;
                    this.toast.success('Nuevo código enviado.');
                    this.cdr.detectChanges();
                },
                error: () => {
                    this.isProcessing2FA = false;
                    this.toast.error('No se pudo reenviar el código.');
                    this.cdr.detectChanges();
                },
            });
    }

    // ============================================================
    // Notificaciones
    // ============================================================
    onNotifToggle<K extends keyof NotifPrefs>(key: K, value: boolean): void {
        const prev = this.notif[key];
        this.notif = { ...this.notif, [key]: value };
        this.isSavingNotif = true;
        this.authService.updateNotificationPrefs({ [key]: value } as any)
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (server) => {
                    this.notif = { ...this.notif, ...server };
                    this.isSavingNotif = false;
                    this.toast.success('Preferencias de notificación actualizadas.');
                    this.cdr.detectChanges();
                },
                error: (err) => {
                    this.notif = { ...this.notif, [key]: prev };
                    this.isSavingNotif = false;
                    const detail = err?.error?.detail || 'No se pudieron guardar las preferencias.';
                    this.toast.error(detail);
                    this.cdr.detectChanges();
                },
            });
    }

    markAllNotificationsRead(): void {
        this.notifications.markAllRead()
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (r) => {
                    const n = r?.marked ?? 0;
                    if (n > 0) this.toast.success(`${n} notificación${n === 1 ? '' : 'es'} marcada${n === 1 ? '' : 's'} como leída${n === 1 ? '' : 's'}.`);
                    else this.toast.info('No hay notificaciones pendientes.');
                },
                error: () => this.toast.error('No se pudo marcar las notificaciones.'),
            });
    }

    onUiPrefChange(key: string, value: any): void {
        (this.preferences as any).set(key, value);
        this.toast.success('Preferencia actualizada.');
    }

    // ============================================================
    // Actividad reciente
    // ============================================================
    refreshActivity(): void {
        this.isLoadingActivity = true;
        this.authService.getMyActivity(15)
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (r) => {
                    this.activity = r?.items || [];
                    this.isLoadingActivity = false;
                    this.cdr.detectChanges();
                },
                error: () => {
                    this.activity = [];
                    this.isLoadingActivity = false;
                    this.cdr.detectChanges();
                },
            });
    }

    // ============================================================
    // Navegación interactiva
    // ============================================================
    focusFirstEmpty(): void {
        type ProfileKey = 'full_name' | 'phone' | 'position' | 'department' | 'location' | 'bio';
        const fieldOrder: Array<{ key: ProfileKey; selector: string }> = [
            { key: 'phone',      selector: '#pr-phone' },
            { key: 'department', selector: '#pr-department' },
            { key: 'position',   selector: '#pr-position' },
            { key: 'location',   selector: '#pr-location' },
            { key: 'bio',        selector: '#pr-bio' },
            { key: 'full_name',  selector: '#pr-fullname' },
        ];
        const next = fieldOrder.find((f) => !this.profile[f.key]);
        const sel = next?.selector || '#pr-fullname';
        const el = document.querySelector<HTMLElement>(sel);
        if (el) {
            el.scrollIntoView({ behavior: 'smooth', block: 'center' });
            setTimeout(() => el.focus(), 350);
        }
    }

    scrollToNotifications(): void {
        document.querySelector<HTMLElement>('#pr-section-notif')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    scrollToSecurity(): void {
        document.querySelector<HTMLElement>('#pr-section-security')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    scrollToActivity(): void {
        document.querySelector<HTMLElement>('#pr-section-activity')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    onOpenHelp(): void {
        this.router.navigate(['/help']);
    }
}
