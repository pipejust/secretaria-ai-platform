import { Component, OnInit, OnDestroy, ChangeDetectorRef, HostListener } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { Subject } from 'rxjs';
import { takeUntil } from 'rxjs/operators';
import { AuthService } from '../../services/auth.service';
import { ToastService } from '../../services/toast.service';
import { environment } from '../../../environments/environment';

interface BreakdownEntry { key: string; value: number; }
interface TimelinePoint { iso: string; label: string; value: number; }
interface TopProjectRow {
    id: number;
    name: string;
    owner: string;
    progress: number;
    tasks_progress?: number;
    status: 'completed' | 'in_progress' | 'pending' | 'overdue' | 'not_started';
    items_breakdown?: { pending: number; done: number; blocked: number; cancelled: number; overdue: number; };
    sessions_breakdown?: { completed: number; pending: number; total: number };
    items_total?: number;
    had_activity_in_window?: boolean;
}
interface TeamMember { owner: string; value: number; }
interface TeamCombined { owner: string; meetings: number; actions: number; }
interface WorkloadEntry {
    owner: string;
    email: string;
    total: number;
    by_status: { pending: number; done: number; blocked: number; cancelled: number; overdue: number };
    meetings: number;
}
interface AvailableProject { id: number; name: string; }

interface ReportData {
    period: 'week' | 'month' | 'custom' | 'all';
    label: string;
    start: string;
    end: string;
    project_id: number | null;
    project_name: string;
    metrics: {
        sessions_count: number;
        sessions_curated: number;
        sessions_pending: number;
        action_items_total: number;
        action_items_by_status: Record<string, number>;
        tasks_completed: number;
        completed_in_window: number;
        auto_dispatched: number;
        decisions_count: number;
        overdue_tasks: number;
        projects_count: number;
        completion_rate: number;
        curation_rate: number;
    };
    sessions: Array<{ id: number; title: string; date: string; status: string; project_name: string }>;
    top_owners: Array<{ owner: string; count: number }>;
    projects_breakdown: BreakdownEntry[];
    decisions_timeline: TimelinePoint[];
    top_projects: TopProjectRow[];
    team_activity: { meetings: TeamMember[]; actions: TeamMember[]; combined: TeamCombined[] };
    workload: WorkloadEntry[];
    available_projects: AvailableProject[];
}

interface DonutSlice {
    key: string;
    label: string;
    value: number;
    pct: number;
    color: string;
    dashLength: number;
    dashOffset: number;
}

interface LinePoint { x: number; y: number; label: string; value: number; iso: string; }

const STATUS_PALETTE: Record<string, { color: string; label: string }> = {
    completed:   { color: '#10B981', label: 'Completados'  },
    in_progress: { color: '#155EEF', label: 'En progreso'  },
    pending:     { color: '#F97316', label: 'Pendientes'   },
    overdue:     { color: '#EF4444', label: 'Vencidos'     },
    not_started: { color: '#94A3B8', label: 'No iniciados' },
};

@Component({
    selector: 'app-reportes',
    standalone: true,
    imports: [CommonModule, FormsModule, RouterModule],
    templateUrl: './reportes.component.html',
    styleUrls: ['./reportes.component.css'],
})
export class ReportesComponent implements OnInit, OnDestroy {
    // ============================================================
    // Filtros globales del reporte.
    // ============================================================
    // Default = 'all' (Siempre) — el usuario quiere ver TODO el histórico al
    // entrar a Reportes, y filtrar a período específico solo cuando lo elige.
    period: 'week' | 'month' | 'custom' | 'all' = 'all';
    projectId: number | null = null;
    refDate = '';
    customStart = '';
    customEnd = '';

    // Filtros INDEPENDIENTES por card (no recargan toda la página, solo
    // filtran client-side sobre la data ya cargada).
    donutProjectId:    number | null = null;
    decisionsProjectId: number | null = null;
    teamProjectId:     number | null = null;
    topProjectId:      number | null = null;

