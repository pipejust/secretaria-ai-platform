import { Component, OnInit, OnDestroy, ChangeDetectorRef, HostListener } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterModule } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { Subject } from 'rxjs';
import { takeUntil } from 'rxjs/operators';
import { AuthService } from '../../services/auth.service';
import { ToastService } from '../../services/toast.service';
import { environment } from '../../../environments/environment';

interface PendingItem {
    id: number;
    session_id: number;
    title: string;
    description: string;
    owner_name: string;
    owner_email: string;
    due_date: string | null;
    /** Hora HH:MM (24h) opcional. */
    due_time?: string | null;
    /** Prioridad real (alta|media|baja) — viene del backend. */
    priority?: 'alta' | 'media' | 'baja';
    status: 'pending' | 'done' | 'blocked' | 'cancelled';
    completed_at: string | null;
    is_approved: boolean;
    project_name: string;
    bucket: 'vencido' | 'proximo' | 'pendiente' | 'sin_fecha' | 'completado' | 'cancelado' | 'bloqueado';
}

interface ProjectLite { id: number; name: string; }

interface StatsResponse {
    counts: Record<string, number>;
    active_total: number;
    top_owners_active: { owner: string; count: number }[];
}

type BucketFilter = 'activos' | 'vencido' | 'proximo' | 'pendiente' | 'sin_fecha' | 'bloqueado' | 'completado' | 'cancelado';
type TabKey = 'todas' | 'mias' | 'proyecto' | 'prioridad';

interface Metric {
    key: string;
    label: string;
    value: number;
    delta: string;
    deltaDir: 'up' | 'down' | 'flat';
    deltaTone: 'good' | 'bad' | 'neutral';
    icon: 'open' | 'progress' | 'week' | 'overdue' | 'done';
}

interface SessionLite {
    id: number;
    title: string;
    date: string;
}

interface DonutSlice {
    owner: string;
    count: number;
    pct: number;
    color: string;
    initials: string;
}

@Component({
    selector: 'app-pendientes',
    standalone: true,
    imports: [CommonModule, FormsModule, RouterModule],
    templateUrl: './pendientes.component.html',
    styleUrls: ['./pendientes.component.css'],
})
export class PendientesComponent implements OnInit, OnDestroy {
    items: PendingItem[] = [];
    stats: StatsResponse | null = null;
    isLoading = false;
    /** Filtro de bucket que se manda al backend (preserva la API existente). */
    bucket: BucketFilter = 'activos';

    /** Filtros locales (en frontend) sobre la lista ya cargada. */
    searchQuery = '';
    ownerFilter = '';
    projectFilter = '';        // matches contra project_name (string)
    priorityFilter = '';       // alta | media | baja
    statusFilter = '';
    dueFilter = '';

    /** Tab visual de segmentación. */
    activeTab: TabKey = 'todas';

    /** Toggle del panel de filtros (mismo patrón que /admin/meetings). */
    showFilters = false;

    /** Lista de proyectos del tenant (para selects). */
    projects: ProjectLite[] = [];

    /** Email del usuario logueado — usado por la pestaña "Mis tareas". */
    private get currentUserEmail(): string {
        return (this.authService.currentUserValue?.email || '').toLowerCase();
    }
    private get currentUserName(): string {
        return this.authService.currentUserValue?.full_name || '';
    }

    /** Filtro mini del workload (proyecto / fecha). Solo cambia la vista
     *  del donut; no afecta la tabla principal. */
    workloadProjectFilter = '';
    workloadRangeFilter: 'all' | 'week' | 'month' = 'all';

    /** Paginación local (12 por página, como el mockup). */
    pageSize = 12;
    currentPage = 1;

    /** IDs marcados con checkbox (para selección masiva). */
    selectedIds = new Set<number>();

    /** ID de la fila cuyo menú de acciones (3 puntos) está abierto. */
    openActionsId: number | null = null;

