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
import { MdRenderPipe } from '../../pipes/md-render.pipe';

/** Sub-tab de la card del header (filtro rápido por status). */
type StatusTab = 'all' | 'analyzed' | 'drafts' | 'archived';
/** Tab activo del panel lateral derecho de detalle. */
type DetailTab = 'summary' | 'transcript' | 'insights' | 'notes' | 'files';

interface ActionItemDTO {
    id: number;
    title: string;
    owner_name?: string;
    due_date?: string;
    status?: string;
    priority?: string;          // si el backend lo expone (low/medium/high)
}

/** Participante normalizado para avatares + tooltip. El backend persiste
 *  `processed_attendees` como JSON array de objetos {name, role, entity}.
 *  Lo tipamos acá para no perder rol/empresa en la UI. */
interface Attendee {
    name: string;
    role?: string;
    company?: string;
}

@Component({
    selector: 'app-meetings-list',
    standalone: true,
    imports: [CommonModule, FormsModule, MdRenderPipe],
    templateUrl: './meetings-list.component.html',
    styleUrls: ['./meetings-list.component.css']
})
export class MeetingsListComponent implements OnInit, OnDestroy {
    // ---------- Datos cargados ----------
    sessions: any[] = [];
    projects: any[] = [];
    isLoading = false;
    isUploading = false;
    generatingIds: { [key: string]: boolean } = {};

    // ---------- Modales (sin cambios) ----------
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

    // ---------- Filtros + búsqueda + paginación ----------
    searchText: string = '';
    statusFilter: string = '';
    filterProjectId: string = '';
    /** Filtros visuales adicionales que el mockup pide. Por ahora son
     *  placeholders client-side: el backend no expone aún team/source/
     *  date-range. Se mantienen para que la UI quede igual al mockup
     *  y se cableen cuando los endpoints estén. */
    filterTeam = '';
    filterDate = '';
    filterSource = '';

    /** Visibilidad del bloque de filtros (Status / Equipo / Fecha / Origen).
     *  El botón "Filtros" lo hace toggle. Por defecto OCULTO para que la
     *  vista arranque limpia y el user los expanda si los necesita. */
    showFilters = false;

    currentPage: number = 1;
    limit: number = 10;
    totalPages: number = 1;
    totalItems: number = 0;
    sortColumn: string = 'id';
    sortDirection: 'asc' | 'desc' = 'desc';

    /** Sub-tab del header: All / Analyzed / Drafts / Archived. */
    statusTab: StatusTab = 'all';

    // ---------- Panel lateral de detalle ----------
    selectedSession: any = null;
    selectedActionItems: ActionItemDTO[] = [];
    isLoadingDetail = false;
    detailTab: DetailTab = 'summary';

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

    // ============================================================
    // CARGA DE DATOS
    // ============================================================

