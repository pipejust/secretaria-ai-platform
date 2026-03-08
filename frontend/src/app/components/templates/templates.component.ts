import { Component, OnInit, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
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

    // Drag & Drop Configurator
    availableTokens = [
        { id: '1', label: 'Resumen Ejecutivo', tag: '{{RESUMEN_REUNION}}' },
        { id: '2', label: 'Lista de Asistentes', tag: '{{ASISTENTES}}' },
        { id: '3', label: 'Tabla de Tareas/Compromisos', tag: '{{TABLA_TAREAS}}' },
        { id: '4', label: 'Decisiones Tomadas', tag: '{{DECISIONES}}' },
        { id: '5', label: 'Riesgos Identificados', tag: '{{RIESGOS}}' }
    ];
    activeTokens: any[] = [];

    showConfigurator = false;

    constructor(private http: HttpClient, private authService: AuthService, private cdr: ChangeDetectorRef) { }

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

        // Mockup projects fetching
        setTimeout(() => {
            this.projects = [
                { id: 1, name: 'Comité Organizacional Mensual' },
                { id: 2, name: 'Entrevistas Técnicas' }
            ]
        }, 500);
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

    uploadFile() {
        if (!this.selectedFile || !this.selectedProjectId) return;

        this.isUploading = true;
        this.errorMsg = '';
        this.successMsg = '';

        const formData = new FormData();
        formData.append('file', this.selectedFile);

        const qs = `?project_id=${this.selectedProjectId}`;

        this.http.post<any>(`${environment.apiUrl}/templates/upload${qs}`, formData, {
            headers: this.authService.getAuthHeaders() // El navegador setea automáticamente multipart boundary
        }).subscribe({
            next: (res) => {
                this.successMsg = 'Documento subido con éxito. Ahora configura las etiquetas del Word.';
                this.lastUploadedTemplateId = res.template_id;
                this.loadData(); // Recargar la tabla
                this.isUploading = false;
                this.showConfigurator = true;
            },
            error: (err) => {
                this.errorMsg = err.error?.detail || 'Error al subir la plantilla.';
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

    confirmMapping() {
        if (!this.lastUploadedTemplateId) {
            this.showConfigurator = false;
            return;
        }

        const mappingPayload = {
            mapping_config: JSON.stringify(this.activeTokens)
        };

        this.http.put(`${environment.apiUrl}/templates/${this.lastUploadedTemplateId}/mapping`, mappingPayload, {
            headers: this.authService.getAuthHeaders()
        }).subscribe({
            next: () => {
                this.successMsg = 'Mapeo guardado exitosamente en la base de datos.';
                this.showConfigurator = false;
                setTimeout(() => this.successMsg = '', 4000);
            },
            error: (err) => {
                this.errorMsg = 'Error al guardar el mapeo: ' + (err.error?.detail || err.message);
            }
        });
    }
}
