import { Component, OnInit, OnDestroy, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { Subject } from 'rxjs';
import { takeUntil } from 'rxjs/operators';
import { AuthService } from '../../services/auth.service';
import { ToastService } from '../../services/toast.service';
import { environment } from '../../../environments/environment';

@Component({
    selector: 'app-dashboard',
    standalone: true,
    imports: [CommonModule, FormsModule],
    templateUrl: './dashboard.component.html',
    styleUrls: ['./dashboard.component.css']
})
export class DashboardComponent implements OnInit, OnDestroy {
    sessions: any[] = [];
    projects: any[] = [];
    isLoading = false;
    isUploading = false;
    generatingIds: { [key: string]: boolean } = {};

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

    private readonly destroy$ = new Subject<void>();

    constructor(
        private http: HttpClient,
        private authService: AuthService,
        private cdr: ChangeDetectorRef,
        private router: Router,
        private toast: ToastService,
    ) { }

    ngOnInit(): void {
        this.loadSessions();
        this.loadProjects();
    }

    ngOnDestroy(): void {
        this.destroy$.next();
        this.destroy$.complete();
    }

    loadProjects() {
        const headers = this.authService.getAuthHeaders();
        this.http.get<any[]>(`${environment.apiUrl}/api/projects`, { headers }).pipe(takeUntil(this.destroy$)).subscribe({
            next: (data) => {
                this.projects = data;
                this.cdr.detectChanges();
            },
            error: (err) => console.error("Error cargando proyectos", err)
        });
    }

    getProjectName(projectId: any): string {
        if (!projectId) return 'General';
        const p = this.projects.find(proj => proj.id === projectId);
        return p ? p.name : 'General';
    }

    /** Convierte la fecha de la sesión a 'dd/mm/aaaa' para que el buscador
     *  pueda matchear cuando el usuario tipea fragmentos como '15/03'. */
    private _sessionDateLabel(s: any): string {
        let value = s?.date;
        if (value == null) return '';
        if (typeof value === 'string' && !isNaN(Number(value))) {
            value = Number(value);
        }
        const d = new Date(value);
        if (isNaN(d.getTime())) return String(s.date || '');
        const dd = String(d.getDate()).padStart(2, '0');
        const mm = String(d.getMonth() + 1).padStart(2, '0');
        const yyyy = d.getFullYear();
        return `${dd}/${mm}/${yyyy}`;
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
            this.toast.warning('El título/motivo es obligatorio.');
            return;
        }

        if (this.uploadTab === 'audio' && !this.uploadForm.file) {
            this.toast.warning('Debe subir un archivo de audio para transcribir.');
            return;
        }

        if (this.uploadTab === 'text' && !this.uploadForm.textContent.trim()) {
            this.toast.warning('Debe pegar el texto de la transcripción.');
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
        
        this.http.post(`${environment.apiUrl}/api/sessions/upload`, formData, { headers }).pipe(takeUntil(this.destroy$)).subscribe({
            next: () => {
                this.toast.success('Sesión creada exitosamente.');
                this.showUploadModal = false;
                this.isUploading = false;
                this.loadSessions();
            },
            error: (err) => {
                this.toast.error(
                    'Error subiendo o creando la sesión: ' + (err?.error?.detail || err?.message || 'desconocido'),
                );
                this.isUploading = false;
            }
        });
    }

    loadSessions() {
        this.isLoading = true;

        let params = `?page=${this.currentPage}&limit=${this.limit}`;
        if (this.statusFilter) params += `&status=${this.statusFilter}`;
        if (this.searchText.trim()) params += `&search=${encodeURIComponent(this.searchText.trim())}`;
        if (this.filterProjectId) params += `&project_id=${this.filterProjectId}`;
        
        const headers = this.authService.getAuthHeaders();
        this.http.get<any>(`${environment.apiUrl}/api/sessions/${params}`, { headers }).pipe(takeUntil(this.destroy$)).subscribe({
            next: (data) => {
                const isPaginatedResponse = !!data.items;
                const items = isPaginatedResponse ? data.items : data;
                
                // Ensure dates are parsed correctly
                let parsedSessions = items.map((s: any) => {
                    let parsedDate = s.date;
                    if (typeof parsedDate === 'string' && !isNaN(Number(parsedDate))) {
                        parsedDate = Number(parsedDate);
                    }
                    return { ...s, date: parsedDate };
                });
                
                this.limit = data.limit || 20;
                this.totalItems = data.total || parsedSessions.length;
                this.totalPages = data.pages || Math.ceil(this.totalItems / this.limit) || 1;
                // Si la respuesta no es paginada (backend viejo), aplicamos rebanado local
                if (!isPaginatedResponse) {
                    const startIdx = (this.currentPage - 1) * this.limit;
                    const endIdx = startIdx + this.limit;
                    this.sessions = parsedSessions.slice(startIdx, endIdx);
                } else {
                    this.sessions = parsedSessions;
                    this.currentPage = data.page || 1;
                }

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
    filterProjectId: string = '';
    currentPage: number = 1;
    limit: number = 20;
    totalPages: number = 1;
    totalItems: number = 0;
    sortColumn: string = 'id';
    sortDirection: 'asc' | 'desc' = 'desc';

    /** Cuenta sesiones por status sobre el lote actualmente cargado.
        Sirve para los KPI tiles del hero del dashboard. */
    countByStatus(status: string): number {
        return (this.sessions || []).filter((s) => s?.status === status).length;
    }

    changePage(page: number) {
        if (page >= 1 && page <= this.totalPages) {
            this.currentPage = page;
            this.loadSessions();
        }
    }

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

    /**
     * Live filter del buscador. Reemplaza la búsqueda por ID por:
     * título, proyecto (nombre) y fecha. Soporta formato dd/mm/aaaa
     * o dd/mm. Cuando el usuario borra el texto, el listado vuelve a
     * mostrarse completo automáticamente (sin necesidad de Enter).
     */
    onSearchInput(): void {
        // Solo refresca el filtro local; no recarga del backend en cada tecla
        // para no saturar la API. Si quisiera buscar a nivel servidor,
        // hago debounce y disparo loadSessions(). Por ahora client-side.
        this.cdr.detectChanges();
    }

    get filteredSessions() {
        let filtered = this.sessions || [];

        if (this.statusFilter) {
            filtered = filtered.filter(s => s.status === this.statusFilter);
        }

        const rawSearch = (this.searchText || '').trim().toLowerCase();
        if (rawSearch) {
            filtered = filtered.filter(s => {
                const title = (s.title || '').toLowerCase();
                const projectName = this.getProjectName(s.project_id).toLowerCase();
                const dateLabel = this._sessionDateLabel(s);
                return (
                    title.includes(rawSearch) ||
                    projectName.includes(rawSearch) ||
                    dateLabel.includes(rawSearch)
                );
            });
        }
        
        // Sorting logic based on selected column: Clone array to trigger Angular Change Detection
        return [...filtered].sort((a, b) => {
            let valA = a[this.sortColumn];
            let valB = b[this.sortColumn];

            // Normalize values for sorting
            if (this.sortColumn === 'project_id') {
                valA = this.getProjectName(a.project_id).toLowerCase();
                valB = this.getProjectName(b.project_id).toLowerCase();
            } else if (this.sortColumn === 'date') {
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
        const genKey = `${session.id}_${format}`;
        if (this.generatingIds[genKey]) return;
        this.generatingIds[genKey] = true;
        this.cdr.detectChanges();
        
        const headers = this.authService.getAuthHeaders();
        this.http.get(`${environment.apiUrl}/api/sessions/${session.id}/export/${format}`, { headers, responseType: 'blob' }).pipe(takeUntil(this.destroy$)).subscribe({
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
                this.generatingIds[genKey] = false;
                this.cdr.detectChanges();
            },
            error: () => {
                this.toast.error(
                    `Error descargando el documento ${format.toUpperCase()}. Verifique su conexión.`,
                );
                this.generatingIds[genKey] = false;
                this.cdr.detectChanges();
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
        this.http.delete(`${environment.apiUrl}/api/sessions/${this.sessionToDelete.id}`, { headers }).pipe(takeUntil(this.destroy$)).subscribe({
            next: () => {
                this.isDeleting = false;
                this.showDeleteModal = false;
                this.sessionToDelete = null;
                this.toast.success('Sesión eliminada correctamente.');
                this.loadSessions();
            },
            error: () => {
                this.isDeleting = false;
                this.toast.error('Error al intentar eliminar la sesión.');
            }
        });
    }
}
