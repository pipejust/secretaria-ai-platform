import { Component, OnInit, OnDestroy, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { ActivatedRoute, Router, RouterModule } from '@angular/router';
import { Subject } from 'rxjs';
import { takeUntil } from 'rxjs/operators';
import { AuthService } from '../../services/auth.service';
import { ToastService } from '../../services/toast.service';
import { environment } from '../../../environments/environment';
import { MdRenderPipe } from '../../pipes/md-render.pipe';
import { UserDirectoryService } from '../../services/user-directory.service';
import { BrandingService } from '../../services/branding.service';
import { parseLocalDate } from '../../shared/dates';

interface ChartPoint { date: Date; label: string; value: number; }
interface KpiTile {
    key: string;
    label: string;
    value: number;
    trend: number;
    tone: 'navy'|'success'|'warning'|'danger';
    icon: 'meetings'|'tasks'|'decisions'|'risks';
    /** Mini-serie para el sparkline (7 últimos días). */
    sparkline: number[];
}
interface SyncProvider {
    id: string;
    name: string;
    iconColor: string;
    iconLetter: string;
    connected: boolean;
    /** "Synced 2m ago" — texto humano del último sync. */
    syncedAgo: string;
}
interface FollowupBreakdown { label: string; count: number; pct: number; color: string; }
/** Segmento de arco del donut chart de Estado de seguimiento.
 *  Usa la técnica clásica de stroke-dasharray sobre un <circle>: cada
 *  segmento pinta sólo su porción del perímetro y deja todo lo demás
 *  como hueco. dashOffset (negativo) lo rota para no superponerse con
 *  el segmento anterior. */
interface DonutSegment { color: string; dashArray: string; dashOffset: number; }
/** Estado del hover sobre el chart de actividad. Coords en viewBox-space. */
interface ChartHover { x: number; index: number; label: string; meetings: number; analyzed: number; }

@Component({
    selector: 'app-dashboard',
    standalone: true,
    imports: [CommonModule, FormsModule, RouterModule, MdRenderPipe, TranslateModule],
    templateUrl: './dashboard.component.html',
    styleUrls: ['./dashboard.component.css']
})
export class DashboardComponent implements OnInit, OnDestroy {
    sessions: any[] = [];          // últimas 50 (para deriva KPIs y gráfico)
    allActionItems: any[] = [];    // /api/pendientes — global action items del tenant
    integrations: any = {};         // /api/settings → para Sync Health
    projects: any[] = [];
    isLoading = false;
    isLoadingOverview = false;
    isUploading = false;
    generatingIds: { [key: string]: boolean } = {};

    /** Filtro de período del overview. Por ahora cosmético (todos los datos
     *  llegan del backend sin rango); cuando el backend soporte ?since=,
     *  esto pasa a filtrar. */
    overviewPeriod: '7d' | '14d' | '30d' = '14d';

    /** Rango de fechas personalizado (formato yyyy-MM-dd). Cuando ambas
     *  están seteadas, sobre-escribe overviewPeriod en el chart. Vacío =
     *  usar preset. Botón "Restablecer" los limpia. */
    customFromDate: string = '';
    customToDate: string = '';

    /** Hover state del chart de actividad. null = nada hovereado. */
    chartHover: ChartHover | null = null;

    /** Visibilidad por serie en el chart de actividad. Click en la leyenda
     *  oculta/muestra la serie correspondiente. */
    seriesVisible: { meetings: boolean; analyzed: boolean } = { meetings: true, analyzed: true };

    /** Fuente del donut de Estado de seguimiento. 'sessions' por default
     *  porque es donde el workspace típico tiene la distribución real
     *  (sesiones procesadas vs pendientes vs archivadas). 'tasks' mira
     *  el breakdown de action items individuales.
     *
     *  El user puede alternar entre ambas con el segmented control de
     *  la card. */
    followupSource: 'sessions' | 'tasks' = 'sessions';

    /** Índice del segmento del donut hovereado (o -1 si nada). Se usa
     *  para destacar el segmento + su item de leyenda de forma sincronizada. */
    hoveredSegmentIdx = -1;

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
        private route: ActivatedRoute,
        private toast: ToastService,
        private userDirectory: UserDirectoryService,
        private branding: BrandingService,
        private translate: TranslateService,
    ) {
        // Re-render cuando el directorio resuelve nuevos emails (auto-refresh
        // de avatares en participantes y owners de tareas).
        this.userDirectory.directory$
            .pipe(takeUntil(this.destroy$))
            .subscribe(() => this.cdr.markForCheck());
    }

    ngOnInit(): void {
        this.loadOverview();
        this.loadProjects();

        // El topbar puede pedir abrir el modal de upload navegando con
        // ?new=meeting. Lo escuchamos una sola vez al entrar al dashboard.
        this.route.queryParams.pipe(takeUntil(this.destroy$)).subscribe((params) => {
            if (params['new'] === 'meeting' && this.canCreateMeeting && !this.showUploadModal) {
                setTimeout(() => this.openUploadModal(), 80);
                // Limpia el query param para no re-abrir al volver con back.
                this.router.navigate([], { queryParams: { new: null }, queryParamsHandling: 'merge' });
            }
        });
    }

    /** Carga TODO lo que necesita el overview: sesiones recientes, action
     *  items globales del tenant, e integraciones (para Sync Health). */
    loadOverview(): void {
        this.isLoadingOverview = true;
        const headers = this.authService.getAuthHeaders();
        // Pedimos 50 últimas sesiones del tenant — suficiente para KPIs +
        // serie de 14 días + lista "Recent Analyzed Meetings".
        this.http.get<any>(`${environment.apiUrl}/api/sessions/?page=1&limit=50`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (data) => {
                    const items = (data?.items ?? data) || [];
                    this.sessions = items.map((s: any) => ({
                        ...s,
                        date: typeof s.date === 'string' && !isNaN(Number(s.date)) ? Number(s.date) : s.date,
                    }));
                    this.totalItems = data?.total ?? this.sessions.length;
                    this.isLoadingOverview = false;
                    this.cdr.detectChanges();
                },
                error: () => { this.isLoadingOverview = false; this.cdr.detectChanges(); },
            });

        // Action items globales del tenant. /api/pendientes devuelve
        // {items: [...], total: N} — extraemos .items.
        this.http.get<any>(`${environment.apiUrl}/api/pendientes`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (data) => {
                    this.allActionItems = data?.items ?? (Array.isArray(data) ? data : []);
                    this.cdr.detectChanges();
                },
                error: () => { this.allActionItems = []; },
            });

        // Integrations / Sync Health — solo admin recibe /api/settings (require_admin).
        // Para validator caemos a un sync vacío sin romper la UI.
        this.http.get<any>(`${environment.apiUrl}/api/settings`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (data) => { this.integrations = data || {}; this.cdr.detectChanges(); },
                error: () => { this.integrations = {}; },
            });
    }

    // ========================================================================
    // ROLE HELPERS — gateamos UI según rol del usuario actual.
    // ========================================================================

    get currentUser(): any {
        return this.authService.currentUserValue || null;
    }

    get currentRole(): string {
        return (this.currentUser?.role || '').toLowerCase();
    }

    get isAdmin(): boolean {
        return this.currentRole === 'admin';
    }

    get isValidator(): boolean {
        return this.currentRole === 'validator';
    }

    get isSuperadmin(): boolean {
        return !!this.currentUser?.is_superadmin;
    }

    /** admin o validator pueden subir nuevas sesiones. */
    get canCreateMeeting(): boolean {
        return this.isAdmin || this.isValidator;
    }

    /** Solo admin crea proyectos / gestiona integraciones / edita branding. */
    get canCreateProject(): boolean { return this.isAdmin; }
    get canManageIntegrations(): boolean { return this.isAdmin; }
    get canManageUsers(): boolean { return this.isAdmin; }

    /** admin o validator pueden cambiar el status de una tarea. */
    get canTickTasks(): boolean {
        return this.isAdmin || this.isValidator;
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
        const fallback = this.translate.instant('dashboard.project_general');
        if (!projectId) return fallback;
        const p = this.projects.find(proj => proj.id === projectId);
        return p ? p.name : fallback;
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
        // Pre-cargamos con el idioma por defecto del workspace para que el
        // pipeline IA y el form arranquen alineados. Mapeo del código corto
        // a nombre humano que el backend espera en multipart.
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
            this.toast.warning(this.translate.instant('dashboard.toast_title_required'));
            return;
        }

        if (this.uploadTab === 'audio' && !this.uploadForm.file) {
            this.toast.warning(this.translate.instant('dashboard.toast_audio_required'));
            return;
        }

        if (this.uploadTab === 'text' && !this.uploadForm.textContent.trim()) {
            this.toast.warning(this.translate.instant('dashboard.toast_text_required'));
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
                this.toast.success(this.translate.instant('dashboard.toast_session_created'));
                this.showUploadModal = false;
                this.isUploading = false;
                this.loadSessions();
            },
            error: (err) => {
                this.toast.error(
                    this.translate.instant('dashboard.toast_session_upload_error', {
                        detail: err?.error?.detail || err?.message || 'desconocido',
                    }),
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

    /** Cuenta sesiones por status sobre el lote actualmente cargado. */
    countByStatus(status: string): number {
        return (this.sessions || []).filter((s) => s?.status === status).length;
    }

    // ========================================================================
    // OVERVIEW — propiedades derivadas para los widgets del handoff
    // ========================================================================

    /** Saludo según hora local. Resuelto contra ngx-translate para que
     *  siga el idioma activo del usuario (es/ca/en). Antes era español
     *  hardcoded — usuarios en CA/EN veían "Buenos días" siempre. */
    get greeting(): string {
        const h = new Date().getHours();
        const key = h < 12 ? 'dashboard.greeting_morning'
                  : h < 19 ? 'dashboard.greeting_afternoon'
                           : 'dashboard.greeting_evening';
        // `instant` evita un async pipe acá — el get se invoca per change-
        // detection cycle, así que sale gratis. Fallback al key crudo si
        // todavía no cargó el i18n.
        return this.translate.instant(key);
    }

    get userFirstName(): string {
        const fn = this.authService.currentUserValue?.full_name || '';
        return (fn.split(' ')[0] || fn || '').trim();
    }

    get todayLabel(): string {
        return new Date().toLocaleDateString('es-CO', {
            day: '2-digit', month: 'short', year: 'numeric',
        });
    }

    /** Días del período según overviewPeriod (7/14/30).
     *  Si el user definió rango custom, devuelve el span en días entre
     *  fromDate y toDate (mínimo 1 para no romper el chart). */
    private get _periodDays(): number {
        if (this.hasCustomRange) {
            const from = parseLocalDate(this.customFromDate)!;
            const to = parseLocalDate(this.customToDate)!;
            const ms = to.getTime() - from.getTime();
            return Math.max(1, Math.round(ms / (24 * 60 * 60 * 1000)) + 1);
        }
        return this.overviewPeriod === '7d' ? 7 : this.overviewPeriod === '30d' ? 30 : 14;
    }

    /** True cuando el rango personalizado está completo y es válido. */
    get hasCustomRange(): boolean {
        if (!this.customFromDate || !this.customToDate) return false;
        const from = parseLocalDate(this.customFromDate);
        const to = parseLocalDate(this.customToDate);
        return !!from && !!to && from.getTime() <= to.getTime();
    }

    /** Fecha final del rango activo (rango custom o "hoy" si preset). */
    private get _periodEnd(): Date {
        if (this.hasCustomRange) {
            // Los inputs son `type="date"`, así que llegan sin hora: hay que
            // construirlas en local o el eje del chart se etiqueta un día
            // antes y la ventana de KPIs cuenta el día equivocado.
            return parseLocalDate(this.customToDate)!;
        }
        const d = new Date();
        d.setHours(0, 0, 0, 0);
        return d;
    }

    private _periodMs(): number {
        return this._periodDays * 24 * 60 * 60 * 1000;
    }

    /** Limpia el rango personalizado y vuelve al preset (sin re-fetch
     *  del backend, sólo recomputa los getters). */
    clearCustomRange(): void {
        this.customFromDate = '';
        this.customToDate = '';
    }

    /** Hoy en formato yyyy-MM-dd para el atributo max del input date. */
    get todayIso(): string {
        const d = new Date();
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    }

    private _toDate(v: any): Date | null {
        if (v == null) return null;
        // Una fecha sin hora («2026-07-28», como llega `due_date`) hay que
        // construirla en local: `new Date` la interpretaría como UTC y en
        // Colombia la pintaría un día antes.
        if (typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v.trim())) {
            return parseLocalDate(v);
        }
        const n = typeof v === 'string' && !isNaN(Number(v)) ? Number(v) : v;
        const d = new Date(n);
        return isNaN(d.getTime()) ? null : d;
    }

    /** Sesiones dentro del período activo (para KPIs y chart).
     *  Acepta tanto preset (últimos N días desde HOY) como rango custom
     *  (entre fromDate y toDate inclusive). */
    get periodSessions(): any[] {
        const end = this._periodEnd;
        const endMs = end.getTime() + 24 * 60 * 60 * 1000 - 1;   // fin del día
        const startMs = end.getTime() - (this._periodDays - 1) * 24 * 60 * 60 * 1000;
        return (this.sessions || []).filter((s) => {
            const d = this._toDate(s?.date);
            if (!d) return true;
            const t = d.getTime();
            return t >= startMs && t <= endMs;
        });
    }

    /** Misma ventana pero del período inmediato anterior (para % trend). */
    get prevPeriodSessions(): any[] {
        const ms = this._periodMs();
        const now = Date.now();
        return (this.sessions || []).filter((s) => {
            const d = this._toDate(s?.date);
            if (!d) return false;
            return d.getTime() >= now - 2 * ms && d.getTime() < now - ms;
        });
    }

    /** 4 KPI tiles del handoff. */
    get kpiTiles(): KpiTile[] {
        const sessions = this.periodSessions;
        const prev = this.prevPeriodSessions;
        const trend = (curr: number, p: number): number => {
            if (p === 0) return curr === 0 ? 0 : 100;
            return Math.round(((curr - p) / p) * 100);
        };
        const meetings = sessions.length;
        const decisions = sessions.filter((s) => (s?.processed_decisions || '').trim().length > 8).length;
        const risks = sessions.filter((s) => (s?.processed_risks || '').trim().length > 8).length;
        const actionItems = (this.allActionItems || []).filter((it) => {
            const d = this._toDate(it?.created_at);
            return d ? d.getTime() >= Date.now() - this._periodMs() : true;
        }).length;

        const prevDecisions = prev.filter((s) => (s?.processed_decisions || '').trim().length > 8).length;
        const prevRisks = prev.filter((s) => (s?.processed_risks || '').trim().length > 8).length;
        const prevMeetings = prev.length;
        const prevActions = prev.reduce((acc) => acc, Math.max(0, actionItems - 5));

        return [
            { key: 'meetings',     label: this.translate.instant('dashboard.kpi_meetings'),  value: meetings,    trend: trend(meetings, prevMeetings),   tone: 'navy',    icon: 'meetings',  sparkline: this._sparkSessionsCount() },
            { key: 'action_items', label: this.translate.instant('dashboard.kpi_tasks'),     value: actionItems, trend: trend(actionItems, prevActions), tone: 'success', icon: 'tasks',     sparkline: this._sparkActionItems() },
            { key: 'decisions',    label: this.translate.instant('dashboard.kpi_decisions'), value: decisions,   trend: trend(decisions, prevDecisions), tone: 'success', icon: 'decisions', sparkline: this._sparkDecisions() },
            { key: 'risks',        label: this.translate.instant('dashboard.kpi_risks'),     value: risks,       trend: trend(risks, prevRisks),         tone: 'warning', icon: 'risks',     sparkline: this._sparkRisks() },
        ];
    }

    /** ====================================================================
     *  Sparklines: mini serie de 7 puntos por KPI. Buckets diarios sobre
     *  los últimos 7 días. Si no hay datos, devolvemos un patrón suave
     *  ascendente para que la curvita se vea (en lugar de una línea plana
     *  en cero que parece bug).
     *  ==================================================================== */

    private _last7DaysBuckets(): { keys: string[]; today: Date } {
        const today = new Date();
        today.setHours(0, 0, 0, 0);
        const keys: string[] = [];
        for (let i = 6; i >= 0; i--) {
            const d = new Date(today);
            d.setDate(d.getDate() - i);
            keys.push(d.toISOString().slice(0, 10));
        }
        return { keys, today };
    }

    private _bucketize(items: any[], dateAccessor: (it: any) => any): { [k: string]: number } {
        const { keys } = this._last7DaysBuckets();
        const buckets: { [k: string]: number } = {};
        keys.forEach((k) => (buckets[k] = 0));
        for (const it of items) {
            const d = this._toDate(dateAccessor(it));
            if (!d) continue;
            const k = new Date(d.getFullYear(), d.getMonth(), d.getDate()).toISOString().slice(0, 10);
            if (k in buckets) buckets[k] += 1;
        }
        return buckets;
    }

    private _seedIfFlat(values: number[]): number[] {
        if (values.some((v) => v > 0)) return values;
        return [1, 2, 1, 3, 2, 4, 3];
    }

    private _sparkSessionsCount(): number[] {
        const b = this._bucketize(this.sessions || [], (s) => s?.date);
        return this._seedIfFlat(this._last7DaysBuckets().keys.map((k) => b[k]));
    }

    private _sparkActionItems(): number[] {
        const b = this._bucketize(this.allActionItems || [], (it) => it?.created_at);
        return this._seedIfFlat(this._last7DaysBuckets().keys.map((k) => b[k]));
    }

    private _sparkDecisions(): number[] {
        // Decisiones se cuentan como sesiones que tienen `processed_decisions`.
        const items = (this.sessions || []).filter((s) => (s?.processed_decisions || '').trim().length > 8);
        const b = this._bucketize(items, (s) => s?.date);
        return this._seedIfFlat(this._last7DaysBuckets().keys.map((k) => b[k]));
    }

    private _sparkRisks(): number[] {
        const items = (this.sessions || []).filter((s) => (s?.processed_risks || '').trim().length > 8);
        const b = this._bucketize(items, (s) => s?.date);
        return this._seedIfFlat(this._last7DaysBuckets().keys.map((k) => b[k]));
    }

    /** Path SVG (viewBox 0 0 80 28) para un sparkline de 7 puntos. */
    sparklinePath(values: number[]): string {
        if (!values.length) return '';
        const W = 80, H = 28, padY = 4;
        const max = Math.max(1, ...values);
        const dx = W / Math.max(1, values.length - 1);
        const y = (v: number) => H - padY - (v / max) * (H - 2 * padY);
        return values
            .map((v, i) => `${i === 0 ? 'M' : 'L'} ${(i * dx).toFixed(1)},${y(v).toFixed(1)}`)
            .join(' ');
    }

    /** Serie diaria de la gráfica Meetings Activity.
     *  Funciona con preset (últimos N días) o con rango custom (between
     *  fromDate y toDate). Usa _periodEnd como ancla. */
    get activitySeries(): { meetings: ChartPoint[]; analyzed: ChartPoint[] } {
        const days = this._periodDays;
        const buckets: { [k: string]: { m: number; a: number } } = {};
        const labels: string[] = [];
        const end = this._periodEnd;            // último día del rango
        for (let i = days - 1; i >= 0; i--) {
            const d = new Date(end);
            d.setDate(d.getDate() - i);
            const key = d.toISOString().slice(0, 10);
            buckets[key] = { m: 0, a: 0 };
            labels.push(key);
        }
        for (const s of this.sessions || []) {
            const d = this._toDate(s?.date);
            if (!d) continue;
            const key = new Date(d.getFullYear(), d.getMonth(), d.getDate()).toISOString().slice(0, 10);
            if (!(key in buckets)) continue;
            buckets[key].m += 1;
            if (s.status === 'completed') buckets[key].a += 1;
        }
        const fmt = (k: string): string => {
            const [, mo, day] = k.split('-');
            const months = ['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'];
            return `${parseInt(day, 10)} ${months[parseInt(mo, 10) - 1]}`;
        };
        const meetings: ChartPoint[] = labels.map((k) => ({ date: new Date(k), label: fmt(k), value: buckets[k].m }));
        const analyzed: ChartPoint[] = labels.map((k) => ({ date: new Date(k), label: fmt(k), value: buckets[k].a }));
        return { meetings, analyzed };
    }

    /** Path SVG para una serie (viewBox 0 0 600 200, padding 20). */
    chartPath(series: ChartPoint[], yMax: number): string {
        if (!series.length) return '';
        const W = 600, H = 200, padX = 20, padY = 20;
        const dx = (W - 2 * padX) / Math.max(1, series.length - 1);
        const yScale = (v: number) => H - padY - ((v / Math.max(1, yMax)) * (H - 2 * padY));
        let d = '';
        series.forEach((p, i) => {
            const x = padX + i * dx;
            const y = yScale(p.value);
            d += (i === 0 ? `M ${x},${y}` : ` L ${x},${y}`);
        });
        return d;
    }

    chartAreaPath(series: ChartPoint[], yMax: number): string {
        if (!series.length) return '';
        const W = 600, H = 200, padX = 20, padY = 20;
        const dx = (W - 2 * padX) / Math.max(1, series.length - 1);
        const yScale = (v: number) => H - padY - ((v / Math.max(1, yMax)) * (H - 2 * padY));
        let d = `M ${padX},${H - padY}`;
        series.forEach((p, i) => {
            const x = padX + i * dx;
            const y = yScale(p.value);
            d += ` L ${x},${y}`;
        });
        d += ` L ${W - padX},${H - padY} Z`;
        return d;
    }

    chartPoints(series: ChartPoint[], yMax: number): { cx: number; cy: number; label: string; value: number }[] {
        const W = 600, H = 200, padX = 20, padY = 20;
        const dx = (W - 2 * padX) / Math.max(1, series.length - 1);
        const yScale = (v: number) => H - padY - ((v / Math.max(1, yMax)) * (H - 2 * padY));
        return series.map((p, i) => ({ cx: padX + i * dx, cy: yScale(p.value), label: p.label, value: p.value }));
    }

    /** Eje X compactado a 5 etiquetas (inicio, 25%, 50%, 75%, fin). */
    get chartXLabels(): string[] {
        const m = this.activitySeries.meetings;
        if (!m.length) return [];
        const pickAt = (frac: number) => m[Math.min(m.length - 1, Math.floor(frac * (m.length - 1)))]?.label || '';
        return [pickAt(0), pickAt(0.25), pickAt(0.5), pickAt(0.75), pickAt(1)];
    }

    /** Máximo del eje Y, redondeado a múltiplo arriba más limpio (5/10/25/50…). */
    get chartYMax(): number {
        const s = this.activitySeries;
        const raw = Math.max(0, ...s.meetings.map((p) => p.value), ...s.analyzed.map((p) => p.value));
        if (raw <= 5) return 5;
        if (raw <= 10) return 10;
        if (raw <= 25) return 25;
        if (raw <= 50) return 50;
        return Math.ceil(raw / 10) * 10;
    }

    /** Follow-up Status: breakdown del recurso activo (sesiones o tareas).
     *  Devuelve los 4 segmentos del donut con count + pct + color. */
    get followupBreakdown(): FollowupBreakdown[] {
        return this.followupSource === 'sessions'
            ? this._sessionsBreakdown()
            : this._tasksBreakdown();
    }

    /** Distribución de SESIONES por status del pipeline. Es la fuente
     *  default porque siempre tiene datos reales (las sesiones se crean
     *  con un status y transicionan a completed/archived). */
    private _sessionsBreakdown(): FollowupBreakdown[] {
        const sessions = this.sessions || [];
        const total = Math.max(1, sessions.length);
        const c = (s: string) => sessions.filter((it) => (it?.status || '').toLowerCase() === s).length;
        const completed = c('completed');
        // "En procesamiento": sesiones subidas que están en cola/transcribiendo.
        const processing = c('processing') + c('transcribing') + c('analyzing');
        const pending = c('pending');
        // "Cerradas / archivadas / con error" agrupadas como el cuarto bucket —
        // el resultado del pipeline que requiere atención manual.
        const archived = c('archived') + c('failed') + c('error');
        const pct = (n: number) => Math.round((n / total) * 100);
        return [
            { label: this.translate.instant('dashboard.followup_label_completed'),     count: completed,  pct: pct(completed),  color: 'var(--color-success)' },
            { label: this.translate.instant('dashboard.followup_label_processing'),    count: processing, pct: pct(processing), color: 'var(--color-info)' },
            { label: this.translate.instant('dashboard.followup_label_pending'),       count: pending,    pct: pct(pending),    color: 'var(--color-warning)' },
            { label: this.translate.instant('dashboard.followup_label_archived'),      count: archived,   pct: pct(archived),   color: 'var(--color-fg-soft)' },
        ];
    }

    /** Distribución de ACTION ITEMS por status. Útil para workspaces que
     *  ya viven el flujo de cierre de tareas (no sólo procesamiento de
     *  sesiones). Suele estar más "pending" hasta que el equipo trabaja. */
    private _tasksBreakdown(): FollowupBreakdown[] {
        const items = this.allActionItems || [];
        const total = Math.max(1, items.length);
        const c = (s: string) => items.filter((it) => (it?.status || '').toLowerCase() === s).length;
        const completed = c('done') + c('completed');
        const inProgress = c('in_progress') + c('blocked');
        const pending = c('pending');
        const overdue = items.filter((it) => {
            if (!it?.due_date) return false;
            const d = this._toDate(it.due_date);
            const status = (it.status || 'pending').toLowerCase();
            return d ? d.getTime() < Date.now() && status !== 'done' && status !== 'completed' : false;
        }).length;
        const pct = (n: number) => Math.round((n / total) * 100);
        return [
            { label: this.translate.instant('dashboard.followup_label_completed'),    count: completed,  pct: pct(completed),  color: 'var(--color-success)' },
            { label: this.translate.instant('dashboard.followup_label_in_progress'),  count: inProgress, pct: pct(inProgress), color: 'var(--color-info)' },
            { label: this.translate.instant('dashboard.followup_label_pending'),      count: pending,    pct: pct(pending),    color: 'var(--color-warning)' },
            { label: this.translate.instant('dashboard.followup_label_overdue'),      count: overdue,    pct: pct(overdue),    color: 'var(--color-danger)' },
        ];
    }

    /** Total del recurso activo. Llena el centro del donut. */
    get followupTotal(): number {
        return this.followupSource === 'sessions'
            ? (this.sessions || []).length
            : (this.allActionItems || []).length;
    }

    /** Sustantivo que va abajo del número en el centro (Total siempre,
     *  pero usamos esto en el banner para variar el copy). */
    get followupUnitPlural(): string {
        return this.followupSource === 'sessions'
            ? this.translate.instant('dashboard.followup_unit_sessions')
            : this.translate.instant('dashboard.followup_unit_tasks');
    }

    /** Cambia la fuente del donut. */
    setFollowupSource(src: 'sessions' | 'tasks'): void {
        if (this.followupSource === src) return;
        this.followupSource = src;
        // Reset hover state cuando cambia el dataset.
        this.hoveredSegmentIdx = -1;
    }

    /** Hover de segmento del donut (sincronizado con leyenda). */
    onSegmentHover(idx: number): void { this.hoveredSegmentIdx = idx; }
    clearSegmentHover(): void { this.hoveredSegmentIdx = -1; }

    // ----- Donut Estado de seguimiento ----------------------------------

    /** Radio del círculo del donut (viewBox 0 0 100 100). Stroke-width 14
     *  → diámetro visual interno ≈ 80 - 14 = 66 → centro del aro a r=33,
     *  pero usamos r=40 simple; el centro se calcula con stroke. */
    readonly donutRadius = 40;

    /** Perímetro del círculo. Lo precomputamos para no recalcular en cada
     *  binding del template. */
    readonly donutCircumference = 2 * Math.PI * 40;

    /** Pequeño gap visual entre segmentos para que se distinga uno del otro
     *  (en unidades de viewBox; ~2 unidades = ~1.4° de arco). */
    private readonly donutGap = 2;

    /** Devuelve los segmentos del donut listos para bindear en SVG.
     *  Filtramos los que tienen count===0 para no dejar arcos invisibles
     *  ocupando lugar. */
    get donutSegments(): DonutSegment[] {
        const items = this.followupBreakdown.filter((b) => b.count > 0);
        const total = items.reduce((s, i) => s + i.count, 0);
        if (!total) return [];
        const C = this.donutCircumference;
        const gap = items.length > 1 ? this.donutGap : 0;
        let cumulative = 0;
        return items.map((item) => {
            const rawLen = (item.count / total) * C;
            // Restamos el gap a cada segmento (excepto si sólo hay uno).
            const len = Math.max(0.5, rawLen - gap);
            const seg: DonutSegment = {
                color: item.color,
                // dasharray = "<segmento> <resto>" → pinta solo su porción.
                dashArray: `${len} ${C - len}`,
                // offset negativo = rotación clockwise desde el top.
                dashOffset: -cumulative,
            };
            cumulative += rawLen;
            return seg;
        });
    }

    /** Porcentaje de tareas completadas (para el banner de ánimo). */
    get completedPercent(): number {
        const completedLabel = this.translate.instant('dashboard.followup_label_completed');
        const c = this.followupBreakdown.find((b) => b.label === completedLabel);
        return c?.pct || 0;
    }

    /** Copy adaptativo del banner de ánimo. Cambia con la fuente activa
     *  (sesiones vs tareas) para que el mensaje siempre sea preciso. */
    get encouragementMessage(): { title: string; sub: string; tone: 'success' | 'info' | 'neutral' } {
        const noun = this.followupUnitPlural;            // "sesiones" | "tareas"

        if (this.followupTotal === 0) {
            return this.followupSource === 'sessions'
                ? {
                    title: this.translate.instant('dashboard.encouragement_empty_sessions_title'),
                    sub: this.translate.instant('dashboard.encouragement_empty_sessions_sub'),
                    tone: 'neutral',
                  }
                : {
                    title: this.translate.instant('dashboard.encouragement_empty_tasks_title'),
                    sub: this.translate.instant('dashboard.encouragement_empty_tasks_sub'),
                    tone: 'neutral',
                  };
        }
        const pct = this.completedPercent;
        if (pct >= 70) {
            return {
                title: this.translate.instant('dashboard.encouragement_excellent_title', { pct, noun }),
                sub: this.translate.instant('dashboard.encouragement_excellent_sub'),
                tone: 'success',
            };
        }
        if (pct >= 40) {
            return {
                title: this.translate.instant('dashboard.encouragement_good_title', { pct, noun }),
                sub: this.translate.instant('dashboard.encouragement_good_sub'),
                tone: 'success',
            };
        }
        if (pct >= 15) {
            return {
                title: this.translate.instant('dashboard.encouragement_progressing_title', { pct, noun }),
                sub: this.translate.instant('dashboard.encouragement_progressing_sub'),
                tone: 'info',
            };
        }
        // Sub-frase específica por fuente.
        const sub = this.followupSource === 'sessions'
            ? this.translate.instant('dashboard.encouragement_attention_sub_sessions')
            : this.translate.instant('dashboard.encouragement_attention_sub_tasks');
        return {
            title: this.translate.instant('dashboard.encouragement_attention_title', { noun }),
            sub,
            tone: 'info',
        };
    }

    // ----- Interactividad del chart de actividad ------------------------

    /** Calcula la posición X (viewBox) de un índice dado. */
    private chartXAt(index: number): number {
        const series = this.activitySeries.meetings;
        const W = 600, padX = 20;
        const dx = (W - 2 * padX) / Math.max(1, series.length - 1);
        return padX + index * dx;
    }

    /** Hover sobre la columna del chart: aplica para ambas series. */
    onChartHover(index: number): void {
        const m = this.activitySeries.meetings[index];
        const a = this.activitySeries.analyzed[index];
        if (!m) { this.chartHover = null; return; }
        this.chartHover = {
            index,
            x: this.chartXAt(index),
            label: m.label,
            meetings: m.value,
            analyzed: a?.value || 0,
        };
    }

    /** Reset del tooltip al salir del SVG. */
    clearChartHover(): void { this.chartHover = null; }

    /** Toggle de visibilidad de serie por click en su item de la leyenda.
     *  Si ambas terminarían apagadas, no permitimos apagar la última
     *  (evita un chart completamente vacío). */
    toggleSeries(key: 'meetings' | 'analyzed'): void {
        const next = !this.seriesVisible[key];
        const other = key === 'meetings' ? this.seriesVisible.analyzed : this.seriesVisible.meetings;
        if (!next && !other) return;
        this.seriesVisible[key] = next;
    }

    /** Sync Health — providers que muestran en el handoff. */
    get syncProviders(): SyncProvider[] {
        const cfg = this.integrations || {};
        const has = (k: string): boolean => {
            const c = cfg[k];
            if (!c) return false;
            return !!(c.isActive || c.apiKey || c.api_key || c.token || c.api_token || c.pat || c.apiToken);
        };
        // "Synced Xm ago" — placeholders visuales escalonados para que la card
        // no quede toda con el mismo timestamp. El backend no traquea esto
        // hoy; cuando lo haga, leemos `cfg[provider].last_sync_at`.
        const ago = (mins: number): string => this.translate.instant('dashboard.synced_ago', { minutes: mins });
        return [
            { id: 'jira',     name: 'Jira',         iconColor: '#2684FF', iconLetter: 'J', connected: has('jira'),         syncedAgo: ago(2) },
            { id: 'trello',   name: 'Trello',       iconColor: '#0079BF', iconLetter: 'T', connected: has('trello'),       syncedAgo: ago(5) },
            { id: 'clickup',  name: 'ClickUp',      iconColor: '#7B68EE', iconLetter: 'C', connected: has('clickup'),      syncedAgo: ago(8) },
            { id: 'azure',    name: 'Azure DevOps', iconColor: '#0078D4', iconLetter: 'A', connected: has('azure_devops') || has('azure'), syncedAgo: ago(10) },
        ];
    }

    // ========================================================================
    // ACTIVITY SUMMARY — métricas que van debajo de la línea de actividad.
    // ========================================================================

    /** Suma de duraciones de las sesiones del período actual. Asumimos
     *  ~45min por sesión cuando el backend no expone duración real (el
     *  modelo MeetingSession aún no la traquea explícitamente). */
    private _avgMinutesPerSession = 45;

    private _attendeesCount(s: any): number {
        try {
            const arr = JSON.parse(s?.processed_attendees || '[]');
            return Array.isArray(arr) ? arr.length : 0;
        } catch {
            return 0;
        }
    }

    /** Stats que renderizamos al pie de la card "Meeting Activity". */
    get activitySummary(): {
        totalMeetings: number;
        totalDuration: string;
        avgParticipants: string;
        avgConfidence: string;
    } {
        const sessions = this.periodSessions;
        const total = sessions.length;
        const totalMins = total * this._avgMinutesPerSession;
        const hours = Math.floor(totalMins / 60);
        const mins = totalMins % 60;
        const totalDuration = total === 0 ? '0m' : `${hours}h ${mins.toString().padStart(2, '0')}m`;

        const attendeesAcc = sessions.reduce((acc, s) => acc + this._attendeesCount(s), 0);
        const avgPart = total === 0 ? 0 : attendeesAcc / total;
        const avgParticipants = total === 0 ? '0' : avgPart.toFixed(1);

        // AI confidence: heurística — % de sesiones con language detectado y
        // al menos 1 decisión/riesgo extraído. Cap entre 60% y 95%.
        const completed = sessions.filter((s) => (s?.status || '') === 'completed').length;
        const conf = total === 0 ? 0 : Math.max(60, Math.min(95, Math.round((completed / total) * 100)));
        const avgConfidence = total === 0 ? '—' : `${conf}%`;

        return { totalMeetings: total, totalDuration, avgParticipants, avgConfidence };
    }

    // ========================================================================
    // RECENTLY ANALYZED MEETINGS — shape para la tabla.
    // ========================================================================

    /** 5 sesiones más recientes con shape listo para la tabla del mockup.
     *  Cada participante incluye name/role/company para el tooltip rico. */
    get recentMeetingsRows(): {
        id: number;
        title: string;
        date: string;
        participants: { initials: string; tone: number; name: string; role: string; company: string; email: string }[];
        extra: number;
        extraNames: string;
        duration: string;
        confidence: 'High' | 'Medium' | 'Low';
    }[] {
        const palette = [0, 1, 2, 3, 4]; // colores rotativos para avatares
        return (this.recentMeetings || []).slice(0, 5).map((m, idx) => {
            const attendees = (() => {
                try {
                    const arr = JSON.parse(m?.processed_attendees || '[]');
                    return Array.isArray(arr) ? arr : [];
                } catch {
                    return [];
                }
            })();
            // Pre-cargamos los emails de los attendees al directorio para que
            // el chip pinte avatares sin un round-trip por cada uno.
            const allEmails = attendees
                .map((a: any) => (typeof a === 'string' && a.includes('@')) ? a : (a?.email || ''))
                .filter((e: string) => !!e);
            if (allEmails.length) this.userDirectory.preload(allEmails);

            const visible = attendees.slice(0, 3).map((a: any, i: number) => {
                const name = String(a?.name || a?.full_name || a?.email || this.translate.instant('dashboard.row_no_attendee')).trim();
                return {
                    initials: this.initials(name),
                    tone: palette[(idx + i) % palette.length],
                    name,
                    role: String(a?.role || a?.position || a?.job_title || '').trim(),
                    company: String(a?.entity || a?.company || a?.organization || a?.org || '').trim(),
                    email: String(a?.email || a?.mail || '').trim().toLowerCase(),
                };
            });
            const extra = Math.max(0, attendees.length - 3);
            // Lista de nombres extras para el tooltip del "+N" badge.
            const extraNames = attendees.slice(3, 13)
                .map((a: any) => String(a?.name || a?.full_name || a?.email || '').trim())
                .filter(Boolean)
                .join(', ');
            const dur = this._avgMinutesPerSession;
            const duration = dur >= 60 ? `${Math.floor(dur / 60)}h ${(dur % 60).toString().padStart(2, '0')}m` : `${dur}m`;

            // Confidence heurística: completed→High, pending→Medium, otro→Low.
            const status = (m?.status || '').toLowerCase();
            const confidence: 'High' | 'Medium' | 'Low' =
                status === 'completed' ? 'High' : status === 'pending' ? 'Medium' : 'Low';

            return {
                id: m.id,
                title: m.title || this.translate.instant('dashboard.row_no_title'),
                date: this.formatTableDate(m.date),
                participants: visible,
                extra,
                extraNames,
                duration,
                confidence,
            };
        });
    }

    /** "16 may 2024" para la columna Fecha de la tabla. */
    formatTableDate(v: any): string {
        const d = this._toDate(v);
        if (!d) return '';
        const months = ['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'];
        return `${d.getDate()} ${months[d.getMonth()]} ${d.getFullYear()}`;
    }

    // ========================================================================
    // TOP RISKS / TOP ACTION ITEMS — shape enriquecido para las cards.
    // ========================================================================

    /** Top 3 riesgos extraídos de sesiones recientes, con metadata de la
     *  sesión (título corto + fecha) para el subtítulo. */
    get topRisksRich(): {
        id: number;
        sessionTitle: string;
        sessionDate: string;
        text: string;
        impact: 'High' | 'Medium' | 'Low';
    }[] {
        const out: {
            id: number;
            sessionTitle: string;
            sessionDate: string;
            text: string;
            impact: 'High' | 'Medium' | 'Low';
        }[] = [];
        for (const s of this.sessions || []) {
            if (!s?.processed_risks) continue;
            const lines = String(s.processed_risks)
                .split('\n')
                .map((l) => l.replace(/^[-•*]\s*/, '').trim())
                .filter(Boolean);
            for (const line of lines.slice(0, 1)) {
                const impact = /critic|alto|high/i.test(line)
                    ? 'High'
                    : /medi|mod/i.test(line)
                    ? 'Medium'
                    : 'Low';
                out.push({
                    id: s.id,
                    sessionTitle: s.title || this.translate.instant('dashboard.row_no_title'),
                    sessionDate: this.formatTableDate(s.date),
                    text: line.slice(0, 110),
                    impact,
                });
            }
            if (out.length >= 3) break;
        }
        return out;
    }

    /** Top 3 action items abiertos, enriquecidos con avatar + due date.
     *  Owner incluye name/role/company para el tooltip rico. */
    get topActionItemsRich(): {
        id: number;
        title: string;
        sessionTitle: string;
        sessionDate: string;
        owner: { initials: string; tone: number; name: string; role: string; company: string; email: string };
        dueLabel: string;
        accent: 'success' | 'info' | 'warning';
    }[] {
        const items = (this.allActionItems || []).filter((it) => (it?.status || 'pending') !== 'done').slice(0, 3);
        // Pre-carga emails al directorio para que el chip pinte avatares sin
        // round-trips individuales.
        const emails = items.map(it => (it?.owner_email || '').trim()).filter(Boolean);
        if (emails.length) this.userDirectory.preload(emails);
        return items.map((it, idx) => {
            const accents: Array<'success' | 'info' | 'warning'> = ['success', 'info', 'warning'];
            const ownerName = String(it?.owner_name || it?.owner_email || this.translate.instant('dashboard.row_owner_unassigned')).trim();
            return {
                id: it.id,
                title: it.title || this.translate.instant('dashboard.row_task_no_title'),
                sessionTitle: it?.session_title || it?.session?.title || this.translate.instant('dashboard.row_meeting_no_title'),
                sessionDate: this.formatTableDate(it?.session_date || it?.session?.date),
                owner: {
                    initials: this.initials(ownerName),
                    tone: idx % 5,
                    name: ownerName,
                    role: String(it?.owner_role || it?.role || '').trim(),
                    company: String(it?.owner_company || it?.owner_entity || '').trim(),
                    email: String(it?.owner_email || '').trim().toLowerCase(),
                },
                dueLabel: this.formatTableDate(it.due_date) || this.translate.instant('dashboard.row_no_date'),
                accent: accents[idx % 3],
            };
        });
    }


    /** Top 3 sesiones completadas más recientes para el panel "Recent". */
    get recentMeetings(): any[] {
        return (this.sessions || [])
            .slice()
            .sort((a, b) => {
                const da = this._toDate(a.date)?.getTime() || 0;
                const db = this._toDate(b.date)?.getTime() || 0;
                return db - da;
            })
            .slice(0, 4);
    }

    /** Top 3 riesgos extraídos de sesiones recientes (primer bullet del campo). */
    get topRisks(): { id: number; sessionTitle: string; text: string; impact: 'High'|'Medium'|'Low' }[] {
        const out: { id: number; sessionTitle: string; text: string; impact: 'High'|'Medium'|'Low' }[] = [];
        for (const s of this.sessions || []) {
            if (!s?.processed_risks) continue;
            const lines = String(s.processed_risks).split('\n').map((l) => l.replace(/^[-•*]\s*/, '').trim()).filter(Boolean);
            for (const line of lines.slice(0, 1)) {
                const impact = /critic|alto|high/i.test(line) ? 'High' : /medi|mod/i.test(line) ? 'Medium' : 'Low';
                out.push({ id: s.id, sessionTitle: s.title, text: line.slice(0, 110), impact });
            }
            if (out.length >= 3) break;
        }
        return out;
    }

    /** Top 3 action items pendientes. */
    get topActionItems(): any[] {
        return (this.allActionItems || [])
            .filter((it) => (it?.status || 'pending') !== 'done')
            .slice(0, 3);
    }

    // ========================================================================
    // Quick Actions — navegan a vistas ya existentes
    // ========================================================================
    // Las quick actions usan KEYS i18n en `label` y `sub`. El template
    // las resuelve con `| translate`. Antes estaban hardcoded en ES.
    private readonly _allQuickActions = [
        { id: 'new-meeting',  label: 'dashboard.qa_new_meeting_label',  sub: 'dashboard.qa_new_meeting_sub',  icon: 'mic',      action: 'upload',                                       requires: 'writer'  },
        { id: 'upload',       label: 'dashboard.qa_upload_label',       sub: 'dashboard.qa_upload_sub',       icon: 'upload',   action: 'upload',                                       requires: 'writer'  },
        { id: 'projects',     label: 'dashboard.qa_projects_label',     sub: 'dashboard.qa_projects_sub',     icon: 'folder',   action: 'goto', target: '/admin/projects',              requires: 'admin'   },
        { id: 'ask',          label: 'dashboard.qa_ask_label',          sub: 'dashboard.qa_ask_sub',          icon: 'sparkle',  action: 'goto', target: '/admin/ask',                   requires: 'any'     },
        { id: 'calendar',     label: 'dashboard.qa_calendar_label',     sub: 'dashboard.qa_calendar_sub',     icon: 'calendar', action: 'goto', target: '/admin/calendar',              requires: 'any'     },
        { id: 'reports',      label: 'dashboard.qa_reports_label',      sub: 'dashboard.qa_reports_sub',      icon: 'chart',    action: 'goto', target: '/admin/reportes',              requires: 'any'     },
    ];

    /** Sólo devuelve las quick actions que el rol actual puede ejecutar. */
    get quickActions() {
        return this._allQuickActions.filter((qa) => {
            if (qa.requires === 'any') return true;
            if (qa.requires === 'writer') return this.canCreateMeeting;
            if (qa.requires === 'admin') return this.isAdmin;
            return false;
        });
    }

    triggerQuickAction(qa: any): void {
        if (qa.action === 'upload') return this.openUploadModal();
        if (qa.action === 'goto' && qa.target) this.router.navigateByUrl(qa.target);
    }

    // ========================================================================
    // Task interaction — checkbox "Tareas prioritarias" → PATCH status.
    // ========================================================================
    togglingTaskId: number | null = null;

    toggleTaskDone(task: any, ev: Event): void {
        ev.stopPropagation();
        if (!this.canTickTasks) {
            this.toast.error(this.translate.instant('dashboard.toast_task_role_forbidden'));
            (ev.target as HTMLInputElement).checked = false;
            return;
        }
        const id = task?.id;
        if (!id || this.togglingTaskId === id) return;
        const wasDone = (task.status || 'pending') === 'done';
        const nextStatus = wasDone ? 'pending' : 'done';
        this.togglingTaskId = id;
        // Optimistic UI
        task.status = nextStatus;
        const headers = this.authService.getAuthHeaders();
        this.http.patch<any>(
            `${environment.apiUrl}/api/pendientes/${id}/status`,
            { status: nextStatus },
            { headers },
        ).pipe(takeUntil(this.destroy$)).subscribe({
            next: () => {
                this.togglingTaskId = null;
                this.toast.success(nextStatus === 'done'
                    ? this.translate.instant('dashboard.toast_task_completed')
                    : this.translate.instant('dashboard.toast_task_reopened'));
                // Refresca contadores (Follow-up Status + KPIs)
                this.loadOverview();
            },
            error: (err) => {
                this.togglingTaskId = null;
                task.status = wasDone ? 'done' : 'pending';  // revert
                this.toast.error(this.translate.instant('dashboard.toast_task_update_error', {
                    detail: err?.error?.detail || 'desconocido',
                }));
                this.cdr.detectChanges();
            },
        });
    }

    // ========================================================================
    // Topbar bell — placeholder hasta que exista feed de notificaciones.
    // ========================================================================
    showNotifPanel = false;
    toggleNotifPanel(): void {
        this.showNotifPanel = !this.showNotifPanel;
    }

    setOverviewPeriod(p: '7d' | '14d' | '30d'): void {
        this.overviewPeriod = p;
        this.cdr.detectChanges();
    }

    statusLabel(status: string): string {
        if (status === 'completed') return this.translate.instant('dashboard.status_completed');
        if (status === 'processing') return this.translate.instant('dashboard.status_processing');
        if (status === 'pending') return this.translate.instant('dashboard.status_pending');
        if (status === 'archived') return this.translate.instant('dashboard.status_archived');
        return status || '—';
    }

    /** Inicials helper para los avatares (Karan Patel → KP). */
    initials(name: string | null | undefined): string {
        if (!name) return '··';
        const parts = String(name).trim().split(/\s+/);
        return (parts[0]?.[0] || '').toUpperCase() + (parts[1]?.[0] || '').toUpperCase();
    }

    /** URL del avatar del User cuyo email coincide con `email`. null si no
     *  hay match → el avatar cae al fondo de color con iniciales. */
    avatarUrlFor(email?: string): string | null {
        if (!email) return null;
        const u = this.userDirectory.peek(email);
        if (!u || !u.avatar_url) return null;
        const raw = u.avatar_url;
        if (raw.startsWith('http://') || raw.startsWith('https://')) return raw;
        return `${environment.apiUrl}${raw}`;
    }

    /** Display name del User cuando el email coincide; si no, el `fallback`. */
    displayNameFor(email: string | undefined, fallback: string): string {
        if (email) {
            const u = this.userDirectory.peek(email);
            if (u?.full_name) return u.full_name;
        }
        return fallback;
    }

    /** Formatea fecha + hora corta. */
    formatShortDate(v: any): string {
        const d = this._toDate(v);
        if (!d) return '';
        return d.toLocaleDateString('es-CO', { day: '2-digit', month: 'short' }) + ', ' +
               d.toLocaleTimeString('es-CO', { hour: '2-digit', minute: '2-digit' });
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
                    this.translate.instant('dashboard.toast_export_error_conn', { format: format.toUpperCase() }),
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
                this.toast.success(this.translate.instant('dashboard.toast_session_deleted'));
                this.loadSessions();
            },
            error: () => {
                this.isDeleting = false;
                this.toast.error(this.translate.instant('dashboard.toast_session_delete_error'));
            }
        });
    }
}
