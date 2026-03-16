import { Component, OnInit, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { ActivatedRoute } from '@angular/router';
import { AuthService } from '../../services/auth.service';
import { SettingsService } from '../../services/settings.service';
import { environment } from '../../../environments/environment';

@Component({
    selector: 'app-projects',
    standalone: true,
    imports: [CommonModule, FormsModule],
    templateUrl: './projects.component.html',
    styleUrls: ['./projects.component.css']
})
export class ProjectsComponent implements OnInit {
    projects: any[] = [];
    isLoading = false;

    newProject = { name: '', description: '' };
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
    newContact = { name: '', email: '', role: '', phone: '' };
    isAddingContact = false;
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

    ngOnInit() {
        this.loadProjects();
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
        this.http.get<any[]>(`${environment.apiUrl}/projects/`).subscribe({
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

        const payload = {
            name: this.newProject.name,
            description: this.newProject.description,
            is_active: true
        };

        this.http.post<any>(`${environment.apiUrl}/projects/`, payload).subscribe({
            next: (data) => {
                this.projects.push(data);
                this.newProject = { name: '', description: '' };
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
        this.newProject = { name: project.name, description: project.description || '' };
        this.showProjectModal = true;
    }

    cancelEdit() {
        this.editingProject = null;
        this.newProject = { name: '', description: '' };
        this.errorMsg = '';
        this.showProjectModal = false;
    }

    updateProject() {
        if (!this.editingProject) return;
        this.isUpdating = true;
        this.errorMsg = '';
        this.successMsg = '';

        const payload = {
            name: this.newProject.name,
            description: this.newProject.description,
            is_active: this.editingProject.is_active
        };

        this.http.put<any>(`${environment.apiUrl}/projects/${this.editingProject.id}`, payload).subscribe({
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

        this.http.delete(`${environment.apiUrl}/projects/${projectId}`).subscribe({
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
        this.newContact = { name: '', email: '', role: '', phone: '' };
        this.errorMsg = '';
        this.successMsg = '';
    }

    loadContacts(projectId: number) {
        this.isLoadingContacts = true;
        this.http.get<any[]>(`${environment.apiUrl}/projects/${projectId}/contacts`).subscribe({
            next: (data) => {
                this.projectContacts = data;
                this.isLoadingContacts = false;
                this.cdr.detectChanges();
            },
            error: (err) => {
                console.error(err);
                this.errorMsg = 'Error al cargar los contactos';
                this.isLoadingContacts = false;
                this.cdr.detectChanges();
            }
        });
    }

    addContact() {
        if (!this.managingContactsForProject) return;
        this.isAddingContact = true;
        this.errorMsg = '';
        this.successMsg = '';

        const payload = {
            name: this.newContact.name,
            email: this.newContact.email,
            role: this.newContact.role,
            phone: this.newContact.phone
        };

        this.http.post<any>(`${environment.apiUrl}/projects/${this.managingContactsForProject.id}/contacts`, payload).subscribe({
            next: (data) => {
                this.projectContacts.push(data);
                this.newContact = { name: '', email: '', role: '', phone: '' };
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

    deleteContact(contactId: number) {
        if (!confirm('¿Estás seguro de que deseas eliminar este contacto?')) return;

        this.isDeletingContactId = contactId;
        this.errorMsg = '';
        this.successMsg = '';

        this.http.delete(`${environment.apiUrl}/projects/contacts/${contactId}`).subscribe({
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
        this.http.get<any[]>(`${environment.apiUrl}/projects/${projectId}/routings`).subscribe({
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

        this.http.post<any>(`${environment.apiUrl}/projects/${this.managingRoutingsForProject.id}/routings`, payload).subscribe({
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

        this.http.delete(`${environment.apiUrl}/projects/${this.managingRoutingsForProject.id}/routings/${routingId}`).subscribe({
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
        this.http.patch<any>(`${environment.apiUrl}/projects/routings/${routing.id}/toggle`, {}).subscribe({
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
}
