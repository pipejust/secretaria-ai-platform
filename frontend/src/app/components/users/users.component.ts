import { Component, OnInit, OnDestroy, ChangeDetectorRef, HostListener, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { Subject } from 'rxjs';
import { takeUntil } from 'rxjs/operators';
import { AuthService } from '../../services/auth.service';
import { ToastService } from '../../services/toast.service';
import { BrandingService } from '../../services/branding.service';
import { environment } from '../../../environments/environment';

/**
 * Extrae el SLD (segundo nivel de dominio) de una URL/dominio/email.
 * Mismo algoritmo que `extract_sld` del backend en `auth.py` para que la
 * validación cliente sea consistente con la del servidor.
 *
 * Ejemplos:
 *   https://www.acten.app  → "acten"
 *   user@mail.acten.com.mx → "acten"
 *   acten.co               → "acten"
 */
const TWO_PART_TLDS = new Set([
    'com.ar', 'com.br', 'com.co', 'com.mx', 'com.pe', 'com.uy', 'com.ve',
    'co.uk', 'org.uk', 'ac.uk', 'gob.ar', 'gob.mx', 'edu.co', 'edu.mx',
]);
function extractSld(value: string): string {
    let raw = (value || '').trim().toLowerCase();
    if (!raw) return '';
    if (raw.includes('@')) raw = raw.split('@').pop()!;
    if (raw.includes('://')) raw = raw.split('://')[1];
    raw = raw.split('/')[0].split('?')[0].split('#')[0].split(':')[0];
    if (raw.startsWith('www.')) raw = raw.slice(4);
    if (!raw || !raw.includes('.')) return raw;
    const parts = raw.split('.');
    if (parts.length >= 3 && TWO_PART_TLDS.has(parts.slice(-2).join('.'))) {
        return parts[parts.length - 3];
    }
    return parts[parts.length - 2];
}

interface UserRow {
    id: number;
    email: string;
    full_name?: string;
    role?: string;
    role_id?: number;
    is_active: boolean;
    phone?: string | null;
    department?: string | null;
    position?: string | null;
    location?: string | null;
    bio?: string | null;
    /** Avatar subido por el usuario en Mi Perfil; ruta relativa que se
     *  resuelve contra apiUrl. Si es null, mostramos iniciales. */
    avatar_url?: string | null;
    last_login_at?: string | null;
    created_at?: string | null;
    updated_at?: string | null;
    is_superadmin?: boolean;
    // Derivado client-side.
    status?: 'active' | 'inactive' | 'invited';
}

interface AccessSummary {
    roles_assigned: number;
    direct_permissions: number;
    role_permissions: number;
    active_sessions: number;
}

interface ActivityEntry {
    id: number;
    action: string;
    title: string;
    color: 'green' | 'orange' | 'blue';
    resource_type: string | null;
    resource_id: string | null;
    ip: string | null;
    user_agent: string | null;
    created_at: string;
}

@Component({
    selector: 'app-users',
    standalone: true,
    imports: [CommonModule, FormsModule, RouterModule],
    templateUrl: './users.component.html',
    styleUrls: ['./users.component.css'],
})
export class UsersComponent implements OnInit, OnDestroy {
    private readonly branding = inject(BrandingService);

    /** SLD del dominio del tenant — derivado del company_website del branding.
     *  Vacío si el admin no configuró aún la página web. */
    get tenantSld(): string {
        return extractSld(this.branding.brand().company_website || '');
    }
    /** Mismo dominio usado en el placeholder del campo email del modal. */
    get tenantDomainHint(): string {
        const sld = this.tenantSld;
        return sld ? `usuario@${sld}.com` : 'usuario@empresa.com';
    }

    users: UserRow[] = [];
    roles: any[] = [];
    isLoading = false;

    // Modal de creación.
    showCreateModal = false;
    isCreating = false;
    newUser: {
        email: string;
        password: string;
        confirm_password: string;
        full_name: string;
        role_id: string;
        phone: string;
        department: string;
        position: string;
    } = {
        email: '', password: '', confirm_password: '',
        full_name: '', role_id: '',
        phone: '', department: '', position: '',
    };

    /** Lista curada de departamentos comunes en empresas SaaS. El backend
     *  acepta cualquier string; este listado es solo una UX guiada. */
    readonly departmentOptions: string[] = [
        'Producto',
        'Operaciones',
        'Comercial',
        'Tecnología',
        'Marketing',
        'Servicio al cliente',
        'Recursos Humanos',
        'Finanzas',
        'Legal',
        'Compras',
        'Logística',
        'Calidad',
        'Otro',
    ];

    // Modal de edición.
    showEditModal = false;
    isEditing = false;
    editUserData: {
        id?: number;
        email: string;
        full_name: string;
        role_id: string;
        is_active: boolean;
        phone: string;
        department: string;
        position: string;
        password: string;
    } = {
        email: '', full_name: '', role_id: '', is_active: true,
        phone: '', department: '', position: '', password: '',
    };

    // Modal de detalle.
    showDetailModal = false;

    // Confirm dialog genérico reutilizable (desactivar / eliminar / bloquear).
    showConfirmModal = false;
    confirmDialog: {
        title: string;
        message: string;
        confirmLabel: string;
        confirmVariant: 'danger' | 'warning' | 'primary';
        action: () => void;
    } | null = null;

    errorMsg = '';
    successMsg = '';

    // Filtros y búsqueda.
    searchText = '';
    roleFilter = '';
    statusFilter = '';
    lastAccessFilter = '';
    showFilters = false;

    // Paginación.
    currentPage = 1;
    pageLimit = 10;

    // Usuario seleccionado para el panel derecho.
    selectedUser: UserRow | null = null;
    showSelectedPanel = true;

    // Datos reales del panel derecho.
    accessSummary: AccessSummary | null = null;
    activityEntries: ActivityEntry[] = [];

    // Kebab del row.
    openRowMenuId: number | null = null;

    private readonly destroy$ = new Subject<void>();

    constructor(
        private http: HttpClient,
        private authService: AuthService,
        private toast: ToastService,
        private cdr: ChangeDetectorRef,
    ) {}

    ngOnInit() { this.loadData(); }

    ngOnDestroy(): void {
        this.destroy$.next();
        this.destroy$.complete();
    }

    @HostListener('document:click')
    onDocClick(): void { this.openRowMenuId = null; }

    @HostListener('document:keydown.escape')
    onEsc(): void {
        this.openRowMenuId = null;
        if (this.showDetailModal) this.showDetailModal = false;
        if (this.showCreateModal) this.showCreateModal = false;
        if (this.showEditModal) this.showEditModal = false;
    }

    // ============================================================
    // Backend
    // ============================================================
    loadData() {
        this.isLoading = true;
        const headers = this.authService.getAuthHeaders();

        this.http.get<UserRow[]>(`${environment.apiUrl}/users`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (data) => {
                    this.users = (data || []).map((u) => ({
                        ...u,
                        status: this.deriveStatus(u),
                    }));
                    this.isLoading = false;
                    if (this.selectedUser) {
                        const fresh = this.users.find((u) => u.id === this.selectedUser!.id);
                        if (fresh) this.selectedUser = fresh;
                        else this.selectedUser = this.users[0] || null;
                    } else if (this.users.length) {
                        this.selectedUser = this.users[0];
                    }
                    if (this.selectedUser) this.loadPanelData(this.selectedUser.id);
                    this.cdr.detectChanges();
                },
                error: () => {
                    this.toast.error('Error al cargar usuarios.');
                    this.isLoading = false;
                    this.cdr.detectChanges();
                },
            });

        this.http.get<any[]>(`${environment.apiUrl}/auth/roles`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (data) => { this.roles = data || []; this.cdr.detectChanges(); },
                error: () => { this.cdr.detectChanges(); },
            });
    }

    /** Refresca el panel derecho cuando cambia el usuario seleccionado. */
    private loadPanelData(userId: number): void {
        const headers = this.authService.getAuthHeaders();
        this.http.get<AccessSummary>(`${environment.apiUrl}/users/${userId}/access-summary`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (s) => { this.accessSummary = s; this.cdr.detectChanges(); },
                error: () => { this.accessSummary = null; this.cdr.detectChanges(); },
            });
        this.http.get<ActivityEntry[]>(`${environment.apiUrl}/users/${userId}/activity?limit=8`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (a) => { this.activityEntries = a || []; this.cdr.detectChanges(); },
                error: () => { this.activityEntries = []; this.cdr.detectChanges(); },
            });
    }

    // ============================================================
    // Create user
    // ============================================================
    openCreateModal(): void {
        this.newUser = {
            email: '', password: '', confirm_password: '',
            full_name: '', role_id: '',
            phone: '', department: '', position: '',
        };
        this.errorMsg = ''; this.successMsg = '';
        this.showCreateModal = true;
    }

    /** Validación cliente para el formulario de registro. */
    private validateNewUser(): string | null {
        const u = this.newUser;
        if (!u.full_name || !u.full_name.trim()) return 'El nombre completo es obligatorio.';
        if (!u.email || !u.email.trim()) return 'El correo electrónico es obligatorio.';
        const emailOk = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(u.email.trim());
        if (!emailOk) return 'El correo electrónico no tiene un formato válido.';
        // Validación de dominio: el email debe pertenecer al mismo SLD que el
        // sitio web de la empresa (sin importar el TLD). El backend también
        // valida — esto es solo para feedback inmediato sin round-trip.
        const tenantSld = this.tenantSld;
        if (!tenantSld) {
            return (
                'La empresa no tiene página web configurada. Pedile al administrador ' +
                'que cargue la URL en Personalización de marca antes de crear usuarios.'
            );
        }
        const emailSld = extractSld(u.email);
        if (emailSld !== tenantSld) {
            return (
                `El email debe ser del dominio '${tenantSld}' (con cualquier extensión: ` +
                `.com, .co, .net, .app, etc.). El que ingresaste pertenece a '${emailSld || 'otro'}'.`
            );
        }
        if (!u.password || u.password.length < 6) return 'La contraseña debe tener al menos 6 caracteres.';
        if (u.password !== u.confirm_password) return 'La confirmación de contraseña no coincide.';
        if (!u.role_id) return 'Debes seleccionar un rol.';
        if (u.phone) {
            // Validación laxa: dígitos, +, espacios y guiones; mínimo 7 dígitos.
            const digits = u.phone.replace(/\D/g, '');
            if (digits.length < 7) return 'El teléfono parece incompleto.';
        }
        return null;
    }

    createUser() {
        const err = this.validateNewUser();
        if (err) { this.errorMsg = err; return; }
        this.isCreating = true;
        this.errorMsg = '';
        this.successMsg = '';
        const { confirm_password, ...rest } = this.newUser;
        const payload = {
            ...rest,
            role_id: parseInt(this.newUser.role_id, 10),
        };
        this.http.post(
            `${environment.apiUrl}/auth/register/admin-only`,
            payload,
            { headers: this.authService.getAuthHeaders() },
        ).pipe(takeUntil(this.destroy$))
        .subscribe({
            next: () => {
                this.successMsg = 'Usuario registrado exitosamente.';
                this.loadData();
                this.isCreating = false;
                setTimeout(() => {
                    this.showCreateModal = false;
                    this.successMsg = '';
                    this.cdr.detectChanges();
                }, 1200);
            },
            error: (err) => {
                this.errorMsg = err?.error?.detail || 'Error al registrar el usuario.';
                this.isCreating = false;
                this.cdr.detectChanges();
            },
        });
    }

    // ============================================================
    // Confirm dialog genérico
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
    // Toggle status — con confirm modal cuando se va a desactivar.
    // ============================================================
    private _performToggleStatus(user: UserRow): void {
        const newStatus = !user.is_active;
        this.http.put(
            `${environment.apiUrl}/users/${user.id}/status?is_active=${newStatus}`,
            {},
            { headers: this.authService.getAuthHeaders() },
        ).pipe(takeUntil(this.destroy$))
        .subscribe({
            next: () => {
                user.is_active = newStatus;
                user.status = this.deriveStatus(user);
                this.cdr.detectChanges();
                this.toast.success(newStatus ? 'Usuario activado.' : 'Usuario desactivado.');
            },
            error: (err) => {
                this.toast.error(err?.error?.detail || 'No pude cambiar el estado del usuario.');
            },
        });
    }

    toggleStatus(user: UserRow, evt?: Event) {
        if (evt) evt.stopPropagation();
        this.closeRowMenu();
        // Activar es seguro → directo. Desactivar pide confirmación SIEMPRE
        // (regla pedida por el usuario para toda acción destructiva).
        if (user.is_active) {
            this.confirmAction({
                title: 'Desactivar usuario',
                message: `¿Estás seguro de desactivar a ${user.full_name || user.email}? Perderá el acceso a la plataforma hasta que lo actives nuevamente.`,
                confirmLabel: 'Desactivar',
                confirmVariant: 'danger',
                action: () => this._performToggleStatus(user),
            });
        } else {
            this._performToggleStatus(user);
        }
    }

    // ============================================================
    // Edit user (real)
    // ============================================================
    openEditModal(u: UserRow, evt?: Event): void {
        if (evt) evt.stopPropagation();
        this.openRowMenuId = null;
        this.errorMsg = ''; this.successMsg = '';
        this.editUserData = {
            id: u.id,
            email: u.email || '',
            full_name: u.full_name || '',
            role_id: u.role_id ? String(u.role_id) : '',
            is_active: !!u.is_active,
            phone: u.phone || '',
            department: u.department || '',
            position: u.position || '',
            password: '',
        };
        this.showEditModal = true;
    }

    saveEdit(): void {
        if (!this.editUserData.id) return;
        this.isEditing = true;
        this.errorMsg = ''; this.successMsg = '';
        const payload: Record<string, any> = {
            full_name: this.editUserData.full_name,
            email: this.editUserData.email,
            role_id: this.editUserData.role_id ? parseInt(this.editUserData.role_id, 10) : null,
            is_active: this.editUserData.is_active,
            phone: this.editUserData.phone,
            department: this.editUserData.department,
            position: this.editUserData.position,
        };
        if (this.editUserData.password && this.editUserData.password.trim()) {
            payload['password'] = this.editUserData.password;
        }
        this.http.put<UserRow>(
            `${environment.apiUrl}/users/${this.editUserData.id}`,
            payload,
            { headers: this.authService.getAuthHeaders() },
        ).pipe(takeUntil(this.destroy$))
        .subscribe({
            next: (updated) => {
                this.successMsg = 'Usuario actualizado correctamente.';
                this.isEditing = false;
                // Sustituir en la lista + selección sin recargar todo.
                const idx = this.users.findIndex((u) => u.id === updated.id);
                if (idx >= 0) {
                    this.users[idx] = { ...updated, status: this.deriveStatus(updated) };
                    if (this.selectedUser?.id === updated.id) this.selectedUser = this.users[idx];
                }
                this.toast.success('Usuario actualizado.');
                setTimeout(() => {
                    this.showEditModal = false;
                    this.successMsg = '';
                    this.cdr.detectChanges();
                }, 800);
            },
            error: (err) => {
                this.errorMsg = err?.error?.detail || 'No pude actualizar el usuario.';
                this.isEditing = false;
                this.cdr.detectChanges();
            },
        });
    }

    // ============================================================
    // View detail (modal)
    // ============================================================
    viewUserDetail(u: UserRow, evt?: Event): void {
        if (evt) evt.stopPropagation();
        this.openRowMenuId = null;
        this.selectedUser = u;
        this.loadPanelData(u.id);
        this.showDetailModal = true;
    }

    closeDetailModal(): void { this.showDetailModal = false; }

    // ============================================================
    // Selection (click on row)
    // ============================================================
    selectUser(u: UserRow): void {
        this.selectedUser = u;
        this.showSelectedPanel = true;
        this.loadPanelData(u.id);
        this.cdr.detectChanges();
    }

    // Kebab
    toggleRowMenu(id: number, evt: Event): void {
        evt.stopPropagation();
        this.openRowMenuId = this.openRowMenuId === id ? null : id;
    }
    closeRowMenu(): void { this.openRowMenuId = null; }

    // ============================================================
    // Métricas
    // ============================================================
    private deriveStatus(u: UserRow): 'active' | 'inactive' | 'invited' {
        if (!u.is_active) return 'inactive';
        if (!u.last_login_at) return 'invited';
        return 'active';
    }

    get totalUsers(): number { return this.users.length; }
    get activeUsers(): number { return this.users.filter((u) => u.status === 'active').length; }
    get inactiveUsers(): number { return this.users.filter((u) => u.status === 'inactive').length; }
    get adminUsers(): number {
        return this.users.filter((u) => (u.role || '').toLowerCase().includes('admin')).length;
    }
    get activePercent(): string {
        if (!this.totalUsers) return '0%';
        return `${Math.round((this.activeUsers / this.totalUsers) * 1000) / 10}% del total`;
    }
    get adminPercent(): string {
        if (!this.totalUsers) return '0%';
        return `${Math.round((this.adminUsers / this.totalUsers) * 1000) / 10}% del total`;
    }

    /** Cuántos usuarios fueron creados en el mes en curso. */
    get newUsersThisMonth(): number {
        const now = new Date();
        const monthStart = new Date(now.getFullYear(), now.getMonth(), 1);
        return this.users.filter((u) => {
            if (!u.created_at) return false;
            const d = new Date(u.created_at);
            return !isNaN(d.getTime()) && d >= monthStart;
        }).length;
    }

    /** Cuántos usuarios fueron creados el mes pasado. Sirve para el delta. */
    get newUsersLastMonth(): number {
        const now = new Date();
        const monthStart = new Date(now.getFullYear(), now.getMonth(), 1);
        const prevStart = new Date(now.getFullYear(), now.getMonth() - 1, 1);
        return this.users.filter((u) => {
            if (!u.created_at) return false;
            const d = new Date(u.created_at);
            return !isNaN(d.getTime()) && d >= prevStart && d < monthStart;
        }).length;
    }

    /** Cuántos usuarios hicieron login en los últimos 7 días. */
    get activeLast7Days(): number {
        const cutoff = Date.now() - 7 * 86400000;
        return this.users.filter((u) => {
            if (!u.last_login_at) return false;
            const d = new Date(u.last_login_at);
            return !isNaN(d.getTime()) && d.getTime() >= cutoff;
        }).length;
    }

    /** Direccionalidad de la card "Usuarios totales" comparando este mes vs anterior. */
    get usersDelta(): { dir: 'up' | 'down' | 'flat'; label: string } {
        const now = this.newUsersThisMonth;
        const prev = this.newUsersLastMonth;
        if (!now && !prev) {
            return { dir: 'flat', label: 'Sin cambios este mes' };
        }
        const diff = now - prev;
        if (diff > 0) return { dir: 'up',   label: `+${diff} este mes` };
        if (diff < 0) return { dir: 'down', label: `${diff} este mes` };
        return { dir: 'flat', label: 'Igual que el mes pasado' };
    }

    get roleOptions(): string[] {
        const set = new Set<string>();
        for (const u of this.users) if (u.role) set.add(u.role);
        return Array.from(set).sort();
    }

    get filteredUsers(): UserRow[] {
        const q = this.searchText.trim().toLowerCase();
        return this.users.filter((u) => {
            if (this.roleFilter && (u.role || '').toLowerCase() !== this.roleFilter.toLowerCase()) return false;
            if (this.statusFilter && u.status !== this.statusFilter) return false;
            if (this.lastAccessFilter) {
                const d = u.last_login_at ? new Date(u.last_login_at) : null;
                if (!d || isNaN(d.getTime())) return false;
                const diffDays = (Date.now() - d.getTime()) / 86400000;
                if (this.lastAccessFilter === 'today' && diffDays > 1) return false;
                if (this.lastAccessFilter === 'week' && diffDays > 7) return false;
                if (this.lastAccessFilter === 'month' && diffDays > 30) return false;
            }
            if (q) {
                const hay = `${u.email || ''} ${u.full_name || ''} ${u.role || ''} ${u.id}`.toLowerCase();
                if (!hay.includes(q)) return false;
            }
            return true;
        });
    }

    get paginatedUsers(): UserRow[] {
        const start = (this.currentPage - 1) * this.pageLimit;
        return this.filteredUsers.slice(start, start + this.pageLimit);
    }
    get totalPages(): number { return Math.max(1, Math.ceil(this.filteredUsers.length / this.pageLimit)); }
    get pageButtons(): number[] {
        const pages = this.totalPages;
        return Array.from({ length: Math.min(5, pages) }, (_, i) => i + 1);
    }
    get rangeLabel(): string {
        const total = this.filteredUsers.length;
        if (!total) return 'Sin usuarios';
        const start = (this.currentPage - 1) * this.pageLimit + 1;
        const end = Math.min(this.currentPage * this.pageLimit, total);
        return `Mostrando ${start}-${end} de ${total} usuarios`;
    }
    goToPage(p: number): void { if (p >= 1 && p <= this.totalPages) this.currentPage = p; }
    changePageLimit(n: number): void { this.pageLimit = n; this.currentPage = 1; }

    get activeFiltersCount(): number {
        let n = 0;
        if (this.roleFilter) n++;
        if (this.statusFilter) n++;
        if (this.lastAccessFilter) n++;
        return n;
    }
    toggleFiltersBar(): void {
        this.showFilters = !this.showFilters;
        if (!this.showFilters) {
            this.roleFilter = '';
            this.statusFilter = '';
            this.lastAccessFilter = '';
            this.currentPage = 1;
        }
    }

    // ============================================================
    // Labels / colores
    // ============================================================
    statusLabel(s?: string): string {
        switch (s) {
            case 'active':   return 'Activo';
            case 'inactive': return 'Inactivo';
            case 'invited':  return 'Invitado';
            default:         return '—';
        }
    }
    roleBadgeKey(role?: string): string {
        const r = (role || '').toLowerCase();
        if (r.includes('admin')) return 'admin';
        if (r.includes('valid')) return 'validator';
        if (r.includes('project') || r === 'lead') return 'lead';
        if (r.includes('analyst')) return 'analyst';
        if (r.includes('collab')) return 'collab';
        if (r.includes('support')) return 'support';
        return 'neutral';
    }
    initials(name?: string, email?: string): string {
        const src = (name && name.trim()) ? name : (email || '?');
        const parts = src.replace(/[._@-]+/g, ' ').trim().split(/\s+/);
        const a = parts[0]?.[0] || '';
        const b = parts.length > 1 ? parts[parts.length - 1][0] : '';
        return (a + b).toUpperCase() || '?';
    }
    private readonly _avatarPalette = [
        '#155EEF', '#10B981', '#F97316', '#EF4444',
        '#7C3AED', '#0EA5E9', '#D97706', '#0F766E',
    ];
    avatarColor(seed?: string): string {
        const s = seed || '?';
        let h = 0;
        for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
        return this._avatarPalette[Math.abs(h) % this._avatarPalette.length];
    }

    /** URL absoluta de la foto de perfil de un usuario. null si no tiene
     *  → el caller cae al avatar de iniciales. */
    avatarUrl(u?: { avatar_url?: string | null }): string | null {
        const raw = u?.avatar_url;
        if (!raw) return null;
        if (raw.startsWith('http://') || raw.startsWith('https://')) return raw;
        return `${environment.apiUrl}${raw}`;
    }

    formatLastAccess(value?: string | null): { line1: string; line2: string } {
        if (!value) return { line1: 'Nunca', line2: 'Sin acceso' };
        const d = new Date(value);
        if (isNaN(d.getTime())) return { line1: '—', line2: '' };
        const now = new Date();
        const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const yest  = new Date(today.getTime() - 86400000);
        const target = new Date(d.getFullYear(), d.getMonth(), d.getDate());
        const time = d.toLocaleTimeString('es-CO', { hour: '2-digit', minute: '2-digit', hour12: false });
        const longDate = d.toLocaleDateString('es-CO', { day: '2-digit', month: 'short', year: 'numeric' });
        if (target.getTime() === today.getTime()) return { line1: `Hoy, ${time}`, line2: longDate };
        if (target.getTime() === yest.getTime())  return { line1: `Ayer, ${time}`, line2: longDate };
        return { line1: longDate, line2: time };
    }
    formatCreationDate(value?: string | null): string {
        if (!value) return '—';
        const d = new Date(value);
        if (isNaN(d.getTime())) return '—';
        return d.toLocaleDateString('es-CO', { day: '2-digit', month: 'short', year: 'numeric' });
    }
    formatActivityMeta(a: ActivityEntry): string {
        const parts: string[] = [];
        const d = a.created_at ? new Date(a.created_at) : null;
        if (d && !isNaN(d.getTime())) {
            parts.push(this.formatLastAccess(a.created_at).line1);
        }
        if (a.user_agent) {
            // Heurística simple para "Chrome · macOS" sin parser pesado.
            const ua = a.user_agent;
            const browser = /Chrome/i.test(ua) ? 'Chrome'
                          : /Firefox/i.test(ua) ? 'Firefox'
                          : /Safari/i.test(ua)  ? 'Safari'
                          : /Edge/i.test(ua)    ? 'Edge'
                          : 'Navegador';
            const os = /Mac OS X/i.test(ua) ? 'macOS'
                     : /Windows/i.test(ua)  ? 'Windows'
                     : /Linux/i.test(ua)    ? 'Linux'
                     : /Android/i.test(ua)  ? 'Android'
                     : /iPhone|iOS/i.test(ua) ? 'iOS'
                     : '';
            parts.push([browser, os].filter(Boolean).join(' · '));
        }
        if (a.ip) parts.push(a.ip);
        return parts.join(' · ') || a.action;
    }

    trackById(_i: number, u: UserRow): number { return u.id; }
    trackByActivity(_i: number, a: ActivityEntry): number { return a.id; }
}
