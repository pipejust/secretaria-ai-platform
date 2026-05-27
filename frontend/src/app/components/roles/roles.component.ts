import { Component, OnInit, OnDestroy, ChangeDetectorRef, HostListener } from '@angular/core';
import { CommonModule } from '@angular/common';
import { TranslateModule } from '@ngx-translate/core';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { Subject, forkJoin } from 'rxjs';
import { takeUntil } from 'rxjs/operators';
import { AuthService } from '../../services/auth.service';
import { ToastService } from '../../services/toast.service';
import { environment } from '../../../environments/environment';

interface Role {
    id: number;
    name: string;
    description?: string;
    is_active?: boolean;
    is_system?: boolean;
    created_at?: string;
    updated_at?: string;
    user_count?: number;
    permissions_count?: number;
    modules_count?: number;
}

interface UserSummary {
    id: number;
    email: string;
    full_name?: string;
    is_active?: boolean;
    role?: string;
}

interface CatalogModule {
    key: string;
    label: string;
    actions: string[];
}

interface PermSummary {
    modules_count: number;
    permissions_total: number;
    permissions_critical: number;
    integrations_accessible: number;
    modules_available: number;
}

interface ActivityItem {
    id: number;
    action: string;
    actor_name: string;
    note: string;
    created_at: string;
}

interface PermissionRow { module_key: string; action: string; is_granted: boolean; }

type ViewMode = 'list' | 'grid';
type StatusFilter = 'all' | 'active' | 'inactive';
type TypeFilter = 'all' | 'system' | 'custom';
type UsersFilter = 'all' | 'with' | 'without';

const ACTION_LABELS: Record<string, string> = {
    view: 'Ver',
    create: 'Crear',
    edit: 'Editar',
    delete: 'Eliminar',
    manage: 'Administrar',
    export: 'Exportar',
};

@Component({
    selector: 'app-roles',
    standalone: true,
    imports: [CommonModule, FormsModule, TranslateModule],
    templateUrl: './roles.component.html',
    styleUrls: ['./roles.component.css'],
})
export class RolesComponent implements OnInit, OnDestroy {
    roles: Role[] = [];
    users: UserSummary[] = [];
    catalog: CatalogModule[] = [];
    permSummary: PermSummary | null = null;
    selectedActivity: ActivityItem[] = [];
    selectedUsers: UserSummary[] = [];
    selectedPermissions: PermissionRow[] = [];

    isLoading = false;
    errorMsg = '';

    // Search + filters
    searchText = '';
    filterStatus: StatusFilter = 'all';
    filterType: TypeFilter = 'all';
    filterUsers: UsersFilter = 'all';
    showFilters = false;

    viewMode: ViewMode = 'list';

    currentPage = 1;
    readonly pageSize = 10;

    // Kebab menu (igual a Reuniones)
    openRowMenuId: number | null = null;

    // Modal: create + edit (mismo modal con modo)
    showRoleModal = false;
    roleModalMode: 'create' | 'edit' = 'create';
    roleForm: { name: string; description: string; is_active: boolean; permissions: Record<string, Record<string, boolean>> } = {
        name: '',
        description: '',
        is_active: true,
        permissions: {},
    };
    editingRoleId: number | null = null;
    isSavingRole = false;
    roleFormError = '';

    // Modal: detalle de rol
    showDetailModal = false;
    detailRole: Role | null = null;

    // Modal: confirmación de borrado
    showDeleteModal = false;
    deleteTarget: Role | null = null;
    isDeleting = false;

    selectedRole: Role | null = null;
    panelCollapsed = false;

    private readonly destroy$ = new Subject<void>();

    constructor(
        private http: HttpClient,
        private authService: AuthService,
        private toast: ToastService,
        private cdr: ChangeDetectorRef,
    ) {}

    ngOnInit(): void { this.loadAll(); }

    ngOnDestroy(): void {
        this.destroy$.next();
        this.destroy$.complete();
    }

    @HostListener('document:click')
    onDocClick(): void { this.openRowMenuId = null; }

    // ============================================================
    // Carga inicial
    // ============================================================
    private get headers() { return this.authService.getAuthHeaders(); }

