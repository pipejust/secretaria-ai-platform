import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { BehaviorSubject, Observable, of, timer } from 'rxjs';
import { catchError, switchMap, tap } from 'rxjs/operators';
import { environment } from '../../environments/environment';
import { AuthService } from './auth.service';

/** Una notificación in-app — match exacto del shape que devuelve
 *  GET /api/notifications/. */
export interface AcnNotification {
    id: number;
    kind: string;
    title: string;
    body: string;
    link_to: string | null;
    entity_type: string | null;
    entity_id: number | null;
    is_read: boolean;
    read_at: string | null;
    created_at: string;
}

interface NotificationListResponse {
    items: AcnNotification[];
    unread_count: number;
}

/**
 * Centraliza toda la interacción con /api/notifications:
 *  - polling del unread_count (badge del topbar)
 *  - lista paginada para el panel
 *  - mark-as-read individual + return del link_to para navegar
 *  - mark-all-read
 *
 * Cualquier componente que necesite el contador puede suscribirse a
 * `unreadCount$` — emite cada vez que hay un cambio (poll, mark-read,
 * mark-all-read).
 */
@Injectable({ providedIn: 'root' })
export class NotificationService {
    private readonly http = inject(HttpClient);
    private readonly auth = inject(AuthService);
    private readonly baseUrl = `${environment.apiUrl}/api/notifications`;

    /** Stream del contador no-leído, observable por la UI. */
    private readonly _unreadCount$ = new BehaviorSubject<number>(0);
    readonly unreadCount$ = this._unreadCount$.asObservable();

    /** Polling cada 60s del unread_count. Llamarlo una sola vez (desde
     *  el AdminLayout); se auto-renueva mientras el usuario esté logueado.
     *  Si el endpoint falla (no auth, network), no rompe — emite 0 y reintenta
     *  en el próximo ciclo. */
    startPolling(intervalMs = 60_000): Observable<number> {
        return timer(0, intervalMs).pipe(
            switchMap(() => this.fetchUnreadCount()),
        );
    }

    /** Actualiza el contador one-shot (sin esperar el polling). */
    refreshUnreadCount(): void {
        this.fetchUnreadCount().subscribe();
    }

    private fetchUnreadCount(): Observable<number> {
        if (!this.auth.token) {
            this._unreadCount$.next(0);
            return of(0);
        }
        return this.http
            .get<{ unread_count: number }>(`${this.baseUrl}/unread_count`)
            .pipe(
                tap((r) => this._unreadCount$.next(r?.unread_count ?? 0)),
                catchError(() => {
                    // No spam de errors si el endpoint falla — la UI sólo
                    // muestra 0 hasta el próximo intento.
                    return of({ unread_count: 0 });
                }),
                switchMap((r) => of(r.unread_count)),
            );
    }

    /** Lista las últimas N notificaciones (default 20). Refresca el contador. */
    list(onlyUnread = false, limit = 20): Observable<NotificationListResponse> {
        const params: Record<string, string> = { limit: String(limit) };
        if (onlyUnread) params['only_unread'] = 'true';
        const search = new URLSearchParams(params).toString();
        return this.http
            .get<NotificationListResponse>(`${this.baseUrl}/?${search}`)
            .pipe(
                tap((r) => this._unreadCount$.next(r?.unread_count ?? 0)),
            );
    }

    /** Marca una como leída. Devuelve `link_to` para que el caller navegue. */
    markRead(id: number): Observable<{ id: number; link_to: string | null }> {
        return this.http
            .post<{ id: number; is_read: boolean; read_at: string; link_to: string | null }>(
                `${this.baseUrl}/${id}/read`,
                {},
            )
            .pipe(tap(() => this.refreshUnreadCount()));
    }

    markAllRead(): Observable<{ marked: number }> {
        return this.http
            .post<{ marked: number }>(`${this.baseUrl}/mark_all_read`, {})
            .pipe(tap(() => this._unreadCount$.next(0)));
    }
}
