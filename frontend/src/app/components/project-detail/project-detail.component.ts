import { Component, OnInit, OnDestroy, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, RouterModule } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { Subject } from 'rxjs';
import { takeUntil } from 'rxjs/operators';
import { AuthService } from '../../services/auth.service';
import { ToastService } from '../../services/toast.service';
import { environment } from '../../../environments/environment';

interface ProjectDetail {
    id: number;
    name: string;
    description: string;
    is_active: boolean;
    owner_user: { id: number; full_name: string; email: string } | null;
    auto_dispatch_enabled: boolean | null;
    auto_dispatch_timeout_hours: number | null;
}

interface Metrics {
    total_sessions: number;
    active_action_items: number;
    overdue: number;
    upcoming_7d: number;
    completed_action_items: number;
    contacts_count: number;
    routings_active: number;
}

interface SessionLite {
    id: number;
    title: string;
    date: string;
    status: string;
    language: string;
}

interface RecentDecision {
    session_id: number;
    session_title: string;
    session_date: string;
    text: string;
}

interface ActionItemLite {
    id: number;
    session_id: number;
    title: string;
    owner_name: string;
    owner_email: string;
    due_date: string | null;
    status: string;
    is_overdue: boolean;
    is_upcoming: boolean;
}

interface ContactLite {
    id: number;
    name: string;
    email: string;
    role: string;
    entity: string | null;
}

interface DashboardResponse {
    project: ProjectDetail;
    metrics: Metrics;
    last_sessions: SessionLite[];
    recent_decisions: RecentDecision[];
    active_action_items: ActionItemLite[];
    contacts: ContactLite[];
}

@Component({
    selector: 'app-project-detail',
    standalone: true,
    imports: [CommonModule, RouterModule],
    templateUrl: './project-detail.component.html',
    styleUrls: ['./project-detail.component.css'],
})
export class ProjectDetailComponent implements OnInit, OnDestroy {
    data: DashboardResponse | null = null;
    isLoading = true;
    private readonly destroy$ = new Subject<void>();

    constructor(
        private route: ActivatedRoute,
        private http: HttpClient,
        private authService: AuthService,
        private toast: ToastService,
        private cdr: ChangeDetectorRef,
    ) {}

    ngOnInit(): void {
        this.route.paramMap
            .pipe(takeUntil(this.destroy$))
            .subscribe(params => {
                const id = params.get('id');
                if (id) this.load(parseInt(id, 10));
            });
    }

    ngOnDestroy(): void {
        this.destroy$.next();
        this.destroy$.complete();
    }

    load(projectId: number): void {
        this.isLoading = true;
        const headers = this.authService.getAuthHeaders();
        this.http
            .get<DashboardResponse>(`${environment.apiUrl}/api/projects/${projectId}/dashboard`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (res) => {
                    this.data = res;
                    this.isLoading = false;
                    this.cdr.detectChanges();
                },
                error: (err) => {
                    const detail = err?.error?.detail || 'No se pudo cargar el detalle del proyecto.';
                    this.toast.error(detail);
                    this.isLoading = false;
                    this.cdr.detectChanges();
                },
            });
    }

    formatDate(d: string | null | undefined): string {
        if (!d) return '—';
        let val: any = d;
        if (typeof val === 'string' && !isNaN(Number(val))) val = Number(val);
        const dt = new Date(val);
        if (isNaN(dt.getTime())) return String(d);
        return dt.toLocaleDateString('es-CO', { day: '2-digit', month: '2-digit', year: 'numeric' });
    }

    statusLabel(s: string): string {
        return ({
            pending: 'Pendiente', processing: 'Procesando IA',
            approved: 'Aprobada', completed: 'Completada', processed: 'Procesada',
            done: 'Hecha', blocked: 'Bloqueada', cancelled: 'Cancelada',
        } as Record<string, string>)[s] || s;
    }

    trackById(_i: number, item: { id: number }): number {
        return item.id;
    }

    /** RecentDecision no tiene `id`, así que usamos session_id como llave. */
    trackByDecision(_i: number, dec: RecentDecision): number {
        return dec.session_id;
    }
}