    data: ReportData | null = null;
    isLoading = false;
    isExporting = false;

    teamActivityMode: 'meetings' | 'actions' = 'meetings';
    showPeriodMenu = false;
    showExportModal = false;

    exportFormat: 'pdf' | 'excel' = 'pdf';
    exportProjectId: number | null = null;
    exportStartDate = '';
    exportEndDate = '';
    exportPeriod: 'week' | 'month' | 'custom' | 'all' = 'all';

    // Tooltip interactivo para charts.
    hoveredDonutKey: string | null = null;
    hoveredLineIdx:  number | null = null;
    hoveredBarIdx:   number | null = null;
    hoveredTeamIdx:  number | null = null;
    hoveredWorkloadIdx: number | null = null;

    // Filtros del nuevo card "Carga de trabajo".
    workloadProjectId: number | null = null;
    workloadStatus:    'all' | 'pending' | 'done' | 'blocked' | 'overdue' = 'all';
    workloadSortBy:    'total' | 'overdue' | 'meetings' = 'total';

    // Modo de la card "Actividad del equipo" (ya no es segmented binary).
    teamActivitySort: 'meetings' | 'actions' | 'combined' = 'combined';

    topProjectsPage = 1;
    readonly topProjectsLimit = 5;

    readonly periodLabels: Record<'week' | 'month' | 'custom' | 'all', string> = {
        week:   'Esta semana',
        month:  'Este mes',
        all:    'Siempre',
        custom: 'Rango personalizado',
    };

    private readonly destroy$ = new Subject<void>();

    constructor(
        private http: HttpClient,
        private authService: AuthService,
        private toast: ToastService,
        private cdr: ChangeDetectorRef,
    ) {}

    ngOnInit(): void { this.load(); }
    ngOnDestroy(): void { this.destroy$.next(); this.destroy$.complete(); }

