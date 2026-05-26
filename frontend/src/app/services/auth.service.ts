import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { BehaviorSubject, Observable, tap } from 'rxjs';
import { environment } from '../../environments/environment';
import { TenantService } from './tenant.service';

@Injectable({
    providedIn: 'root'
})
export class AuthService {
    private apiUrl = `${environment.apiUrl}/auth`;
    private currentUserSubject = new BehaviorSubject<any>(null);
    public currentUser$ = this.currentUserSubject.asObservable();
    private tenants = inject(TenantService);

    constructor(private http: HttpClient) {
        this.loadUserFromStorage();
    }

    get token(): string | null {
        return localStorage.getItem('access_token');
    }

    get currentUserValue(): any {
        return this.currentUserSubject.value;
    }

    login(email: string, password: string, tenantSlug?: string): Observable<any> {
        // Multi-tenant: el slug se manda en el body (canónico) y también queda
        // como header gracias al tenantInterceptor. El backend usa el del body
        // si viene, sino cae al header, sino al default ('acten').
        const slug = tenantSlug || this.tenants.slug();
        const body = {
            username: email,
            password: password,
            tenant_slug: slug,
        };

        const headers = new HttpHeaders({ 'Content-Type': 'application/json' });

        return this.http.post<any>(`${this.apiUrl}/login`, body, { headers })
            .pipe(
                tap(response => {
                    if (response?.two_factor_required) {
                        // El caller debe redirigir al paso de verificación 2FA.
                        // No persistimos token todavía.
                        return;
                    }
                    if (response && response.access_token) {
                        localStorage.setItem('access_token', response.access_token);
                        if (response.tenant?.slug) {
                            this.tenants.setSlug(response.tenant.slug);
                        }
                        this.loadUserProfile();
                    }
                })
            );
    }

    /** Completa el login cuando el backend pidió 2FA. */
    verifyLogin2FA(email: string, code: string, tenantSlug?: string): Observable<any> {
        const slug = tenantSlug || this.tenants.slug();
        return this.http.post<any>(`${this.apiUrl}/login/2fa-verify`, {
            username: email,
            code,
            tenant_slug: slug,
        }).pipe(
            tap((response) => {
                if (response?.access_token) {
                    localStorage.setItem('access_token', response.access_token);
                    if (response.tenant?.slug) this.tenants.setSlug(response.tenant.slug);
                    this.loadUserProfile();
                }
            }),
        );
    }

    changePassword(current_password: string, new_password: string): Observable<any> {
        return this.http.put(`${this.apiUrl}/password`, {
            current_password,
            new_password
        }, { headers: this.getAuthHeaders() });
    }

    /** Cambio de password vía POST /auth/me/change-password. Si el user tiene
     *  must_change_password=true, omitir current_password (el backend acepta).
     *  Devuelve { ok: true } al éxito. */
    changePasswordForced(new_password: string, current_password?: string): Observable<any> {
        const body: any = { new_password };
        if (current_password) body.current_password = current_password;
        return this.http.post(`${this.apiUrl}/me/change-password`, body, {
            headers: this.getAuthHeaders(),
        });
    }

    /** Actualiza los campos editables del perfil (PUT /auth/me).
     *  Devuelve el shape completo del perfil y refresca el currentUser$ stream. */
    updateProfile(payload: Partial<{
        full_name: string;
        phone: string;
        position: string;
        department: string;
        location: string;
        bio: string;
        avatar_url: string;
    }>): Observable<any> {
        return this.http.put<any>(`${this.apiUrl}/me`, payload).pipe(
            tap((user) => this.currentUserSubject.next(user)),
        );
    }

    /** Actualiza las preferencias de notificación (PUT /auth/me/notifications). */
    updateNotificationPrefs(payload: Partial<{
        email_enabled: boolean;
        push_enabled: boolean;
        meeting_reminders: boolean;
        task_assigned: boolean;
        session_processed: boolean;
        weekly_report: boolean;
        security_alerts: boolean;
    }>): Observable<any> {
        return this.http.put<any>(`${this.apiUrl}/me/notifications`, payload).pipe(
            tap((notifications) => {
                const u = this.currentUserSubject.value;
                if (u) this.currentUserSubject.next({ ...u, notifications });
            }),
        );
    }

    /** Devuelve las últimas N entradas del audit log del propio usuario. */
    getMyActivity(limit = 20): Observable<{ items: Array<{
        id: number;
        action: string;
        label: string;
        icon: string;
        resource_type: string | null;
        resource_id: string | null;
        created_at: string;
        ip: string | null;
    }>; total: number }> {
        return this.http.get<any>(`${this.apiUrl}/me/activity?limit=${limit}`);
    }

    /** Inicia el flujo de activación 2FA (genera código + envía email). */
    init2FA(): Observable<{ status: string; email: string; ttl_minutes: number }> {
        return this.http.post<any>(`${this.apiUrl}/me/2fa/init`, {});
    }

    /** Confirma el código de activación 2FA. */
    confirm2FA(code: string): Observable<any> {
        return this.http.post<any>(`${this.apiUrl}/me/2fa/confirm`, { code }).pipe(
            tap((user) => this.currentUserSubject.next(user)),
        );
    }

    /** Desactiva 2FA (requiere contraseña). */
    disable2FA(password: string): Observable<any> {
        return this.http.request<any>('DELETE', `${this.apiUrl}/me/2fa`, {
            body: { password },
        }).pipe(
            tap((user) => this.currentUserSubject.next(user)),
        );
    }

    /** Sube un avatar (multipart/form-data). */
    uploadAvatar(file: File): Observable<any> {
        const fd = new FormData();
        fd.append('file', file);
        return this.http.post<any>(`${this.apiUrl}/me/avatar`, fd).pipe(
            tap((user) => this.currentUserSubject.next(user)),
        );
    }

    /** Elimina el avatar actual. */
    deleteAvatar(): Observable<any> {
        return this.http.delete<any>(`${this.apiUrl}/me/avatar`).pipe(
            tap((user) => this.currentUserSubject.next(user)),
        );
    }

    logout() {
        localStorage.removeItem('access_token');
        this.currentUserSubject.next(null);
        // Force fully reload en caso extremo o solo routing limpio
        setTimeout(() => {
            // Forzar en frontend refresh para evitar bugs en menús colapsados state
            window.location.href = '/login';
        }, 100);
    }

    loadUserProfile() {
        if (!this.token) return;

        this.http.get<any>(`${this.apiUrl}/me`).subscribe({
            next: (user) => {
                this.currentUserSubject.next(user);
            },
            error: (err) => {
                // Solo desloguear si es explícitamente un error de token inválido (401)
                // Esto previene que una caída temporal de Render o Timeouts borren la sesión.
                if (err.status === 401) {
                    this.logout();
                } else {
                    console.error('No se pudo cargar el perfil, backend no disponible temporalmente', err);
                }
            }
        });
    }

    private loadUserFromStorage() {
        if (this.token) {
            this.loadUserProfile();
        }
    }

    // Use to get headers in other services
    getAuthHeaders(): HttpHeaders {
        return new HttpHeaders({
            'Authorization': `Bearer ${this.token || ''}`
        });
    }
}
