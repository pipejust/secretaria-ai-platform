import { Component, OnInit, OnDestroy, ChangeDetectorRef, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { AuthService } from '../../services/auth.service';
import { BrandingService, Branding } from '../../services/branding.service';
import { ToastService } from '../../services/toast.service';
import { environment } from '../../../environments/environment';

interface BrandActivityItem {
    id: number;
    title: string;
    meta: string;
    tone: 'green' | 'orange' | 'blue' | 'gray';
}

type ViewMode = 'list' | 'grid';
type SectionFilter = 'all' | 'identidad' | 'logo' | 'icono' | 'tema' | 'previews';
type StatusFilter = 'all' | 'configured' | 'pending';

@Component({
    selector: 'app-branding-settings',
    standalone: true,
    imports: [CommonModule, FormsModule],
    templateUrl: './branding-settings.component.html',
    styleUrls: ['./branding-settings.component.css'],
})
export class BrandingSettingsComponent implements OnInit, OnDestroy {
    readonly branding = inject(BrandingService);
    private readonly auth = inject(AuthService);
    private readonly toast = inject(ToastService);
    private readonly cdr = inject(ChangeDetectorRef);
    private readonly http = inject(HttpClient);

    // Prueba de envío de correo
    testEmailTo = '';
    isSendingTest = false;

    /** Modelo local para edición — copiado de la marca actual al entrar. */
    form: Branding = {
        platform_name: 'Acten',
        platform_tagline: '',
        company_name: '',
        company_tagline: '',
        company_email: '',
        company_address: '',
        company_website: '',
        company_phone: '',
        primary_color: '#223148',
        secondary_color: '#1B7F67',
        accent_color: '#D9A441',
        logo_data_url: '',
        logo_dark_data_url: '',
        icon_data_url: '',
        favicon_data_url: '',
    };

    isSaving = false;
    isUploading = false;
    isUploadingDark = false;
    isUploadingIcon = false;

    // UI
    showFilters = false;
    viewMode: ViewMode = 'list';
    filterSearch = '';
    filterSection: SectionFilter = 'all';
    filterStatus: StatusFilter = 'all';
    panelCollapsed = false;

    // Modal de vista previa
    showPreviewModal = false;

    // Color presets — nombre legible para el bloque de tema visual.
    readonly primaryName = 'Ink Blue';
    readonly secondaryName = 'Emerald';
    readonly accentName = 'Amber';

    // Activity (placeholder local — el backend aún no expone bitácora de marca).
    readonly recentActivity: BrandActivityItem[] = [
        { id: 1, title: 'Logo actualizado',         meta: 'Hoy, 09:42 · System Admin',   tone: 'green' },
        { id: 2, title: 'Colores modificados',      meta: 'Ayer, 11:18 · Carlos Neura',  tone: 'orange' },
        { id: 3, title: 'Vista previa enviada',     meta: '15 may, 10:21 · Maya Patel',  tone: 'blue' },
        { id: 4, title: 'Datos de contacto editados', meta: '3 may, 09:16 · Maya Patel', tone: 'gray' },
    ];

    ngOnInit(): void {
        this.branding.loadFromServer().finally(() => {
            this.form = { ...this.branding.brand() };
            // Defaults sugeridos del diseño cuando los del backend vienen vacíos.
            if (!this.form.primary_color || /^#?(?:0+)$/i.test(this.form.primary_color)) this.form.primary_color = '#223148';
            if (!this.form.secondary_color) this.form.secondary_color = '#1B7F67';
            if (!this.form.accent_color) this.form.accent_color = '#D9A441';
            this.cdr.detectChanges();
        });
    }

    ngOnDestroy(): void {
        // Si el admin sale sin guardar, revertimos el preview al estado real.
        // Si guardó, `update()` ya consolidó los colores en brand() — revert
        // simplemente reaplica los persistidos, así que es seguro.
        this.branding.revertPreview();
    }

    /** Live-preview: cada cambio de color en el form se proyecta inmediatamente
     *  sobre el sidebar y resto de UI sin tocar lo persistido. */
    onColorChange(): void {
        this.branding.applyPreview({
            primary_color: this.form.primary_color,
            secondary_color: this.form.secondary_color,
            accent_color: this.form.accent_color,
        });
    }

    // ============================================================
    // Métricas — derivadas del estado actual del form.
    // ============================================================
    /** Activos de marca: logo, logo oscuro, icono, favicon. */
    get brandAssetsCount(): number {
        let n = 0;
        if (this.form.logo_data_url) n++;
        if (this.form.logo_dark_data_url) n++;
        if (this.form.icon_data_url) n++;
        if (this.form.favicon_data_url) n++;
        // Identidad cuenta como 1 activo siempre.
        n += 1;
        return n;
    }

    /** Canales configurados: email + sitio web + teléfono. */
    get channelsCount(): number {
        let n = 0;
        if ((this.form.company_email || '').trim()) n++;
        if ((this.form.company_website || '').trim()) n++;
        if ((this.form.company_phone || '').trim()) n++;
        return n;
    }

    /** Colores activos: cuenta los 3 colores si tienen valor. */
    get colorsCount(): number {
        let n = 0;
        if (this.form.primary_color) n++;
        if (this.form.secondary_color) n++;
        if (this.form.accent_color) n++;
        return n;
    }

    /** Vistas previas disponibles: email + interfaz = 2. */
    readonly previewsCount = 2;

    /** Logo a mostrar en la vista previa del correo. Mirror EXACTO de la
     *  lógica que aplica el backend en `EmailService`:
     *    1) si el tenant tiene logo subido (form en edición), se usa
     *    2) si no, cae al imagologo Acten que el backend embebe como
     *       data URL en los correos reales.
     *  Así el admin ve exactamente lo que llegará al destinatario. */
    get emailPreviewLogoUrl(): string {
        return this.form.logo_data_url || 'brand/acten-logo-full.png';
    }

    // ============================================================
    // KPIs auxiliares para el panel derecho.
    // ============================================================
    get logosUploaded(): number {
        let n = 0;
        if (this.form.logo_data_url) n++;
        if (this.form.logo_dark_data_url) n++;
        if (this.form.icon_data_url) n++;
        return n;
    }

    get tenantSlug(): string {
        // Por ahora no hay otra fuente — derivamos del nombre de empresa.
        return (this.form.company_name || 'acten').toLowerCase().replace(/[^a-z0-9-]+/g, '').slice(0, 24) || 'acten';
    }

    get tenantDomain(): string {
        // Si hay sitio web, extraemos el host; si no, derivamos del slug.
        const raw = (this.form.company_website || '').trim();
        if (raw) {
            try {
                const u = new URL(raw.startsWith('http') ? raw : `https://${raw}`);
                return u.hostname.replace(/^www\./, '');
            } catch { /* fallback abajo */ }
        }
        return `${this.tenantSlug}.ai`;
    }

    get lastUpdatedLabel(): string {
        const d = new Date();
        return d.toLocaleString('es-CO', {
            day: '2-digit', month: 'short', year: 'numeric',
            hour: '2-digit', minute: '2-digit',
        }).replace(',', ' ·');
    }

    // ============================================================
    // Filtros — visuales (no backend).
    // ============================================================
    toggleFilters(): void { this.showFilters = !this.showFilters; }
    setViewMode(m: ViewMode): void { this.viewMode = m; }

    get activeFilterCount(): number {
        let n = 0;
        if (this.filterSection !== 'all') n++;
        if (this.filterStatus !== 'all') n++;
        if (this.filterSearch.trim()) n++;
        return n;
    }
    clearFilters(): void {
        this.filterSearch = '';
        this.filterSection = 'all';
        this.filterStatus = 'all';
    }

    /** Decide si una sección es visible según el filtro. */
    showSection(key: 'identidad' | 'logo' | 'icono' | 'tema' | 'email' | 'interfaz'): boolean {
        const q = this.filterSearch.trim().toLowerCase();
        if (this.filterSection !== 'all') {
            // El check `!== 'all'` ya descartó esa variante; el map solo cubre el resto.
            const map: Record<Exclude<SectionFilter, 'all'>, string[]> = {
                identidad: ['identidad'],
                logo: ['logo'],
                icono: ['icono'],
                tema: ['tema'],
                previews: ['email','interfaz'],
            };
            if (!map[this.filterSection].includes(key)) return false;
        }
        if (q) {
            const haystack: Record<string, string> = {
                identidad: 'identidad empresa nombre tagline email sitio web teléfono dirección',
                logo: 'logo wordmark monograma png svg webp',
                icono: 'imagologo icono avatar badge',
                tema: 'tema visual colores primario secundario acento hex',
                email: 'email preview correo plantilla',
                interfaz: 'interfaz preview sidebar header login botones colores',
            };
            if (!haystack[key].includes(q)) return false;
        }
        if (this.filterStatus === 'configured') {
            const filled = this.sectionConfigured(key);
            if (!filled) return false;
        }
        if (this.filterStatus === 'pending') {
            const filled = this.sectionConfigured(key);
            if (filled) return false;
        }
        return true;
    }

    private sectionConfigured(key: 'identidad' | 'logo' | 'icono' | 'tema' | 'email' | 'interfaz'): boolean {
        switch (key) {
            case 'identidad': return !!(this.form.company_name || this.form.company_email);
            case 'logo':      return !!this.form.logo_data_url;
            case 'icono':     return !!this.form.icon_data_url;
            case 'tema':      return !!(this.form.primary_color && this.form.secondary_color && this.form.accent_color);
            case 'email':     return true;
            case 'interfaz':  return true;
        }
    }

    // ============================================================
    // Acciones del header
    // ============================================================
    openPreview(): void { this.showPreviewModal = true; }
    closePreview(): void { this.showPreviewModal = false; }
    togglePanel(): void { this.panelCollapsed = !this.panelCollapsed; }

    async save(): Promise<void> {
        // Validación cliente: el sitio web es obligatorio. Sin él, los admins
        // no pueden crear usuarios (el backend valida que el email de cada
        // usuario nuevo pertenezca al mismo SLD que company_website).
        const website = (this.form.company_website || '').trim();
        if (!website) {
            this.toast.error(
                'El sitio web es obligatorio. Se usa para validar el dominio de los usuarios que creás.',
            );
            return;
        }
        // Mini-check de formato: necesita al menos un punto y caracteres válidos.
        const looksValid = /^(https?:\/\/)?[a-z0-9.-]+\.[a-z]{2,}/i.test(website);
        if (!looksValid) {
            this.toast.error(
                'El sitio web no tiene un formato válido. Ejemplo: https://tuempresa.com',
            );
            return;
        }
        this.isSaving = true;
        try {
            const patch = {
                company_name: this.form.company_name,
                company_tagline: this.form.company_tagline,
                company_email: this.form.company_email,
                company_address: this.form.company_address,
                company_website: website,
                company_phone: this.form.company_phone,
                primary_color: this.form.primary_color,
                secondary_color: this.form.secondary_color,
                accent_color: this.form.accent_color,
            };
            await this.branding.update(patch, this.auth.token);
            this.toast.success('Marca actualizada. Los cambios ya están aplicados.');
        } catch (err: any) {
            this.toast.error(err?.error?.detail || 'No se pudo guardar la marca.');
        } finally {
            this.isSaving = false;
            this.cdr.detectChanges();
        }
    }

    // ============================================================
    // Uploaders (preservan funcionalidad real)
    // ============================================================
    async onLogoSelected(ev: Event): Promise<void> {
        const input = ev.target as HTMLInputElement;
        const file = input.files?.[0];
        if (!file) return;
        this.isUploading = true;
        try {
            await this.branding.uploadLogo(file, this.auth.token);
            this.form.logo_data_url = this.branding.brand().logo_data_url;
            this.toast.success('Logo subido correctamente.');
        } catch (err: any) {
            this.toast.error(err?.error?.detail || 'No se pudo subir el logo.');
        } finally {
            this.isUploading = false;
            input.value = '';
            this.cdr.detectChanges();
        }
    }

    async removeLogo(): Promise<void> {
        if (!confirm('¿Quitar el logo? Volverá a mostrarse el nombre de la empresa.')) return;
        this.isUploading = true;
        try {
            await this.branding.deleteLogo(this.auth.token);
            this.form.logo_data_url = '';
            this.toast.success('Logo eliminado.');
        } catch (err: any) {
            this.toast.error(err?.error?.detail || 'No se pudo eliminar el logo.');
        } finally {
            this.isUploading = false;
            this.cdr.detectChanges();
        }
    }

    // ============================================================
    // Logo oscuro (para fondos oscuros — landing hero, dark mode)
    // ============================================================
    async onDarkLogoSelected(ev: Event): Promise<void> {
        const input = ev.target as HTMLInputElement;
        const file = input.files?.[0];
        if (!file) return;
        this.isUploadingDark = true;
        try {
            await this.branding.uploadDarkLogo(file, this.auth.token);
            this.form.logo_dark_data_url = this.branding.brand().logo_dark_data_url;
            this.toast.success('Logo oscuro subido correctamente.');
        } catch (err: any) {
            this.toast.error(err?.error?.detail || 'No se pudo subir el logo oscuro.');
        } finally {
            this.isUploadingDark = false;
            input.value = '';
            this.cdr.detectChanges();
        }
    }

    async removeDarkLogo(): Promise<void> {
        if (!confirm('¿Quitar el logo oscuro? Los fondos oscuros volverán a usar el logo regular.')) return;
        this.isUploadingDark = true;
        try {
            await this.branding.deleteDarkLogo(this.auth.token);
            this.form.logo_dark_data_url = '';
            this.toast.success('Logo oscuro eliminado.');
        } catch (err: any) {
            this.toast.error(err?.error?.detail || 'No se pudo eliminar el logo oscuro.');
        } finally {
            this.isUploadingDark = false;
            this.cdr.detectChanges();
        }
    }

    async onIconSelected(ev: Event): Promise<void> {
        const input = ev.target as HTMLInputElement;
        const file = input.files?.[0];
        if (!file) return;
        this.isUploadingIcon = true;
        try {
            await this.branding.uploadIcon(file, this.auth.token);
            this.form.icon_data_url = this.branding.brand().icon_data_url;
            this.toast.success('Imagologo subido correctamente.');
        } catch (err: any) {
            this.toast.error(err?.error?.detail || 'No se pudo subir el imagologo.');
        } finally {
            this.isUploadingIcon = false;
            input.value = '';
            this.cdr.detectChanges();
        }
    }

    async removeIcon(): Promise<void> {
        if (!confirm('¿Quitar el imagologo? El sidebar colapsado volverá al icono Acten por defecto.')) return;
        this.isUploadingIcon = true;
        try {
            await this.branding.deleteIcon(this.auth.token);
            this.form.icon_data_url = '';
            this.toast.success('Imagologo eliminado.');
        } catch (err: any) {
            this.toast.error(err?.error?.detail || 'No se pudo eliminar el imagologo.');
        } finally {
            this.isUploadingIcon = false;
            this.cdr.detectChanges();
        }
    }

    // ============================================================
    // Probar envío de correo (POST /api/branding/test_email)
    // ============================================================
    async sendTestEmail(): Promise<void> {
        const target = (this.testEmailTo || '').trim();
        if (this.isSendingTest) return;
        this.isSendingTest = true;
        try {
            const headers = this.auth.getAuthHeaders();
            const res = await firstValueFrom(
                this.http.post<{ status: string; to: string; smtp_configured: boolean }>(
                    `${environment.apiUrl}/api/branding/test_email`,
                    { to_email: target || null },
                    { headers },
                ),
            );
            if (!res?.smtp_configured) {
                this.toast.warning(
                    `Correo de prueba simulado a ${res?.to} (revisa logs del backend). Configura SMTP en /admin/settings para envío real.`,
                );
            } else {
                this.toast.success(`Correo de prueba enviado a ${res.to}.`);
            }
        } catch (err: any) {
            const msg = err?.error?.detail || 'No pude enviar el correo de prueba.';
            this.toast.error(msg);
        } finally {
            this.isSendingTest = false;
            this.cdr.detectChanges();
        }
    }

    // ============================================================
    // Helpers visuales
    // ============================================================
    initials(value: string): string {
        if (!value) return 'A';
        return value.trim().charAt(0).toUpperCase();
    }

    trackActivity = (_: number, a: BrandActivityItem) => a.id;
}
