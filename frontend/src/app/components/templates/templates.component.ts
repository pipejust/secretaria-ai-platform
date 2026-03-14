import { Component, OnInit, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { DragDropModule, CdkDragDrop, moveItemInArray, transferArrayItem } from '@angular/cdk/drag-drop';
import { AuthService } from '../../services/auth.service';
import { environment } from '../../../environments/environment';

@Component({
    selector: 'app-templates',
    standalone: true,
    imports: [CommonModule, FormsModule, DragDropModule],
    templateUrl: './templates.component.html',
    styleUrls: ['./templates.component.css']
})
export class TemplatesComponent implements OnInit {
    templates: any[] = [];
    projects: any[] = [];
    isLoading = false;
    isUploading = false;

    errorMsg = '';
    successMsg = '';

    selectedFile: File | null = null;
    selectedProjectId: string = '';
    templateName: string = '';
    editingTemplateId: number | null = null;
    searchText = '';
    showUploadModal = false;

    get filteredTemplates() {
        if (!this.searchText.trim()) return this.templates;
        const search = this.searchText.toLowerCase();
        return this.templates.filter(t => 
            (t.name && t.name.toLowerCase().includes(search)) ||
            (t.id && t.id.toString().includes(search)) ||
            (t.project && t.project.name && t.project.name.toLowerCase().includes(search))
        );
    }

    // Drag & Drop Configurator
    allPossibleTokens = [
        { id: 'meta', label: 'Cabecera (Título, Fecha, Estado)' },
        { id: 'summary', label: 'Resumen Ejecutivo' },
        { id: 'attendees', label: 'Lista de Asistentes' },
        { id: 'themes', label: 'Temas y Puntos de Discusión' },
        { id: 'decisions', label: 'Decisiones Clave' },
        { id: 'risks', label: 'Riesgos Identificados' },
        { id: 'agreements', label: 'Acuerdos' },
        { id: 'action_items', label: 'Tabla de Tareas/Compromisos' }
    ];
    
    availableTokens: any[] = [];
    activeTokens: any[] = [];

    showConfigurator = false;
    isTraditionalConfigurator = false;

    constructor(private http: HttpClient, private authService: AuthService, private cdr: ChangeDetectorRef, private router: Router) { }

    ngOnInit() {
        this.loadData();
    }

    loadData() {
        this.isLoading = true;
        const headers = this.authService.getAuthHeaders();

        this.http.get<any[]>(`${environment.apiUrl}/templates`, { headers }).subscribe({
            next: (data) => {
                this.templates = data;
                this.isLoading = false;
                this.cdr.detectChanges();
            },
            error: () => {
                this.errorMsg = 'Error al cargar plantillas';
                this.isLoading = false;
                this.cdr.detectChanges();
            }
        });

        this.http.get<any[]>(`${environment.apiUrl}/projects/`, { headers }).subscribe({
            next: (data) => {
                this.projects = data;
                this.cdr.detectChanges();
            },
            error: (err) => {
                console.error('Error loading projects for templates', err);
            }
        });
    }

    goToProjectContacts(projectId: number) {
        if (!projectId) return;
        // Navega a la vista de proyectos. Nota: Para manejar el estado interno de "Contactos abiertos" 
        // requeriría un state service, pero redirigir a la vista de proyectos es el primer paso.
        this.router.navigate(['/projects'], { queryParams: { openContacts: projectId } });
    }

    onFileSelected(event: any) {
        const file = event.target.files[0];
        if (file) {
            if (file.name.endsWith('.docx')) {
                this.selectedFile = file;
                this.errorMsg = '';
            } else {
                this.errorMsg = 'Solo se permiten archivos de formato Word (.docx)';
                this.selectedFile = null;
            }
        }
    }

    openCreateModal() {
        this.editingTemplateId = null;
        this.selectedFile = null;
        this.selectedProjectId = '';
        this.templateName = '';
        this.errorMsg = '';
        this.successMsg = '';
        this.showUploadModal = true;
    }

    editTemplate(template: any) {
        this.editingTemplateId = template.id;
        this.templateName = template.name;
        this.selectedProjectId = template.project_id;
        this.selectedFile = null; // No forzamos re-subir archivo a menos que lo deseen
        this.errorMsg = '';
        this.successMsg = '';
        this.showUploadModal = true;
    }

    uploadFile() {
        if (!this.selectedProjectId || !this.templateName || (!this.selectedFile && !this.editingTemplateId)) return;

        this.isUploading = true;
        this.errorMsg = '';
        this.successMsg = '';

        const formData = new FormData();
        if (this.selectedFile) {
            formData.append('file', this.selectedFile);
        }
        formData.append('project_id', this.selectedProjectId.toString());
        formData.append('name', this.templateName);

        const url = this.editingTemplateId 
            ? `${environment.apiUrl}/templates/${this.editingTemplateId}`
            : `${environment.apiUrl}/templates/upload`;
            
        const requestBase = this.editingTemplateId
            ? this.http.put<any>(url, formData, { headers: this.authService.getAuthHeaders() })
            : this.http.post<any>(url, formData, { headers: this.authService.getAuthHeaders() });

        requestBase.subscribe({
            next: (res) => {
                this.successMsg = this.editingTemplateId ? 'Plantilla actualizada exitosamente' : 'Documento subido con éxito. Ahora configura las etiquetas del Word.';
                this.lastUploadedTemplateId = this.editingTemplateId || res.template_id;
                this.loadData();
                this.isUploading = false;
                
                if (!this.editingTemplateId) {
                    this.showConfigurator = true;
                } else {
                    setTimeout(() => {
                        this.showUploadModal = false;
                    }, 1500);
                }
            },
            error: (err) => {
                this.errorMsg = err.error?.detail || 'Error al guardar la plantilla.';
                this.isUploading = false;
            }
        });
    }

    drop(event: CdkDragDrop<any[]>) {
        if (event.previousContainer === event.container) {
            moveItemInArray(event.container.data, event.previousIndex, event.currentIndex);
        } else {
            transferArrayItem(
                event.previousContainer.data,
                event.container.data,
                event.previousIndex,
                event.currentIndex,
            );
        }
    }

    copyTag(tag: string) {
        navigator.clipboard.writeText(tag);
        // Podríamos mostrar un micro-toast aquí, por ahora basta con el portapapeles
    }

    lastUploadedTemplateId: number | null = null;
    
    openConfigurator(template: any) {
        this.lastUploadedTemplateId = template.id;
        this.successMsg = '';
        this.errorMsg = '';
        this.isTraditionalConfigurator = false; // Reset toggle state
        
        let loadedMapping: string[] = [];
        try {
            if (template.mapping_config) {
                const parsed = JSON.parse(template.mapping_config);
                if (Array.isArray(parsed)) {
                    loadedMapping = parsed;
                }
            }
        } catch (e) {
            loadedMapping = [];
        }
        
        // Populate activeTokens based on IDs
        this.activeTokens = [];
        for (const blockId of loadedMapping) {
            const found = this.allPossibleTokens.find(t => t.id === blockId);
            if (found) {
                this.activeTokens.push({...found});
            }
        }
        
        // Populate availableTokens with whatever is NOT in activeTokens
        this.availableTokens = this.allPossibleTokens.filter(at => !loadedMapping.includes(at.id));
        
        this.showConfigurator = true;
    }

    confirmMapping() {
        if (!this.lastUploadedTemplateId) {
            this.showConfigurator = false;
            return;
        }

        const mappingPayload = {
            mapping_config: JSON.stringify(this.activeTokens.map(t => t.id))
        };

        this.http.put(`${environment.apiUrl}/templates/${this.lastUploadedTemplateId}/mapping`, mappingPayload, {
            headers: this.authService.getAuthHeaders()
        }).subscribe({
            next: () => {
                this.successMsg = 'Mapeo guardado exitosamente en la base de datos.';
                this.loadData();
                setTimeout(() => {
                    this.showConfigurator = false;
                    this.successMsg = '';
                }, 2000);
            },
            error: (err) => {
                this.errorMsg = 'Error al guardar el mapeo: ' + (err.error?.detail || err.message);
            }
        });
    }

    isDeleting = false;

    deleteTemplate(templateId: number) {
        if (!confirm('¿Estás seguro de que deseas eliminar esta plantilla?')) return;

        this.isDeleting = true;
        this.errorMsg = '';
        this.successMsg = '';

        this.http.delete(`${environment.apiUrl}/templates/${templateId}`, {
            headers: this.authService.getAuthHeaders()
        }).subscribe({
            next: () => {
                this.templates = this.templates.filter(t => t.id !== templateId);
                this.isDeleting = false;
                this.successMsg = 'Plantilla eliminada exitosamente';
                this.cdr.detectChanges();
            },
            error: (err) => {
                console.error(err);
                this.errorMsg = err.error?.detail || 'Error al eliminar la plantilla';
                this.isDeleting = false;
                this.cdr.detectChanges();
            }
        });
    }
}
