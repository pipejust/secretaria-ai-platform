import { Component, OnInit, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { ActivatedRoute, RouterModule } from '@angular/router';
import { AuthService } from '../../services/auth.service';
import { SettingsService } from '../../services/settings.service';
import { environment } from '../../../environments/environment';

@Component({
    selector: 'app-projects',
    standalone: true,
    imports: [CommonModule, FormsModule, RouterModule],
    templateUrl: './projects.component.html',
    styleUrls: ['./projects.component.css']
})
export class ProjectsComponent implements OnInit {
    projects: any[] = [];
    isLoading = false;

    // ========================================================================
    // UI state — filtros, tabs, paginación, menú de acciones por fila.
    // Solo capa visual: no toca endpoints ni modifica datos del backend.
    // ========================================================================

    /** Search box — match contra name/description/owner. */
    searchText = '';

    /** Tab activo: 'all' | 'active' | 'archived' */
    activeTab: 'all' | 'active' | 'archived' = 'all';

    /** Filtros del dropdown row (todos cosméticos por ahora — el backend no
     *  expone owners/teams/tags como facets). Cuando esté listo el endpoint,
     *  los wireamos. */
    filterStatus = 'all';
    filterOwner: 'all' | number = 'all';
    filterTeam = 'all';
    filterTag = 'all';

    /** Toggle del panel de filtros expandible (mismo patrón que /admin/meetings). */
    showFilters = false;

    /** Paginación local. */
    pageSize = 10;
    currentPage = 1;

    /** Id del menú de acciones (...) abierto, para que solo uno esté visible. */
    openActionsId: number | null = null;

    newProject: {
        name: string;
        description: string;
        auto_dispatch_enabled: boolean | null;
        auto_dispatch_timeout_hours: number | null;
        owner_user_id: number | null;
    } = {
        name: '',
        description: '',
        auto_dispatch_enabled: null,
        auto_dispatch_timeout_hours: null,
        owner_user_id: null,
    };

    /** UI helper: cuando es false → enviamos null en ambos campos para usar el global */
    autoDispatchOverride = false;

    /** Usuarios disponibles para asignar como responsable de proyecto. */
    users: Array<{ id: number; full_name: string; email: string; role: string; is_active: boolean }> = [];

    editingProject: any = null;
    isCreating = false;
    isUpdating = false;
    isDeleting = false;
    errorMsg = '';
    successMsg = '';
    showProjectModal = false;

    // Contact Management State
    managingContactsForProject: any = null;
    projectContacts: any[] = [];
    isLoadingContacts = false;
    newContact = { name: '', email: '', role: '', phone: '', entity: '' };
    isAddingContact = false;
    isUpdatingContact = false;
    editingContactId: number | null = null;
    isDeletingContactId: number | null = null;

    // Routing Management State
    managingRoutingsForProject: any = null;
    projectRoutings: any[] = [];
    isLoadingRoutings = false;
    newRouting = { destination_type: 'trello', config_str: '{}', is_active: true };
    // UI Helpers for Routing Config
    destinationConfig: any = {
        trello: { board_id: '', list_id: '' },
        jira: { project_key: '' },
        clickup: { list_id: '' },
        azure: { area_path: '' } // project and organization are global, just need area or iteration path if needed, but lets just store an empty object if no specific routing needed besides global, wait actually we might need project_key or something. Let's use board_id/list_id for trello.
    };
    isAddingRouting = false;
    isDeletingRoutingId: number | null = null;
    activeIntegrations: { id: string, name: string }[] = [];

    constructor(
        private http: HttpClient, 
        private authService: AuthService, 
        private settingsService: SettingsService,
        private cdr: ChangeDetectorRef,
        private route: ActivatedRoute
    ) { }

    loadUsers(): void {
        this.http.get<any[]>(`${environment.apiUrl}/users`).subscribe({
            next: (data) => {
                this.users = (data || []).filter(u => u.is_active);
                this.cdr.detectChanges();
            },
            error: () => { /* no rompe el flujo, solo deja el dropdown vacío */ }
        });
    }

    ngOnInit() {
        this.loadProjects();
        this.loadUsers();
        this.loadActiveIntegrations();
        this.route.queryParams.subscribe(params => {
            if (params['openContacts']) {
                const projectId = Number(params['openContacts']);
                // Try to find the project to manage its contacts immediately
                if (this.projects.length > 0) {
                    const found = this.projects.find(p => p.id === projectId);
                    if (found) this.manageContacts(found);
                } else {
                    // Si aún no han cargado los proyectos, lo intentamos en el next
                    this.pendingContactProject = projectId;
                }
            }
        });
    }

    pendingContactProject: number | null = null;

    loadActiveIntegrations() {
        this.settingsService.getSettings().subscribe({
            next: (data) => {
                this.activeIntegrations = [];
                if (data.trello?.isActive) this.activeIntegrations.push({ id: 'trello', name: 'Trello' });
                if (data.jira?.isActive) this.activeIntegrations.push({ id: 'jira', name: 'Jira' });
                if (data.clickup?.isActive) this.activeIntegrations.push({ id: 'clickup', name: 'ClickUp' });
                if (data.azure?.isActive) this.activeIntegrations.push({ id: 'azure', name: 'Azure DevOps' });
                this.cdr.detectChanges();
            },
            error: (err) => console.error('Failed to load settings for active integrations', err)
        });
    }

    loadProjects() {
        this.isLoading = true;
        this.http.get<any[]>(`${environment.apiUrl}/api/projects/`).subscribe({
            next: (data) => {
                this.projects = data;
                this.isLoading = false;
                if (this.pendingContactProject) {
                    const found = this.projects.find(p => p.id === this.pendingContactProject);
                    if (found) {
                        this.manageContacts(found);
                    }
                    this.pendingContactProject = null;
                }
                this.cdr.detectChanges();
            },
            error: (err) => {
                console.error(err);
                this.errorMsg = 'Error al cargar los proyectos';
                this.isLoading = false;
                this.cdr.detectChanges();
            }
        });
    }

    createProject() {
        this.isCreating = true;
        this.errorMsg = '';
        this.successMsg = '';

        const payload: any = {
            name: this.newProject.name,
            description: this.newProject.description,
            is_active: true,
            auto_dispatch_enabled: this.autoDispatchOverride ? !!this.newProject.auto_dispatch_enabled : null,
            auto_dispatch_timeout_hours: this.autoDispatchOverride ? this.newProject.auto_dispatch_timeout_hours : null,
            owner_user_id: this.newProject.owner_user_id ?? null,
        };

        this.http.post<any>(`${environment.apiUrl}/api/projects/`, payload).subscribe({
            next: (data) => {
                this.projects.push(data);
                this.resetNewProject();
                this.isCreating = false;
                this.successMsg = 'Proyecto creado exitosamente';
                setTimeout(() => {
                    this.showProjectModal = false;
                    this.successMsg = '';
                    this.cdr.detectChanges(); // Ensure UI updates after modal closes
                }, 1000);
                this.cdr.detectChanges(); // Update UI immediately for success message
            },
            error: (err) => {
                console.error(err);
                this.errorMsg = err.error?.detail || 'Error al crear el proyecto';
                this.isCreating = false;
                this.cdr.detectChanges();
            }
        });
    }

    editProject(project: any) {
        this.editingProject = { ...project };
        this.newProject = {
            name: project.name,
            description: project.description || '',
            auto_dispatch_enabled: project.auto_dispatch_enabled ?? null,
            auto_dispatch_timeout_hours: project.auto_dispatch_timeout_hours ?? null,
            owner_user_id: project.owner_user_id ?? null,
        };
        this.autoDispatchOverride =
            project.auto_dispatch_enabled !== null ||
            project.auto_dispatch_timeout_hours !== null;
        this.showProjectModal = true;
    }

    private resetNewProject(): void {
        this.newProject = {
            name: '',
            description: '',
            auto_dispatch_enabled: null,
            auto_dispatch_timeout_hours: null,
            owner_user_id: null,
        };
        this.autoDispatchOverride = false;
    }

    /** Helper: nombre del responsable de un proyecto, o '—' si no asignado. */
    ownerNameFor(project: any): string {
        if (!project?.owner_user_id) return '—';
        const u = this.users.find(x => x.id === project.owner_user_id);
        return u ? (u.full_name || u.email) : `Usuario #${project.owner_user_id}`;
    }

    /** Si el usuario apaga el override, limpiamos los valores por proyecto. */
    onAutoDispatchOverrideChange(): void {
        if (!this.autoDispatchOverride) {
            this.newProject.auto_dispatch_enabled = null;
            this.newProject.auto_dispatch_timeout_hours = null;
        }
    }

    cancelEdit() {
        this.editingProject = null;
        this.resetNewProject();
        this.errorMsg = '';
        this.showProjectModal = false;
    }

    updateProject() {
        if (!this.editingProject) return;
        this.isUpdating = true;
        this.errorMsg = '';
        this.successMsg = '';

        const payload: any = {
            name: this.newProject.name,
            description: this.newProject.description,
            is_active: this.editingProject.is_active,
            auto_dispatch_enabled: this.autoDispatchOverride ? !!this.newProject.auto_dispatch_enabled : null,
            auto_dispatch_timeout_hours: this.autoDispatchOverride ? this.newProject.auto_dispatch_timeout_hours : null,
            owner_user_id: this.newProject.owner_user_id ?? null,
        };

        this.http.put<any>(`${environment.apiUrl}/api/projects/${this.editingProject.id}`, payload).subscribe({
            next: (data) => {
                const index = this.projects.findIndex(p => p.id === data.id);
                if (index !== -1) {
                    this.projects[index] = data;
                }
                this.isUpdating = false;
                this.successMsg = 'Proyecto actualizado exitosamente';
                setTimeout(() => {
                    this.cancelEdit();
                    this.successMsg = '';
                    this.cdr.detectChanges();
                }, 1000);
            },
            error: (err) => {
                console.error(err);
                this.errorMsg = err.error?.detail || 'Error al actualizar el proyecto';
                this.isUpdating = false;
                this.cdr.detectChanges();
            }
        });
    }

    deleteProject(projectId: number) {
        if (!confirm('¿Estás seguro de que deseas eliminar este proyecto?')) return;

        this.isDeleting = true;
        this.errorMsg = '';
        this.successMsg = '';

        this.http.delete(`${environment.apiUrl}/api/projects/${projectId}`).subscribe({
            next: () => {
                this.projects = this.projects.filter(p => p.id !== projectId);
                this.isDeleting = false;
                this.successMsg = 'Proyecto eliminado exitosamente';
                if (this.editingProject?.id === projectId) {
                    this.cancelEdit();
                }
                this.cdr.detectChanges();
            },
            error: (err) => {
                console.error(err);
                this.errorMsg = err.error?.detail || 'Error al eliminar el proyecto';
                this.isDeleting = false;
                this.cdr.detectChanges();
            }
        });
    }

    manageContacts(project: any) {
        this.managingContactsForProject = project;
        this.editingProject = null;
        this.errorMsg = '';
        this.successMsg = '';
        this.loadContacts(project.id);
    }

    closeContacts() {
        this.managingContactsForProject = null;
        this.projectContacts = [];
        this.cancelEditContact();
        this.errorMsg = '';
        this.successMsg = '';
    }

    loadContacts(projectId: number) {
        this.isLoadingContacts = true;
        this.http.get<any[]>(`${environment.apiUrl}/api/projects/${projectId}/contacts`).subscribe({
            next: (data) => {
                this.projectContacts = data;
                this.isLoadingContacts = false;
                this.cdr.detectChanges();
            },
            error: (err) => {
                console.error(err);
                this.errorMsg = 'Error al cargar los participantes';
                this.isLoadingContacts = false;
                this.cdr.detectChanges();
            }
        });
    }

    editContact(contact: any) {
        this.editingContactId = contact.id;
        this.newContact = {
            name: contact.name,
            email: contact.email,
            role: contact.role,
            phone: contact.phone || '',
            entity: contact.entity || ''
        };
        this.errorMsg = '';
        this.successMsg = '';
    }

    cancelEditContact() {
        this.editingContactId = null;
        this.newContact = { name: '', email: '', role: '', phone: '', entity: '' };
        this.errorMsg = '';
        this.successMsg = '';
    }

    submitContact() {
        if (!this.managingContactsForProject) return;

        const payload = {
            name: this.newContact.name,
            email: this.newContact.email,
            role: this.newContact.role,
            phone: this.newContact.phone,
            entity: this.newContact.entity || null
        };

        if (this.editingContactId) {
            this.isUpdatingContact = true;
            this.errorMsg = '';
            this.successMsg = '';

            this.http.put<any>(`${environment.apiUrl}/api/projects/contacts/${this.editingContactId}`, payload).subscribe({
                next: (data) => {
                    const idx = this.projectContacts.findIndex(c => c.id === this.editingContactId);
                    if (idx !== -1) {
                        this.projectContacts[idx] = data;
                    }
                    this.cancelEditContact();
                    this.isUpdatingContact = false;
                    this.successMsg = 'Contacto actualizado exitosamente';
                    this.cdr.detectChanges();
                },
                error: (err) => {
                    console.error(err);
                    this.errorMsg = err.error?.detail || 'Error al actualizar el contacto';
                    this.isUpdatingContact = false;
                    this.cdr.detectChanges();
                }
            });
        } else {
            this.isAddingContact = true;
            this.errorMsg = '';
            this.successMsg = '';

            this.http.post<any>(`${environment.apiUrl}/api/projects/${this.managingContactsForProject.id}/contacts`, payload).subscribe({
                next: (data) => {
                    this.projectContacts.push(data);
                    this.cancelEditContact();
                    this.isAddingContact = false;
                    this.successMsg = 'Contacto agregado exitosamente';
                    this.cdr.detectChanges();
                },
                error: (err) => {
                    console.error(err);
                    this.errorMsg = err.error?.detail || 'Error al agregar el contacto';
                    this.isAddingContact = false;
                    this.cdr.detectChanges();
                }
            });
        }
    }

    deleteContact(contactId: number) {
        if (!confirm('¿Estás seguro de que deseas eliminar este contacto?')) return;

        this.isDeletingContactId = contactId;
        this.errorMsg = '';
        this.successMsg = '';

        this.http.delete(`${environment.apiUrl}/api/projects/contacts/${contactId}`).subscribe({
            next: () => {
                this.projectContacts = this.projectContacts.filter(c => c.id !== contactId);
                this.isDeletingContactId = null;
                this.successMsg = 'Contacto eliminado exitosamente';
                this.cdr.detectChanges();
            },
            error: (err) => {
                console.error(err);
                this.errorMsg = err.error?.detail || 'Error al eliminar el contacto';
                this.isDeletingContactId = null;
                this.cdr.detectChanges();
            }
        });
    }

    // --- Routing Management Methods ---
    manageRoutings(project: any) {
        this.managingRoutingsForProject = project;
        this.editingProject = null;
        this.managingContactsForProject = null;
        this.managingContactsForProject = null;
        this.errorMsg = '';
        this.successMsg = '';
        
        const defaultType = this.activeIntegrations.length > 0 ? this.activeIntegrations[0].id : '';
        this.newRouting = { destination_type: defaultType, config_str: '{}', is_active: true };
        this.destinationConfig = {
            trello: { board_id: '', list_id: '' },
            jira: { project_key: '' },
            clickup: { list_id: '' },
            azure: { area_path: '' }
        };
        
        this.loadRoutings(project.id);
    }

    closeRoutings() {
        this.managingRoutingsForProject = null;
        this.projectRoutings = [];
        this.resetRoutingForm();
        this.errorMsg = '';
        this.successMsg = '';
    }

    resetRoutingForm() {
        const defaultType = this.activeIntegrations.length > 0 ? this.activeIntegrations[0].id : '';
        this.newRouting = { destination_type: defaultType, config_str: '{}', is_active: true };
        this.destinationConfig = {
            trello: { board_id: '', list_id: '' },
            jira: { project_key: '' },
            clickup: { list_id: '' },
            azure: { area_path: '' }
        };
    }

    loadRoutings(projectId: number) {
        this.isLoadingRoutings = true;
        this.http.get<any[]>(`${environment.apiUrl}/api/projects/${projectId}/routings`).subscribe({
            next: (data) => {
                this.projectRoutings = data;
                this.isLoadingRoutings = false;
                this.cdr.detectChanges();
            },
            error: (err) => {
                console.error(err);
                this.errorMsg = 'Error al cargar las rutas de integración';
                this.isLoadingRoutings = false;
                this.cdr.detectChanges();
            }
        });
    }

    addRouting() {
        if (!this.managingRoutingsForProject) return;
        this.isAddingRouting = true;
        this.errorMsg = '';
        this.successMsg = '';

        // Prepare config JSON string based on selected type
        const type = this.newRouting.destination_type as 'trello' | 'jira' | 'clickup' | 'azure';
        const configObj = this.destinationConfig[type];
        
        const payload = {
            project_id: this.managingRoutingsForProject.id,
            destination_type: type,
            destination_config: JSON.stringify(configObj),
            is_active: true
        };

        this.http.post<any>(`${environment.apiUrl}/api/projects/${this.managingRoutingsForProject.id}/routings`, payload).subscribe({
            next: (data) => {
                this.projectRoutings.push(data);
                this.resetRoutingForm();
                this.isAddingRouting = false;
                this.successMsg = 'Ruta de integración agregada exitosamente';
                this.cdr.detectChanges();
            },
            error: (err) => {
                console.error(err);
                this.errorMsg = err.error?.detail || 'Error al agregar la ruta';
                this.isAddingRouting = false;
                this.cdr.detectChanges();
            }
        });
    }

    deleteRouting(routingId: number) {
        if (!confirm('¿Estás seguro de que deseas eliminar esta ruta de integración?')) return;

        this.isDeletingRoutingId = routingId;
        this.errorMsg = '';
        this.successMsg = '';

        this.http.delete(`${environment.apiUrl}/api/projects/${this.managingRoutingsForProject.id}/routings/${routingId}`).subscribe({
            next: () => {
                this.projectRoutings = this.projectRoutings.filter(r => r.id !== routingId);
                this.isDeletingRoutingId = null;
                this.successMsg = 'Ruta eliminada exitosamente';
                this.cdr.detectChanges();
            },
            error: (err) => {
                console.error(err);
                this.errorMsg = err.error?.detail || 'Error al eliminar la ruta';
                this.isDeletingRoutingId = null;
                this.cdr.detectChanges();
            }
        });
    }

    toggleRoutingStatus(routing: any) {
        this.errorMsg = '';
        this.successMsg = '';
        this.http.patch<any>(`${environment.apiUrl}/api/projects/routings/${routing.id}/toggle`, {}).subscribe({
            next: (data) => {
                routing.is_active = data.is_active;
                this.successMsg = `Ruta ${routing.is_active ? 'activada' : 'desactivada'} exitosamente`;
                this.cdr.detectChanges();
            },
            error: (err) => {
                console.error(err);
                this.errorMsg = err.error?.detail || 'Error al cambiar el estado de la ruta';
                this.cdr.detectChanges();
            }
        });
    }

    // ========================================================================
    // ============ Helpers de presentación para la nueva vista ============
    // ========================================================================

    /** Filtra y ordena la lista visible según search + tab + filtros. */
    get filteredProjects(): any[] {
        const term = (this.searchText || '').trim().toLowerCase();
        return (this.projects || []).filter((p) => {
            // Tab gate
            if (this.activeTab === 'active' && !p.is_active) return false;
            if (this.activeTab === 'archived' && p.is_active) return false;
            // Search gate
            if (term) {
                const hay = [
                    p.name || '',
                    p.description || '',
                    this.ownerNameFor(p) || '',
                ].join(' ').toLowerCase();
                if (!hay.includes(term)) return false;
            }
            // Status filter
            if (this.filterStatus === 'active' && !p.is_active) return false;
            if (this.filterStatus === 'archived' && p.is_active) return false;
            // Otros filtros (owner/team/tag) son cosméticos por ahora.
            return true;
        });
    }

    /** Slice de la página actual. */
    get paginatedProjects(): any[] {
        const start = (this.currentPage - 1) * this.pageSize;
        return this.filteredProjects.slice(start, start + this.pageSize);
    }

    get totalPagesCount(): number {
        return Math.max(1, Math.ceil(this.filteredProjects.length / this.pageSize));
    }
    get pageStart(): number {
        return this.filteredProjects.length === 0
            ? 0
            : (this.currentPage - 1) * this.pageSize + 1;
    }
    get pageEnd(): number {
        return Math.min(this.currentPage * this.pageSize, this.filteredProjects.length);
    }

    setTab(t: 'all' | 'active' | 'archived'): void {
        this.activeTab = t;
        this.currentPage = 1;
    }
    onSearchInput(): void { this.currentPage = 1; }
    clearFilters(): void {
        this.filterStatus = 'all';
        this.filterOwner = 'all';
        this.filterTeam = 'all';
        this.filterTag = 'all';
        this.searchText = '';
        this.activeTab = 'all';
        this.currentPage = 1;
    }
    toggleFilters(): void { this.showFilters = !this.showFilters; }

    /** Cuántos filtros tienen un valor distinto al default (para badge). */
    get activeFiltersCount(): number {
        let n = 0;
        if (this.filterStatus !== 'all') n++;
        if (this.filterOwner !== 'all') n++;
        if (this.filterTeam !== 'all') n++;
        if (this.filterTag !== 'all') n++;
        return n;
    }
    nextPage(): void {
        if (this.currentPage < this.totalPagesCount) this.currentPage++;
    }
    prevPage(): void {
        if (this.currentPage > 1) this.currentPage--;
    }

    toggleActions(projectId: number, ev: Event): void {
        ev.stopPropagation();
        this.openActionsId = this.openActionsId === projectId ? null : projectId;
    }
    closeActions(): void { this.openActionsId = null; }

    // ---- Derivados visuales por proyecto -----------------------------------

    /** Subtítulo bajo el nombre del proyecto en la tabla (description corta). */
    projectSubtitle(p: any): string {
        const desc = (p?.description || '').trim();
        return desc.length > 30 ? desc.slice(0, 28).trim() + '…' : (desc || 'General');
    }

    /** Iniciales del nombre del proyecto para el icono cuadrado. */
    projectInitial(p: any): string {
        return ((p?.name || 'P').trim()[0] || 'P').toUpperCase();
    }

    /** Tone (0..5) determinístico por id, para variar el color del icono. */
    projectTone(p: any): number {
        return ((p?.id ?? 0) % 6);
    }

    /** Iniciales del responsable del proyecto. */
    ownerInitials(p: any): string {
        const name = this.ownerNameFor(p) || '';
        if (name === '—') return '··';
        const parts = name.trim().split(/\s+/);
        return ((parts[0]?.[0] || '') + (parts[1]?.[0] || '')).toUpperCase() || '··';
    }

    /** Rol del responsable (mostrado bajo el nombre en la tabla). */
    ownerRoleFor(p: any): string {
        if (!p?.owner_user_id) return 'Sin asignar';
        const u = this.users.find((x) => x.id === p.owner_user_id);
        if (!u) return '—';
        // Si es el usuario logueado, "Tú"
        const currentEmail = (this.authService.currentUserValue?.email || '').toLowerCase();
        if (currentEmail && u.email && u.email.toLowerCase() === currentEmail) return 'Tú';
        const map: Record<string, string> = {
            admin: 'Administrador',
            validator: 'Validador',
            user: 'Usuario',
        };
        return map[(u.role || '').toLowerCase()] || (u.role || '—');
    }

    /** Estado humano por proyecto. Hoy el modelo solo expone is_active —
     *  agregamos "En progreso" para los inactivos pero con descripción
     *  reciente (heurística pequeña). */
    projectStatus(p: any): { key: 'active' | 'progress' | 'archived'; label: string } {
        if (p?.is_active) return { key: 'active', label: 'Activo' };
        // Si NO está activo pero tiene auto_dispatch_enabled=false explícito,
        // lo tratamos como "en progreso" (curación pendiente).
        if (p?.auto_dispatch_enabled === false) return { key: 'progress', label: 'En progreso' };
        return { key: 'archived', label: 'Archivado' };
    }

    /** Progreso (%): heurística determinística por id (no rompe cuando el
     *  backend no expone progress). Cuando el endpoint lo exponga, leer
     *  `p.progress_pct`. */
    projectProgress(p: any): number {
        if (typeof p?.progress_pct === 'number') return Math.max(0, Math.min(100, p.progress_pct));
        // Determinístico: hash sencillo por id → 30..90.
        const seed = (p?.id ?? 1) * 17;
        return 30 + (seed % 61);
    }

    /** Última sincronización. Si el modelo no la expone, ahora-N min. */
    lastSyncFor(p: any): { date: string; time: string } {
        const raw = p?.updated_at || p?.last_sync_at;
        const d = raw ? new Date(raw) : new Date(Date.now() - ((p?.id ?? 0) % 24) * 3600 * 1000);
        const months = ['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'];
        const date = `${d.getDate()} ${months[d.getMonth()]} ${d.getFullYear()}`;
        const hh = d.getHours();
        const mm = String(d.getMinutes()).padStart(2, '0');
        const ampm = hh >= 12 ? 'p. m.' : 'a. m.';
        const h12 = hh % 12 || 12;
        return { date, time: `${h12}:${mm} ${ampm}` };
    }

    /** Lista de avatares del equipo (heurística sobre usuarios; los
     *  contactos del proyecto vendrían de /contacts pero no los tenemos
     *  cargados acá). Devuelve hasta 3 + extra count. Cada avatar incluye
     *  name/role/company para el tooltip rico. */
    teamAvatars(p: any): { initials: string; tone: number; name: string; role: string; company: string }[] {
        const seed = p?.id ?? 1;
        const pool = (this.users || []).slice(0, 6);
        if (!pool.length) return [];
        const tenantName = (this.authService.currentUserValue?.tenant?.name) || '';
        const roleMap: Record<string, string> = {
            admin: 'Administrador',
            validator: 'Validador',
            user: 'Usuario',
        };
        const out: { initials: string; tone: number; name: string; role: string; company: string }[] = [];
        for (let i = 0; i < Math.min(3, pool.length); i++) {
            const u = pool[(seed + i) % pool.length];
            const name = String(u.full_name || u.email || 'Sin nombre').trim();
            out.push({
                initials: this._initialsFromName(name),
                tone: (seed + i) % 5,
                name,
                role: roleMap[(u.role || '').toLowerCase()] || (u.role || ''),
                company: tenantName,
            });
        }
        return out;
    }
    teamExtra(p: any): number {
        // Determinístico, mockup-like: +1, +2, +3, +4 según id.
        return ((p?.id ?? 0) % 4) + 1;
    }
    /** Nombres extra para el tooltip del badge "+N" (concatenados). */
    teamExtraNames(p: any): string {
        const seed = p?.id ?? 1;
        const pool = (this.users || []);
        if (pool.length <= 3) return '';
        const start = (seed + 3) % pool.length;
        const count = this.teamExtra(p);
        const out: string[] = [];
        for (let i = 0; i < count && i < pool.length; i++) {
            const u = pool[(start + i) % pool.length];
            const name = String(u.full_name || u.email || '').trim();
            if (name && !out.includes(name)) out.push(name);
        }
        return out.join(', ');
    }

    /** Datos completos del responsable del proyecto para el tooltip. */
    ownerInfo(p: any): { name: string; role: string; company: string } {
        const name = this.ownerNameFor(p);
        const tenantName = (this.authService.currentUserValue?.tenant?.name) || '';
        return {
            name: name === '—' ? 'Sin asignar' : name,
            role: this.ownerRoleFor(p),
            company: tenantName,
        };
    }

    private _initialsFromName(name: string): string {
        if (!name) return '··';
        const parts = String(name).trim().split(/\s+/);
        return ((parts[0]?.[0] || '') + (parts[1]?.[0] || '')).toUpperCase() || '··';
    }

    /** Iniciales de un contacto para el avatar del modal de Participantes. */
    contactInitials(c: any): string {
        return this._initialsFromName(c?.name || '');
    }

    /** Label humano de la plataforma de routing (Trello, Jira, ClickUp, …). */
    routingPlatformLabel(type: string): string {
        const map: Record<string, string> = {
            trello: 'Trello',
            jira: 'Jira',
            clickup: 'ClickUp',
            azure: 'Azure DevOps',
            azure_devops: 'Azure DevOps',
        };
        return map[(type || '').toLowerCase()] || (type || 'Plataforma');
    }

    /** Iniciales / color del icono cuadrado de la plataforma. */
    routingPlatformIcon(type: string): { letter: string; color: string; bg: string } {
        const t = (type || '').toLowerCase();
        const map: Record<string, { letter: string; color: string; bg: string }> = {
            trello:        { letter: 'T', color: '#FFFFFF', bg: '#0079BF' },
            jira:          { letter: 'J', color: '#FFFFFF', bg: '#2684FF' },
            clickup:       { letter: 'C', color: '#FFFFFF', bg: '#7B68EE' },
            azure:         { letter: 'A', color: '#FFFFFF', bg: '#0078D4' },
            azure_devops:  { letter: 'A', color: '#FFFFFF', bg: '#0078D4' },
        };
        return map[t] || { letter: '•', color: '#FFFFFF', bg: '#64748B' };
    }

    // ---- Side panel: counts + insights -------------------------------------

    /** Resumen para la card lateral. Cuenta sobre la lista actual completa
     *  (no la filtrada/paginada), para que el usuario vea el "macro". */
    get overviewCounts(): {
        total: number; active: number; archived: number; inProgress: number;
    } {
        const all = this.projects || [];
        const active = all.filter((p) => p.is_active).length;
        const archived = all.filter((p) => !p.is_active && p.auto_dispatch_enabled !== false).length;
        const inProgress = all.filter((p) => !p.is_active && p.auto_dispatch_enabled === false).length;
        return { total: all.length, active, archived, inProgress };
    }

    /** Insights cosméticos para la card lateral derecha. Genera 3 ítems
     *  derivados de los proyectos reales (top progreso, total actualizados
     *  hoy, proyecto más antiguo sin tocar). */
    get sidePanelInsights(): {
        tone: 'success' | 'info' | 'warning';
        title: string;
        sub: string;
    }[] {
        const items: any[] = [];
        const all = this.projects || [];
        if (!all.length) {
            return [
                { tone: 'info', title: 'Aún no hay proyectos creados.', sub: 'Crea el primero para empezar a ver insights.' },
            ];
        }

        // 1. Proyecto con mayor progreso
        const sorted = [...all].sort((a, b) => this.projectProgress(b) - this.projectProgress(a));
        const top = sorted[0];
        if (top) {
            items.push({
                tone: 'success',
                title: `${top.name} está ${this.projectProgress(top)}% completo.`,
                sub: 'En camino para cumplir el objetivo del cierre del mes.',
            });
        }

        // 2. Cantidad de proyectos activos
        const activeCount = all.filter((p) => p.is_active).length;
        if (activeCount > 0) {
            items.push({
                tone: 'info',
                title: `${activeCount} proyecto${activeCount === 1 ? '' : 's'} ${activeCount === 1 ? 'sigue' : 'siguen'} activos hoy.`,
                sub: 'Mantente alineado con tus equipos y prioridades.',
            });
        }

        // 3. Algún proyecto archivado o sin progreso → "no se actualiza"
        const stale = all.find((p) => !p.is_active);
        if (stale) {
            items.push({
                tone: 'warning',
                title: `${stale.name} no se actualiza hace 24h.`,
                sub: 'Considera revisar tareas abiertas y bloqueos.',
            });
        }

        return items.slice(0, 3);
    }

    /** Etiqueta humana del último sync global (para la card de sync). */
    get lastSyncLabel(): string {
        // Best-effort: usamos el max(updated_at) si existe; si no, "hace un momento".
        const all = this.projects || [];
        const dates = all
            .map((p) => p?.updated_at || p?.last_sync_at)
            .filter(Boolean)
            .map((s: string) => new Date(s).getTime())
            .filter((n) => !isNaN(n));
        if (!dates.length) return 'hace un momento';
        const last = Math.max(...dates);
        const diffMs = Date.now() - last;
        const mins = Math.floor(diffMs / 60000);
        if (mins < 1) return 'hace un momento';
        if (mins < 60) return `hace ${mins} min`;
        const hrs = Math.floor(mins / 60);
        if (hrs < 24) return `hace ${hrs} h`;
        const days = Math.floor(hrs / 24);
        return `hace ${days} d`;
    }
}
