import { Component, OnInit, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { AuthService } from '../../services/auth.service';
import { environment } from '../../../environments/environment';

@Component({
    selector: 'app-dashboard',
    standalone: true,
    imports: [CommonModule, FormsModule],
    templateUrl: './dashboard.component.html',
    styleUrls: ['./dashboard.component.css']
})
export class DashboardComponent implements OnInit {
    sessions: any[] = [];
    projects: any[] = [];
    isLoading = false;
    isUploading = false;

    showUploadModal = false;
    uploadTab: 'audio' | 'text' = 'audio';
    uploadForm: any = {
        title: '',
        date: '',
        language: 'Español',
        projectId: '',
        textContent: '',
        file: null as File | null
    };

    showDeleteModal = false;
    sessionToDelete: any = null;
    isDeleting = false;

    constructor(
        private http: HttpClient, 
        private authService: AuthService, 
        private cdr: ChangeDetectorRef,
        private router: Router
    ) { }

    ngOnInit() {
        this.loadSessions();
        this.loadProjects();
    }

    loadProjects() {
        const headers = this.authService.getAuthHeaders();
        this.http.get<any[]>(`${environment.apiUrl}/api/projects`, { headers }).subscribe({
            next: (data) => {
                this.projects = data;
                this.cdr.detectChanges();
            },
            error: (err) => console.error("Error cargando proyectos", err)
        });
    }

    openUploadModal() {
        // Set default date to now in yyyy-MM-ddThh:mm format for datetime-local input
        const now = new Date();
        now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
        this.uploadForm.date = now.toISOString().slice(0,16);
        this.uploadForm.title = '';
        this.uploadForm.language = 'Español';
        this.uploadForm.projectId = '';
        this.uploadForm.textContent = '';
        this.uploadForm.file = null;
        this.showUploadModal = true;
    }

    closeUploadModal() {
        this.showUploadModal = false;
    }

    onFileSelected(event: any) {
        const file: File = event.target.files[0];
        if (file) {
            this.uploadForm.file = file;
            if (!this.uploadForm.title) {
                // Remove extension for default title
                this.uploadForm.title = file.name.replace(/\.[^/.]+$/, "");
            }
        }
    }

    submitUpload() {
        if (!this.uploadForm.title) {
            alert('El título/motivo es obligatorio.');
            return;
        }

        if (this.uploadTab === 'audio' && !this.uploadForm.file) {
            alert('Debe subir un archivo de audio para transcribir.');
            return;
        }
        
        if (this.uploadTab === 'text' && !this.uploadForm.textContent.trim()) {
            alert('Debe pegar el texto de la transcripción.');
            return;
        }

        this.isUploading = true;
        const formData = new FormData();
        formData.append('title', this.uploadForm.title);
        
        if (this.uploadForm.date) {
            // Convert back to UTC ISO string if needed, or keep local
            const d = new Date(this.uploadForm.date);
            formData.append('date', d.toISOString());
        }
        
        formData.append('language', this.uploadForm.language);
        
        if (this.uploadForm.projectId) {
            formData.append('project_id', this.uploadForm.projectId);
        }

        if (this.uploadTab === 'audio' && this.uploadForm.file) {
            formData.append('file', this.uploadForm.file);
        } else if (this.uploadTab === 'text') {
            formData.append('text_content', this.uploadForm.textContent);
        }

        const headers = this.authService.getAuthHeaders();
        // Angular's HttpClient will automatically set the correct Content-Type for FormData
        
        this.http.post(`${environment.apiUrl}/api/sessions/upload`, formData, { headers }).subscribe({
            next: (res: any) => {
                alert('Sesión creada exitosamente.');
                this.showUploadModal = false;
                this.isUploading = false;
                this.loadSessions();
            },
            error: (err) => {
                console.error('Upload Error:', err);
                alert('Error subiendo o creando la sesión: ' + (err.error?.detail || err.message));
                this.isUploading = false;
            }
        });
    }

    loadSessions() {
        this.isLoading = true;

        this.http.get<any[]>(`${environment.apiUrl}/api/sessions/`).subscribe({
            next: (data) => {
                // Ensure dates are parsed correctly
                this.sessions = data.map(s => {
                    let parsedDate = s.date;
                    if (typeof parsedDate === 'string' && !isNaN(Number(parsedDate))) {
                        parsedDate = Number(parsedDate);
                    }
                    return { ...s, date: parsedDate };
                });
                this.isLoading = false;
                this.cdr.detectChanges();
            },
            error: (err) => {
                console.error('Error fetching sessions:', err);
                this.sessions = [];
                this.isLoading = false;
                this.cdr.detectChanges();
            }
        });
    }

    searchText: string = '';
    statusFilter: string = '';
    sortColumn: string = 'date';
    sortDirection: 'asc' | 'desc' = 'desc';

    sortBy(column: string) {
        if (this.sortColumn === column) {
            this.sortDirection = this.sortDirection === 'asc' ? 'desc' : 'asc';
        } else {
            this.sortColumn = column;
            this.sortDirection = 'asc'; // Default to asc when clicking a new column
            if (column === 'date' || column === 'id') {
                this.sortDirection = 'desc'; // Exception: new IDs and dates default to descending
            }
        }
    }

    get filteredSessions() {
        let filtered = this.sessions || [];
        
        if (this.statusFilter) {
            filtered = filtered.filter(s => s.status === this.statusFilter);
        }
        
        if (this.searchText.trim()) {
            const search = this.searchText.toLowerCase();
            filtered = filtered.filter(s => 
                (s.title && s.title.toLowerCase().includes(search)) ||
                (s.id && s.id.toString().includes(search))
            );
        }
        
        // Sorting logic based on selected column: Clone array to trigger Angular Change Detection
        return [...filtered].sort((a, b) => {
            let valA = a[this.sortColumn];
            let valB = b[this.sortColumn];

            // Normalize values for sorting
            if (this.sortColumn === 'date') {
                if (typeof valA === 'string' && !isNaN(Number(valA))) valA = Number(valA);
                if (typeof valB === 'string' && !isNaN(Number(valB))) valB = Number(valB);
                valA = new Date(valA).getTime() || 0;
                valB = new Date(valB).getTime() || 0;
            } else if (typeof valA === 'string') {
                valA = valA.toLowerCase();
                valB = valB.toLowerCase();
            } else {
                valA = valA || 0;
                valB = valB || 0;
            }

            if (valA < valB) {
                return this.sortDirection === 'asc' ? -1 : 1;
            }
            if (valA > valB) {
                return this.sortDirection === 'asc' ? 1 : -1;
            }
            return 0;
        });
    }

    generateActa(session: any, format: 'word' | 'pdf' = 'word') {
        const headers = this.authService.getAuthHeaders();
        this.http.get(`${environment.apiUrl}/api/sessions/${session.id}/export/${format}`, { headers, responseType: 'blob' }).subscribe({
            next: (blob: Blob) => {
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                const safeTitle = (session.title || 'Sesion').replace(/[^a-z0-9]/gi, '_').substring(0, 30);
                const ext = format === 'word' ? 'docx' : 'pdf';
                a.download = `Sesion_${session.id}_${safeTitle}.${ext}`;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                window.URL.revokeObjectURL(url);
            },
            error: (err) => {
                console.error(`Error generando documento ${format}`, err);
                alert(`Error descargando el documento ${format.toUpperCase()}. Asegúrese de tener conexión.`);
            }
        });
    }

    viewCuration(sessionId: number) {
        this.router.navigate(['/admin/curation', sessionId]);
    }

    deleteSession(session: any) {
        this.sessionToDelete = session;
        this.showDeleteModal = true;
    }

    cancelDeleteSession() {
        this.showDeleteModal = false;
        this.sessionToDelete = null;
    }

    confirmDeleteSession() {
        if (!this.sessionToDelete) return;
        
        this.isDeleting = true;
        const headers = this.authService.getAuthHeaders();
        this.http.delete(`${environment.apiUrl}/api/sessions/${this.sessionToDelete.id}`, { headers }).subscribe({
            next: () => {
                this.isDeleting = false;
                this.showDeleteModal = false;
                this.sessionToDelete = null;
                alert('Sesión eliminada correctamente.');
                this.loadSessions();
            },
            error: (err) => {
                this.isDeleting = false;
                console.error('Error eliminando sesión:', err);
                alert('Error al intentar eliminar la sesión.');
            }
        });
    }
}
