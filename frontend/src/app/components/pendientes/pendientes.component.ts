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
    bucket: BucketFilter = 'activos';
    ownerFilter = '';
    private readonly destroy$ = new Subject<void>();

    readonly bucketLabels: Record<string, string> = {
        activos: 'Activos',
        vencido: 'Vencidos',
        proximo: 'Próximos (≤7d)',
        pendiente: 'Pendientes',
        sin_fecha: 'Sin fecha',
        bloqueado: 'Bloqueados',
        completado: 'Completados',
        cancelado: 'Cancelados',
    };

    readonly bucketBadgeColor: Record<string, string> = {
        vencido: '#ef4444',
        proximo: '#f59e0b',
        pendiente: '#3b82f6',
        sin_fecha: '#6b7280',
        bloqueado: '#a855f7',
        completado: '#10b981',
        cancelado: '#9ca3af',
    };

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

    setBucket(b: BucketFilter): void {
        this.bucket = b;
        this.load();
    }

    load(): void {
        this.isLoading = true;
        const headers = this.authService.getAuthHeaders();
        const params: any = { bucket: this.bucket };
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
                    this.items = res.items;
                    this.isLoading = false;
                    this.cdr.detectChanges();
                },
                error: () => {
                    this.toast.error('No se pudieron cargar los pendientes.');
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
                error: () => {
                    /* silencio: no es crítico para el listado */
                },
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

    statusLabel(s: string): string {
        return ({
            pending: 'Pendiente',
            done: 'Completada',
            blocked: 'Bloqueada',
            cancelled: 'Cancelada',
        } as Record<string, string>)[s] || s;
    }

    bucketLabel(b: string): string {
        return this.bucketLabels[b] || b;
    }

    bucketColor(b: string): string {
        return this.bucketBadgeColor[b] || '#6b7280';
    }

    formatDate(d: string | null): string {
        if (!d) return '—';
        try {
            const v = new Date(d);
            if (isNaN(v.getTime())) return d;
            return v.toLocaleDateString('es-CO', { day: '2-digit', month: '2-digit', year: 'numeric' });
        } catch {
            return d;
        }
    }

    trackById(_i: number, item: PendingItem): number {
        return item.id;
    }
}
