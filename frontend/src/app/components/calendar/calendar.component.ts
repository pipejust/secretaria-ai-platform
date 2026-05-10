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

interface CalAccount {
    id: number;
    provider: 'google' | 'microsoft';
    account_email: string;
    is_active: boolean;
    created_at: string;
}

interface CalEvent {
    id: number;
    title: string;
    start_at: string;
    end_at: string;
    attendees: string[];
    meeting_url: string | null;
    session_id: number | null;
}

@Component({
    selector: 'app-calendar',
    standalone: true,
    imports: [CommonModule, FormsModule, RouterModule],
    templateUrl: './calendar.component.html',
    styleUrls: ['./calendar.component.css'],
})
export class CalendarComponent implements OnInit, OnDestroy {
    accounts: CalAccount[] = [];
    events: CalEvent[] = [];
    isSyncing = false;
    isLoading = true;
    private readonly destroy$ = new Subject<void>();

    constructor(
        private http: HttpClient,
        private auth: AuthService,
        private toast: ToastService,
        private cdr: ChangeDetectorRef,
    ) {}

    ngOnInit(): void {
        this.loadAccounts();
        this.loadEvents();
    }

    ngOnDestroy(): void {
        this.destroy$.next();
        this.destroy$.complete();
    }

    loadAccounts(): void {
        this.http.get<CalAccount[]>(`${environment.apiUrl}/api/calendar/accounts`,
            { headers: this.auth.getAuthHeaders() })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (data) => { this.accounts = data || []; this.cdr.detectChanges(); },
                error: () => this.toast.error('No pude listar tus cuentas conectadas.'),
            });
    }

    loadEvents(): void {
        this.isLoading = true;
        this.http.get<{events: CalEvent[]; linked_accounts: number}>(
            `${environment.apiUrl}/api/calendar/upcoming?days=7`,
            { headers: this.auth.getAuthHeaders() })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (res) => {
                    this.events = res.events || [];
                    this.isLoading = false;
                    this.cdr.detectChanges();
                },
                error: () => {
                    this.events = [];
                    this.isLoading = false;
                    this.cdr.detectChanges();
                },
            });
    }

    connect(provider: 'google' | 'microsoft'): void {
        this.http.get<{url: string; state: string}>(
            `${environment.apiUrl}/api/calendar/${provider}/auth_url`,
            { headers: this.auth.getAuthHeaders() })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (res) => { window.location.href = res.url; },
                error: (err) => {
                    const detail = err?.error?.detail || `OAuth de ${provider} no está configurado en el backend.`;
                    this.toast.error(detail);
                },
            });
    }

    disconnect(account: CalAccount): void {
        if (!confirm(`¿Desconectar la cuenta ${account.account_email}?`)) return;
        this.http.delete(`${environment.apiUrl}/api/calendar/accounts/${account.id}`,
            { headers: this.auth.getAuthHeaders() })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: () => { this.toast.success('Cuenta desconectada.'); this.loadAccounts(); this.loadEvents(); },
                error: () => this.toast.error('No pude desconectar.'),
            });
    }

    syncNow(): void {
        this.isSyncing = true;
        this.http.post<{events_added: number}>(
            `${environment.apiUrl}/api/calendar/sync`, {},
            { headers: this.auth.getAuthHeaders() })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (res) => {
                    this.isSyncing = false;
                    this.toast.success(`${res.events_added} eventos nuevos sincronizados.`);
                    this.loadEvents();
                },
                error: () => { this.isSyncing = false; this.toast.error('Sincronización falló.'); this.cdr.detectChanges(); },
            });
    }

    formatDate(s: string): string {
        if (!s) return '—';
        const d = new Date(s);
        if (isNaN(d.getTime())) return s;
        return d.toLocaleString('es-CO', { dateStyle: 'short', timeStyle: 'short' });
    }

    trackById(_i: number, item: { id: number }): number { return item.id; }
}
