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
interface KpiTile { key: string; label: string; value: number; trend: number; tone: 'navy'|'success'|'warning'|'danger'; icon: 'meetings'|'tasks'|'decisions'|'risks'; }
interface SyncProvider { id: string; name: string; iconColor: string; iconLetter: string; connected: boolean; }
interface FollowupBreakdown { label: string; count: number; pct: number; color: string; }

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

        // Action items globales (todo el tenant). Endpoint /api/pendientes.
        this.http.get<any>(`${environment.apiUrl}/api/pendientes`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (data) => {
                    this.allActionItems = Array.isArray(data) ? data : (data?.items ?? []);
                    this.cdr.detectChanges();
                },
                error: () => { this.allActionItems = []; },
            });

        // Integrations / Sync Health
        this.http.get<any>(`${environment.apiUrl}/api/settings`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (data) => { this.integrations = data || {}; this.cdr.detectChanges(); },
                error: () => { this.integrations = {}; },
            });
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

    /** Saludo según hora local. */
    get greeting(): string {
        const h = new Date().getHours();
        if (h < 12) return 'Buenos días';
        if (h < 19) return 'Buenas tardes';
        return 'Buenas noches';
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
            { key: 'meetings',     label: 'Reuniones',     value: meetings,    trend: trend(meetings, prevMeetings),   tone: 'navy',    icon: 'meetings' },
            { key: 'action_items', label: 'Tareas',        value: actionItems, trend: trend(actionItems, prevActions), tone: 'success', icon: 'tasks' },
            { key: 'decisions',    label: 'Decisiones',    value: decisions,   trend: trend(decisions, prevDecisions), tone: 'navy',    icon: 'decisions' },
            { key: 'risks',        label: 'Riesgos',       value: risks,       trend: trend(risks, prevRisks),         tone: 'warning', icon: 'risks' },
        ];
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

    /** Follow-up Status: distribución de action items por status. */
    get followupBreakdown(): FollowupBreakdown[] {
        const items = this.allActionItems || [];
        const total = Math.max(1, items.length);
        const c = (s: string) => items.filter((it) => (it?.status || '').toLowerCase() === s).length;
        const completed = c('done') || c('completed');
        const inProgress = c('in_progress') || c('blocked');
        const pending = c('pending');
        const overdue = items.filter((it) => {
            if (!it?.due_date) return false;
            const d = this._toDate(it.due_date);
            return d ? d.getTime() < Date.now() && (it.status || 'pending') !== 'done' : false;
        }).length;
        const pct = (n: number) => Math.round((n / total) * 100);
        return [
            { label: 'Completadas',    count: completed,  pct: pct(completed),  color: 'var(--color-success)' },
            { label: 'En progreso',    count: inProgress, pct: pct(inProgress), color: 'var(--color-info)' },
            { label: 'Pendientes',     count: pending,    pct: pct(pending),    color: 'var(--color-warning)' },
            { label: 'Vencidas',       count: overdue,    pct: pct(overdue),    color: 'var(--color-danger)' },
        ];
    }

    get followupTotal(): number {
        return (this.allActionItems || []).length;
    }

    /** Sync Health — providers que muestran en el handoff. */
    get syncProviders(): SyncProvider[] {
        const cfg = this.integrations || {};
        const has = (k: string): boolean => {
            const c = cfg[k];
            if (!c) return false;
            return !!(c.isActive || c.apiKey || c.api_key || c.token || c.api_token || c.pat || c.apiToken);
        };
        return [
            { id: 'jira',     name: 'Jira',         iconColor: '#2684FF', iconLetter: 'J', connected: has('jira') },
            { id: 'trello',   name: 'Trello',       iconColor: '#0079BF', iconLetter: 'T', connected: has('trello') },
            { id: 'clickup',  name: 'ClickUp',      iconColor: '#7B68EE', iconLetter: 'C', connected: has('clickup') },
            { id: 'azure',    name: 'Azure DevOps', iconColor: '#0078D4', iconLetter: 'A', connected: has('azure_devops') || has('azure') },
        ];
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
    quickActions = [
        { id: 'new-meeting',  label: 'Iniciar reunión',       sub: 'Subir audio o texto',         icon: 'mic',      action: 'upload' },
        { id: 'upload',       label: 'Subir transcripción',   sub: 'Analizar reunión pasada',     icon: 'upload',   action: 'upload' },
        { id: 'projects',     label: 'Crear proyecto',        sub: 'Organiza trabajo y equipos',  icon: 'folder',   action: 'goto', target: '/admin/projects' },
        { id: 'ask',          label: 'Preguntá a Acten',      sub: 'Insights con IA',             icon: 'sparkle',  action: 'goto', target: '/admin/ask' },
        { id: 'calendar',     label: 'Ver mi calendario',     sub: 'Sincronizado con Google/MS',  icon: 'calendar', action: 'goto', target: '/admin/calendar' },
        { id: 'reports',      label: 'Ver reportes',          sub: 'Explora analíticas',          icon: 'chart',    action: 'goto', target: '/admin/reportes' },
    ];

    triggerQuickAction(qa: any): void {
        if (qa.action === 'upload') return this.openUploadModal();
        if (qa.action === 'goto' && qa.target) this.router.navigateByUrl(qa.target);
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
