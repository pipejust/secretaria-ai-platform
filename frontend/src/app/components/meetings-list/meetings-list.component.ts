import { Component, OnInit, OnDestroy, ChangeDetectorRef, HostListener } from '@angular/core';
import { CommonModule } from '@angular/common';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { Router, RouterModule } from '@angular/router';
import { Subject } from 'rxjs';
import { takeUntil } from 'rxjs/operators';
import { AuthService } from '../../services/auth.service';
import { ToastService } from '../../services/toast.service';
import { environment } from '../../../environments/environment';
import { MdRenderPipe } from '../../pipes/md-render.pipe';
import { UserChipComponent } from '../shared/user-chip/user-chip.component';
import { UserDirectoryService } from '../../services/user-directory.service';
import { BrandingService } from '../../services/branding.service';

/** Sub-tab de la card del header (filtro rápido por status). */
type StatusTab = 'all' | 'analyzed' | 'drafts' | 'archived';
/** Tab activo del panel lateral derecho de detalle. */
type DetailTab = 'summary' | 'transcript' | 'insights' | 'notes' | 'files';

interface ActionItemDTO {
    id: number;
    title: string;
    owner_name?: string;
    owner_email?: string;
    /** Resolución a User del tenant inyectada por el backend (
     *  GET /api/sessions/{id}). Si owner_user_id está presente, el chip
     *  pinta el avatar real del usuario y su nombre editado. */
    owner_user_id?: number | null;
    owner_full_name?: string;
    owner_avatar_url?: string | null;
    owner_is_user?: boolean;
    due_date?: string;
    status?: string;
    priority?: string;
}

/** Participante normalizado para avatares + tooltip. El backend persiste
 *  `processed_attendees` como JSON array de objetos {name, role, entity, email}.
 *  Lo tipamos acá para no perder rol/empresa/email en la UI. El email
 *  permite que el <app-user-chip> resuelva al User real del tenant. */
interface Attendee {
    name: string;
    role?: string;
    company?: string;
    email?: string;
}

