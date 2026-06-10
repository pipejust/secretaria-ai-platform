import { Injectable } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Observable, forkJoin, of } from 'rxjs';
import { catchError, map } from 'rxjs/operators';
import { AuthService } from './auth.service';
import { environment } from '../../environments/environment';

/** Providers cuya configuración es per-user (las credenciales viven en
 *  la fila IntegrationSetting con user_id NOT NULL). Cualquier provider
 *  fuera de este set es per-tenant (smtp, fireflies, branding, ...).
 *  Debe coincidir con backend/models.py::PER_USER_INTEGRATION_PROVIDERS. */
const PER_USER_PROVIDERS = new Set<string>([
    'trello', 'jira', 'clickup', 'azure',
    'google', 'microsoft',
]);

@Injectable({
    providedIn: 'root'
})
export class SettingsService {
    private tenantUrl = `${environment.apiUrl}/api/settings`;
    private userUrl = `${environment.apiUrl}/api/settings/me`;

    constructor(private http: HttpClient, private authService: AuthService) { }

    /** Devuelve la configuración combinada (per-tenant + per-user del
     *  current_user) como un objeto plano `{ provider_name: config }` —
     *  formato compatible con el viejo `/api/settings` para que los
     *  componentes consumidores no tengan que distinguir el alcance.
     *
     *  Si el caller no es admin, el GET per-tenant devuelve 403 y lo
     *  tratamos como `{}` — el user verá únicamente sus propios per-user,
     *  que es el comportamiento esperado (la UI de admin maneja la pieza
     *  per-tenant aparte).
     */
    getSettings(): Observable<any> {
        const headers = this.authService.getAuthHeaders();
        const tenant$ = this.http.get<any>(this.tenantUrl, { headers }).pipe(
            catchError((err: HttpErrorResponse) => {
                // 403 = user no-admin sin permiso al per-tenant. No es error
                // de la UI, solo significa "no aplica para ti".
                if (err.status === 403) return of({});
                throw err;
            }),
        );
        const user$ = this.http.get<any>(this.userUrl, { headers }).pipe(
            catchError(() => of({})),
        );
        return forkJoin([tenant$, user$]).pipe(
            map(([tenant, user]) => ({ ...(tenant || {}), ...(user || {}) })),
        );
    }

    /** Guarda configuración. Divide el payload por provider — per-user
     *  va a POST /api/settings/me (sus credenciales personales),
     *  per-tenant va a POST /api/settings (resend/fireflies/branding,
     *  solo admin). Espera a ambas para reportar éxito al caller.
     *
     *  Si solo viene un lado, omitimos el otro round-trip para no
     *  generar requests vacíos.
     */
    saveSettings(payload: any): Observable<any> {
        const headers = this.authService.getAuthHeaders();
        const perUser: any = {};
        const perTenant: any = {};
        for (const [provider, cfg] of Object.entries(payload || {})) {
            if (PER_USER_PROVIDERS.has(provider)) {
                perUser[provider] = cfg;
            } else {
                perTenant[provider] = cfg;
            }
        }

        const calls: Observable<any>[] = [];
        if (Object.keys(perTenant).length) {
            calls.push(this.http.post<any>(this.tenantUrl, perTenant, { headers }));
        }
        if (Object.keys(perUser).length) {
            calls.push(this.http.post<any>(this.userUrl, perUser, { headers }));
        }
        if (!calls.length) return of({ status: 'noop' });

        return forkJoin(calls).pipe(
            map(() => ({ status: 'success' })),
        );
    }
}
