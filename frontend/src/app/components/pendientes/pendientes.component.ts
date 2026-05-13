import { Component, OnInit, OnDestroy, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
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
    status: 'pending' | 'done' | 'blocked' | 'cancelled';
    completed_at: string | null;
    is_approved: boolean;
    project_name: string;
    bucket: 'vencido' | 'proximo' | 'pendiente' | 'sin_fecha' | 'completado' | 'cancelado' | 'bloqueado';
}

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
    icon: 'open' | 'progress' | 'week' | 'overdue' | 'done';
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
    projectFilter = '';
    priorityFilter = '';
    statusFilter = '';
    dueFilter = '';

    /** Tab visual de segmentación. */
    activeTab: TabKey = 'todas';

    /** Paginación local (12 por página, como el mockup). */
    pageSize = 12;
    currentPage = 1;

    /** IDs marcados con checkbox (para selección masiva). */
    selectedIds = new Set<number>();

    private readonly destroy$ = new Subject<void>();

    /** Paleta de colores para el donut de carga de trabajo (orden estable). */
    private readonly donutPalette = ['#155EEF', '#F59E0B', '#7C3AED', '#10B981', '#EF4444', '#94A3B8'];

    constructor(
        private http: HttpClient,
        private authService: AuthService,
        private toast: ToastService,
        private cdr: ChangeDetectorRef,
    ) {}

    ngOnInit(): void {
        this.loadStats();
        this.load();
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
        // "Mis tareas" filtra por owner = usuario logueado.
        if (tab === 'mias') {
            const me = this.authService.currentUserValue?.full_name || '';
            this.ownerFilter = me;
        } else if (this.activeTab !== 'mias') {
            // Si salimos de Mis tareas, conservamos el filtro escrito.
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

    // ============================================================
    // Derivados visuales (priority, status, progress, code)
    // ============================================================

    /** Genera un código tipo "TAS-1024" a partir del id. Se usa solo
     *  para la línea secundaria visual; no es persistido. */
    taskCode(item: PendingItem): string {
        return `TAS-${String(item.id).padStart(4, '0')}`;
    }

    /** Prioridad derivada del bucket. ActionItem no almacena priority. */
    priority(item: PendingItem): 'alta' | 'media' | 'baja' {
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
        return [
            { key: 'open',     label: 'Tareas abiertas',    value: open,                icon: 'open',     delta: this._deltaText(open, 'nuevas esta semana') },
            { key: 'progress', label: 'En progreso',        value: c['proximo'] || 0,    icon: 'progress', delta: this._deltaText(c['proximo'] || 0, 'esta semana') },
            { key: 'week',     label: 'Vencen esta semana', value: c['proximo'] || 0,    icon: 'week',     delta: 'Próximas 7 días' },
            { key: 'overdue',  label: 'Vencidas',           value: c['vencido'] || 0,    icon: 'overdue',  delta: this._deltaText(c['vencido'] || 0, 'requieren acción') },
            { key: 'done',     label: 'Completadas',        value: c['completado'] || 0, icon: 'done',     delta: this._deltaText(c['completado'] || 0, 'cerradas') },
        ];
    }
    private _deltaText(n: number, suffix: string): string {
        if (!n) return `Sin cambios — ${suffix}`;
        return `${n} ${suffix}`;
    }

    // ============================================================
    // Listas filtradas (para tabla, pagina, panel)
    // ============================================================

    /** Lista filtrada (tabla principal) según los filtros del header. */
    get filteredItems(): PendingItem[] {
        const q = this.searchQuery.trim().toLowerCase();
        const proj = this.projectFilter.trim().toLowerCase();
        const pri = this.priorityFilter.trim().toLowerCase();
        const st = this.statusFilter.trim().toLowerCase();

        return this.items.filter(it => {
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

    /** Slices del donut "Carga de trabajo" + porcentajes. */
    get workloadSlices(): DonutSlice[] {
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
        // "Otros" = active_total − suma de top 5
        const restCount = (this.stats?.active_total || 0) - total;
        if (restCount > 0) {
            out.push({
                owner: 'Otros',
                count: restCount,
                pct: tot > 0 ? Math.round((restCount / tot) * 100) : 0,
                color: this.donutPalette[5],
                initials: '··',
            });
        }
        return out;
    }

    get workloadTotal(): number {
        return this.stats?.active_total || 0;
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
    // trackBy
    // ============================================================

    trackById(_i: number, item: PendingItem): number { return item.id; }
    trackByIdx(i: number): number { return i; }
}
