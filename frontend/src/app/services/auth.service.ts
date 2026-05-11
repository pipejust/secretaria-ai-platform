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
                    if (response && response.access_token) {
                        localStorage.setItem('access_token', response.access_token);
                        // Confirmamos el tenant que el backend devolvió (puede
                        // diferir si el frontend mandó un slug equivocado).
                        if (response.tenant?.slug) {
                            this.tenants.setSlug(response.tenant.slug);
                        }
                        this.loadUserProfile();
                    }
                })
            );
    }

    changePassword(current_password: string, new_password: string): Observable<any> {
        return this.http.put(`${this.apiUrl}/password`, {
            current_password,
            new_password
        }, { headers: this.getAuthHeaders() });
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
