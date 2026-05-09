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

interface ReportData {
    period: 'week' | 'month';
    label: string;
    start: string;
    end: string;
    project_id: number | null;
    project_name: string;
    metrics: {
        sessions_count: number;
        action_items_total: number;
        action_items_by_status: Record<string, number>;
        completed_in_window: number;
        auto_dispatched: number;
    };
    sessions: Array<{ id: number; title: string; date: string; status: string; project_name: string }>;
    top_owners: Array<{ owner: string; count: number }>;
}

@Component({
    selector: 'app-reportes',
    standalone: true,
    imports: [CommonModule, FormsModule, RouterModule],
    templateUrl: './reportes.component.html',
    styleUrls: ['./reportes.component.css'],
})
export class ReportesComponent implements OnInit, OnDestroy {
    period: 'week' | 'month' = 'week';
    projectId: number | null = null;
    refDate: string = ''; // ISO yyyy-mm-dd. Vacío = hoy.

    data: ReportData | null = null;
    isLoading = false;
    isDownloading = false;
    projects: Array<{ id: number; name: string }> = [];

    private readonly destroy$ = new Subject<void>();

    constructor(
        private http: HttpClient,
        private authService: AuthService,
        private toast: ToastService,
        private cdr: ChangeDetectorRef,
    ) {}

    ngOnInit(): void {
        this.loadProjects();
        this.load();
    }

    ngOnDestroy(): void {
        this.destroy$.next();
        this.destroy$.complete();
    }

    loadProjects(): void {
        this.http.get<any[]>(`${environment.apiUrl}/api/projects/`).subscribe({
            next: (data) => {
                this.projects = (data || []).map(p => ({ id: p.id, name: p.name }));
                this.cdr.detectChanges();
            },
            error: () => { /* no rompe el reporte */ }
        });
    }

    load(): void {
        this.isLoading = true;
        const params = this.buildParams();
        const headers = this.authService.getAuthHeaders();
        this.http
            .get<ReportData>(`${environment.apiUrl}/api/reports/data?${params}`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (res) => {
                    this.data = res;
                    this.isLoading = false;
                    this.cdr.detectChanges();
                },
                error: (err) => {
                    const detail = err?.error?.detail || 'No se pudo generar el reporte.';
                    this.toast.error(detail);
                    this.isLoading = false;
                    this.cdr.detectChanges();
                },
            });
    }

    downloadPdf(): void {
        this.isDownloading = true;
        const params = this.buildParams();
        const headers = this.authService.getAuthHeaders();
        this.http
            .get(`${environment.apiUrl}/api/reports/pdf?${params}`, {
                headers, responseType: 'blob',
            })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (blob: Blob) => {
                    this.isDownloading = false;
                    const url = window.URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    const safe = (this.data?.label || 'reporte').replace(/[^a-z0-9]/gi, '_').slice(0, 60);
                    a.download = `Reporte_Notiva_${safe}.pdf`;
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    window.URL.revokeObjectURL(url);
                    this.toast.success('Reporte PDF descargado.');
                },
                error: () => {
                    this.isDownloading = false;
                    this.toast.error('No se pudo descargar el PDF.');
                },
            });
    }

    private buildParams(): string {
        const p: string[] = [`period=${this.period}`];
        if (this.refDate) p.push(`ref=${encodeURIComponent(this.refDate)}`);
        if (this.projectId) p.push(`project_id=${this.projectId}`);
        return p.join('&');
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
        } as Record<string, string>)[s] || s;
    }
}
