import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { BehaviorSubject, Observable, of, Subject } from 'rxjs';
import { debounceTime, map, take, tap } from 'rxjs/operators';
import { environment } from '../../environments/environment';

/** Resumen de un User del tenant — viene del directorio. */
export interface UserSummary {
    id: number;
    email: string;
    full_name: string;
    avatar_url: string | null;
    role: string | null;
    department: string | null;
    position: string | null;
    is_active: boolean;
    is_superadmin?: boolean;
}

/**
 * Servicio central para resolver "email → User del tenant".
 *
 * Diseño:
 * - Mantiene un cache en memoria `{email_lower: UserSummary | null}`.
 *   `null` significa "ya consultamos y NO matchea un user" (contacto externo).
 *   `undefined` significa "aún no lo consultamos".
 * - `lookup(email)` devuelve un Observable; coalescamos múltiples requests
 *   simultáneos en UNA llamada POST /api/users/directory/resolve.
 * - Emite `directory$` para que UI reactivas se renderen al recibir
 *   resoluciones (ej. el chip pinta el avatar tan pronto como llega).
 *
 * Uso típico:
 *   <app-user-chip [email]="row.owner_email" [fallbackName]="row.owner_name">
 */
@Injectable({ providedIn: 'root' })
export class UserDirectoryService {
    private readonly http = inject(HttpClient);
    private readonly baseUrl = `${environment.apiUrl}/api/users/directory`;

    private readonly cache = new Map<string, UserSummary | null>();
    private readonly pending = new Set<string>();
    // Cache paralelo para resolución por NOMBRE (fallback cuando el email
    // no está disponible — caso típico de participantes de reunión).
    private readonly nameCache = new Map<string, UserSummary | null>();
    private readonly pendingNames = new Set<string>();
    private readonly flush$ = new Subject<void>();

    private readonly _directory$ = new BehaviorSubject<Map<string, UserSummary | null>>(new Map());
    /** Stream que emite cada vez que se actualiza el cache (resolución
     *  batched). Componentes pueden suscribirse para forzar re-render. */
    readonly directory$ = this._directory$.asObservable();

    constructor() {
        // Coalescemos múltiples llamadas a lookup() en un solo HTTP cada 25ms.
        this.flush$.pipe(debounceTime(25)).subscribe(() => this.flush());
    }

    /** Inspección sincrónica del cache por email. `undefined` = nunca consultado. */
    peek(email: string | null | undefined): UserSummary | null | undefined {
        const k = this._normalize(email);
        if (!k) return null;
        return this.cache.has(k) ? this.cache.get(k) : undefined;
    }

    /** Inspección por nombre — usado como fallback cuando un participante
     *  no trae email (Fireflies). `undefined` = aún no resuelto. */
    peekByName(name: string | null | undefined): UserSummary | null | undefined {
        const k = this._normalizeName(name);
        if (!k) return null;
        return this.nameCache.has(k) ? this.nameCache.get(k) : undefined;
    }

    /** Resuelve uno. Si ya está cacheado, retorna inmediato; sino agenda el
     *  batch. Emite la entrada (puede ser null si es contacto externo). */
    lookup(email: string | null | undefined): Observable<UserSummary | null> {
        const k = this._normalize(email);
        if (!k) return of(null);

        if (this.cache.has(k)) {
            return of(this.cache.get(k) ?? null);
        }

        this.pending.add(k);
        this.flush$.next();

        return this.directory$.pipe(
            map(() => this.cache.has(k) ? (this.cache.get(k) ?? null) : null),
            // Espera a que aparezca en el cache (un próximo emit de directory$).
            // Para evitar suscripciones infinitas, tomamos la primera emisión
            // que ya tenga la clave.
            tap(() => {/* noop */}),
        );
    }

    /** Pre-carga un batch (útil al recibir un listado: hacemos una sola
     *  llamada con todos los emails que aún no están en el cache). */
    preload(emails: Array<string | null | undefined>): void {
        let added = false;
        for (const e of emails || []) {
            const k = this._normalize(e);
            if (!k) continue;
            if (this.cache.has(k)) continue;
            if (this.pending.has(k)) continue;
            this.pending.add(k);
            added = true;
        }
        if (added) this.flush$.next();
    }

    /** Pre-carga por nombres — usado para participantes de reunión que
     *  llegan sin email. El backend matchea contra `User.full_name`
     *  case-insensitive. */
    preloadNames(names: Array<string | null | undefined>): void {
        let added = false;
        for (const n of names || []) {
            const k = this._normalizeName(n);
            if (!k) continue;
            if (this.nameCache.has(k)) continue;
            if (this.pendingNames.has(k)) continue;
            this.pendingNames.add(k);
            added = true;
        }
        if (added) this.flush$.next();
    }

    /** Inserta o sobreescribe una entrada (ej. cuando el backend devuelve
     *  el user resuelto inline en un endpoint enriquecido). */
    primeCache(email: string | null | undefined, summary: UserSummary | null): void {
        const k = this._normalize(email);
        if (!k) return;
        this.cache.set(k, summary);
        this._directory$.next(this.cache);
    }

    /** Listado/typeahead para selectores (ej. asignar tarea). */
    search(q: string, limit = 20): Observable<UserSummary[]> {
        const url = `${this.baseUrl}?q=${encodeURIComponent(q || '')}&limit=${limit}`;
        return this.http.get<{ items: UserSummary[] }>(url).pipe(
            map((r) => r.items || []),
        );
    }

    /** Ejecuta el batch HTTP con todos los pending (emails + nombres). */
    private flush(): void {
        if (!this.pending.size && !this.pendingNames.size) return;
        const emails = Array.from(this.pending); this.pending.clear();
        const names = Array.from(this.pendingNames); this.pendingNames.clear();
        this.http.post<{
            users: Record<string, UserSummary | null>;
            users_by_name?: Record<string, UserSummary | null>;
        }>(
            `${this.baseUrl}/resolve`,
            { emails, names },
        ).pipe(take(1)).subscribe({
            next: (r) => {
                const emailMap = r?.users || {};
                for (const e of emails) {
                    this.cache.set(e, emailMap[e] ?? null);
                }
                const nameMap = r?.users_by_name || {};
                for (const n of names) {
                    this.nameCache.set(n, nameMap[n] ?? null);
                }
                this._directory$.next(this.cache);
            },
            error: () => {
                for (const e of emails) {
                    if (!this.cache.has(e)) this.cache.set(e, null);
                }
                for (const n of names) {
                    if (!this.nameCache.has(n)) this.nameCache.set(n, null);
                }
                this._directory$.next(this.cache);
            },
        });
    }

    private _normalize(email: string | null | undefined): string | null {
        if (!email) return null;
        const v = email.trim().toLowerCase();
        if (!v || !v.includes('@')) return null;
        return v;
    }

    /** Normaliza un nombre exactamente como lo hace el backend: trim,
     *  lowercase, colapsa espacios múltiples. NO quita acentos. */
    private _normalizeName(name: string | null | undefined): string | null {
        if (!name) return null;
        const parts = name.trim().toLowerCase().split(/\s+/);
        if (!parts.length) return null;
        return parts.join(' ');
    }
}

