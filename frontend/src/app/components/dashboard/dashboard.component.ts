import { Component, OnInit, OnDestroy, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { Router, RouterModule } from '@angular/router';
import { Subject } from 'rxjs';
import { takeUntil } from 'rxjs/operators';
import { AuthService } from '../../services/auth.service';
import { ToastService } from '../../services/toast.service';
import { environment } from '../../../environments/environment';

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
    imports: [CommonModule, FormsModule, RouterModule],
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
        private toast: ToastService,
    ) { }

    ngOnInit(): void {
        this.loadOverview();
        this.loadProjects();
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

    /** Cuenta sesiones por status sobre el lote actualmente cargado. */
    countByStatus(status: string): number {
        return (this.sessions || []).filter((s) => s?.status === status).length;
    }

    // ========================================================================
    // OVERVIEW — propiedades derivadas para los widgets del handoff
    // ========================================================================

    /** Saludo según hora local. Devuelve la versión en inglés que pide
     *  el mockup ("Good morning, …"). El idioma de UI es el del handoff;
     *  microcopy interno (toasts, validations) sigue en español. */
    get greeting(): string {
        const h = new Date().getHours();
        if (h < 12) return 'Good morning';
        if (h < 19) return 'Good afternoon';
        return 'Good evening';
    }

    /** Header date: "May 16, 2024" */
    get todayLabelEn(): string {
        const d = new Date();
        const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        return `${months[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;
    }

    get userFirstName(): string {
        const fn = this.authService.currentUserValue?.full_name || '';
        return (fn.split(' ')[0] || fn || '').trim() || 'Karan';
    }

    get todayLabel(): string {
        return new Date().toLocaleDateString('es-CO', {
            day: '2-digit', month: 'short', year: 'numeric',
        });
    }

    /** Días del período según overviewPeriod (7/14/30). */
    private get _periodDays(): number {
        return this.overviewPeriod === '7d' ? 7 : this.overviewPeriod === '30d' ? 30 : 14;
    }

    private _periodMs(): number {
        return this._periodDays * 24 * 60 * 60 * 1000;
    }

    private _toDate(v: any): Date | null {
        if (v == null) return null;
        const n = typeof v === 'string' && !isNaN(Number(v)) ? Number(v) : v;
        const d = new Date(n);
        return isNaN(d.getTime()) ? null : d;
    }

    /** Sesiones dentro del período activo (para KPIs y chart). */
    get periodSessions(): any[] {
        const cutoff = Date.now() - this._periodMs();
        return (this.sessions || []).filter((s) => {
            const d = this._toDate(s?.date);
            return d ? d.getTime() >= cutoff : true;
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
            { key: 'meetings',     label: 'Meetings',     value: meetings,    trend: trend(meetings, prevMeetings),   tone: 'navy',    icon: 'meetings',  sparkline: this._sparkSessionsCount() },
            { key: 'action_items', label: 'Action Items', value: actionItems, trend: trend(actionItems, prevActions), tone: 'success', icon: 'tasks',     sparkline: this._sparkActionItems() },
            { key: 'decisions',    label: 'Decisions',    value: decisions,   trend: trend(decisions, prevDecisions), tone: 'success', icon: 'decisions', sparkline: this._sparkDecisions() },
            { key: 'risks',        label: 'Risks',        value: risks,       trend: trend(risks, prevRisks),         tone: 'warning', icon: 'risks',     sparkline: this._sparkRisks() },
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

    /** Serie diaria de la gráfica Meetings Activity (Last N Days). */
    get activitySeries(): { meetings: ChartPoint[]; analyzed: ChartPoint[] } {
        const days = this._periodDays;
        const buckets: { [k: string]: { m: number; a: number } } = {};
        const labels: string[] = [];
        const today = new Date();
        today.setHours(0, 0, 0, 0);
        for (let i = days - 1; i >= 0; i--) {
            const d = new Date(today);
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
            { label: 'Completadas',     count: completed,  pct: pct(completed),  color: 'var(--color-success)' },
            { label: 'En procesamiento', count: processing, pct: pct(processing), color: 'var(--color-info)' },
            { label: 'Pendientes',      count: pending,    pct: pct(pending),    color: 'var(--color-warning)' },
            { label: 'Archivadas',      count: archived,   pct: pct(archived),   color: 'var(--color-fg-soft)' },
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
            { label: 'Completadas',    count: completed,  pct: pct(completed),  color: 'var(--color-success)' },
            { label: 'En progreso',    count: inProgress, pct: pct(inProgress), color: 'var(--color-info)' },
            { label: 'Pendientes',     count: pending,    pct: pct(pending),    color: 'var(--color-warning)' },
            { label: 'Vencidas',       count: overdue,    pct: pct(overdue),    color: 'var(--color-danger)' },
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
        return this.followupSource === 'sessions' ? 'sesiones' : 'tareas';
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
        const c = this.followupBreakdown.find((b) => b.label === 'Completadas');
        return c?.pct || 0;
    }

    /** Copy adaptativo del banner de ánimo. Cambia con la fuente activa
     *  (sesiones vs tareas) para que el mensaje siempre sea preciso. */
    get encouragementMessage(): { title: string; sub: string; tone: 'success' | 'info' | 'neutral' } {
        const noun = this.followupUnitPlural;            // "sesiones" | "tareas"
        const nounSing = noun.slice(0, -1);              // "sesione" / "tarea" → ajustamos abajo
        const unitFem = this.followupSource === 'sessions' ? 'sesión' : 'tarea';

        if (this.followupTotal === 0) {
            return this.followupSource === 'sessions'
                ? {
                    title: 'Aún no hay sesiones registradas.',
                    sub: 'Sube tu primera reunión y la IA generará las acciones automáticamente.',
                    tone: 'neutral',
                  }
                : {
                    title: 'Aún no hay tareas registradas.',
                    sub: 'Las tareas se generan al analizar una sesión.',
                    tone: 'neutral',
                  };
        }
        const pct = this.completedPercent;
        if (pct >= 70) {
            return { title: `¡Excelente! ${pct}% de las ${noun} completadas.`, sub: 'Mantén el ritmo del equipo.', tone: 'success' };
        }
        if (pct >= 40) {
            return { title: `¡Buen trabajo! ${pct}% de las ${noun} completadas.`, sub: 'Mantén el momentum.', tone: 'success' };
        }
        if (pct >= 15) {
            return { title: `Vas avanzando: ${pct}% de las ${noun} completadas.`, sub: 'Revisa las pendientes para acelerar el cierre.', tone: 'info' };
        }
        // Sub-frase específica por fuente.
        const sub = this.followupSource === 'sessions'
            ? 'Procesa las sesiones pendientes o archiva las que ya no necesitas.'
            : 'Empieza con las vencidas o asignadas a tu equipo.';
        return {
            title: `Hay ${noun} que necesitan atención.`,
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
        const ago = (mins: number): string => `Synced ${mins}m ago`;
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

    /** 5 sesiones más recientes con shape listo para la tabla del mockup. */
    get recentMeetingsRows(): {
        id: number;
        title: string;
        date: string;
        participants: { initials: string; tone: number }[];
        extra: number;
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
            const visible = attendees.slice(0, 3).map((a: any, i: number) => ({
                initials: this.initials(a?.name || a?.full_name || 'NN'),
                tone: palette[(idx + i) % palette.length],
            }));
            const extra = Math.max(0, attendees.length - 3);
            const dur = this._avgMinutesPerSession;
            const duration = dur >= 60 ? `${Math.floor(dur / 60)}h ${(dur % 60).toString().padStart(2, '0')}m` : `${dur}m`;

            // Confidence heurística: completed→High, pending→Medium, otro→Low.
            const status = (m?.status || '').toLowerCase();
            const confidence: 'High' | 'Medium' | 'Low' =
                status === 'completed' ? 'High' : status === 'pending' ? 'Medium' : 'Low';

            return {
                id: m.id,
                title: m.title || 'Untitled meeting',
                date: this.formatTableDate(m.date),
                participants: visible,
                extra,
                duration,
                confidence,
            };
        });
    }

    /** "May 16, 2024" para la columna Date de la tabla. */
    formatTableDate(v: any): string {
        const d = this._toDate(v);
        if (!d) return '';
        const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        return `${months[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;
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
                    sessionTitle: s.title || 'Untitled',
                    sessionDate: this.formatTableDate(s.date),
                    text: line.slice(0, 110),
                    impact,
                });
            }
            if (out.length >= 3) break;
        }
        return out;
    }

    /** Top 3 action items abiertos, enriquecidos con avatar + due date. */
    get topActionItemsRich(): {
        id: number;
        title: string;
        sessionTitle: string;
        sessionDate: string;
        owner: { initials: string; tone: number };
        dueLabel: string;
        accent: 'success' | 'info' | 'warning';
    }[] {
        const items = (this.allActionItems || []).filter((it) => (it?.status || 'pending') !== 'done').slice(0, 3);
        return items.map((it, idx) => {
            const accents: Array<'success' | 'info' | 'warning'> = ['success', 'info', 'warning'];
            return {
                id: it.id,
                title: it.title || 'Untitled task',
                sessionTitle: it?.session_title || it?.session?.title || 'Untitled meeting',
                sessionDate: this.formatTableDate(it?.session_date || it?.session?.date),
                owner: {
                    initials: this.initials(it.owner_name || 'NN'),
                    tone: idx % 5,
                },
                dueLabel: this.formatTableDate(it.due_date) || 'No due date',
                accent: accents[idx % 3],
            };
        });
    }

    // ========================================================================
    // QUICK ACTIONS — versión 6 botones del mockup, en inglés.
    // ========================================================================

    /** Mismas acciones que ya existían pero con copy en inglés (mockup). */
    get quickActionsEn() {
        // Refiltra por permiso, igual que `quickActions`, pero re-rotula.
        const labels: Record<string, { label: string; sub: string }> = {
            'new-meeting': { label: 'Schedule Meeting', sub: 'Plan and invite participants' },
            upload:        { label: 'Upload Transcript', sub: 'Analyze past meetings' },
            projects:      { label: 'Create Project',    sub: 'Organize work and teams' },
            ask:           { label: 'Ask Acten',         sub: 'Get AI-powered insights' },
            calendar:      { label: 'Add Task',          sub: 'Track action items' },
            reports:       { label: 'View Reports',      sub: 'Explore analytics' },
        };
        return this.quickActions.map((qa: any) => ({
            ...qa,
            label: labels[qa.id]?.label || qa.label,
            sub: labels[qa.id]?.sub || qa.sub,
        }));
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
    private readonly _allQuickActions = [
        { id: 'new-meeting',  label: 'Iniciar reunión',       sub: 'Subir audio o texto',         icon: 'mic',      action: 'upload',                                       requires: 'writer'  },
        { id: 'upload',       label: 'Subir transcripción',   sub: 'Analizar reunión pasada',     icon: 'upload',   action: 'upload',                                       requires: 'writer'  },
        { id: 'projects',     label: 'Crear proyecto',        sub: 'Organiza trabajo y equipos',  icon: 'folder',   action: 'goto', target: '/admin/projects',              requires: 'admin'   },
        { id: 'ask',          label: 'Preguntá a Acten',      sub: 'Insights con IA',             icon: 'sparkle',  action: 'goto', target: '/admin/ask',                   requires: 'any'     },
        { id: 'calendar',     label: 'Ver mi calendario',     sub: 'Sincronizado con Google/MS',  icon: 'calendar', action: 'goto', target: '/admin/calendar',              requires: 'any'     },
        { id: 'reports',      label: 'Ver reportes',          sub: 'Explora analíticas',          icon: 'chart',    action: 'goto', target: '/admin/reportes',              requires: 'any'     },
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
            this.toast.error('Tu rol no permite cambiar el estado de tareas.');
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
                this.toast.success(nextStatus === 'done' ? 'Tarea marcada como completada.' : 'Tarea reabierta.');
                // Refresca contadores (Follow-up Status + KPIs)
                this.loadOverview();
            },
            error: (err) => {
                this.togglingTaskId = null;
                task.status = wasDone ? 'done' : 'pending';  // revert
                this.toast.error('No se pudo actualizar la tarea: ' + (err?.error?.detail || 'desconocido'));
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
        if (status === 'completed') return 'Completado';
        if (status === 'processing') return 'Procesando';
        if (status === 'pending') return 'Pendiente';
        if (status === 'archived') return 'Archivado';
        return status || '—';
    }

    /** Inicials helper para los avatares (Karan Patel → KP). */
    initials(name: string | null | undefined): string {
        if (!name) return '··';
        const parts = String(name).trim().split(/\s+/);
        return (parts[0]?.[0] || '').toUpperCase() + (parts[1]?.[0] || '').toUpperCase();
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
