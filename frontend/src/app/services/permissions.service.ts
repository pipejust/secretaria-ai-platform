import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { BehaviorSubject, Observable, of } from 'rxjs';
import { catchError, tap } from 'rxjs/operators';
import { AuthService } from './auth.service';
import { environment } from '../../environments/environment';

export type PermissionAction = 'view' | 'create' | 'edit' | 'delete' | 'manage' | 'export';

export interface MePermissions {
    role: string | null;
    is_superadmin: boolean;
    permissions: Record<string, string[]>;
}

/**
 * PermissionsService — fetches the current user's effective permissions from
 * `/auth/me/permissions` and caches them in a BehaviorSubject so any component
 * can synchronously query `can(module, action)` after the initial load.
 *
 * - Admin and superadmin always see everything.
 * - Otherwise the backend returns `{module: [actions...]}` and we use it as
 *   the source of truth.
 */
@Injectable({ providedIn: 'root' })
export class PermissionsService {
    private readonly _state$ = new BehaviorSubject<MePermissions | null>(null);
    readonly state$ = this._state$.asObservable();

    constructor(private http: HttpClient, private auth: AuthService) {}

    /** Trigger a fetch (or refetch) from the backend. */
    load(): Observable<MePermissions> {
        const headers = this.auth.getAuthHeaders();
        return this.http
            .get<MePermissions>(`${environment.apiUrl}/auth/me/permissions`, { headers })
            .pipe(
                tap((res) => this._state$.next(res || { role: null, is_superadmin: false, permissions: {} })),
                catchError(() => {
                    // Si falla, no bloqueamos navegación: comportamiento neutro
                    // (sin permisos especiales). Cuando la sesión expira el
                    // interceptor de auth se encarga de redirigir al login.
                    const empty: MePermissions = { role: null, is_superadmin: false, permissions: {} };
                    this._state$.next(empty);
                    return of(empty);
                }),
            );
    }

    /** Snapshot of the current cached state. */
    get snapshot(): MePermissions | null {
        return this._state$.value;
    }

    /** Synchronous permission check. Returns true if the user can perform
     *  `action` on `module`. Admin/superadmin always returns true. */
    can(module: string, action: PermissionAction = 'view'): boolean {
        const s = this._state$.value;
        if (!s) return false;
        if (s.is_superadmin) return true;
        if ((s.role || '').toLowerCase() === 'admin') return true;
        const allowed = s.permissions?.[module] || [];
        return allowed.includes(action);
    }

    /** True if the user can see ANY action of a module (used to hide nav). */
    canSeeModule(module: string): boolean {
        const s = this._state$.value;
        if (!s) return false;
        if (s.is_superadmin) return true;
        if ((s.role || '').toLowerCase() === 'admin') return true;
        const allowed = s.permissions?.[module] || [];
        return allowed.length > 0;
    }

    clear(): void {
        this._state$.next(null);
    }
}