    loadProjects() {
        const headers = this.authService.getAuthHeaders();
        this.http.get<any[]>(`${environment.apiUrl}/api/projects`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (data) => { this.projects = data; this.cdr.detectChanges(); },
                error: (err) => console.error('Error cargando proyectos', err),
            });
    }

    getProjectName(projectId: any): string {
        if (!projectId) return 'General';
        const p = this.projects.find(proj => proj.id === projectId);
        return p ? p.name : 'General';
    }

    /** Carga la página actual de sesiones del backend. Status server-side
     *  cuando hay filtro explícito; el sub-tab del header se aplica
     *  client-side encima del resultado para evitar refetches. */
    loadSessions() {
        this.isLoading = true;
        let params = `?page=${this.currentPage}&limit=${this.limit}`;
        if (this.statusFilter) params += `&status=${this.statusFilter}`;
        if (this.searchText.trim()) params += `&search=${encodeURIComponent(this.searchText.trim())}`;
        if (this.filterProjectId) params += `&project_id=${this.filterProjectId}`;

        const headers = this.authService.getAuthHeaders();
        this.http.get<any>(`${environment.apiUrl}/api/sessions/${params}`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (data) => {
                    const isPaginatedResponse = !!data.items;
                    const items = isPaginatedResponse ? data.items : data;
                    const parsed = items.map((s: any) => ({
                        ...s,
                        date: typeof s.date === 'string' && !isNaN(Number(s.date)) ? Number(s.date) : s.date,
                    }));
                    this.limit = data.limit || 10;
                    this.totalItems = data.total ?? parsed.length;
                    this.totalPages = data.pages ?? Math.max(1, Math.ceil(this.totalItems / this.limit));
                    if (!isPaginatedResponse) {
                        const start = (this.currentPage - 1) * this.limit;
                        this.sessions = parsed.slice(start, start + this.limit);
                    } else {
                        this.sessions = parsed;
                        this.currentPage = data.page || 1;
                    }
                    // Auto-seleccionar la primera fila para mostrar el panel
                    // lateral con datos reales en lugar de un placeholder
                    // vacío. Sólo si nada está seleccionado todavía.
                    if (!this.selectedSession && this.sessions.length) {
                        this.selectSession(this.sessions[0]);
                    }
                    this.isLoading = false;
                    this.cdr.detectChanges();
                },
                error: (err) => {
                    console.error('Error fetching sessions:', err);
                    this.sessions = [];
                    this.isLoading = false;
                    this.cdr.detectChanges();
                },
            });
    }

    /** Carga el detalle completo (con action items) de una sesión para
     *  el panel lateral. Endpoint: GET /api/sessions/{id}. */
    private loadSessionDetail(id: number) {
        this.isLoadingDetail = true;
        const headers = this.authService.getAuthHeaders();
        this.http.get<any>(`${environment.apiUrl}/api/sessions/${id}`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (data) => {
                    // Merge: el detalle puede traer campos que el listado
                    // pagiando no expuso (raw_summary etc.).
                    this.selectedSession = { ...this.selectedSession, ...(data?.session || {}) };
                    this.selectedActionItems = (data?.action_items || []) as ActionItemDTO[];
                    this.isLoadingDetail = false;
                    this.cdr.detectChanges();
                },
                error: () => {
                    this.selectedActionItems = [];
                    this.isLoadingDetail = false;
                    this.cdr.detectChanges();
                },
            });
    }

    // ============================================================
    // FILTROS / SUB-TABS / PAGINACIÓN
    // ============================================================

    /** Devuelve el conteo client-side del status (sobre la página actual)
     *  para alimentar los KPI tiles del header sin pedir otra API. */
    countByStatus(status: string): number {
        return (this.sessions || []).filter((s) => s?.status === status).length;
    }

    /** Total reportado por el backend (página) — para Total Meetings. */
    get totalMeetings(): number { return this.totalItems || this.sessions.length; }

    /** Sesiones completadas en la página actual. */
    get analyzedCount(): number { return this.countByStatus('completed'); }
    /** Pendientes en la página actual. */
    get pendingCount(): number { return this.countByStatus('pending'); }
    /** Archivadas — visible si filtras por archivadas. Sin endpoint
     *  dedicado todavía, devuelve lo que esté en el lote. */
    get archivedCount(): number { return this.countByStatus('archived'); }

    /** Porcentaje del total para los KPI tiles ("66% of total"). */
    pctOfTotal(count: number): number {
        const t = this.totalMeetings;
        if (!t) return 0;
        return Math.round((count / t) * 100);
    }

    /** Duración media del lote en minutos. Como el modelo no tiene un
     *  campo `duration_min`, mostramos guion hasta que exista. */
    get avgDurationLabel(): string {
        // TODO(backend): exponer duration_min en MeetingSession.
        // Por ahora derivamos algo razonable del raw_transcript si lo
        // hubiera (~1 min por cada 150 palabras estimadas). Si la
        // página actual no tiene transcripts cargados, devolvemos '—'.
        const withTranscript = (this.sessions || []).filter((s) => s?.raw_transcript);
        if (!withTranscript.length) return '—';
        const avgWords = withTranscript.reduce((acc, s) => acc + ((s.raw_transcript || '').split(/\s+/).length), 0) / withTranscript.length;
        const minutes = Math.max(1, Math.round(avgWords / 150));
        return `${minutes}m`;
    }

    setStatusTab(tab: StatusTab): void {
        this.statusTab = tab;
        this.currentPage = 1;
        this.cdr.detectChanges();
    }

    changePage(page: number) {
        if (page >= 1 && page <= this.totalPages) {
            this.currentPage = page;
            this.loadSessions();
        }
    }

    changeLimit(newLimit: number) {
        this.limit = newLimit;
        this.currentPage = 1;
        this.loadSessions();
    }

    /** Página[]s a mostrar en el paginator — compactado con elipsis si
     *  hay muchas. Estilo "1 2 3 … 13" del mockup. */
    get pageButtons(): (number | '…')[] {
        const total = Math.max(1, this.totalPages);
        const cur = this.currentPage;
        if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
        const out: (number | '…')[] = [];
        out.push(1);
        if (cur > 3) out.push('…');
        for (let p = Math.max(2, cur - 1); p <= Math.min(total - 1, cur + 1); p++) {
            out.push(p);
        }
        if (cur < total - 2) out.push('…');
        out.push(total);
        return out;
    }

    onSearchInput(): void { this.cdr.detectChanges(); }

    /** Toggle del panel de filtros. Si se cierra, limpia los selectores
     *  visuales para no dejar filtros aplicados "invisibles". El filtro
     *  de estado y proyecto SÍ se conservan porque viven en el header
     *  tabular original (no en este panel). */
    toggleFilters(): void {
        this.showFilters = !this.showFilters;
        if (!this.showFilters) {
            // Cuando cerrás los filtros, los reseteamos para evitar el caso
            // "filtros aplicados pero no visibles". Si los querés mantener,
            // dejá el panel abierto.
            this.filterDate = '';
            this.filterSource = '';
        }
    }

    /** Cuántos filtros del panel están activos. Se muestra como contador
     *  en el botón "Filtros (N)" para que el user sepa que hay filtros
     *  aplicados aunque el panel esté cerrado. */
    get activeFiltersCount(): number {
        return [this.filterDate, this.filterSource].filter(Boolean).length;
    }

    /** Resultado final de la tabla: aplica filtros y sub-tab del header. */
    get filteredSessions() {
        let filtered = this.sessions || [];

        // Sub-tab del header sobre el lote ya cargado.
        if (this.statusTab === 'analyzed') {
            filtered = filtered.filter((s) => s?.status === 'completed');
        } else if (this.statusTab === 'drafts') {
            filtered = filtered.filter((s) => s?.status === 'pending');
        } else if (this.statusTab === 'archived') {
            filtered = filtered.filter((s) => s?.status === 'archived');
        }

        // Filtro de origen (Subida manual / Web). Client-side: derivado de
        // fireflies_id en sessionSource().
        if (this.filterSource) {
            filtered = filtered.filter((s) => this.sessionSource(s).key === this.filterSource);
        }

        // Buscador local (encima de filtros server-side).
        const raw = (this.searchText || '').trim().toLowerCase();
        if (raw) {
            filtered = filtered.filter((s) => {
                const title = (s.title || '').toLowerCase();
                const project = this.getProjectName(s.project_id).toLowerCase();
                const date = this._sessionDateLabel(s);
                return title.includes(raw) || project.includes(raw) || date.includes(raw);
            });
        }

        // Sort.
        return [...filtered].sort((a, b) => {
            let va = a[this.sortColumn];
            let vb = b[this.sortColumn];
            if (this.sortColumn === 'project_id') {
                va = this.getProjectName(a.project_id).toLowerCase();
                vb = this.getProjectName(b.project_id).toLowerCase();
            } else if (this.sortColumn === 'date') {
                if (typeof va === 'string' && !isNaN(Number(va))) va = Number(va);
                if (typeof vb === 'string' && !isNaN(Number(vb))) vb = Number(vb);
                va = new Date(va).getTime() || 0;
                vb = new Date(vb).getTime() || 0;
            } else if (typeof va === 'string') {
                va = va.toLowerCase(); vb = (vb || '').toLowerCase();
            } else {
                va = va || 0; vb = vb || 0;
            }
            if (va < vb) return this.sortDirection === 'asc' ? -1 : 1;
            if (va > vb) return this.sortDirection === 'asc' ? 1 : -1;
            return 0;
        });
    }

    sortBy(column: string) {
        if (this.sortColumn === column) {
            this.sortDirection = this.sortDirection === 'asc' ? 'desc' : 'asc';
        } else {
            this.sortColumn = column;
            this.sortDirection = column === 'date' || column === 'id' ? 'desc' : 'asc';
        }
    }

    // ============================================================
    // SELECCIÓN / PANEL LATERAL
    // ============================================================

    selectSession(s: any) {
        this.selectedSession = s;
        this.selectedActionItems = [];
        this.detailTab = 'summary';
        if (s?.id) this.loadSessionDetail(s.id);
    }

    closeDetailPanel() {
        this.selectedSession = null;
        this.selectedActionItems = [];
    }

    setDetailTab(tab: DetailTab) { this.detailTab = tab; }

    /** Resumen ejecutivo del panel (usa raw_summary si existe). */
    get selectedExecutiveSummary(): string {
        const s = this.selectedSession;
        if (!s?.raw_summary) return '';
        // Si viene como JSON-string lo intentamos parsear; si no, plano.
        try {
            const obj = JSON.parse(s.raw_summary);
            if (typeof obj === 'string') return obj;
            if (obj?.summary) return String(obj.summary);
        } catch { /* plain text */ }
        return String(s.raw_summary);
    }

    /** Decisiones parseadas del campo processed_decisions. */
    get selectedKeyDecisions(): string[] {
        return this._parseList(this.selectedSession?.processed_decisions);
    }

    /** Lista de attendees tipados de la sesión seleccionada (panel lateral). */
    get selectedAttendees(): Attendee[] {
        return this.attendeesOf(this.selectedSession);
    }

    /** Lista de attendees tipados (name + role + company) para una sesión.
     *  Maneja los tres formatos en los que el backend persiste el campo:
     *  (1) array de objetos {name, role, entity}; (2) JSON-string del (1);
     *  (3) texto plano separado por saltos/viñetas. */
    attendeesOf(s: any): Attendee[] {
        const raw = s?.processed_attendees;
        if (!raw) return [];
        const arr: any[] = Array.isArray(raw)
            ? raw
            : this._tryParseArray(String(raw));
        if (!arr.length) return [];
        return arr
            .map((x): Attendee | null => {
                if (!x) return null;
                if (typeof x === 'string') {
                    const trimmed = x.trim();
                    return trimmed ? { name: trimmed } : null;
                }
                const name = x.name || x.full_name || x.fullName || x.email || '';
                if (!name) return null;
                return {
                    name: String(name).trim(),
                    role: x.role || x.position || x.job_title || x.jobTitle || undefined,
                    company: x.entity || x.company || x.organization || x.org || undefined,
                };
            })
            .filter((a): a is Attendee => !!a);
    }

    private _tryParseArray(s: string): any[] {
        // 1) intento JSON
        try {
            const obj = JSON.parse(s);
            if (Array.isArray(obj)) return obj;
            if (typeof obj === 'string') {
                return obj.split(/\n|,|•/).map((x) => x.trim()).filter(Boolean);
            }
        } catch { /* fall through */ }
        // 2) plain text separado por saltos/viñetas/comas
        return s.split(/\n|,|•/).map((x) => x.trim()).filter(Boolean);
    }

    /** Helper genérico: el backend persiste algunos campos como JSON-string
     *  (lista) y otros como texto plano con saltos de línea o viñetas.
     *  Esto los normaliza a string[]. */
    private _parseList(raw: any): string[] {
        if (!raw) return [];
        if (Array.isArray(raw)) return raw.map(String);
        try {
            const obj = JSON.parse(String(raw));
            if (Array.isArray(obj)) return obj.map((x) => typeof x === 'string' ? x : (x?.text || x?.title || JSON.stringify(x)));
            if (typeof obj === 'string') return obj.split('\n').filter(Boolean);
            return [];
        } catch {
            return String(raw).split(/[\n•]/).map((s) => s.trim()).filter(Boolean);
        }
    }

    // ============================================================
    // RENDER HELPERS
    // ============================================================

    /** Convierte la fecha a 'dd/mm/aaaa' para el buscador. */
    private _sessionDateLabel(s: any): string {
        let value = s?.date;
        if (value == null) return '';
        if (typeof value === 'string' && !isNaN(Number(value))) value = Number(value);
        const d = new Date(value);
        if (isNaN(d.getTime())) return String(s.date || '');
        const dd = String(d.getDate()).padStart(2, '0');
        const mm = String(d.getMonth() + 1).padStart(2, '0');
        return `${dd}/${mm}/${d.getFullYear()}`;
    }

    /** Devuelve la fecha "May 16, 2024" + hora "10:00 AM" desglosadas
     *  para mostrarlas en líneas separadas (como en el mockup). */
    formatDateLine(s: any): string {
        const v = typeof s?.date === 'string' && !isNaN(Number(s.date)) ? Number(s.date) : s?.date;
        const d = new Date(v);
        if (isNaN(d.getTime())) return String(s?.date || '');
        return d.toLocaleDateString('es-ES', { day: 'numeric', month: 'short', year: 'numeric' });
    }
    formatTimeLine(s: any): string {
        const v = typeof s?.date === 'string' && !isNaN(Number(s.date)) ? Number(s.date) : s?.date;
        const d = new Date(v);
        if (isNaN(d.getTime())) return '';
        return d.toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' });
    }
    formatDueDate(due: any): string {
        if (!due) return '—';
        const d = new Date(due);
        if (isNaN(d.getTime())) return String(due);
        return d.toLocaleDateString('es-ES', { day: 'numeric', month: 'short' });
    }

    /** Iniciales para los avatares stub. */
    initials(name: string): string {
        if (!name) return '?';
        const parts = name.trim().split(/\s+/).slice(0, 2);
        return parts.map((p) => p.charAt(0).toUpperCase()).join('') || '?';
    }

    /** Hash determinista para asignar un color al avatar a partir del
     *  nombre — así dos veces el mismo participante se ven igual. */
    initialsColor(name: string): string {
        const palette = ['#155EEF', '#1B7F67', '#D9A441', '#7C3AED', '#0EA5E9', '#E11D48'];
        if (!name) return palette[0];
        let h = 0;
        for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) | 0;
        return palette[Math.abs(h) % palette.length];
    }

    /** Origen real de la sesión.
     *  - 'manual': el usuario subió el archivo/transcripción desde la UI
     *    (sessions_upload genera fireflies_id con prefijo "MANUAL-").
     *  - 'web':    la sesión llegó por webhook (Fireflies real), donde
     *    el fireflies_id es el ID del proveedor.
     *  Cualquier otra heurística (Zoom/Teams) requiere un campo `source`
     *  dedicado en el modelo, todavía no expuesto. */
    sessionSource(s: any): { name: string; key: 'manual' | 'web' } {
        const ff = String(s?.fireflies_id || '').toUpperCase();
        if (!ff || ff.startsWith('MANUAL-')) return { name: 'Subida manual', key: 'manual' };
        return { name: 'Web', key: 'web' };
    }

    /** Duración estimada del item de tabla. Hasta que exista el campo
     *  real, se infiere igual que avgDurationLabel pero por sesión. */
    durationLabel(s: any): string {
        const t = (s?.raw_transcript || '');
        if (!t) return '—';
        const words = t.split(/\s+/).length;
        return `${Math.max(1, Math.round(words / 150))}m`;
    }

    /** Texto legible del status para el badge. */
    statusBadge(status: string): { label: string; key: string } {
        switch ((status || '').toLowerCase()) {
            case 'completed': return { label: 'Analizada', key: 'analyzed' };
            case 'pending':   return { label: 'Pendiente', key: 'pending' };
            case 'processing': return { label: 'Procesando', key: 'processing' };
            case 'archived':  return { label: 'Archivada', key: 'archived' };
            default:          return { label: 'Borrador', key: 'draft' };
        }
    }

    /** Color del badge de prioridad de las action items del panel. */
    priorityBadge(p: string): { label: string; key: 'high' | 'medium' | 'low' } {
        const v = (p || '').toLowerCase();
        if (v === 'high' || v === 'alto')    return { label: 'Alta',   key: 'high' };
        if (v === 'medium' || v === 'medio') return { label: 'Media',  key: 'medium' };
        return { label: 'Baja', key: 'low' };
    }

    /** ¿La action item está cerrada? — para tacharla en la lista. */
    isActionDone(a: ActionItemDTO): boolean {
        const s = (a?.status || '').toLowerCase();
        return s === 'done' || s === 'completed';
    }

    // ============================================================
    // ACCIONES (upload / export / delete) — sin cambios funcionales
    // ============================================================

    openUploadModal() {
        const now = new Date();
        now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
        this.uploadForm.date = now.toISOString().slice(0, 16);
        this.uploadForm.title = '';
        this.uploadForm.language = 'Español';
        this.uploadForm.projectId = '';
        this.uploadForm.textContent = '';
        this.uploadForm.file = null;
        this.showUploadModal = true;
    }

    closeUploadModal() { this.showUploadModal = false; }

    onFileSelected(event: any) {
        const file: File = event.target.files[0];
        if (file) {
            this.uploadForm.file = file;
            if (!this.uploadForm.title) {
                this.uploadForm.title = file.name.replace(/\.[^/.]+$/, '');
            }
        }
    }

    submitUpload() {
        if (!this.uploadForm.title) { this.toast.warning('El título/motivo es obligatorio.'); return; }
        if (this.uploadTab === 'audio' && !this.uploadForm.file) {
            this.toast.warning('Debe subir un archivo de audio para transcribir.'); return;
        }
        if (this.uploadTab === 'text' && !this.uploadForm.textContent.trim()) {
            this.toast.warning('Debe pegar el texto de la transcripción.'); return;
        }
        this.isUploading = true;
        const formData = new FormData();
        formData.append('title', this.uploadForm.title);
        if (this.uploadForm.date) {
            formData.append('date', new Date(this.uploadForm.date).toISOString());
        }
        formData.append('language', this.uploadForm.language);
        if (this.uploadForm.projectId) formData.append('project_id', this.uploadForm.projectId);
        if (this.uploadTab === 'audio' && this.uploadForm.file) {
            formData.append('file', this.uploadForm.file);
        } else if (this.uploadTab === 'text') {
            formData.append('text_content', this.uploadForm.textContent);
        }

        const headers = this.authService.getAuthHeaders();
        this.http.post(`${environment.apiUrl}/api/sessions/upload`, formData, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
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
                },
            });
    }

    generateActa(session: any, format: 'word' | 'pdf' = 'word', evt?: Event) {
        if (evt) { evt.stopPropagation(); evt.preventDefault(); }
        const genKey = `${session.id}_${format}`;
        if (this.generatingIds[genKey]) return;
        this.generatingIds[genKey] = true;
        this.cdr.detectChanges();

        const headers = this.authService.getAuthHeaders();
        this.http.get(`${environment.apiUrl}/api/sessions/${session.id}/export/${format}`, { headers, responseType: 'blob' })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
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
                    this.toast.error(`Error descargando el documento ${format.toUpperCase()}.`);
                    this.generatingIds[genKey] = false;
                    this.cdr.detectChanges();
                },
            });
    }

    viewCuration(sessionId: number, evt?: Event) {
        if (evt) { evt.stopPropagation(); }
        this.router.navigate(['/admin/curation', sessionId]);
    }

    deleteSession(session: any, evt?: Event) {
        if (evt) { evt.stopPropagation(); }
        this.sessionToDelete = session;
        this.showDeleteModal = true;
    }

    cancelDeleteSession() { this.showDeleteModal = false; this.sessionToDelete = null; }

    confirmDeleteSession() {
        if (!this.sessionToDelete) return;
        this.isDeleting = true;
        const headers = this.authService.getAuthHeaders();
        this.http.delete(`${environment.apiUrl}/api/sessions/${this.sessionToDelete.id}`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
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
                },
            });
    }

    // ============================================================
    // KEBAB MENU (3-dots de cada fila)
    // ============================================================
    openRowMenuId: number | null = null;
    toggleRowMenu(id: number, evt: Event) {
        evt.stopPropagation();
        this.openRowMenuId = this.openRowMenuId === id ? null : id;
    }
    closeRowMenu() { this.openRowMenuId = null; }
}