    /** Estado del modal "Agregar tarea". */
    showCreateModal = false;
    /** Vista interna del modal: chooser inicial o formulario meeting. */
    createMode: 'chooser' | 'meeting' = 'chooser';
    sessions: SessionLite[] = [];
    sessionSearch = '';
    isCreating = false;
    newTask = {
        session_id: null as number | null,
        title: '',
        owner_name: '',
        owner_email: '',
        due_date: '',
        due_time: '',
        priority: 'media' as 'alta' | 'media' | 'baja',
        description: '',
    };

    private readonly destroy$ = new Subject<void>();

    /** Paleta de colores para el donut de carga de trabajo (orden estable). */
    private readonly donutPalette = ['#155EEF', '#F59E0B', '#7C3AED', '#10B981', '#EF4444', '#94A3B8'];

    constructor(
        private http: HttpClient,
        private authService: AuthService,
        private toast: ToastService,
        private cdr: ChangeDetectorRef,
        private router: Router,
    ) {}

    ngOnInit(): void {
        this.loadStats();
        this.load();
        this.loadProjects();
    }

    /** Carga la lista de proyectos del tenant para el select de filtros y modal. */
    loadProjects(): void {
        const headers = this.authService.getAuthHeaders();
        this.http.get<any[]>(`${environment.apiUrl}/api/projects/`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (data) => {
                    this.projects = (data || []).map(p => ({ id: p.id, name: p.name }));
                    this.cdr.detectChanges();
                },
                error: () => { /* silencio */ }
            });
    }

    ngOnDestroy(): void {
        this.destroy$.next();
        this.destroy$.complete();
    }

    // ============================================================
    // Carga de datos (preserva la API existente)
    // ============================================================

    setBucket(b: BucketFilter): void {
        this.bucket = b;
        this.currentPage = 1;
        this.load();
    }

    load(): void {
        this.isLoading = true;
        const headers = this.authService.getAuthHeaders();
        const params: any = { bucket: this.bucket, limit: 500 };
        if (this.ownerFilter.trim()) params.owner = this.ownerFilter.trim();

        const qs = new URLSearchParams(params as any).toString();
        this.http
            .get<{ items: PendingItem[]; total: number }>(
                `${environment.apiUrl}/api/pendientes?${qs}`,
                { headers },
            )
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (res) => {
                    this.items = res.items || [];
                    this.isLoading = false;
                    this.currentPage = 1;
                    this.cdr.detectChanges();
                },
                error: () => {
                    this.toast.error('No se pudieron cargar las tareas.');
                    this.isLoading = false;
                    this.cdr.detectChanges();
                },
            });
    }

    loadStats(): void {
        const headers = this.authService.getAuthHeaders();
        this.http
            .get<StatsResponse>(`${environment.apiUrl}/api/pendientes/stats`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (res) => {
                    this.stats = res;
                    this.cdr.detectChanges();
                },
                error: () => { /* opcional */ }
            });
    }

    setStatus(item: PendingItem, status: PendingItem['status']): void {
        const headers = this.authService.getAuthHeaders();
        this.http
            .patch<{ id: number; status: string; completed_at: string | null }>(
                `${environment.apiUrl}/api/pendientes/${item.id}/status`,
                { status },
                { headers },
            )
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: () => {
                    this.toast.success(`Tarea marcada como "${this.statusLabel(status)}".`);
                    this.load();
                    this.loadStats();
                },
                error: (err) => {
                    const detail = err?.error?.detail || 'No se pudo cambiar el estado.';
                    this.toast.error(detail);
                },
            });
    }

    // ============================================================
    // Tabs
    // ============================================================

    setTab(tab: TabKey): void {
        this.activeTab = tab;
        this.currentPage = 1;

        // Cada tab ajusta los filtros de forma DETERMINÍSTICA:
        if (tab === 'todas') {
            // Limpiamos filtros derivados (preservamos la búsqueda manual).
            this.ownerFilter = '';
            this.priorityFilter = '';
            this.bucket = 'activos';
            this.load();
        } else if (tab === 'mias') {
            // Filtra por correo del usuario logueado (más confiable que nombre).
            const email = this.currentUserEmail;
            const name  = this.currentUserName;
            this.ownerFilter = email || name;
            this.load();
        } else if (tab === 'proyecto') {
            // Vista agrupada por proyecto: la tabla se ordena por proyecto.
            // No imponemos filtro adicional. El usuario puede picar "Filtros"
            // y elegir un proyecto específico si quiere.
        } else if (tab === 'prioridad') {
            // Vista agrupada por prioridad: ordenamos alta → media → baja.
        }
        this.cdr.detectChanges();
    }

    clearFilters(): void {
        this.searchQuery = '';
        this.ownerFilter = '';
        this.projectFilter = '';
        this.priorityFilter = '';
        this.statusFilter = '';
        this.dueFilter = '';
        this.activeTab = 'todas';
        this.bucket = 'activos';
        this.currentPage = 1;
        this.load();
    }

    toggleFilters(): void {
        this.showFilters = !this.showFilters;
        this.cdr.detectChanges();
    }

    /** Conteo de filtros activos para el badge del botón Filtros. */
    get activeFiltersCount(): number {
        let n = 0;
        if (this.ownerFilter.trim())    n++;
        if (this.projectFilter.trim())  n++;
        if (this.priorityFilter.trim()) n++;
        if (this.statusFilter.trim())   n++;
        if (this.bucket && this.bucket !== 'activos') n++;
        return n;
    }

    // ============================================================
    // Derivados visuales (priority, status, progress, code)
    // ============================================================

    /** Genera un código tipo "TAS-1024" a partir del id. Se usa solo
     *  para la línea secundaria visual; no es persistido. */
    taskCode(item: PendingItem): string {
        return `TAS-${String(item.id).padStart(4, '0')}`;
    }

    /** Prioridad: usa la real del backend (`item.priority`) si existe;
     *  cae al derivado por bucket como fallback para entradas viejas. */
    priority(item: PendingItem): 'alta' | 'media' | 'baja' {
        const real = (item.priority || '').toLowerCase();
        if (real === 'alta' || real === 'media' || real === 'baja') return real;
        if (item.bucket === 'vencido' || item.bucket === 'proximo') return 'alta';
        if (item.bucket === 'pendiente' || item.bucket === 'bloqueado') return 'media';
        return 'baja';
    }

    priorityLabel(p: string): string {
        return ({ alta: 'Alta', media: 'Media', baja: 'Baja' } as any)[p] || p;
    }

    /** Etiqueta de estado para el badge — combina status + bucket para
     *  mostrar Vencida/En progreso/Abierta/Completada/Bloqueada. */
    estadoLabel(item: PendingItem): string {
        if (item.status === 'cancelled') return 'Cancelada';
        if (item.status === 'done') return 'Completada';
        if (item.status === 'blocked') return 'Bloqueada';
        if (item.bucket === 'vencido') return 'Vencida';
        if (item.bucket === 'proximo') return 'En progreso';
        return 'Abierta';
    }

    estadoTone(item: PendingItem): 'red' | 'amber' | 'blue' | 'green' | 'gray' | 'violet' {
        const lbl = this.estadoLabel(item);
        if (lbl === 'Vencida') return 'red';
        if (lbl === 'En progreso') return 'amber';
        if (lbl === 'Completada') return 'green';
        if (lbl === 'Bloqueada') return 'violet';
        if (lbl === 'Cancelada') return 'gray';
        return 'blue';
    }

    statusLabel(s: string): string {
        return ({
            pending: 'Pendiente',
            done: 'Completada',
            blocked: 'Bloqueada',
            cancelled: 'Cancelada',
        } as Record<string, string>)[s] || s;
    }

    /** Progreso visible en la barra. Como ActionItem no almacena % real,
     *  derivamos un valor coherente con el estado:
     *    done → 100, cancelled → 100 (gris), blocked → 50,
     *    overdue → 20, próximo → 60, abierta → 0/10
     *  La barra es informativa, no editable.
     */
    progress(item: PendingItem): number {
        if (item.status === 'done' || item.status === 'cancelled') return 100;
        if (item.status === 'blocked') return 50;
        if (item.bucket === 'vencido') return 20;
        if (item.bucket === 'proximo') return 60;
        if (item.bucket === 'pendiente') return 10;
        return 0;
    }

    progressTone(item: PendingItem): 'red' | 'amber' | 'blue' | 'green' | 'gray' | 'violet' {
        if (item.status === 'cancelled') return 'gray';
        if (item.status === 'done') return 'green';
        if (item.status === 'blocked') return 'violet';
        if (item.bucket === 'vencido') return 'red';
        if (item.bucket === 'proximo') return 'amber';
        return 'blue';
    }

    /** Iniciales del owner para avatar circular. */
    ownerInitials(name: string): string {
        const n = (name || '').trim();
        if (!n || n.toLowerCase() === 'unknown') return '··';
        const parts = n.split(/\s+/);
        return ((parts[0]?.[0] || '') + (parts[1]?.[0] || '')).toUpperCase() || '··';
    }

    /** Color estable para el avatar (hash simple sobre el nombre). */
    ownerColor(name: string): string {
        const palette = ['#2563EB', '#7C3AED', '#DB2777', '#EA580C', '#16A34A', '#0891B2'];
        const n = (name || '').toLowerCase();
        let h = 0;
        for (let i = 0; i < n.length; i++) h = (h * 31 + n.charCodeAt(i)) >>> 0;
        return palette[h % palette.length];
    }

    formatDate(d: string | null): string {
        if (!d) return '—';
        try {
            const v = new Date(d);
            if (isNaN(v.getTime())) return d;
            return v.toLocaleDateString('es-CO', { day: '2-digit', month: 'short', year: 'numeric' })
                .replace('.', '');
        } catch {
            return d;
        }
    }

    /** "16 may" – formato corto para próximas fechas / vencidas. */
    formatDateShort(d: string | null): string {
        if (!d) return '';
        try {
            const v = new Date(d);
            if (isNaN(v.getTime())) return d;
            const months = ['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'];
            return `${months[v.getMonth()]}\n${String(v.getDate()).padStart(2,'0')}`;
        } catch {
            return d;
        }
    }

    formatDateLong(d: string | null): string {
        if (!d) return '';
        try {
            const v = new Date(d);
            if (isNaN(v.getTime())) return d;
            const months = ['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'];
            return `${v.getDate()} ${months[v.getMonth()]} ${v.getFullYear()}`;
        } catch {
            return d;
        }
    }

    isOverdue(item: PendingItem): boolean {
        return item.bucket === 'vencido';
    }

    /** Color del badge de proyecto (suave, basado en hash del nombre). */
    projectTone(name: string): { bg: string; fg: string } {
        const palette: Array<{ bg: string; fg: string }> = [
            { bg: '#EFF6FF', fg: '#1E40AF' },
            { bg: '#ECFDF5', fg: '#047857' },
            { bg: '#FFF7ED', fg: '#C2410C' },
            { bg: '#F5F3FF', fg: '#6D28D9' },
            { bg: '#FEF2F2', fg: '#B91C1C' },
            { bg: '#FFFBEB', fg: '#B45309' },
            { bg: '#F0F9FF', fg: '#075985' },
        ];
        const n = (name || '').toLowerCase();
        let h = 0;
        for (let i = 0; i < n.length; i++) h = (h * 31 + n.charCodeAt(i)) >>> 0;
        return palette[h % palette.length];
    }

    // ============================================================
    // Métricas (top cards) — derivadas de stats
    // ============================================================

    get metrics(): Metric[] {
        const c = this.stats?.counts || {};
        const open = (c['pendiente'] || 0) + (c['proximo'] || 0) + (c['vencido'] || 0) + (c['sin_fecha'] || 0);
        const inprog = c['proximo'] || 0;
        const week = c['proximo'] || 0;
        const overdue = c['vencido'] || 0;
        const done = c['completado'] || 0;

        return [
            // Más tareas abiertas = neutral (no hay baseline histórico).
            { key: 'open',     label: 'Tareas abiertas',    value: open,    icon: 'open',
              delta: this._weekly(open, 'nuevas'),
              deltaDir: open > 0 ? 'up' : 'flat',
              deltaTone: 'neutral' },

            { key: 'progress', label: 'En progreso',        value: inprog,  icon: 'progress',
              delta: this._weekly(inprog, 'esta semana'),
              deltaDir: inprog > 0 ? 'up' : 'flat',
              deltaTone: 'neutral' },

            { key: 'week',     label: 'Vencen esta semana', value: week,    icon: 'week',
              delta: 'En los próximos 7 días',
              deltaDir: 'flat', deltaTone: 'neutral' },

            // Vencidas: más es PEOR.
            { key: 'overdue',  label: 'Vencidas',           value: overdue, icon: 'overdue',
              delta: this._weekly(overdue, 'requieren acción'),
              deltaDir: overdue > 0 ? 'up' : 'flat',
              deltaTone: overdue > 0 ? 'bad' : 'good' },

            // Completadas: más es MEJOR.
            { key: 'done',     label: 'Completadas',        value: done,    icon: 'done',
              delta: this._weekly(done, 'cerradas'),
              deltaDir: done > 0 ? 'up' : 'flat',
              deltaTone: done > 0 ? 'good' : 'neutral' },
        ];
    }
    private _weekly(n: number, suffix: string): string {
        if (!n) return `Sin cambios esta semana`;
        return `${n} ${suffix}`;
    }

    // ============================================================
    // Listas filtradas (para tabla, pagina, panel)
    // ============================================================

    /** Lista filtrada (tabla principal) según los filtros del header + tab. */
    get filteredItems(): PendingItem[] {
        const q    = this.searchQuery.trim().toLowerCase();
        const proj = this.projectFilter.trim().toLowerCase();
        const pri  = this.priorityFilter.trim().toLowerCase();
        const st   = this.statusFilter.trim().toLowerCase();

        let out = this.items.filter(it => {
            if (q) {
                const hay = (it.title || '').toLowerCase() + ' ' + (it.description || '').toLowerCase();
                if (!hay.includes(q)) return false;
            }
            if (proj && !(it.project_name || '').toLowerCase().includes(proj)) return false;
            if (pri && this.priority(it) !== pri) return false;
            if (st) {
                const lbl = this.estadoLabel(it).toLowerCase();
                if (lbl !== st) return false;
            }
            return true;
        });

        // Ordenamiento según la pestaña activa.
        if (this.activeTab === 'proyecto') {
            out = [...out].sort((a, b) =>
                (a.project_name || '').localeCompare(b.project_name || '')
                || (a.due_date || '9999').localeCompare(b.due_date || '9999')
            );
        } else if (this.activeTab === 'prioridad') {
            const rank: Record<string, number> = { alta: 0, media: 1, baja: 2 };
            out = [...out].sort((a, b) => {
                const ra = rank[this.priority(a)] ?? 99;
                const rb = rank[this.priority(b)] ?? 99;
                if (ra !== rb) return ra - rb;
                return (a.due_date || '9999').localeCompare(b.due_date || '9999');
            });
        }
        return out;
    }

    get totalFiltered(): number { return this.filteredItems.length; }
    get totalPages(): number { return Math.max(1, Math.ceil(this.totalFiltered / this.pageSize)); }

    /** Slice paginado para renderizar. */
    get pagedItems(): PendingItem[] {
        const start = (this.currentPage - 1) * this.pageSize;
        return this.filteredItems.slice(start, start + this.pageSize);
    }

    get pageStart(): number {
        return this.totalFiltered === 0 ? 0 : (this.currentPage - 1) * this.pageSize + 1;
    }
    get pageEnd(): number {
        return Math.min(this.currentPage * this.pageSize, this.totalFiltered);
    }

    /** Páginas visibles a mostrar (max 3 al rededor de la current). */
    get pageNumbers(): number[] {
        const t = this.totalPages;
        const c = this.currentPage;
        const pages = new Set<number>();
        pages.add(1);
        for (let p = c - 1; p <= c + 1; p++) {
            if (p >= 1 && p <= t) pages.add(p);
        }
        pages.add(t);
        return Array.from(pages).sort((a, b) => a - b);
    }

    goToPage(p: number): void {
        if (p < 1 || p > this.totalPages) return;
        this.currentPage = p;
    }

    // ============================================================
    // Selección
    // ============================================================

    isSelected(id: number): boolean { return this.selectedIds.has(id); }
    toggleSelected(id: number): void {
        if (this.selectedIds.has(id)) this.selectedIds.delete(id);
        else this.selectedIds.add(id);
    }
    get allOnPageSelected(): boolean {
        return this.pagedItems.length > 0 && this.pagedItems.every(it => this.selectedIds.has(it.id));
    }
    toggleAllOnPage(): void {
        const all = this.allOnPageSelected;
        for (const it of this.pagedItems) {
            if (all) this.selectedIds.delete(it.id);
            else this.selectedIds.add(it.id);
        }
    }

    // ============================================================
    // Panel derecho
    // ============================================================

    get overdueItems(): PendingItem[] {
        return this.items.filter(it => it.bucket === 'vencido').slice(0, 3);
    }

    get upcomingItems(): PendingItem[] {
        return this.items.filter(it => it.bucket === 'proximo').slice(0, 5);
    }

    /** Slices del donut. Si NO hay filtros del workload, usamos el agregado
     *  oficial del backend (`stats.top_owners_active`). Si el usuario aplica
     *  un filtro de proyecto o rango, recomputamos sobre `this.items`. */
    get workloadSlices(): DonutSlice[] {
        const hasFilter = !!this.workloadProjectFilter || this.workloadRangeFilter !== 'all';
        if (!hasFilter) {
            const top = (this.stats?.top_owners_active || []).slice(0, 5);
            const total = top.reduce((acc, x) => acc + x.count, 0);
            const tot = this.stats?.active_total || total;
            const out: DonutSlice[] = top.map((o, i) => ({
                owner: o.owner || 'Sin asignar',
                count: o.count,
                pct: tot > 0 ? Math.round((o.count / tot) * 100) : 0,
                color: this.donutPalette[i] || this.donutPalette[5],
                initials: this.ownerInitials(o.owner || ''),
            }));
            const restCount = (this.stats?.active_total || 0) - total;
            if (restCount > 0) {
                out.push({
                    owner: 'Otros', count: restCount,
                    pct: tot > 0 ? Math.round((restCount / tot) * 100) : 0,
                    color: this.donutPalette[5], initials: '··',
                });
            }
            return out;
        }

        // Recalcular en frontend con los filtros activos.
        const filtered = this.items.filter(it => {
            if (this.workloadProjectFilter &&
                it.project_name !== this.workloadProjectFilter) return false;
            if (this.workloadRangeFilter === 'week' || this.workloadRangeFilter === 'month') {
                const d = it.due_date ? new Date(it.due_date) : null;
                if (!d || isNaN(d.getTime())) return false;
                const now = new Date();
                const diffDays = (d.getTime() - now.getTime()) / 86_400_000;
                if (this.workloadRangeFilter === 'week'  && diffDays > 7)  return false;
                if (this.workloadRangeFilter === 'month' && diffDays > 31) return false;
                if (diffDays < -1) return false; // ya muy vencidas no cuentan en el rango futuro
            }
            return it.bucket !== 'completado' && it.bucket !== 'cancelado';
        });

        // Agrupar por owner_name.
        const byOwner: Record<string, number> = {};
        for (const it of filtered) {
            const k = it.owner_name || 'Sin asignar';
            byOwner[k] = (byOwner[k] || 0) + 1;
        }
        const sorted = Object.entries(byOwner)
            .sort((a, b) => b[1] - a[1])
            .slice(0, 5);
        const total = filtered.length;
        return sorted.map(([owner, count], i) => ({
            owner, count,
            pct: total > 0 ? Math.round((count / total) * 100) : 0,
            color: this.donutPalette[i] || this.donutPalette[5],
            initials: this.ownerInitials(owner),
        }));
    }

    get workloadTotal(): number {
        const hasFilter = !!this.workloadProjectFilter || this.workloadRangeFilter !== 'all';
        if (!hasFilter) return this.stats?.active_total || 0;
        return this.workloadSlices.reduce((s, x) => s + x.count, 0);
    }

    /** Lista plana de pcts para que el template calcule el offset de cada
     *  slice del donut sin armar arrays inline (Angular no permite). */
    get workloadSlicesPcts(): number[] {
        return this.workloadSlices.map(s => s.pct);
    }

    /** Genera el atributo `stroke-dasharray` para cada slice del donut.
     *  Usamos un círculo de circunferencia 2πr donde r = 56 → 351.86. */
    donutDasharray(pct: number): string {
        const C = 2 * Math.PI * 56;
        const len = (pct / 100) * C;
        return `${len.toFixed(2)} ${(C - len).toFixed(2)}`;
    }
    donutDashoffset(pcts: number[], i: number): string {
        const C = 2 * Math.PI * 56;
        // Desplazamos el inicio al final del slice anterior. Empezamos
        // en el "norte" (12 en punto) → offset inicial = C / 4.
        const before = pcts.slice(0, i).reduce((s, p) => s + p, 0);
        const offset = C / 4 - (before / 100) * C;
        return `${offset.toFixed(2)}`;
    }

    // ============================================================
    // Menú de acciones por fila (3 puntos)
    // ============================================================

    toggleActions(id: number, ev: Event): void {
        ev.stopPropagation();
        this.openActionsId = this.openActionsId === id ? null : id;
        this.cdr.detectChanges();
    }

    closeActions(): void {
        if (this.openActionsId !== null) {
            this.openActionsId = null;
            this.cdr.detectChanges();
        }
    }

    @HostListener('document:click') onDocClick(): void {
        this.closeActions();
    }

    /** Navega a la curación de la sesión origen de la tarea. */
    goToSource(item: PendingItem): void {
        this.openActionsId = null;
        this.router.navigate(['/admin/curation', item.session_id]);
    }

    /** Marca como completada (atajo del menú). */
    actionComplete(item: PendingItem): void {
        this.openActionsId = null;
        this.setStatus(item, 'done');
    }
    actionBlock(item: PendingItem): void {
        this.openActionsId = null;
        this.setStatus(item, 'blocked');
    }
    actionReopen(item: PendingItem): void {
        this.openActionsId = null;
        this.setStatus(item, 'pending');
    }
    actionCancel(item: PendingItem): void {
        this.openActionsId = null;
        if (!confirm('¿Cancelar esta tarea? Quedará archivada con estado "Cancelada".')) return;
        this.setStatus(item, 'cancelled');
    }

    // ============================================================
    // Modal "Agregar tarea"
    // ============================================================

    openCreateModal(): void {
        this.showCreateModal = true;
        this.createMode = 'chooser';
        this.newTask = {
            session_id: null,
            title: '',
            owner_name: '',
            owner_email: '',
            due_date: '',
            due_time: '',
            priority: 'media',
            description: '',
        };
        this.sessionSearch = '';
        if (!this.sessions.length) this.loadSessions();
        this.cdr.detectChanges();
    }

    closeCreateModal(): void {
        this.showCreateModal = false;
        this.cdr.detectChanges();
    }

    /** Lista las últimas N sesiones para el selector del modal. */
    loadSessions(): void {
        const headers = this.authService.getAuthHeaders();
        this.http.get<any>(`${environment.apiUrl}/api/sessions/?page=1&limit=100`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (data) => {
                    const items = (data?.items ?? data) || [];
                    this.sessions = items.map((s: any) => ({
                        id: s.id,
                        title: s.title || `Sesión #${s.id}`,
                        date: this._fmtDate(s.date),
                    }));
                    this.cdr.detectChanges();
                },
                error: () => { /* lista opcional */ }
            });
    }

    private _fmtDate(v: any): string {
        if (v == null) return '';
        const n = typeof v === 'string' && !isNaN(Number(v)) ? Number(v) : v;
        const d = new Date(n);
        if (isNaN(d.getTime())) return String(v);
        const months = ['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'];
        return `${d.getDate()} ${months[d.getMonth()]} ${d.getFullYear()}`;
    }

    get filteredSessions(): SessionLite[] {
        const q = this.sessionSearch.trim().toLowerCase();
        if (!q) return this.sessions.slice(0, 60);
        return this.sessions
            .filter(s => s.title.toLowerCase().includes(q) || String(s.id).includes(q))
            .slice(0, 60);
    }

    chooseMeeting(): void { this.createMode = 'meeting'; this.cdr.detectChanges(); }

    /** Click en "Tarea de calendario" — redirige a /admin/calendar con
     *  un flag `new=task` que el componente del calendario lee al
     *  cargar para abrir directamente el popover de creación. */
    chooseCalendar(): void {
        this.closeCreateModal();
        this.router.navigate(['/admin/calendar'], { queryParams: { new: 'task' } });
    }

    /** Selecciona una sesión del listado. */
    pickSessionForTask(sid: number): void {
        this.newTask.session_id = sid;
    }

    /** POST /api/sessions/{id}/action_items con multipart/form-data. */
    submitNewTask(): void {
        if (!this.newTask.session_id) {
            this.toast.warning('Selecciona una reunión.');
            return;
        }
        if (!this.newTask.title.trim()) {
            this.toast.warning('La tarea necesita un título.');
            return;
        }
        this.isCreating = true;
        const headers = this.authService.getAuthHeaders();
        const form = new FormData();
        form.append('title', this.newTask.title.trim());
        form.append('owner_name', this.newTask.owner_name.trim());
        form.append('owner_email', this.newTask.owner_email.trim());
        form.append('due_date', this.newTask.due_date);
        form.append('due_time', this.newTask.due_time);
        form.append('priority', this.newTask.priority || 'media');
        form.append('description', this.newTask.description.trim());

        this.http.post<any>(
            `${environment.apiUrl}/api/sessions/${this.newTask.session_id}/action_items`,
            form, { headers }
        ).pipe(takeUntil(this.destroy$)).subscribe({
            next: () => {
                this.isCreating = false;
                this.toast.success('Tarea agregada correctamente.');
                this.showCreateModal = false;
                this.load();
                this.loadStats();
            },
            error: (err) => {
                this.isCreating = false;
                const msg = err?.error?.detail || 'No se pudo crear la tarea.';
                this.toast.error(msg);
                this.cdr.detectChanges();
            },
        });
    }

    // ============================================================
    // trackBy
    // ============================================================

    trackById(_i: number, item: PendingItem): number { return item.id; }
    trackByIdx(i: number): number { return i; }
    trackBySid(_i: number, s: SessionLite): number { return s.id; }
}
