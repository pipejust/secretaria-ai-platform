import { Component, OnInit, OnDestroy, ChangeDetectorRef, HostListener, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { FormsModule } from '@angular/forms';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../environments/environment';
import { AuthService } from '../../services/auth.service';
import { ToastService } from '../../services/toast.service';
import { PasswordInputComponent } from '../shared/password-input/password-input.component';

interface TenantOut {
    id: number;
    slug: string;
    name: string;
    domain: string | null;
    is_active: boolean;
    user_count: number;
    created_at: string;
    /** Branding parseado del backend (data URLs base64 desde branding_json). */
    logo_data_url?: string | null;
    logo_dark_data_url?: string | null;
    icon_data_url?: string | null;
    primary_color?: string | null;
    company_name?: string | null;
    /** Idioma por defecto del workspace ('es', 'ca', 'en'). Lo usa el
     *  pipeline IA para generar tareas/resumen/decisiones en ese idioma. */
    default_language?: string | null;
    // Aliases tolerantes que algunos templates referencian.
    logo_url?: string | null;
    icon_url?: string | null;
    /** Campos opcionales del backend (futuros). */
    admin_email?: string | null;
    admin_full_name?: string | null;
    project_count?: number;
    session_count?: number;
    integration_count?: number;
}

interface CreateForm {
    slug: string;
    name: string;
    domain: string;
    admin_email: string;
    admin_password: string;
    admin_full_name: string;
    /** Si true: el backend autogenera password temporal + el admin debe
     *  cambiarla en el primer login. Si false: admin_password es manual. */
    admin_must_change_password: boolean;
    // Identidad de la empresa (espejo de /admin/branding).
    company_tagline: string;
    company_email: string;
    company_website: string;       // OBLIGATORIO — su SLD valida usuarios
    company_phone: string;
    company_address: string;
    // Paleta visual.
    primary_color: string;
    secondary_color: string;
    accent_color: string;
    // Assets gráficos.
    logo_data_url: string;
    logo_dark_data_url: string;
    icon_data_url: string;
    favicon_data_url: string;
    /** Idioma por defecto del workspace — define el idioma de toda
     *  la salida de IA (tasks, resumen, decisiones, headers). */
    default_language: 'es' | 'ca' | 'en';
}

const MAX_BRAND_FILE_BYTES = 2 * 1024 * 1024;

@Component({
    selector: 'app-super-tenants',
    standalone: true,
    imports: [CommonModule, FormsModule, PasswordInputComponent, TranslateModule],
    templateUrl: './super-tenants.component.html',
    styleUrls: ['./super-tenants.component.css'],
})
export class SuperTenantsComponent implements OnInit, OnDestroy {
    private http = inject(HttpClient);
    private auth = inject(AuthService);
    private toast = inject(ToastService);
    private cdr = inject(ChangeDetectorRef);
    private translate = inject(TranslateService);
    private apiUrl = `${environment.apiUrl}/api/super/tenants`;

    tenants: TenantOut[] = [];
    loading = false;
    errorMsg = '';
    successMsg = '';

    // Modal create.
    showCreate = false;
    isCreating = false;
    form: CreateForm = this._emptyForm();

    // Modal edit.
    showEdit = false;
    isEditing = false;
    editForm: {
        id?: number;
        slug: string;             // visible read-only en la UI
        name: string;
        domain: string;
        is_active: boolean;
        company_name: string;
        primary_color: string;
        logo_data_url: string;         // logo claro
        logo_dark_data_url: string;    // logo oscuro (variante para fondos dark)
        icon_data_url: string;
        /** Idioma por defecto del workspace. Editable desde el modal
         *  edit del super-admin — cambia el idioma que el pipeline IA
         *  va a usar para las próximas curaciones del tenant. */
        default_language: 'es' | 'ca' | 'en';
    } = {
        slug: '', name: '', domain: '', is_active: true,
        company_name: '', primary_color: '',
        logo_data_url: '', logo_dark_data_url: '', icon_data_url: '',
        default_language: 'es',
    };
    /** Snapshot inicial para detectar qué cambió y solo mandar lo necesario. */
    private editFormInitial: any = null;

    // Modal de ver detalle.
    showDetail = false;

    // Modal de "Editar dominio" (sustituye al prompt nativo).
    showDomainModal = false;
    isSavingDomain = false;
    domainTarget: TenantOut | null = null;
    domainValue = '';

    // Filtros + búsqueda.
    searchText = '';
    statusFilter: '' | 'active' | 'inactive' = '';
    domainFilter: '' | 'with' | 'without' = '';
    usersFilter: '' | 'zero' | 'low' | 'medium' | 'high' = '';
    dateFilter: '' | 'last30' | 'last90' | 'older' = '';
    showFilters = false;
    viewMode: 'list' | 'grid' = 'list';

    // Paginación.
    currentPage = 1;
    pageLimit = 10;

    // Panel derecho.
    selected: TenantOut | null = null;
    showSelectedPanel = true;

    // Kebab.
    openRowMenuId: number | null = null;

    // Confirm dialog reutilizable.
    showConfirmModal = false;
    confirmDialog: {
        title: string;
        message: string;
        confirmLabel: string;
        confirmVariant: 'danger' | 'warning' | 'primary';
        action: () => void;
    } | null = null;

    ngOnInit(): void { this.refresh(); }
    ngOnDestroy(): void {}

    @HostListener('document:click')
    onDocClick(): void { this.openRowMenuId = null; }

    @HostListener('document:keydown.escape')
    onEsc(): void {
        this.openRowMenuId = null;
        if (this.showCreate) this.showCreate = false;
        if (this.showEdit) this.showEdit = false;
        if (this.showDetail) this.showDetail = false;
        if (this.showDomainModal) this.closeDomainModal();
        if (this.showConfirmModal) this.cancelConfirm();
    }

    // ============================================================
    // Helpers
    // ============================================================
    private _headers(): HttpHeaders {
        return new HttpHeaders({ Authorization: `Bearer ${this.auth.token || ''}` });
    }

    private _emptyForm(): CreateForm {
        return {
            slug: '', name: '', domain: '',
            admin_email: '', admin_password: '', admin_full_name: '',
            admin_must_change_password: true,  // recomendado para invitaciones
            company_tagline: '', company_email: '', company_website: '',
            company_phone: '', company_address: '',
            // Defaults Acten — el admin puede sobreescribirlos al crear.
            primary_color: '#1F2A52', secondary_color: '#3D6B5E', accent_color: '#C8993B',
            logo_data_url: '', logo_dark_data_url: '',
            icon_data_url: '', favicon_data_url: '',
            // Default 'es' por backwards-compat. El super-admin puede
            // cambiarlo a 'ca' o 'en' al crear si el workspace nuevo
            // opera en otro idioma — afecta directo la salida del
            // pipeline IA del nuevo tenant.
            default_language: 'es',
        };
    }

    private async _fileToDataUrl(file: File): Promise<string> {
        if (!file.type.startsWith('image/')) {
            this.errorMsg = this.translate.instant('tenants.msg_file_not_image', { name: file.name });
            return '';
        }
        if (file.size > MAX_BRAND_FILE_BYTES) {
            this.errorMsg = this.translate.instant('tenants.msg_file_too_large', {
                name: file.name,
                size: (file.size / 1024 / 1024).toFixed(1),
                max: MAX_BRAND_FILE_BYTES / 1024 / 1024,
            });
            return '';
        }
        return await new Promise<string>((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(String(reader.result || ''));
            reader.onerror = () => reject(reader.error);
            reader.readAsDataURL(file);
        });
    }

    async onLogoSelected(ev: Event): Promise<void> {
        this.errorMsg = '';
        const input = ev.target as HTMLInputElement;
        const file = input.files?.[0];
        if (!file) return;
        const url = await this._fileToDataUrl(file);
        if (url) this.form.logo_data_url = url;
        input.value = '';
    }
    async onLogoDarkSelected(ev: Event): Promise<void> {
        this.errorMsg = '';
        const input = ev.target as HTMLInputElement;
        const file = input.files?.[0];
        if (!file) return;
        const url = await this._fileToDataUrl(file);
        if (url) this.form.logo_dark_data_url = url;
        input.value = '';
    }
    async onIconSelected(ev: Event): Promise<void> {
        this.errorMsg = '';
        const input = ev.target as HTMLInputElement;
        const file = input.files?.[0];
        if (!file) return;
        const url = await this._fileToDataUrl(file);
        if (url) this.form.icon_data_url = url;
        input.value = '';
    }

    /** Soporta drag & drop sobre las zonas de carga del modal. */
    async onLogoDrop(ev: DragEvent): Promise<void> {
        ev.preventDefault();
        const file = ev.dataTransfer?.files?.[0];
        if (!file) return;
        const url = await this._fileToDataUrl(file);
        if (url) this.form.logo_data_url = url;
    }
    async onLogoDarkDrop(ev: DragEvent): Promise<void> {
        ev.preventDefault();
        const file = ev.dataTransfer?.files?.[0];
        if (!file) return;
        const url = await this._fileToDataUrl(file);
        if (url) this.form.logo_dark_data_url = url;
    }
    async onIconDrop(ev: DragEvent): Promise<void> {
        ev.preventDefault();
        const file = ev.dataTransfer?.files?.[0];
        if (!file) return;
        const url = await this._fileToDataUrl(file);
        if (url) this.form.icon_data_url = url;
    }
    onDragOver(ev: DragEvent): void { ev.preventDefault(); }

    async onFaviconSelected(ev: Event): Promise<void> {
        this.errorMsg = '';
        const input = ev.target as HTMLInputElement;
        const file = input.files?.[0];
        if (!file) return;
        const url = await this._fileToDataUrl(file);
        if (url) this.form.favicon_data_url = url;
        input.value = '';
    }
    clearLogo(): void { this.form.logo_data_url = ''; }
    clearLogoDark(): void { this.form.logo_dark_data_url = ''; }
    clearIcon(): void { this.form.icon_data_url = ''; }
    clearFavicon(): void { this.form.favicon_data_url = ''; }

    // ============================================================
    // Backend
    // ============================================================
    async refresh(): Promise<void> {
        this.loading = true;
        this.errorMsg = '';
        try {
            const data = await firstValueFrom(
                this.http.get<TenantOut[]>(`${this.apiUrl}/`, { headers: this._headers() }),
            );
            this.tenants = data || [];
            // Mantener selección si existe; si no, primera empresa.
            if (this.selected) {
                const stillThere = this.tenants.find((t) => t.id === this.selected!.id);
                this.selected = stillThere || this.tenants[0] || null;
            } else if (this.tenants.length) {
                this.selected = this.tenants[0];
            }
        } catch (err: any) {
            this.errorMsg = err?.error?.detail || this.translate.instant('tenants.msg_load_failed');
        } finally {
            this.loading = false;
            this.cdr.detectChanges();
        }
    }

    openCreateModal(): void {
        this.form = this._emptyForm();
        this.errorMsg = ''; this.successMsg = '';
        this.showCreate = true;
    }

    closeCreateModal(): void {
        this.showCreate = false;
        this.errorMsg = ''; this.successMsg = '';
    }

    private validateCreate(): string | null {
        const f = this.form;
        if (!f.slug.trim()) return this.translate.instant('tenants.msg_slug_required');
        if (!/^[a-z0-9-]+$/.test(f.slug.trim().toLowerCase())) {
            return this.translate.instant('tenants.msg_invalid_slug_chars');
        }
        if (!f.name.trim()) return this.translate.instant('tenants.msg_name_required');
        // Sitio web obligatorio — su SLD se usa luego para validar el email
        // de los usuarios que el admin del tenant cree.
        const website = (f.company_website || '').trim();
        if (!website) {
            return this.translate.instant('tenants.msg_website_required');
        }
        if (!/^(https?:\/\/)?[a-z0-9.-]+\.[a-z]{2,}/i.test(website)) {
            return this.translate.instant('tenants.msg_website_invalid');
        }
        if (!f.admin_email.trim()) return this.translate.instant('tenants.msg_admin_email_required');
        if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(f.admin_email.trim())) {
            return this.translate.instant('tenants.msg_admin_email_invalid');
        }
        // Si NO está activado "forzar cambio", admin_password es obligatorio
        // y mínimo 8 chars. Si SÍ está activado, el backend autogenera.
        if (!f.admin_must_change_password) {
            if (!f.admin_password || f.admin_password.length < 8) {
                return this.translate.instant('tenants.msg_initial_password_min');
            }
        }
        if (!f.admin_full_name.trim()) return this.translate.instant('tenants.msg_admin_name_required');
        // Color hex check — solo si vienen no-vacíos (defaults Acten ya son válidos).
        const hex = /^#[0-9A-Fa-f]{6}$/;
        const colorChecks: ReadonlyArray<readonly [string, string]> = [
            [this.translate.instant('tenants.msg_color_primary_lbl'), f.primary_color],
            [this.translate.instant('tenants.msg_color_secondary_lbl'), f.secondary_color],
            [this.translate.instant('tenants.msg_color_accent_lbl'), f.accent_color],
        ];
        for (const [label, v] of colorChecks) {
            if (v && !hex.test(v)) {
                return this.translate.instant('tenants.msg_color_invalid', { label });
            }
        }
        return null;
    }

    /** Modal post-creación con la URL de acceso para que el admin la copie. */
    createdSuccess: {
        tenant: TenantOut | null;
        accessUrl: string;
        adminEmail: string;
        adminName: string;
        /** Password temporal generada por el backend si admin_must_change_password=true */
        temporaryPassword: string | null;
        copiedUrl: boolean;
        copiedEmail: boolean;
        copiedPassword: boolean;
    } = {
        tenant: null,
        accessUrl: '',
        adminEmail: '',
        adminName: '',
        temporaryPassword: null,
        copiedUrl: false,
        copiedEmail: false,
        copiedPassword: false,
    };

    /** Construye la URL pública de acceso para un tenant nuevo.
     *  En prod: https://admin.acten.app/t/{slug}/login.
     *  En dev local: usa el origen actual + /t/{slug}/login. */
    private _buildTenantAccessUrl(slug: string): string {
        if (typeof window === 'undefined') return `/t/${slug}/login`;
        const host = window.location.hostname;
        if (host === 'localhost' || host.startsWith('127.') || host.endsWith('.local')) {
            return `${window.location.origin}/t/${slug}/login`;
        }
        // En producción siempre forzamos admin.acten.app para que sea consistente
        // sin importar desde dónde se cree el tenant.
        return `https://admin.acten.app/t/${slug}/login`;
    }

    async createTenant(): Promise<void> {
        const err = this.validateCreate();
        if (err) { this.errorMsg = err; return; }
        this.isCreating = true;
        this.errorMsg = '';
        this.successMsg = '';
        try {
            const adminEmail = this.form.admin_email.trim().toLowerCase();
            const adminName = this.form.admin_full_name.trim();
            const out = await firstValueFrom(
                this.http.post<TenantOut & { temporary_password?: string }>(`${this.apiUrl}/`, {
                    slug: this.form.slug.trim().toLowerCase(),
                    name: this.form.name.trim(),
                    domain: this.form.domain.trim() || null,
                    admin_email: adminEmail,
                    // En modo "forzar cambio" no mandamos password — backend la genera.
                    admin_password: this.form.admin_must_change_password ? undefined : this.form.admin_password,
                    admin_must_change_password: this.form.admin_must_change_password,
                    admin_full_name: adminName,
                    // Identidad de la empresa (espejo de /admin/branding).
                    company_website: this.form.company_website.trim(),
                    company_tagline: this.form.company_tagline.trim() || undefined,
                    company_email: this.form.company_email.trim() || undefined,
                    company_phone: this.form.company_phone.trim() || undefined,
                    company_address: this.form.company_address.trim() || undefined,
                    // Paleta (siempre con defaults Acten en el form).
                    primary_color: this.form.primary_color || undefined,
                    secondary_color: this.form.secondary_color || undefined,
                    accent_color: this.form.accent_color || undefined,
                    // Assets gráficos.
                    logo_data_url: this.form.logo_data_url || undefined,
                    logo_dark_data_url: this.form.logo_dark_data_url || undefined,
                    icon_data_url: this.form.icon_data_url || undefined,
                    favicon_data_url: this.form.favicon_data_url || undefined,
                    // Idioma del workspace — backend lo persiste en
                    // Tenant.default_language y manda al pipeline IA.
                    default_language: this.form.default_language,
                }, { headers: this._headers() }),
            );
            this.tenants = [...this.tenants, out];
            this.toast.success(this.translate.instant('tenants.msg_tenant_created_toast', { name: out.name }));
            this.selected = out;
            this.showCreate = false;
            this.form = this._emptyForm();
            // Abrir modal de éxito con la URL de acceso prominente.
            // Si el backend autogeneró password, la mostramos prominente para
            // que el super-admin la copie y la pueda compartir si el email no llega.
            this.createdSuccess = {
                tenant: out,
                accessUrl: this._buildTenantAccessUrl(out.slug),
                adminEmail,
                adminName,
                temporaryPassword: out?.temporary_password ?? null,
                copiedUrl: false,
                copiedEmail: false,
                copiedPassword: false,
            };
        } catch (err: any) {
            this.errorMsg = err?.error?.detail || this.translate.instant('tenants.msg_create_failed');
        } finally {
            this.isCreating = false;
            this.cdr.detectChanges();
        }
    }

    /** Reenvía invitación al primer admin de un tenant — regenera password
     *  temporal y envía email. Useful cuando el admin perdió el correo
     *  original o no le llegó. */
    resendTenantInvitation(t: TenantOut, evt?: Event): void {
        if (evt) { evt.stopPropagation(); evt.preventDefault(); }
        this.closeRowMenu();
        if (!confirm(this.translate.instant('tenants.msg_resend_confirm', { name: t.name }))) return;
        firstValueFrom(
            this.http.post<{ admin_email?: string; temporary_password?: string }>(
                `${this.apiUrl}/${t.slug}/resend-invitation`,
                {},
                { headers: this._headers() },
            ),
        ).then((res) => {
            this.toast.success(this.translate.instant('tenants.msg_invitation_resent', {
                email: res?.admin_email || this.translate.instant('tenants.msg_invitation_resent_fallback_admin'),
            }));
            if (res?.temporary_password) {
                // Mostramos el banner con la temp password para que el super-admin
                // la copie si necesita comunicarla manualmente.
                this.createdSuccess = {
                    tenant: t,
                    accessUrl: this._buildTenantAccessUrl(t.slug),
                    adminEmail: res.admin_email || '',
                    adminName: '',
                    temporaryPassword: res.temporary_password,
                    copiedUrl: false,
                    copiedEmail: false,
                    copiedPassword: false,
                };
            }
            this.cdr.detectChanges();
        }).catch((err) => {
            this.toast.error(err?.error?.detail || this.translate.instant('tenants.msg_resend_failed'));
        });
    }

    /** Copia un texto al clipboard y muestra confirmación temporal. */
    async _copyToClipboard(text: string, kind: 'url' | 'email' | 'password'): Promise<void> {
        try {
            await navigator.clipboard.writeText(text);
            if (kind === 'url') this.createdSuccess.copiedUrl = true;
            else if (kind === 'password') this.createdSuccess.copiedPassword = true;
            else this.createdSuccess.copiedEmail = true;
            this.cdr.detectChanges();
            setTimeout(() => {
                if (kind === 'url') this.createdSuccess.copiedUrl = false;
                else this.createdSuccess.copiedEmail = false;
                this.cdr.detectChanges();
            }, 2000);
        } catch {
            this.toast.error(this.translate.instant('tenants.msg_clipboard_failed'));
        }
    }

    closeCreatedSuccessModal(): void {
        this.createdSuccess = {
            tenant: null, accessUrl: '', adminEmail: '',
            adminName: '', temporaryPassword: null,
            copiedUrl: false, copiedEmail: false, copiedPassword: false,
        };
        this.cdr.detectChanges();
    }

    // ============================================================
    // Acciones por fila
    // ============================================================
    /** Posición fixed del menú flotante calculada del botón que lo abre.
     *  Escapa overflow:hidden de la cadena de padres en responsive. */
    rowMenuPos: { top: number; right: number } | null = null;

    toggleRowMenu(id: number, evt: Event): void {
        evt.stopPropagation();
        if (this.openRowMenuId === id) {
            this.openRowMenuId = null;
            this.rowMenuPos = null;
            return;
        }
        const btn = evt.currentTarget as HTMLElement | null;
        if (btn) {
            const r = btn.getBoundingClientRect();
            this.rowMenuPos = {
                top: r.bottom + 4,
                right: Math.max(8, window.innerWidth - r.right),
            };
        } else {
            this.rowMenuPos = null;
        }
        this.openRowMenuId = id;
    }
    closeRowMenu(): void {
        this.openRowMenuId = null;
        this.rowMenuPos = null;
    }

    @HostListener('window:scroll')
    @HostListener('window:resize')
    onWindowChange(): void {
        if (this.openRowMenuId !== null) this.closeRowMenu();
    }

    private async _performToggleActive(t: TenantOut): Promise<void> {
        const next = !t.is_active;
        try {
            await firstValueFrom(
                this.http.put<TenantOut>(`${this.apiUrl}/${t.slug}`, { is_active: next }, { headers: this._headers() }),
            );
            t.is_active = next;
            this.toast.success(
                next
                    ? this.translate.instant('tenants.msg_tenant_activated')
                    : this.translate.instant('tenants.msg_tenant_deactivated'),
            );
            this.cdr.detectChanges();
        } catch (err: any) {
            this.toast.error(err?.error?.detail || this.translate.instant('tenants.msg_status_change_failed'));
        }
    }

    toggleActive(t: TenantOut, evt?: Event): void {
        if (evt) { evt.stopPropagation(); evt.preventDefault(); }
        this.closeRowMenu();
        // SIEMPRE abrimos confirm modal — el backend protege a 'acten' devolviendo 400.
        // El modal explícitamente bloquea acten en el cliente para feedback inmediato.
        if (t.is_active) {
            if (t.slug === 'acten') {
                this.confirmAction({
                    title: this.translate.instant('tenants.msg_protected_main_title'),
                    message: this.translate.instant('tenants.msg_protected_deactivate_text', { name: t.name }),
                    confirmLabel: this.translate.instant('tenants.msg_understood'),
                    confirmVariant: 'warning',
                    action: () => {}, // No-op: solo informa.
                });
                return;
            }
            this.confirmAction({
                title: this.translate.instant('tenants.msg_deactivate_title'),
                message: this.translate.instant('tenants.msg_deactivate_text', { name: t.name }),
                confirmLabel: this.translate.instant('tenants.msg_deactivate_btn'),
                confirmVariant: 'danger',
                action: () => this._performToggleActive(t),
            });
        } else {
            // Activar es seguro → confirm igual para consistencia UX, variante primary.
            this.confirmAction({
                title: this.translate.instant('tenants.msg_activate_title'),
                message: this.translate.instant('tenants.msg_activate_text', { name: t.name }),
                confirmLabel: this.translate.instant('tenants.msg_activate_btn'),
                confirmVariant: 'primary',
                action: () => this._performToggleActive(t),
            });
        }
    }

    // ============================================================
    // Editar dominio (modal — reemplaza al prompt nativo)
    // ============================================================
    openDomainModal(t: TenantOut, evt?: Event): void {
        if (evt) { evt.stopPropagation(); evt.preventDefault(); }
        this.closeRowMenu();
        this.domainTarget = t;
        this.domainValue = t.domain || '';
        this.errorMsg = '';
        this.showDomainModal = true;
    }
    closeDomainModal(): void {
        this.showDomainModal = false;
        this.domainTarget = null;
        this.domainValue = '';
        this.errorMsg = '';
    }
    async saveDomain(): Promise<void> {
        const t = this.domainTarget;
        if (!t) return;
        this.isSavingDomain = true;
        this.errorMsg = '';
        try {
            const out = await firstValueFrom(
                this.http.put<TenantOut>(
                    `${this.apiUrl}/${t.slug}`,
                    { domain: this.domainValue.trim() || null },
                    { headers: this._headers() },
                ),
            );
            t.domain = out.domain;
            const idx = this.tenants.findIndex((x) => x.id === out.id);
            if (idx >= 0) this.tenants[idx] = { ...this.tenants[idx], ...out };
            this.toast.success(this.translate.instant('tenants.msg_domain_updated', { name: t.name }));
            this.showDomainModal = false;
            this.domainTarget = null;
            this.domainValue = '';
        } catch (err: any) {
            this.errorMsg = err?.error?.detail || this.translate.instant('tenants.msg_domain_update_failed');
        } finally {
            this.isSavingDomain = false;
            this.cdr.detectChanges();
        }
    }

    /** Alias compatible con el HTML existente que sigue llamando setDomain. */
    setDomain(t: TenantOut, evt?: Event): void { this.openDomainModal(t, evt); }

    viewDetail(t: TenantOut, evt?: Event): void {
        if (evt) evt.stopPropagation();
        this.closeRowMenu();
        this.selected = t;
        this.showSelectedPanel = true;
        this.showDetail = true;
        this.cdr.detectChanges();
    }
    closeDetail(): void { this.showDetail = false; }

    openEditModal(t: TenantOut, evt?: Event): void {
        if (evt) { evt.stopPropagation(); evt.preventDefault(); }
        this.closeRowMenu();
        this.errorMsg = '';
        this.successMsg = '';
        const initial = {
            id: t.id,
            slug: t.slug,
            name: t.name || '',
            domain: t.domain || '',
            is_active: !!t.is_active,
            company_name: t.company_name || '',
            primary_color: t.primary_color || '#155EEF',
            logo_data_url: t.logo_data_url || '',
            logo_dark_data_url: t.logo_dark_data_url || '',
            icon_data_url: t.icon_data_url || '',
            default_language: (t.default_language as 'es' | 'ca' | 'en') || 'es',
        };
        this.editForm = { ...initial };
        this.editFormInitial = { ...initial };
        this.showEdit = true;
    }
    closeEditModal(): void {
        this.showEdit = false;
        this.editFormInitial = null;
        this.errorMsg = '';
        this.successMsg = '';
    }

    /** Alias en español para uso desde el kebab. */
    editTenant(t: TenantOut, evt?: Event): void { this.openEditModal(t, evt); }

    /** Logo / Icono inputs DEL MODAL DE EDICIÓN (separados de los de creación). */
    async onEditLogoSelected(ev: Event): Promise<void> {
        this.errorMsg = '';
        const input = ev.target as HTMLInputElement;
        const file = input.files?.[0];
        if (!file) return;
        const url = await this._fileToDataUrl(file);
        if (url) this.editForm.logo_data_url = url;
        input.value = '';
    }
    async onEditLogoDarkSelected(ev: Event): Promise<void> {
        this.errorMsg = '';
        const input = ev.target as HTMLInputElement;
        const file = input.files?.[0];
        if (!file) return;
        const url = await this._fileToDataUrl(file);
        if (url) this.editForm.logo_dark_data_url = url;
        input.value = '';
    }
    async onEditIconSelected(ev: Event): Promise<void> {
        this.errorMsg = '';
        const input = ev.target as HTMLInputElement;
        const file = input.files?.[0];
        if (!file) return;
        const url = await this._fileToDataUrl(file);
        if (url) this.editForm.icon_data_url = url;
        input.value = '';
    }
    clearEditLogo(): void { this.editForm.logo_data_url = ''; }
    clearEditLogoDark(): void { this.editForm.logo_dark_data_url = ''; }
    clearEditIcon(): void { this.editForm.icon_data_url = ''; }

    async saveEdit(): Promise<void> {
        if (!this.editForm.slug) return;
        const name = this.editForm.name.trim();
        if (!name || name.length < 2) {
            this.errorMsg = this.translate.instant('tenants.msg_name_min_length');
            return;
        }
        const init = this.editFormInitial || {};
        this.isEditing = true;
        this.errorMsg = '';
        try {
            // Una sola llamada PUT /api/super/tenants/{slug} con TODO lo
            // que cambió — el endpoint ahora acepta también campos de
            // branding (company_name, primary_color, logo_data_url,
            // logo_dark_data_url, icon_data_url) y los aplica como patch
            // parcial sobre branding_json sin pisar el resto.
            const body: Record<string, any> = {};
            if (name !== init.name) body['name'] = name;
            if ((this.editForm.domain || '') !== (init.domain || '')) {
                body['domain'] = this.editForm.domain.trim() || null;
            }
            if (this.editForm.is_active !== init.is_active) {
                body['is_active'] = this.editForm.is_active;
            }
            if ((this.editForm.company_name || '') !== (init.company_name || '')) {
                body['company_name'] = this.editForm.company_name || '';
            }
            if ((this.editForm.primary_color || '') !== (init.primary_color || '')) {
                body['primary_color'] = this.editForm.primary_color || '';
            }
            if (this.editForm.logo_data_url !== init.logo_data_url) {
                body['logo_data_url'] = this.editForm.logo_data_url || '';
            }
            if (this.editForm.logo_dark_data_url !== init.logo_dark_data_url) {
                body['logo_dark_data_url'] = this.editForm.logo_dark_data_url || '';
            }
            if (this.editForm.icon_data_url !== init.icon_data_url) {
                body['icon_data_url'] = this.editForm.icon_data_url || '';
            }
            if (this.editForm.default_language !== init.default_language) {
                body['default_language'] = this.editForm.default_language;
            }

            let out: TenantOut | null = null;
            if (Object.keys(body).length > 0) {
                out = await firstValueFrom(
                    this.http.put<TenantOut>(
                        `${this.apiUrl}/${this.editForm.slug}`,
                        body,
                        { headers: this._headers() },
                    ),
                );
            }

            // Reflejar cambios en la lista.
            if (out) {
                const idx = this.tenants.findIndex((x) => x.id === out!.id);
                if (idx >= 0) this.tenants[idx] = { ...this.tenants[idx], ...out };
                if (this.selected?.id === out.id) this.selected = this.tenants[idx];
            }
            // Refrescamos para traer branding fresco.
            await this.refresh();

            this.toast.success(this.translate.instant('tenants.msg_tenant_updated'));
            this.showEdit = false;
        } catch (err: any) {
            this.errorMsg = err?.error?.detail || this.translate.instant('tenants.msg_update_failed');
        } finally {
            this.isEditing = false;
            this.cdr.detectChanges();
        }
    }

    /** Hard delete: borra físicamente la empresa Y TODOS sus datos asociados.
     *  IRREVERSIBLE — el backend valida con `?hard=true` y hace cascada manual. */
    private async _performHardDelete(t: TenantOut): Promise<void> {
        try {
            const res: any = await firstValueFrom(
                this.http.delete(
                    `${this.apiUrl}/${t.slug}?hard=true`,
                    { headers: this._headers() },
                ),
            );
            // Removemos la fila del listado en memoria.
            this.tenants = this.tenants.filter(x => x.slug !== t.slug);
            const summaryStr = res?.rows_deleted
                ? Object.entries(res.rows_deleted)
                    .map(([k, v]) => `${k}: ${v}`)
                    .join(', ')
                : '';
            const summarySuffix = summaryStr
                ? this.translate.instant('tenants.msg_rows_deleted_suffix', { summary: summaryStr })
                : '';
            this.toast.success(
                this.translate.instant('tenants.msg_tenant_deleted', { name: t.name, summary: summarySuffix }),
            );
            this.cdr.detectChanges();
        } catch (err: any) {
            this.toast.error(
                err?.error?.detail
                || this.translate.instant('tenants.msg_delete_failed'),
            );
        }
    }

    /** Estado del confirm modal de hard-delete: pide tipear el slug exacto. */
    hardDeleteConfirm: {
        tenant: TenantOut | null;
        typedSlug: string;
    } = { tenant: null, typedSlug: '' };

    /** Acción "Eliminar" del menu — hard delete con confirmación fuerte. */
    deleteTenant(t: TenantOut, evt?: Event): void {
        if (evt) { evt.stopPropagation(); evt.preventDefault(); }
        this.closeRowMenu();
        if (t.slug === 'acten') {
            this.confirmAction({
                title: this.translate.instant('tenants.msg_protected_main_title'),
                message: this.translate.instant('tenants.msg_protected_delete_text', { name: t.name }),
                confirmLabel: this.translate.instant('tenants.msg_understood'),
                confirmVariant: 'warning',
                action: () => {},
            });
            return;
        }
        // Abrimos el modal especializado que requiere tipear el slug.
        this.hardDeleteConfirm = { tenant: t, typedSlug: '' };
        this.cdr.detectChanges();
    }

    /** Cancela el modal de hard-delete. */
    cancelHardDelete(): void {
        this.hardDeleteConfirm = { tenant: null, typedSlug: '' };
        this.cdr.detectChanges();
    }

    /** Ejecuta el hard-delete cuando el slug tipeado coincide. */
    confirmHardDelete(): void {
        const t = this.hardDeleteConfirm.tenant;
        if (!t) return;
        const typed = (this.hardDeleteConfirm.typedSlug || '').trim().toLowerCase();
        if (typed !== t.slug.toLowerCase()) {
            this.toast.warning(this.translate.instant('tenants.msg_slug_mismatch'));
            return;
        }
        const tenant = t;
        this.hardDeleteConfirm = { tenant: null, typedSlug: '' };
        this.cdr.detectChanges();
        this._performHardDelete(tenant);
    }


    selectTenant(t: TenantOut): void {
        this.selected = t;
        this.showSelectedPanel = true;
        this.cdr.detectChanges();
    }

    // ============================================================
    // Confirm dialog
    // ============================================================
    confirmAction(opts: {
        title: string;
        message: string;
        confirmLabel: string;
        confirmVariant?: 'danger' | 'warning' | 'primary';
        action: () => void;
    }): void {
        this.confirmDialog = {
            title: opts.title,
            message: opts.message,
            confirmLabel: opts.confirmLabel,
            confirmVariant: opts.confirmVariant || 'danger',
            action: opts.action,
        };
        this.showConfirmModal = true;
    }
    cancelConfirm(): void {
        this.showConfirmModal = false;
        this.confirmDialog = null;
    }
    runConfirm(): void {
        const cb = this.confirmDialog?.action;
        this.showConfirmModal = false;
        this.confirmDialog = null;
        if (cb) cb();
    }

    // ============================================================
    // KPIs derivados
    // ============================================================
    get totalTenants(): number { return this.tenants.length; }
    get activeTenants(): number { return this.tenants.filter((t) => t.is_active).length; }
    get inactiveTenants(): number { return this.tenants.filter((t) => !t.is_active).length; }
    get totalUsers(): number { return this.tenants.reduce((acc, t) => acc + (t.user_count || 0), 0); }
    get configuredDomains(): number { return this.tenants.filter((t) => !!t.domain).length; }
    get activePercent(): string {
        if (!this.totalTenants) return this.translate.instant('tenants.msg_percent_of_total', { pct: 0 });
        const pct = Math.round((this.activeTenants / this.totalTenants) * 1000) / 10;
        return this.translate.instant('tenants.msg_percent_of_total', { pct });
    }

    /** Empresas creadas este mes (vs el mes pasado) — basado en created_at real. */
    get newThisMonth(): number {
        const monthStart = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
        return this.tenants.filter((t) => {
            if (!t.created_at) return false;
            const d = new Date(t.created_at);
            return !isNaN(d.getTime()) && d >= monthStart;
        }).length;
    }
    get newLastMonth(): number {
        const now = new Date();
        const monthStart = new Date(now.getFullYear(), now.getMonth(), 1);
        const prevStart  = new Date(now.getFullYear(), now.getMonth() - 1, 1);
        return this.tenants.filter((t) => {
            if (!t.created_at) return false;
            const d = new Date(t.created_at);
            return !isNaN(d.getTime()) && d >= prevStart && d < monthStart;
        }).length;
    }
    get tenantsDelta(): { dir: 'up' | 'down' | 'flat'; label: string } {
        const now = this.newThisMonth, prev = this.newLastMonth;
        if (!now && !prev) return { dir: 'flat', label: this.translate.instant('tenants.msg_no_dates_this_month') };
        const diff = now - prev;
        if (diff > 0) return { dir: 'up',   label: this.translate.instant('tenants.msg_delta_this_month_pos', { diff }) };
        if (diff < 0) return { dir: 'down', label: this.translate.instant('tenants.msg_delta_this_month_neg', { diff }) };
        return { dir: 'flat', label: this.translate.instant('tenants.msg_same_as_last_month') };
    }
    /** % vs total para "Empresas activas". */
    get activeDelta(): { dir: 'up' | 'down' | 'flat'; label: string } {
        if (!this.totalTenants) return { dir: 'flat', label: this.translate.instant('tenants.msg_no_companies_label') };
        const pct = (this.activeTenants / this.totalTenants) * 100;
        if (pct >= 80) return { dir: 'up',   label: `${this.activePercent}` };
        if (pct >= 50) return { dir: 'flat', label: `${this.activePercent}` };
        return { dir: 'down', label: `${this.activePercent}` };
    }
    /** Promedio usuarios/empresa para Usuarios distribuidos. */
    get usersDelta(): { dir: 'up' | 'down' | 'flat'; label: string } {
        if (!this.totalTenants) return { dir: 'flat', label: this.translate.instant('tenants.msg_no_companies_label') };
        const avg = Math.round((this.totalUsers / this.totalTenants) * 10) / 10;
        return {
            dir: this.totalUsers > 0 ? 'up' : 'flat',
            label: this.translate.instant('tenants.msg_avg_users_per_company', { avg }),
        };
    }
    /** % de empresas con dominio personalizado. */
    get domainsDelta(): { dir: 'up' | 'down' | 'flat'; label: string } {
        if (!this.totalTenants) return { dir: 'flat', label: this.translate.instant('tenants.msg_no_domains') };
        const pct = Math.round((this.configuredDomains / this.totalTenants) * 100);
        if (pct >= 50) return { dir: 'up',   label: this.translate.instant('tenants.msg_percent_companies', { pct }) };
        if (pct === 0) return { dir: 'flat', label: this.translate.instant('tenants.msg_no_custom_domains') };
        return { dir: 'down', label: this.translate.instant('tenants.msg_percent_companies', { pct }) };
    }

    // ============================================================
    // Filtros + paginación
    // ============================================================
    get filteredTenants(): TenantOut[] {
        const q = this.searchText.trim().toLowerCase();
        return this.tenants.filter((t) => {
            if (this.statusFilter === 'active' && !t.is_active) return false;
            if (this.statusFilter === 'inactive' && t.is_active) return false;
            if (this.domainFilter === 'with' && !t.domain) return false;
            if (this.domainFilter === 'without' && !!t.domain) return false;
            if (this.usersFilter) {
                const n = t.user_count || 0;
                if (this.usersFilter === 'zero'   && n !== 0) return false;
                if (this.usersFilter === 'low'    && (n < 1 || n > 5)) return false;
                if (this.usersFilter === 'medium' && (n < 6 || n > 20)) return false;
                if (this.usersFilter === 'high'   && n <= 20) return false;
            }
            if (this.dateFilter) {
                const d = t.created_at ? new Date(t.created_at) : null;
                if (!d || isNaN(d.getTime())) return false;
                const days = (Date.now() - d.getTime()) / 86400000;
                if (this.dateFilter === 'last30' && days > 30) return false;
                if (this.dateFilter === 'last90' && days > 90) return false;
                if (this.dateFilter === 'older'  && days <= 90) return false;
            }
            if (q) {
                const hay = `${t.name || ''} ${t.slug || ''} ${t.domain || ''}`.toLowerCase();
                if (!hay.includes(q)) return false;
            }
            return true;
        });
    }

    get paginatedTenants(): TenantOut[] {
        const start = (this.currentPage - 1) * this.pageLimit;
        return this.filteredTenants.slice(start, start + this.pageLimit);
    }
    get totalPages(): number { return Math.max(1, Math.ceil(this.filteredTenants.length / this.pageLimit)); }
    get pageButtons(): number[] {
        return Array.from({ length: Math.min(5, this.totalPages) }, (_, i) => i + 1);
    }
    get rangeLabel(): string {
        const total = this.filteredTenants.length;
        if (!total) return this.translate.instant('tenants.msg_no_companies_label');
        const start = (this.currentPage - 1) * this.pageLimit + 1;
        const end = Math.min(this.currentPage * this.pageLimit, total);
        return this.translate.instant('tenants.msg_showing_companies_range', { start, end, total });
    }
    goToPage(p: number): void { if (p >= 1 && p <= this.totalPages) this.currentPage = p; }
    changePageLimit(n: number): void { this.pageLimit = n; this.currentPage = 1; }

    get activeFiltersCount(): number {
        let n = 0;
        if (this.statusFilter) n++;
        if (this.domainFilter) n++;
        if (this.usersFilter) n++;
        if (this.dateFilter) n++;
        return n;
    }
    toggleFiltersBar(): void {
        this.showFilters = !this.showFilters;
        if (!this.showFilters) {
            this.statusFilter = '';
            this.domainFilter = '';
            this.usersFilter = '';
            this.dateFilter = '';
            this.currentPage = 1;
        }
    }

    // ============================================================
    // Visual helpers
    // ============================================================
    /** Color de la "marca" de la empresa (avatar de slug) — hash estable. */
    private readonly _palette = ['#155EEF', '#10B981', '#F97316', '#7C3AED', '#0EA5E9', '#EF4444', '#D97706', '#0F766E'];
    brandColor(seed?: string): string {
        const s = seed || '?';
        let h = 0;
        for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
        return this._palette[Math.abs(h) % this._palette.length];
    }

    initials(name?: string, slug?: string): string {
        const src = (name && name.trim()) ? name : (slug || '?');
        const parts = src.replace(/[._@-]+/g, ' ').trim().split(/\s+/);
        const a = parts[0]?.[0] || '';
        const b = parts.length > 1 ? parts[parts.length - 1][0] : '';
        return (a + b).toUpperCase() || '?';
    }

    formatDate(value?: string | null): string {
        if (!value) return '—';
        const d = new Date(value);
        if (isNaN(d.getTime())) return '—';
        return d.toLocaleDateString('es-CO', { day: '2-digit', month: 'short', year: 'numeric' });
    }

    /** Texto pequeño de la card "Empresa seleccionada" para "Admin principal". */
    get adminPrincipal(): { name: string; email: string } | null {
        const t = this.selected;
        if (!t) return null;
        if (t.admin_full_name || t.admin_email) {
            return {
                name: t.admin_full_name || (t.admin_email ? t.admin_email.split('@')[0] : ''),
                email: t.admin_email || '',
            };
        }
        return null;
    }

    trackById(_i: number, t: TenantOut): number { return t.id; }
}