    loadAll(): void {
        this.isLoading = true;
        forkJoin({
            roles: this.http.get<Role[]>(`${environment.apiUrl}/auth/roles`, { headers: this.headers }),
            users: this.http.get<UserSummary[]>(`${environment.apiUrl}/users`, { headers: this.headers }),
            catalog: this.http.get<{ modules: CatalogModule[] }>(
                `${environment.apiUrl}/auth/roles/_catalog`, { headers: this.headers }),
            summary: this.http.get<PermSummary>(
                `${environment.apiUrl}/auth/roles/permissions/summary`, { headers: this.headers }),
        })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: ({ roles, users, catalog, summary }) => {
                    this.roles = roles || [];
                    this.users = users || [];
                    this.catalog = (catalog?.modules || []);
                    this.permSummary = summary;
                    this.isLoading = false;
                    if (!this.selectedRole && this.roles.length) {
                        this.selectRole(this.roles[0]);
                    }
                    this.cdr.detectChanges();
                },
                error: () => {
                    this.errorMsg = 'No se pudieron cargar los roles.';
                    this.isLoading = false;
                    this.cdr.detectChanges();
                },
            });
    }

    reloadRoles(): void {
        this.http.get<Role[]>(`${environment.apiUrl}/auth/roles`, { headers: this.headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (roles) => {
                    this.roles = roles || [];
                    if (this.selectedRole) {
                        const refreshed = this.roles.find(r => r.id === this.selectedRole!.id);
                        if (refreshed) this.selectedRole = refreshed;
                    }
                    this.cdr.detectChanges();
                },
            });
    }

    reloadSummary(): void {
        this.http.get<PermSummary>(`${environment.apiUrl}/auth/roles/permissions/summary`, { headers: this.headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({ next: (s) => { this.permSummary = s; this.cdr.detectChanges(); } });
    }

    // ============================================================
    // KPIs
    // ============================================================
    get totalRoles(): number { return this.roles.length; }
    get activeRoles(): number { return this.roles.filter(r => r.is_active !== false).length; }
    get activeRolesPct(): number {
        if (!this.totalRoles) return 0;
        return Math.round((this.activeRoles / this.totalRoles) * 100);
    }
    get usersAssignedCount(): number {
        return this.users.filter(u => (u.role || '').toLowerCase() !== 'sin rol').length;
    }
    get customPermissions(): number {
        return this.permSummary?.permissions_total ?? 0;
    }

    // ============================================================
    // Avatares por rol
    // ============================================================
    usersForRole(role: Role): UserSummary[] {
        if (!role?.name) return [];
        const key = role.name.toLowerCase();
        return this.users.filter(u => (u.role || '').toLowerCase() === key);
    }
    usersCountForRole(role: Role): number { return this.usersForRole(role).length; }
    avatarsForRole(role: Role): UserSummary[] { return this.usersForRole(role).slice(0, 4); }
    extraUsersForRole(role: Role): number {
        return Math.max(0, this.usersCountForRole(role) - this.avatarsForRole(role).length);
    }

    // ============================================================
    // Filtros
    // ============================================================
    get filteredRoles(): Role[] {
        const q = this.searchText.trim().toLowerCase();
        return this.roles.filter(r => {
            if (this.filterStatus === 'active' && r.is_active === false) return false;
            if (this.filterStatus === 'inactive' && r.is_active !== false) return false;
            if (this.filterType === 'system' && !r.is_system) return false;
            if (this.filterType === 'custom' && r.is_system) return false;
            const ucount = this.usersCountForRole(r);
            if (this.filterUsers === 'with' && ucount === 0) return false;
            if (this.filterUsers === 'without' && ucount > 0) return false;
            if (q) {
                const hay = `${r.id} ${r.name || ''} ${r.description || ''}`.toLowerCase();
                if (!hay.includes(q)) return false;
            }
            return true;
        });
    }
    get pagedRoles(): Role[] {
        const start = (this.currentPage - 1) * this.pageSize;
        return this.filteredRoles.slice(start, start + this.pageSize);
    }
    get totalFilteredPages(): number {
        return Math.max(1, Math.ceil(this.filteredRoles.length / this.pageSize));
    }
    get pageRangeLabel(): string {
        const total = this.filteredRoles.length;
        if (!total) return 'Mostrando 0-0 de 0 roles';
        const start = (this.currentPage - 1) * this.pageSize + 1;
        const end = Math.min(this.currentPage * this.pageSize, total);
        return `Mostrando ${start}-${end} de ${total} roles`;
    }
    setPage(p: number): void {
        if (p < 1 || p > this.totalFilteredPages) return;
        this.currentPage = p;
    }
    onFilterChange(): void { this.currentPage = 1; }
    toggleFilters(): void { this.showFilters = !this.showFilters; }
    clearFilters(): void {
        this.searchText = '';
        this.filterStatus = 'all';
        this.filterType = 'all';
        this.filterUsers = 'all';
        this.currentPage = 1;
    }
    setViewMode(m: ViewMode): void { this.viewMode = m; }
    get activeFilterCount(): number {
        let n = 0;
        if (this.filterStatus !== 'all') n++;
        if (this.filterType !== 'all')   n++;
        if (this.filterUsers !== 'all')  n++;
        return n;
    }

    // ============================================================
    // Kebab menu (igual a Reuniones)
    // ============================================================
    toggleRowMenu(roleId: number, ev: Event): void {
        ev.stopPropagation();
        this.openRowMenuId = this.openRowMenuId === roleId ? null : roleId;
    }
    closeRowMenu(): void { this.openRowMenuId = null; }

    // ============================================================
    // Selección
    // ============================================================
    selectRole(r: Role): void {
        this.selectedRole = r;
        this.panelCollapsed = false;
        this.loadSelectedDetails(r);
    }

    private loadSelectedDetails(r: Role): void {
        if (!r?.id) return;
        // Permisos
        this.http.get<{ permissions: PermissionRow[] }>(
            `${environment.apiUrl}/auth/roles/${r.id}/permissions`, { headers: this.headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (res) => { this.selectedPermissions = res.permissions || []; this.cdr.detectChanges(); },
                error: () => { this.selectedPermissions = []; },
            });
        // Actividad
        this.http.get<{ items: ActivityItem[] }>(
            `${environment.apiUrl}/auth/roles/${r.id}/activity?limit=10`, { headers: this.headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (res) => { this.selectedActivity = res.items || []; this.cdr.detectChanges(); },
                error: () => { this.selectedActivity = []; },
            });
        // Usuarios
        this.http.get<UserSummary[]>(
            `${environment.apiUrl}/auth/roles/${r.id}/users`, { headers: this.headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (res) => { this.selectedUsers = res || []; this.cdr.detectChanges(); },
                error: () => { this.selectedUsers = []; },
            });
    }

    togglePanel(): void { this.panelCollapsed = !this.panelCollapsed; }
    isSelected(r: Role): boolean { return !!(this.selectedRole && r.id === this.selectedRole.id); }

    // ============================================================
    // Acciones por rol — reales
    // ============================================================
    viewRole(r: Role, ev?: Event): void {
        ev?.stopPropagation();
        this.detailRole = r;
        this.showDetailModal = true;
        // Asegúrate que los detalles cargados sean del rol mostrado.
        if (!this.selectedRole || this.selectedRole.id !== r.id) this.selectRole(r);
    }
    closeDetailModal(): void { this.showDetailModal = false; this.detailRole = null; }

    openCreateModal(): void {
        this.roleModalMode = 'create';
        this.editingRoleId = null;
        this.roleForm = { name: '', description: '', is_active: true, permissions: {} };
        this.roleFormError = '';
        this.showRoleModal = true;
    }

    openEditModal(r: Role, ev?: Event): void {
        ev?.stopPropagation();
        this.roleModalMode = 'edit';
        this.editingRoleId = r.id;
        this.roleForm = {
            name: r.name,
            description: r.description || '',
            is_active: r.is_active !== false,
            permissions: {},
        };
        this.roleFormError = '';
        // Cargar permisos existentes y poblar checkboxes.
        this.http.get<{ permissions: PermissionRow[] }>(
            `${environment.apiUrl}/auth/roles/${r.id}/permissions`, { headers: this.headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (res) => {
                    const map: Record<string, Record<string, boolean>> = {};
                    for (const p of (res.permissions || [])) {
                        if (!map[p.module_key]) map[p.module_key] = {};
                        map[p.module_key][p.action] = !!p.is_granted;
                    }
                    this.roleForm.permissions = map;
                    this.cdr.detectChanges();
                },
            });
        this.showRoleModal = true;
    }

    closeRoleModal(): void {
        this.showRoleModal = false;
        this.editingRoleId = null;
        this.roleFormError = '';
    }

    togglePerm(moduleKey: string, action: string): void {
        if (!this.roleForm.permissions[moduleKey]) this.roleForm.permissions[moduleKey] = {};
        const cur = !!this.roleForm.permissions[moduleKey][action];
        this.roleForm.permissions[moduleKey][action] = !cur;
    }
    isPermChecked(moduleKey: string, action: string): boolean {
        return !!this.roleForm.permissions?.[moduleKey]?.[action];
    }
    selectAllForModule(m: CatalogModule, checked: boolean): void {
        if (!this.roleForm.permissions[m.key]) this.roleForm.permissions[m.key] = {};
        for (const a of m.actions) this.roleForm.permissions[m.key][a] = checked;
    }
    isModuleFullySelected(m: CatalogModule): boolean {
        const ps = this.roleForm.permissions[m.key] || {};
        return m.actions.every(a => ps[a]);
    }
    isModulePartiallySelected(m: CatalogModule): boolean {
        const ps = this.roleForm.permissions[m.key] || {};
        const some = m.actions.some(a => ps[a]);
        const all  = m.actions.every(a => ps[a]);
        return some && !all;
    }

    actionLabel(a: string): string { return ACTION_LABELS[a] || a; }

    private permissionsPayload(): { module_key: string; action: string; is_granted: boolean }[] {
        const out: { module_key: string; action: string; is_granted: boolean }[] = [];
        for (const m of Object.keys(this.roleForm.permissions || {})) {
            for (const a of Object.keys(this.roleForm.permissions[m] || {})) {
                if (this.roleForm.permissions[m][a]) out.push({ module_key: m, action: a, is_granted: true });
            }
        }
        return out;
    }

    saveRole(): void {
        if (!this.roleForm.name.trim()) {
            this.roleFormError = 'El nombre del rol es obligatorio.';
            return;
        }
        this.isSavingRole = true;
        this.roleFormError = '';
        const body = {
            name: this.roleForm.name.trim(),
            description: this.roleForm.description.trim(),
            is_active: this.roleForm.is_active,
            permissions: this.permissionsPayload(),
        };
        const req = this.roleModalMode === 'create'
            ? this.http.post<Role>(`${environment.apiUrl}/auth/roles`, body, { headers: this.headers })
            : this.http.put<Role>(`${environment.apiUrl}/auth/roles/${this.editingRoleId}`, body, { headers: this.headers });
        req.pipe(takeUntil(this.destroy$)).subscribe({
            next: () => {
                this.isSavingRole = false;
                this.toast.success(
                    this.roleModalMode === 'create' ? 'Rol creado exitosamente.' : 'Rol actualizado.'
                );
                this.closeRoleModal();
                this.reloadRoles();
                this.reloadSummary();
            },
            error: (err) => {
                this.isSavingRole = false;
                this.roleFormError = err?.error?.detail || 'No pude guardar el rol.';
                this.cdr.detectChanges();
            },
        });
    }

    toggleActive(r: Role, ev?: Event): void {
        ev?.stopPropagation();
        this.closeRowMenu();
        this.http.patch<Role>(`${environment.apiUrl}/auth/roles/${r.id}/toggle`, {}, { headers: this.headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (updated) => {
                    this.toast.success(`Rol ${updated.is_active ? 'activado' : 'desactivado'}.`);
                    this.reloadRoles();
                    if (this.selectedRole?.id === r.id) this.loadSelectedDetails(r);
                },
                error: (err) => {
                    this.toast.error(err?.error?.detail || 'No pude actualizar el estado.');
                },
            });
    }

    deleteRole(r: Role, ev?: Event): void {
        ev?.stopPropagation();
        this.closeRowMenu();
        this.deleteTarget = r;
        this.showDeleteModal = true;
    }
    cancelDelete(): void { this.showDeleteModal = false; this.deleteTarget = null; }
    confirmDelete(): void {
        if (!this.deleteTarget) return;
        this.isDeleting = true;
        this.http.delete<void>(`${environment.apiUrl}/auth/roles/${this.deleteTarget.id}`, { headers: this.headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: () => {
                    this.isDeleting = false;
                    this.toast.success('Rol eliminado.');
                    if (this.selectedRole?.id === this.deleteTarget!.id) this.selectedRole = null;
                    this.showDeleteModal = false;
                    this.deleteTarget = null;
                    this.reloadRoles();
                    this.reloadSummary();
                },
                error: (err) => {
                    this.isDeleting = false;
                    this.toast.error(err?.error?.detail || 'No pude eliminar el rol.');
                },
            });
    }

    // ============================================================
    // Utilidades
    // ============================================================
    roleTypeLabel(r: Role | null): string {
        if (!r) return '—';
        return r.is_system ? 'Sistema' : 'Personalizado';
    }
    initials(value: string): string {
        if (!value) return '?';
        const at = value.indexOf('@');
        const base = at > 0 ? value.slice(0, at) : value;
        const parts = base.replace(/[._-]+/g, ' ').trim().split(/\s+/);
        const a = parts[0]?.[0] || '';
        const b = parts.length > 1 ? parts[parts.length - 1][0] : '';
        return (a + b).toUpperCase() || base[0].toUpperCase();
    }
    private readonly _palette = ['#155EEF', '#10B981', '#F97316', '#EF4444', '#7C3AED', '#0EA5E9', '#D97706'];
    avatarColor(value: string): string {
        if (!value) return this._palette[0];
        let h = 0;
        for (let i = 0; i < value.length; i++) h = (h * 31 + value.charCodeAt(i)) | 0;
        return this._palette[Math.abs(h) % this._palette.length];
    }
    userTitle(u: UserSummary): string {
        return u.full_name || u.email || `Usuario #${u.id}`;
    }
    userMeta(u: UserSummary): string {
        return u.email || '';
    }
    activityTone(action: string): 'green' | 'blue' | 'orange' | 'red' {
        switch (action) {
            case 'created':       return 'green';
            case 'activated':     return 'green';
            case 'updated':       return 'blue';
            case 'permission_updated': return 'blue';
            case 'deactivated':   return 'orange';
            case 'deleted':       return 'red';
            default:              return 'blue';
        }
    }
    activityLabel(action: string): string {
        return ({
            created: 'Rol creado',
            updated: 'Rol actualizado',
            activated: 'Rol activado',
            deactivated: 'Rol desactivado',
            deleted: 'Rol eliminado',
            permission_updated: 'Permisos actualizados',
        } as Record<string, string>)[action] || action;
    }
    activityMeta(a: ActivityItem): string {
        const when = this.formatDateTime(a.created_at);
        const who = a.actor_name || 'Sistema';
        return `${when} · ${who}`;
    }
    formatDateTime(iso: string): string {
        if (!iso) return '—';
        try {
            const d = new Date(iso);
            if (isNaN(d.getTime())) return iso;
            return d.toLocaleString('es-CO', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
        } catch { return iso; }
    }
    formatDate(iso: string | undefined): string {
        if (!iso) return '—';
        try {
            const d = new Date(iso);
            if (isNaN(d.getTime())) return iso;
            return d.toLocaleDateString('es-CO', { day: '2-digit', month: 'short', year: 'numeric' });
        } catch { return iso; }
    }

    trackRole = (_: number, r: Role) => r.id;
    trackUser = (_: number, u: UserSummary) => u.id;
    trackModule = (_: number, m: CatalogModule) => m.key;
    trackAction = (_: number, a: string) => a;
    trackActivity = (_: number, a: ActivityItem) => a.id;
}