    // ============================================================
    // Carga del reporte real.
    // ============================================================
    load(): void {
        this.isLoading = true;
        const params = this.buildParams(
            this.period, this.refDate, this.projectId, this.customStart, this.customEnd,
        );
        const headers = this.authService.getAuthHeaders();
        this.http
            .get<ReportData>(`${environment.apiUrl}/api/reports/data?${params}`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (res) => {
                    this.data = res;
                    this.topProjectsPage = 1;
                    this.isLoading = false;
                    this.cdr.detectChanges();
                },
                error: (err) => {
                    this.toast.error(err?.error?.detail || 'No se pudo generar el reporte.');
                    this.isLoading = false;
                    this.cdr.detectChanges();
                },
            });
    }

    private buildParams(
        period: 'week' | 'month' | 'custom' | 'all',
        ref: string,
        projectId: number | null,
        startDate: string,
        endDate: string,
    ): string {
        const p: string[] = [`period=${period}`];
        if (ref) p.push(`ref=${encodeURIComponent(ref)}`);
        if (projectId) p.push(`project_id=${projectId}`);
        if (period === 'custom') {
            if (startDate) p.push(`start_date=${encodeURIComponent(startDate)}`);
            if (endDate)   p.push(`end_date=${encodeURIComponent(endDate)}`);
        }
        return p.join('&');
    }

    // ============================================================
    // Toolbar / period menu / custom range
    // ============================================================
    togglePeriodMenu(): void { this.showPeriodMenu = !this.showPeriodMenu; }
    closePeriodMenu(): void { this.showPeriodMenu = false; }
    pickPeriod(p: 'week' | 'month' | 'all'): void {
        this.showPeriodMenu = false;
        this.period = p;
        // Limpiamos las fechas custom para que un re-pick sea predecible.
        this.customStart = '';
        this.customEnd = '';
        // Siempre re-cargamos: el usuario espera feedback visual del cambio.
        this.load();
    }
    applyCustomRange(): void {
        if (!this.customStart || !this.customEnd) {
            this.toast.warning('Debes seleccionar una fecha de inicio y de fin.');
            return;
        }
        if (this.customEnd < this.customStart) {
            this.toast.warning('La fecha de fin no puede ser anterior a la de inicio.');
            return;
        }
        this.period = 'custom';
        this.showPeriodMenu = false;
        this.load();
    }
    onGlobalProjectChange(): void {
        // Re-fetch con el nuevo project_id aplicado en backend.
        this.load();
    }

    @HostListener('document:keydown.escape')
    onEsc(): void {
        if (this.showExportModal) { this.showExportModal = false; return; }
        if (this.showPeriodMenu) this.showPeriodMenu = false;
    }
    @HostListener('document:mousedown', ['$event'])
    onDocClick(evt: MouseEvent): void {
        // Solo cerramos si el menú está abierto Y el click fue FUERA del
        // contenedor `.rp-period`. Usamos `mousedown` (no `click`) para
        // detectar el inicio del evento ANTES de que un date picker nativo
        // o un select abra su overlay y se trague el click subsiguiente.
        if (!this.showPeriodMenu) return;
        const target = evt.target as HTMLElement | null;
        if (!target) { this.showPeriodMenu = false; return; }
        // Si el click cae dentro del dropdown → no cerramos.
        if (target.closest('.rp-period')) return;
        // Si el elemento activo (input/select que abrió overlay) está dentro
        // del dropdown, tampoco — el usuario sigue interactuando con el menú
        // a través del picker nativo del navegador.
        const active = document.activeElement as HTMLElement | null;
        if (active && active.closest('.rp-period')) return;
        this.showPeriodMenu = false;
    }

    // ============================================================
    // KPIs reales.
    // ============================================================
    get metricProjects():       number { return this.data?.metrics?.projects_count    || 0; }
    // "Tasa de finalización" ahora es la tasa de CURACIÓN de sesiones — la
    // métrica que tiene sentido para la plataforma según el flujo real.
    get metricCompletionRate(): number { return this.data?.metrics?.curation_rate     || 0; }
    get metricDecisions():      number { return this.data?.metrics?.decisions_count   || 0; }
    get metricOverdue():        number { return this.data?.metrics?.overdue_tasks     || 0; }
    get metricTasksCompleted(): number { return this.data?.metrics?.tasks_completed   || 0; }
    get metricSessionsCurated(): number { return this.data?.metrics?.sessions_curated || 0; }
    get metricSessionsPending(): number { return this.data?.metrics?.sessions_pending || 0; }

    // ============================================================
    // Donut: proyectos por estado de CURACIÓN de sesiones (real).
    // Si se filtra a un proyecto, muestra cuántas de sus sesiones están
    // curadas vs pendientes — eso es lo que mide la plataforma.
    // ============================================================
    get donutFilteredBreakdown(): BreakdownEntry[] {
        // Sin filtro → distribución global de proyectos por estado.
        if (this.donutProjectId === null) {
            return this.data?.projects_breakdown || [];
        }
        // Con filtro → breakdown de sesiones del proyecto (curadas vs pendientes).
        const proj = (this.data?.top_projects || []).find(p => p.id === this.donutProjectId);
        if (!proj || !proj.sessions_breakdown) return [];
        const b = proj.sessions_breakdown;
        return [
            { key: 'completed', value: b.completed || 0 },
            { key: 'pending',   value: b.pending   || 0 },
        ].filter(e => e.value > 0);
    }

    get donutModeLabel(): string {
        return this.donutProjectId === null ? 'Proyectos totales' : 'Sesiones del proyecto';
    }

    get donutSlices(): DonutSlice[] {
        const items = this.donutFilteredBreakdown;
        const total = items.reduce((acc, s) => acc + s.value, 0) || 1;
        const r = 60;
        const circumference = 2 * Math.PI * r;
        let cumulative = 0;
        return items
            .filter(s => s.value > 0)
            .map((s) => {
                const palette = STATUS_PALETTE[s.key] || { color: '#94A3B8', label: s.key };
                const pct = s.value / total;
                const dashLength = pct * circumference;
                const dashOffset = -cumulative * circumference;
                cumulative += pct;
                return {
                    key: s.key,
                    label: palette.label,
                    value: s.value,
                    pct: Math.round(pct * 100),
                    color: palette.color,
                    dashLength,
                    dashOffset,
                };
            });
    }

    get donutTotal(): number {
        return this.donutFilteredBreakdown.reduce((a, s) => a + s.value, 0);
    }

    get donutLegend(): DonutSlice[] {
        // Mostrar todas las categorías en la leyenda incluso si tienen 0.
        const items = this.donutFilteredBreakdown;
        const total = items.reduce((a, s) => a + s.value, 0) || 1;
        const order: Array<DonutSlice['key']> = ['completed', 'in_progress', 'pending', 'overdue', 'not_started'];
        return order.map((k) => {
            const entry = items.find((e) => e.key === k) || { key: k, value: 0 };
            const palette = STATUS_PALETTE[k];
            return {
                key: k,
                label: palette.label,
                value: entry.value,
                pct: Math.round((entry.value / total) * 100),
                color: palette.color,
                dashLength: 0,
                dashOffset: 0,
            };
        });
    }

    setHoveredDonut(key: string | null): void { this.hoveredDonutKey = key; }

    // ============================================================
    // Line chart: decisiones en el tiempo (real).
    // ============================================================
    get filteredTimeline(): TimelinePoint[] {
        // El filtro por proyecto en este card no se aplica al timeline real
        // (vendría del backend con project_id), pero damos al usuario la
        // opción de RE-FETCH global al cambiar el filtro principal.
        // Si elige un project_id distinto al global, recargamos con ese.
        return this.data?.decisions_timeline || [];
    }

    onDecisionsProjectChange(): void {
        // Cambiar el filtro de esta card recarga con el project_id elegido
        // para que el timeline sea real para ese proyecto.
        if (this.decisionsProjectId === null) {
            // Volver al global.
            this.load();
            return;
        }
        const params = this.buildParams(
            this.period, this.refDate, this.decisionsProjectId, this.customStart, this.customEnd,
        );
        const headers = this.authService.getAuthHeaders();
        this.http.get<ReportData>(`${environment.apiUrl}/api/reports/data?${params}`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (res) => {
                    // Solo actualizamos timeline; el resto del dashboard sigue con el filtro global.
                    if (this.data) {
                        this.data.decisions_timeline = res.decisions_timeline;
                        this.cdr.detectChanges();
                    }
                },
                error: () => this.toast.error('No se pudo aplicar el filtro.'),
            });
    }

    private readonly lineW = 600;
    private readonly lineH = 240;
    private readonly linePadL = 36;
    private readonly linePadR = 16;
    private readonly linePadT = 12;
    private readonly linePadB = 28;

    get lineMaxY(): number {
        const max = Math.max(0, ...this.filteredTimeline.map(p => p.value));
        if (max <= 10) return 10;
        if (max <= 25) return 25;
        if (max <= 50) return 50;
        return Math.ceil(max / 10) * 10;
    }
    get lineYTicks(): number[] {
        const m = this.lineMaxY;
        return [0, Math.round(m * 0.25), Math.round(m * 0.5), Math.round(m * 0.75), m];
    }

    get linePoints(): LinePoint[] {
        const data = this.filteredTimeline;
        if (!data.length) return [];
        const usable = this.lineW - this.linePadL - this.linePadR;
        const stepX = data.length > 1 ? usable / (data.length - 1) : 0;
        const maxY = this.lineMaxY || 1;
        return data.map((p, i) => ({
            x: this.linePadL + i * stepX,
            y: this.linePadT + (this.lineH - this.linePadT - this.linePadB) * (1 - p.value / maxY),
            label: p.label,
            value: p.value,
            iso: p.iso,
        }));
    }

    get linePathD(): string {
        const pts = this.linePoints;
        if (!pts.length) return '';
        return pts.reduce((acc, p, i) => acc + (i === 0 ? `M ${p.x},${p.y}` : ` L ${p.x},${p.y}`), '');
    }

    get lineAreaD(): string {
        const pts = this.linePoints;
        if (!pts.length) return '';
        const base = this.lineH - this.linePadB;
        const first = pts[0], last = pts[pts.length - 1];
        const top = pts.reduce((acc, p, i) => acc + (i === 0 ? `M ${p.x},${p.y}` : ` L ${p.x},${p.y}`), '');
        return `${top} L ${last.x},${base} L ${first.x},${base} Z`;
    }

    yTickPos(t: number): number {
        return this.linePadT + (this.lineH - this.linePadT - this.linePadB) * (1 - t / (this.lineMaxY || 1));
    }

    setHoveredLine(idx: number | null): void { this.hoveredLineIdx = idx; }

    /** Detecta el punto más cercano al cursor para hover suave sobre toda el área. */
    onLineMove(evt: MouseEvent): void {
        const svg = evt.currentTarget as SVGSVGElement;
        if (!svg) return;
        const rect = svg.getBoundingClientRect();
        // Mapeamos del espacio CSS al viewBox 0..600.
        const x = ((evt.clientX - rect.left) / rect.width) * this.lineW;
        const pts = this.linePoints;
        if (!pts.length) return;
        let nearest = 0;
        let minD = Infinity;
        for (let i = 0; i < pts.length; i++) {
            const d = Math.abs(pts[i].x - x);
            if (d < minD) { minD = d; nearest = i; }
        }
        if (this.hoveredLineIdx !== nearest) {
            this.hoveredLineIdx = nearest;
            this.cdr.detectChanges();
        }
    }

    get hoveredLinePoint(): LinePoint | null {
        if (this.hoveredLineIdx === null) return null;
        return this.linePoints[this.hoveredLineIdx] || null;
    }

    /** Delta % vs el punto anterior — para tooltip enriquecido. */
    get hoveredLineDelta(): { sign: string; pct: number } | null {
        const i = this.hoveredLineIdx;
        if (i === null || i <= 0) return null;
        const cur = this.linePoints[i]?.value || 0;
        const prev = this.linePoints[i - 1]?.value || 0;
        if (!prev) return { sign: '+', pct: 100 };
        const d = ((cur - prev) / prev) * 100;
        return { sign: d >= 0 ? '+' : '−', pct: Math.abs(Math.round(d)) };
    }

    onDecisionsViewDetails(): void {
        // Comportamiento explícito: ver "Pendientes" filtrado por proyecto
        // si lo hay; si no, descargar PDF como "detalle exportable".
        const detail = document.getElementById('rp-decisions-detail');
        if (detail) detail.classList.toggle('is-open');
    }

    // ============================================================
    // Bar chart: actividad del equipo (real).
    // ============================================================
    get barRowsRaw(): TeamMember[] {
        if (!this.data) return [];
        return this.teamActivityMode === 'meetings'
            ? (this.data.team_activity.meetings || [])
            : (this.data.team_activity.actions  || []);
    }

    get barRows(): TeamMember[] {
        // El filtro por proyecto de esta card filtra por presencia del owner
        // en sesiones de ese proyecto.
        if (this.teamProjectId === null) return this.barRowsRaw.slice(0, 6);
        if (!this.data) return [];
        const projSessions = new Set(
            this.data.sessions
                .filter(s => this.projectIdMatches(s.project_name, this.teamProjectId))
                .map(s => s.id),
        );
        // Sin más data por owner-por-sesión, hacemos best-effort: si la card
        // está filtrada y no podemos cruzar 1:1, recargamos con project_id.
        // Para simplificar, solo limitamos el set actual sin filtrar (la
        // recarga real se hace via onTeamProjectChange()).
        return this.barRowsRaw.slice(0, 6);
    }

    private projectIdMatches(projectName: string, projectId: number | null): boolean {
        if (!projectId) return true;
        const proj = this.data?.available_projects.find(p => p.id === projectId);
        return !!proj && proj.name === projectName;
    }

    onTeamProjectChange(): void {
        // Recargamos solo team_activity con project_id si el usuario seleccionó uno.
        if (this.teamProjectId === null) { this.load(); return; }
        const params = this.buildParams(
            this.period, this.refDate, this.teamProjectId, this.customStart, this.customEnd,
        );
        this.http.get<ReportData>(`${environment.apiUrl}/api/reports/data?${params}`,
            { headers: this.authService.getAuthHeaders() })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (res) => {
                    if (this.data) {
                        this.data.team_activity = res.team_activity;
                        this.cdr.detectChanges();
                    }
                },
                error: () => this.toast.error('No se pudo aplicar el filtro de equipo.'),
            });
    }

    onTopProjectChange(): void {
        // El filtro de "Top Projects" filtra solo client-side la tabla actual.
        this.topProjectsPage = 1;
    }

    onDonutProjectChange(): void {
        // Solo afecta visualmente al donut, no recarga.
    }

    get barMax(): number {
        const max = Math.max(0, ...this.barRows.map(r => r.value));
        if (max <= 10) return 10;
        if (max <= 25) return 25;
        if (max <= 50) return 50;
        if (max <= 100) return 100;
        return Math.ceil(max / 10) * 10;
    }

    get barYTicks(): number[] {
        const m = this.barMax;
        return [m, Math.round(m * 0.8), Math.round(m * 0.6), Math.round(m * 0.4), Math.round(m * 0.2), 0];
    }

    barHeightPct(value: number): number {
        const m = this.barMax || 1;
        return Math.min(100, (value / m) * 100);
    }

    setHoveredBar(idx: number | null): void { this.hoveredBarIdx = idx; }

    setTeamActivityMode(m: 'meetings' | 'actions'): void {
        this.teamActivityMode = m;
        this.hoveredBarIdx = null;
    }

    // ============================================================
    // Nuevo: lista combinada de "Actividad del equipo" (filas con avatar +
    // dos métricas a la vez + barras horizontales — mucho más rica que el
    // viejo bar chart vertical con segmented control).
    // ============================================================
    get teamRows(): TeamCombined[] {
        const rows = this.data?.team_activity?.combined || [];
        const sorted = [...rows];
        if (this.teamActivitySort === 'meetings') {
            sorted.sort((a, b) => b.meetings - a.meetings);
        } else if (this.teamActivitySort === 'actions') {
            sorted.sort((a, b) => b.actions - a.actions);
        } else {
            // combined: peso ponderado para ranking general
            sorted.sort((a, b) => (b.actions + b.meetings) - (a.actions + a.meetings));
        }
        return sorted.slice(0, 8);
    }

    get teamMaxMeetings(): number {
        return Math.max(1, ...this.teamRows.map(r => r.meetings));
    }
    get teamMaxActions(): number {
        return Math.max(1, ...this.teamRows.map(r => r.actions));
    }
    teamMeetingsPct(v: number): number { return Math.round((v / this.teamMaxMeetings) * 100); }
    teamActionsPct(v: number):  number { return Math.round((v / this.teamMaxActions)  * 100); }

    setTeamSort(s: 'meetings' | 'actions' | 'combined'): void {
        this.teamActivitySort = s;
    }
    setHoveredTeam(i: number | null): void { this.hoveredTeamIdx = i; }

    onTeamSortBlur(): void { /* placeholder for future click-out semantics */ }

    // ============================================================
    // "Carga de trabajo" (nuevo card grande con filtros).
    // ============================================================
    get workloadAll(): WorkloadEntry[] {
        return this.data?.workload || [];
    }

    get workloadFiltered(): WorkloadEntry[] {
        let rows = [...this.workloadAll];
        // Filtro de estado: solo aplica si el usuario eligió uno específico —
        // recalcula totales con esa rebanada.
        if (this.workloadStatus !== 'all') {
            const k = this.workloadStatus;
            rows = rows.map(r => ({
                ...r,
                total: r.by_status[k] || 0,
            }));
        }
        // Sort por modo elegido.
        rows.sort((a, b) => {
            if (this.workloadSortBy === 'overdue')  return (b.by_status.overdue  || 0) - (a.by_status.overdue  || 0);
            if (this.workloadSortBy === 'meetings') return (b.meetings || 0) - (a.meetings || 0);
            return b.total - a.total;
        });
        // El filtro por proyecto NO cambia las filas (el backend ya filtra al
        // cargar con project_id global), pero sí actualiza si llamamos load().
        return rows;
    }

    get workloadTotal(): number {
        return this.workloadFiltered.reduce((a, r) => a + (r.total || 0), 0);
    }

    get workloadMax(): number {
        return Math.max(1, ...this.workloadFiltered.map(r => r.total));
    }

    workloadPct(value: number): number {
        return Math.round((value / this.workloadMax) * 100);
    }

    workloadAvatarColor(name: string): string { return this.avatarColor(name); }

    onWorkloadProjectChange(): void {
        // Recargamos con project_id específico para que el backend filtre y devuelva workload real.
        if (this.workloadProjectId === null) { this.load(); return; }
        const params = this.buildParams(
            this.period, this.refDate, this.workloadProjectId, this.customStart, this.customEnd,
        );
        this.http.get<ReportData>(`${environment.apiUrl}/api/reports/data?${params}`,
            { headers: this.authService.getAuthHeaders() })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (res) => {
                    if (this.data) {
                        this.data.workload = res.workload;
                        this.cdr.detectChanges();
                    }
                },
                error: () => this.toast.error('No se pudo aplicar el filtro de carga.'),
            });
    }

    setHoveredWorkload(i: number | null): void { this.hoveredWorkloadIdx = i; }

    workloadStatusColor(key: string): string {
        const map: Record<string, string> = {
            pending:   '#F97316',
            done:      '#10B981',
            blocked:   '#7C3AED',
            cancelled: '#94A3B8',
            overdue:   '#EF4444',
        };
        return map[key] || '#155EEF';
    }

    // ============================================================
    // Donut "Carga de trabajo" — cada segmento = una persona.
    // ============================================================
    get workloadDonutSlices(): { owner: string; value: number; pct: number; color: string;
                                  dashLength: number; dashOffset: number; }[] {
        const rows = this.workloadFiltered;
        const total = rows.reduce((a, r) => a + (r.total || 0), 0) || 1;
        const r = 60;
        const circumference = 2 * Math.PI * r;
        let cumulative = 0;
        return rows
            .filter(row => row.total > 0)
            .map(row => {
                const pct = row.total / total;
                const dashLength = pct * circumference;
                const dashOffset = -cumulative * circumference;
                cumulative += pct;
                return {
                    owner: row.owner,
                    value: row.total,
                    pct: Math.round(pct * 100),
                    color: this.avatarColor(row.owner),
                    dashLength,
                    dashOffset,
                };
            });
    }

    get workloadHoveredRow(): WorkloadEntry | null {
        if (this.hoveredWorkloadIdx === null) return null;
        return this.workloadFiltered[this.hoveredWorkloadIdx] || null;
    }

    // ============================================================
    // Tabla de proyectos.
    // ============================================================
    get topProjectsFiltered(): TopProjectRow[] {
        const all = this.data?.top_projects || [];
        if (this.topProjectId === null) return all;
        return all.filter(p => p.id === this.topProjectId);
    }

    get topProjectsPages(): number {
        return Math.max(1, Math.ceil(this.topProjectsFiltered.length / this.topProjectsLimit));
    }

    get topProjectsPageButtons(): number[] {
        return Array.from({ length: Math.min(5, this.topProjectsPages) }, (_, i) => i + 1);
    }

    get topProjectsPaged(): TopProjectRow[] {
        const start = (this.topProjectsPage - 1) * this.topProjectsLimit;
        return this.topProjectsFiltered.slice(start, start + this.topProjectsLimit);
    }

    get topProjectsRangeLabel(): string {
        const total = this.topProjectsFiltered.length;
        if (!total) return 'Sin proyectos para mostrar';
        const start = (this.topProjectsPage - 1) * this.topProjectsLimit + 1;
        const end = Math.min(this.topProjectsPage * this.topProjectsLimit, total);
        return `Mostrando ${start}-${end} de ${total} proyectos`;
    }

    goToTopProjectsPage(p: number): void {
        if (p < 1 || p > this.topProjectsPages) return;
        this.topProjectsPage = p;
    }

    statusBadge(s: TopProjectRow['status']): { label: string; key: string } {
        switch (s) {
            case 'completed':   return { label: 'Completado', key: 'completed' };
            case 'in_progress': return { label: 'En progreso', key: 'in_progress' };
            case 'pending':     return { label: 'Pendiente',   key: 'pending' };
            case 'overdue':     return { label: 'Vencido',     key: 'overdue' };
            case 'not_started': return { label: 'No iniciado', key: 'not_started' };
            default:            return { label: '—',           key: 'neutral' };
        }
    }

    initials(name: string): string {
        if (!name) return '?';
        const parts = name.trim().split(/\s+/);
        const a = parts[0]?.[0] || '';
        const b = parts.length > 1 ? parts[parts.length - 1][0] : '';
        return (a + b).toUpperCase();
    }

    private readonly _avatarPalette = ['#155EEF', '#10B981', '#F97316', '#EF4444', '#7C3AED', '#0EA5E9'];
    avatarColor(name: string): string {
        if (!name) return this._avatarPalette[0];
        let h = 0;
        for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) | 0;
        return this._avatarPalette[Math.abs(h) % this._avatarPalette.length];
    }

    // ============================================================
    // Lista de proyectos disponibles (para todos los selectores).
    // ============================================================
    get availableProjects(): AvailableProject[] {
        return this.data?.available_projects || [];
    }

    // ============================================================
    // Export modal — abre, ejecuta descarga PDF o Excel con filtros.
    // ============================================================
    openExportModal(): void {
        // Pre-cargamos el modal con los filtros actuales.
        this.exportFormat = 'pdf';
        this.exportProjectId = this.projectId;
        this.exportPeriod = this.period;
        this.exportStartDate = this.customStart || (this.data?.start || '').slice(0, 10);
        this.exportEndDate   = this.customEnd   || (this.data?.end   || '').slice(0, 10);
        this.showExportModal = true;
    }
    closeExportModal(): void { this.showExportModal = false; }

    confirmExport(): void {
        if (this.exportPeriod === 'custom' && (!this.exportStartDate || !this.exportEndDate)) {
            this.toast.warning('Selecciona fecha de inicio y de fin.');
            return;
        }
        if (this.exportPeriod === 'custom' && this.exportEndDate < this.exportStartDate) {
            this.toast.warning('La fecha de fin no puede ser anterior a la de inicio.');
            return;
        }
        this.isExporting = true;
        const endpoint = this.exportFormat === 'excel' ? 'excel' : 'pdf';
        const params = this.buildParams(
            this.exportPeriod, this.refDate, this.exportProjectId,
            this.exportStartDate, this.exportEndDate,
        );
        const headers = this.authService.getAuthHeaders();
        this.http
            .get(`${environment.apiUrl}/api/reports/${endpoint}?${params}`,
                 { headers, responseType: 'blob' })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (blob: Blob) => {
                    this.isExporting = false;
                    const url = window.URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    const safe = (this.data?.label || 'reporte').replace(/[^a-z0-9]/gi, '_').slice(0, 60);
                    const ext = this.exportFormat === 'excel' ? 'xlsx' : 'pdf';
                    a.download = `Reporte_Notiva_${safe}.${ext}`;
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    window.URL.revokeObjectURL(url);
                    this.toast.success(`Reporte ${this.exportFormat.toUpperCase()} descargado.`);
                    this.showExportModal = false;
                    this.cdr.detectChanges();
                },
                error: () => {
                    this.isExporting = false;
                    this.toast.error('No se pudo descargar el reporte.');
                    this.cdr.detectChanges();
                },
            });
    }
}