@Component({
    selector: 'app-meetings-list',
    standalone: true,
    imports: [CommonModule, FormsModule, RouterModule, MdRenderPipe, UserChipComponent, TranslateModule],
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
    /** Filtros visuales adicionales que el mockup pide. Algunos son aún
     *  placeholders client-side; otros (filterDate, filterDateFrom,
     *  filterDateTo) ya filtran sobre el lote cargado. */
    filterTeam = '';
    /** Preset de fecha — 'today' | 'week' | 'month' | '' (cualquiera). */
    filterDate = '';
    /** Rango custom de fechas (yyyy-MM-dd). Si ambos están seteados,
     *  override del preset. Botón "Limpiar fechas" los vacía. */
    filterDateFrom = '';
    filterDateTo = '';
    filterSource = '';

    /** Modal "Ver participantes" — abre la lista completa del session
     *  seleccionado en el panel lateral. */
    showParticipantsModal = false;

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
        private userDirectory: UserDirectoryService,
        private branding: BrandingService,
        private translate: TranslateService,
    ) {
        // Cuando el directorio resuelve nuevos emails (porque otra vista los
        // pidió o porque preload llegó), forzamos re-render para que los
        // avatares de participantes aparezcan.
        this.userDirectory.directory$
            .pipe(takeUntil(this.destroy$))
            .subscribe(() => this.cdr.markForCheck());
    }

    ngOnInit(): void {
        this.loadSessions();
        this.loadProjects();
        this.loadStats();
    }

    // ---------- KPIs globales (no paginados) ----------
    /** Agregados del backend para alimentar las 4 tarjetas KPI del header.
     *  Los conteos NO dependen de la página actual ni del status visual
     *  seleccionado: son del UNIVERSO de sesiones del tenant (respetando
     *  solo filterProjectId + include_archived). */
    stats: {
        total: number;
        analyzed: number;
        pending: number;
        archived: number;
        avg_duration_minutes: number;
    } | null = null;

    /** Llama GET /api/sessions/_stats. Idempotente: cualquier cambio de
     *  filtro estructural (proyecto, archived) debe re-llamarlo; cambios
     *  de paginación o sub-tab visual NO. */
    loadStats(): void {
        const headers = this.authService.getAuthHeaders();
        let params = '';
        if (this.filterProjectId) params += `?project_id=${this.filterProjectId}`;
        if (this.statusTab === 'archived') {
            params += (params ? '&' : '?') + 'include_archived=true';
        }
        this.http.get<any>(`${environment.apiUrl}/api/sessions/_stats${params}`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (data) => {
                    this.stats = data;
                    this.cdr.detectChanges();
                },
                error: (err) => { console.error('Error fetching session stats:', err); },
            });
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
        const fallback = this.translate.instant('meetings_list.project_general');
        if (!projectId) return fallback;
        const p = this.projects.find(proj => proj.id === projectId);
        return p ? p.name : fallback;
    }

    /** Carga la página actual de sesiones del backend. Status server-side
     *  cuando hay filtro explícito; el sub-tab del header se aplica
     *  client-side encima del resultado para evitar refetches. */
    /** Mapea el sub-tab del header al `status` que entiende el backend.
     *  Devuelve null cuando el tab no debe forzar un filtro en API (caso `all`). */
    private tabToBackendStatus(): string | null {
        switch (this.statusTab) {
            case 'analyzed': return 'completed';
            case 'drafts':   return 'pending';
            case 'archived': return 'archived';
            default:         return null;
        }
    }

    loadSessions() {
        this.isLoading = true;
        let params = `?page=${this.currentPage}&limit=${this.limit}`;
        // Prioridad: si el dropdown manual fija un estado, gana. Si no, miramos
        // el sub-tab del header. Sin status el backend devuelve TODO MENOS
        // archivadas — comportamiento por defecto.
        const tabStatus = this.tabToBackendStatus();
        const effectiveStatus = this.statusFilter || tabStatus;
        if (effectiveStatus) params += `&status=${effectiveStatus}`;
        // Para la tab "Archivadas" el backend igual filtra por status=archived,
        // pero si el usuario combinara filtros (statusFilter !== 'archived') el
        // include_archived garantiza que no oculte filas reales.
        if (this.statusTab === 'archived') params += `&include_archived=true`;
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
                    // Pre-cargamos los emails de participantes en una sola
                    // llamada batched. Solo matcheamos por email: si un
                    // attendee no trae email, no podemos resolverlo a un
                    // user (y eso es correcto — el nombre puede colisionar).
                    const allEmails: string[] = [];
                    for (const s of this.sessions) {
                        for (const a of this.attendeesOf(s)) {
                            if (a.email) allEmails.push(a.email);
                        }
                    }
                    this.preloadAttendeeEmails(allEmails);
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

    /** Devuelve el conteo client-side del status (sobre la página actual).
     *  Solo se usa como fallback cuando `stats` aún no llegó del backend. */
    countByStatus(status: string): number {
        return (this.sessions || []).filter((s) => s?.status === status).length;
    }

    /** Total de sesiones del tenant — del endpoint /_stats (global, NO
     *  paginado). Fallback al totalItems del listado cuando aún no llegó. */
    get totalMeetings(): number {
        return this.stats?.total ?? (this.totalItems || this.sessions.length);
    }

    /** Sesiones completadas — agregado global. */
    get analyzedCount(): number {
        return this.stats?.analyzed ?? this.countByStatus('completed');
    }
    /** Pendientes — agregado global. */
    get pendingCount(): number {
        return this.stats?.pending ?? this.countByStatus('pending');
    }
    /** Archivadas — agregado global. Cero cuando include_archived=false en
     *  el endpoint, lo cual es el comportamiento deseado (no la mostramos
     *  como KPI hasta que el user entre a la tab Archived). */
    get archivedCount(): number {
        return this.stats?.archived ?? this.countByStatus('archived');
    }

    /** Porcentaje del total para los KPI tiles ("66% of total"). */
    pctOfTotal(count: number): number {
        const t = this.totalMeetings;
        if (!t) return 0;
        return Math.round((count / t) * 100);
    }

    /** Duración media global en minutos — agregado del endpoint /_stats.
     *  Mostramos `—` si no hay sesiones con transcripción. */
    get avgDurationLabel(): string {
        const m = this.stats?.avg_duration_minutes;
        if (m == null) {
            // Fallback client-side mientras llega stats.
            const withTranscript = (this.sessions || []).filter((s) => s?.raw_transcript);
            if (!withTranscript.length) return '—';
            const avgWords = withTranscript.reduce(
                (acc, s) => acc + ((s.raw_transcript || '').split(/\s+/).length),
                0,
            ) / withTranscript.length;
            const minutes = Math.max(1, Math.round(avgWords / 150));
            return `${minutes}m`;
        }
        if (m <= 0) return '—';
        return `${m}m`;
    }

    setStatusTab(tab: StatusTab): void {
        if (this.statusTab === tab) return;
        const prevWasArchived = this.statusTab === 'archived';
        this.statusTab = tab;
        this.currentPage = 1;
        // Cada tab corresponde a un filtro real en el backend (analyzed →
        // 'completed', drafts → 'pending', archived → 'archived', all → sin
        // filtro). Re-cargamos para no quedar mostrando un lote viejo.
        this.loadSessions();
        // Los KPIs son globales pero include_archived solo se activa cuando
        // estamos en la tab Archived. Si cruzamos esa frontera, refrescamos.
        if (prevWasArchived || tab === 'archived') {
            this.loadStats();
        }
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
            // "filtros aplicados pero no visibles".
            this.filterDate = '';
            this.filterDateFrom = '';
            this.filterDateTo = '';
            this.filterSource = '';
        }
    }

    /** Cuántos filtros del panel están activos. Se muestra como contador
     *  en el botón "Filtros (N)" para que el user sepa que hay filtros
     *  aplicados aunque el panel esté cerrado. */
    get activeFiltersCount(): number {
        const dateActive = !!this.filterDate || !!this.filterDateFrom || !!this.filterDateTo;
        return [dateActive ? 'date' : '', this.filterSource].filter(Boolean).length;
    }

    /** Limpia el rango custom (botón "Restablecer" del filtro de fecha). */
    clearDateRange(): void {
        this.filterDateFrom = '';
        this.filterDateTo = '';
    }

    /** Util: pasa la fecha de la sesión a Date object. Maneja los 3
     *  formatos: epoch ms (number), epoch ms (string numérica), ISO. */
    private _parseSessionDate(s: any): Date | null {
        let v = s?.date;
        if (v == null) return null;
        if (typeof v === 'string' && !isNaN(Number(v))) v = Number(v);
        const d = new Date(v);
        return isNaN(d.getTime()) ? null : d;
    }

    /** True si la sesión cae en el rango activo (preset O custom). */
    private _matchesDateFilter(s: any): boolean {
        // Sin filtros de fecha → pasa todo.
        if (!this.filterDate && !this.filterDateFrom && !this.filterDateTo) return true;

        const d = this._parseSessionDate(s);
        if (!d) return false;

        // 1) Rango custom — toma precedencia sobre el preset.
        if (this.filterDateFrom || this.filterDateTo) {
            if (this.filterDateFrom) {
                const from = new Date(this.filterDateFrom + 'T00:00:00');
                if (d < from) return false;
            }
            if (this.filterDateTo) {
                const to = new Date(this.filterDateTo + 'T23:59:59');
                if (d > to) return false;
            }
            return true;
        }

        // 2) Preset 'today' / 'week' / 'month'.
        const now = new Date();
        const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        if (this.filterDate === 'today') {
            return d >= todayStart;
        }
        if (this.filterDate === 'week') {
            // Última semana corrida (no la ISO-week — más intuitivo).
            const weekAgo = new Date(todayStart);
            weekAgo.setDate(weekAgo.getDate() - 7);
            return d >= weekAgo;
        }
        if (this.filterDate === 'month') {
            const monthAgo = new Date(todayStart);
            monthAgo.setDate(monthAgo.getDate() - 30);
            return d >= monthAgo;
        }
        return true;
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

        // Filtro de fecha (preset o rango custom). Aplicado client-side
        // sobre el lote ya cargado — cuando el backend exponga ?from=&to=
        // pasamos a filtrar server-side.
        if (this.filterDate || this.filterDateFrom || this.filterDateTo) {
            filtered = filtered.filter((s) => this._matchesDateFilter(s));
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

    /** Abre el modal con la lista completa de participantes de la sesión
     *  seleccionada. Usado por el botón "Ver participantes". */
    openParticipantsModal(): void {
        if (!this.selectedSession) return;
        this.showParticipantsModal = true;
    }
    closeParticipantsModal(): void { this.showParticipantsModal = false; }

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
                    email: (x.email || x.mail || '').trim().toLowerCase() || undefined,
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

    /** Resuelve un attendee a un User del tenant ESTRICTAMENTE por email.
     *  No usamos nombre como fallback: dos personas distintas pueden tener
     *  el mismo nombre, y mostrar la foto equivocada es peor que no mostrar
     *  ninguna foto. */
    private _attendeeUser(email?: string) {
        if (!email) return null;
        return this.userDirectory.peek(email) || null;
    }

    /** URL absoluta de la foto del User solo cuando el email matchea.
     *  null → fallback a iniciales (correcto cuando no hay email o el
     *  email corresponde a un contacto externo). */
    attendeeAvatarUrl(email?: string, _name?: string): string | null {
        const user = this._attendeeUser(email);
        if (!user || !user.avatar_url) return null;
        const raw = user.avatar_url;
        if (raw.startsWith('http://') || raw.startsWith('https://')) return raw;
        return `${environment.apiUrl}${raw}`;
    }

    /** Nombre a mostrar: el `full_name` editado del User solo si el email
     *  matchea; si no hay email o el email no corresponde a un user del
     *  tenant, usamos el texto original que vino con la reunión. */
    attendeeDisplayName(email: string | undefined, fallback: string): string {
        const user = this._attendeeUser(email);
        if (user?.full_name) return user.full_name;
        return fallback;
    }

    /** Pre-carga emails contra el directorio. Una sola llamada batched. */
    preloadAttendeeEmails(emails: Array<string | undefined>): void {
        this.userDirectory.preload(emails || []);
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
        if (!ff || ff.startsWith('MANUAL-')) return { name: this.translate.instant('meetings_list.source_manual'), key: 'manual' };
        return { name: this.translate.instant('meetings_list.source_web'), key: 'web' };
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
            case 'completed': return { label: this.translate.instant('meetings_list.status_analyzed'), key: 'analyzed' };
            case 'pending':   return { label: this.translate.instant('meetings_list.status_pending'), key: 'pending' };
            case 'processing': return { label: this.translate.instant('meetings_list.status_processing'), key: 'processing' };
            case 'archived':  return { label: this.translate.instant('meetings_list.status_archived'), key: 'archived' };
            default:          return { label: this.translate.instant('meetings_list.status_draft'), key: 'draft' };
        }
    }

    /** ¿La sesión está rota / claramente incompleta?
     *
     *  Devuelve true cuando alguno de estos casos aplica:
     *   1. El backend marcó `processing_error` (pipeline falló tras retries).
     *   2. La sesión es legacy (anterior al rollout del flag) PERO está
     *      visiblemente vacía: sin transcripción, sin resumen, sin decisiones,
     *      sin riesgos y sin acuerdos. Si todo eso está en blanco y NO está
     *      en estado 'processing' (que es transitorio normal), claramente
     *      algún paso del pipeline IA se cayó silenciosamente.
     *
     *  Excluimos `archived` porque archivar es decisión humana legítima. */
    isSessionFailing(s: any): boolean {
        if (!s) return false;
        if (((s.processing_error || '') as string).trim()) return true;
        const status = ((s.status || '') as string).toLowerCase();
        if (status === 'processing' || status === 'archived') return false;
        const empty = (v: any) => !((v || '') as string).trim();
        return (
            empty(s.raw_transcript) &&
            empty(s.raw_summary) &&
            empty(s.processed_decisions) &&
            empty(s.processed_risks) &&
            empty(s.processed_agreements)
        );
    }

    /** Mensaje humano del por qué está fallando, para tooltip / banner. */
    failingReason(s: any): string {
        if (!s) return '';
        const explicit = ((s.processing_error || '') as string).trim();
        if (explicit) return explicit;
        return this.translate.instant('meetings_list.failing_reason_default');
    }

    /** Color del badge de prioridad de las action items del panel. */
    priorityBadge(p: string): { label: string; key: 'high' | 'medium' | 'low' } {
        const v = (p || '').toLowerCase();
        if (v === 'high' || v === 'alto')    return { label: this.translate.instant('meetings_list.priority_high'),   key: 'high' };
        if (v === 'medium' || v === 'medio') return { label: this.translate.instant('meetings_list.priority_medium'),  key: 'medium' };
        return { label: this.translate.instant('meetings_list.priority_low'), key: 'low' };
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
        // Pre-cargamos con el idioma por defecto del workspace para que el
        // pipeline IA y el form arranquen alineados sin que el admin tenga
        // que tocar el select. Mapeo del código corto a nombre humano que
        // el backend espera.
        const _langMap: Record<string, string> = {
            es: 'Español', ca: 'Català', en: 'Inglés',
        };
        const _tenantLang = (this.branding.brand().default_language || 'es').toLowerCase();
        this.uploadForm.language = _langMap[_tenantLang] || 'Español';
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
        if (!this.uploadForm.title) { this.toast.warning(this.translate.instant('meetings_list.toast_title_required')); return; }
        if (this.uploadTab === 'audio' && !this.uploadForm.file) {
            this.toast.warning(this.translate.instant('meetings_list.toast_audio_required')); return;
        }
        if (this.uploadTab === 'text' && !this.uploadForm.textContent.trim()) {
            this.toast.warning(this.translate.instant('meetings_list.toast_text_required')); return;
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
                    this.toast.success(this.translate.instant('meetings_list.toast_session_created'));
                    this.showUploadModal = false;
                    this.isUploading = false;
                    this.loadSessions();
                },
                error: (err) => {
                    this.toast.error(
                        this.translate.instant('meetings_list.toast_session_upload_error', {
                            detail: err?.error?.detail || err?.message || 'desconocido',
                        }),
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
                    this.toast.error(this.translate.instant('meetings_list.toast_export_error', { format: format.toUpperCase() }));
                    this.generatingIds[genKey] = false;
                    this.cdr.detectChanges();
                },
            });
    }

    viewCuration(sessionId: number, evt?: Event) {
        if (evt) { evt.stopPropagation(); }
        this.router.navigate(['/admin/curation', sessionId]);
    }

    /** Clic sobre el título (ahora un <a> con href a la curación).
     *  - Clic izquierdo normal → abre el panel lateral (comportamiento
     *    previo), evitando la navegación del href.
     *  - Ctrl/⌘/Shift+clic → deja que el navegador use el href (nueva
     *    pestaña / ventana). El clic central usa `auxclick` nativo, no
     *    pasa por aquí, así que también abre en pestaña nueva.
     *  En todos los casos frenamos la propagación para que la fila no
     *  dispare `selectSession` por debajo. */
    onTitleClick(s: any, evt: MouseEvent): void {
        evt.stopPropagation();
        if (evt.ctrlKey || evt.metaKey || evt.shiftKey) return;
        evt.preventDefault();
        this.selectSession(s);
    }

    deleteSession(session: any, evt?: Event) {
        if (evt) { evt.stopPropagation(); }
        this.sessionToDelete = session;
        this.showDeleteModal = true;
    }

    /** Archiva o desarchiva una reunión sin pedir confirmación adicional.
     *  Usa el endpoint genérico PUT /api/sessions/:id con `{status: ...}`. */
    archiveSession(session: any, evt?: Event): void {
        this.setSessionStatus(session, 'archived', this.translate.instant('meetings_list.status_label_archived'), evt);
    }

    unarchiveSession(session: any, evt?: Event): void {
        this.setSessionStatus(session, 'pending', this.translate.instant('meetings_list.status_label_restored'), evt);
    }

    private setSessionStatus(session: any, newStatus: string, humanLabel: string, evt?: Event): void {
        if (evt) { evt.stopPropagation(); }
        if (!session?.id) return;
        const headers = this.authService.getAuthHeaders();
        // Snapshot por si necesitamos rollback en caso de error.
        const prevStatus = session.status;
        session.status = newStatus;
        this.cdr.detectChanges();
        this.http.put(
            `${environment.apiUrl}/api/sessions/${session.id}`,
            { status: newStatus },
            { headers },
        )
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: () => {
                    this.toast.success(this.translate.instant('meetings_list.toast_meeting_status_changed', { status: humanLabel }));
                    // Re-fetch para que la sesión salga (o entre) del tab actual
                    // sin esperar a que el usuario cambie de página.
                    this.loadSessions();
                },
                error: () => {
                    session.status = prevStatus;
                    this.cdr.detectChanges();
                    this.toast.error(this.translate.instant('meetings_list.toast_meeting_status_error'));
                },
            });
    }

    /** Set de IDs de sesiones con un retry en vuelo (para mostrar spinner
     *  y deshabilitar el botón). El template lo consulta como
     *  `retryingIds.has(s.id)`. */
    retryingIds = new Set<number>();

    /** Reintenta el pipeline IA de una sesión que quedó incompleta.
     *  Si `rehydrate=true`, vuelve a tirar Fireflies para re-traer transcript
     *  + summary nativo y luego correr la IA. Si false, solo re-corre la IA
     *  con lo que ya está en DB (más rápido, menos consumo de tokens).
     *
     *  Polling persistente cada 5 s hasta 120 s para refrescar la fila
     *  cuando el backend termine. */
    retrySessionPipeline(session: any, rehydrate: boolean, evt?: Event): void {
        // PreventDefault + stopPropagation para que el click NO burbujee al
        // (click)="selectSession(s)" del row, ni navegue por accidente.
        if (evt) {
            evt.stopPropagation();
            evt.preventDefault();
        }
        if (!session?.id) {
            this.toast.error(this.translate.instant('meetings_list.toast_retry_session_unknown'));
            return;
        }
        if (this.retryingIds.has(session.id)) {
            return;  // ya hay uno corriendo
        }
        this.retryingIds.add(session.id);
        // Marcamos visualmente el row como "en proceso" sin esperar el server.
        session.status = 'processing';
        session.processing_error = '';
        this.cdr.detectChanges();

        const headers = this.authService.getAuthHeaders();
        const url =
            `${environment.apiUrl}/api/webhook/fireflies/sessions/${session.id}/retry` +
            (rehydrate ? '?rehydrate_from_fireflies=true' : '');
        this.toast.info(
            rehydrate
                ? this.translate.instant('meetings_list.toast_retry_rehydrate_info')
                : this.translate.instant('meetings_list.toast_retry_reanalyze_info'),
        );
        this.http.post(url, {}, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: () => {
                    this.startRetryPolling(session.id);
                },
                error: (err) => {
                    this.retryingIds.delete(session.id);
                    const status = err?.status;
                    const detail = err?.error?.detail || err?.message || '';
                    this.toast.error(
                        status
                            ? this.translate.instant('meetings_list.toast_retry_queue_error_http', { status, detail })
                            : this.translate.instant('meetings_list.toast_retry_queue_error'),
                    );
                    this.cdr.detectChanges();
                },
            });
    }

    /** Polling discreto: GET la sesión cada 5 s hasta 120 s. Cuando el
     *  backend marca processing_completed_at o un nuevo processing_error,
     *  refrescamos toda la lista. */
    private startRetryPolling(sessionId: number): void {
        const headers = this.authService.getAuthHeaders();
        const MAX = 24;
        const pollMs = 5000;
        let attempts = 0;
        const tick = () => {
            attempts += 1;
            this.http.get<any>(
                `${environment.apiUrl}/api/sessions/${sessionId}`,
                { headers },
            )
                .pipe(takeUntil(this.destroy$))
                .subscribe({
                    next: (res) => {
                        const session = res?.session ?? res;
                        const completedAt = (session?.processing_completed_at || '').trim();
                        const errorMsg = (session?.processing_error || '').trim();
                        const stillRunning = (session?.status || '').toLowerCase() === 'processing';
                        const finished = !!completedAt || (!!errorMsg && !stillRunning);
                        if (finished || attempts >= MAX) {
                            this.retryingIds.delete(sessionId);
                            if (completedAt) {
                                this.toast.success(this.translate.instant('meetings_list.toast_pipeline_completed'));
                            } else if (errorMsg) {
                                this.toast.error(this.translate.instant('meetings_list.toast_pipeline_retry_failed', { detail: errorMsg.slice(0, 200) }));
                            } else {
                                this.toast.warning(
                                    this.translate.instant('meetings_list.toast_pipeline_still_processing'),
                                );
                            }
                            this.loadSessions();
                            return;
                        }
                        setTimeout(tick, pollMs);
                    },
                    error: () => {
                        if (attempts < MAX) {
                            setTimeout(tick, pollMs);
                        } else {
                            this.retryingIds.delete(sessionId);
                            this.cdr.detectChanges();
                        }
                    },
                });
        };
        setTimeout(tick, 1500);
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
                    this.toast.success(this.translate.instant('meetings_list.toast_session_deleted'));
                    this.loadSessions();
                },
                error: () => {
                    this.isDeleting = false;
                    this.toast.error(this.translate.instant('meetings_list.toast_session_delete_error'));
                },
            });
    }

    // ============================================================
    // KEBAB MENU (3-dots de cada fila)
    // ============================================================
    openRowMenuId: number | null = null;
    /** Posición fixed del menú flotante. Calculada desde el botón que lo
     *  abre via getBoundingClientRect — necesario porque la cadena de
     *  padres (mt-table-wrap, content-area, etc.) tiene overflow:hidden
     *  que recortaba el menú aunque tuviera z-index 1000. Solución:
     *  posicionar el menú en el viewport. */
    rowMenuPos: { top: number; right: number } | null = null;

    toggleRowMenu(id: number, evt: Event) {
        evt.stopPropagation();
        if (this.openRowMenuId === id) {
            this.openRowMenuId = null;
            this.rowMenuPos = null;
            return;
        }
        const btn = evt.currentTarget as HTMLElement | null;
        if (btn) {
            const r = btn.getBoundingClientRect();
            this.rowMenuPos = {
                top: r.bottom + 4,
                right: Math.max(8, window.innerWidth - r.right),
            };
        } else {
            this.rowMenuPos = null;
        }
        this.openRowMenuId = id;
    }
    closeRowMenu() {
        this.openRowMenuId = null;
        this.rowMenuPos = null;
    }

    @HostListener('window:scroll')
    @HostListener('window:resize')
    onWindowChange(): void {
        if (this.openRowMenuId !== null) this.closeRowMenu();
        if (this.avatarTip) this.avatarTip = null;
    }

    // ============================================================
    // AVATAR TOOLTIP FLOTANTE
    //
    // Los `.avatar-tip` inline (position:absolute) quedaban recortados
    // por overflow:hidden del padre (table-wrap, content-area, td en
    // responsive). Solución: un único overlay fixed al nivel root del
    // componente que se reposiciona desde el rect del host hovered.
    // ============================================================
    avatarTip: {
        top: number; left: number;
        name: string; role?: string; company?: string; email?: string;
    } | null = null;
    private _tipHideTimer: any = null;

    showAttendeeTip(ev: Event, a: { name?: string; email?: string; role?: string; company?: string }): void {
        // Cancela un hide programado — caso típico: mouse se mueve de un
        // avatar a otro adyacente, mouseleave del primero programa el hide
        // y mouseenter del segundo lo debe cancelar antes de que dispare.
        if (this._tipHideTimer) { clearTimeout(this._tipHideTimer); this._tipHideTimer = null; }
        const host = ev.currentTarget as HTMLElement | null;
        if (!host) return;
        const r = host.getBoundingClientRect();
        const display = this.attendeeDisplayName(a.email || '', a.name || '');
        this.avatarTip = {
            top: r.top - 8,
            left: r.left + r.width / 2,
            name: display,
            role: a.role || '',
            company: a.company || '',
            email: a.email || '',
        };
    }
    hideAttendeeTip(): void {
        // Pequeño delay para que el tooltip NO parpadee cuando el mouse se
        // mueve entre dos avatares stackeados — el siguiente mouseenter va
        // a cancelar este timer antes de que dispare.
        if (this._tipHideTimer) clearTimeout(this._tipHideTimer);
        this._tipHideTimer = setTimeout(() => {
            this.avatarTip = null;
            this._tipHideTimer = null;
            this.cdr.detectChanges();
        }, 80);
    }
}
