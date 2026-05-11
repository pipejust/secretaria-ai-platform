import { Injectable, computed, inject, signal } from '@angular/core';

/**
 * Multi-tenant: resuelve el slug del tenant activo en el frontend.
 *
 * Reglas (en orden de prioridad):
 *   1. URL path `/t/{slug}/...`            → tenant explícito en la URL.
 *   2. Hostname custom (`nexura.acten.app`) → tenant inferido del subdominio.
 *   3. Hostname custom guardado en backend  → tenant inferido del dominio
 *      completo (esto requiere round-trip; ya lo hace BrandingService al
 *      llamar al endpoint público con el header X-Tenant-Slug derivado del
 *      subdominio o vacío).
 *   4. Fallback: 'acten' (tenant default).
 *
 * Persistimos el slug elegido en `localStorage` para que sobreviva refresh
 * y para que el AuthService lo mande en el body del login y los
 * interceptores HTTP lo añadan como header `X-Tenant-Slug` en TODA petición.
 */

const STORAGE_KEY = 'tenant_slug';
const DEFAULT_SLUG = 'acten';
const PATH_RE = /^\/t\/([a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?)(\/|$)/;

@Injectable({ providedIn: 'root' })
export class TenantService {
  /** Slug activo. Cualquier componente puede leerlo reactivamente. */
  readonly slug = signal<string>(this._initialSlug());

  /** True si el slug fue forzado por URL/host (no es solo localStorage). */
  readonly isExplicit = signal<boolean>(this._wasExplicitlyResolved());

  readonly isDefault = computed(() => this.slug() === DEFAULT_SLUG);

  /**
   * Setea explícitamente el slug. Útil al loguear contra un tenant nuevo,
   * o al super-admin al cambiar de tenant para administrar.
   */
  setSlug(slug: string): void {
    const norm = (slug || '').trim().toLowerCase() || DEFAULT_SLUG;
    this.slug.set(norm);
    try { localStorage.setItem(STORAGE_KEY, norm); } catch {}
  }

  clear(): void {
    this.slug.set(DEFAULT_SLUG);
    try { localStorage.removeItem(STORAGE_KEY); } catch {}
  }

  // ------------------------------------------------------------------
  // helpers privados
  // ------------------------------------------------------------------

  private _initialSlug(): string {
    if (typeof window === 'undefined') return DEFAULT_SLUG;

    // 1) URL path /t/{slug}/...
    const m = PATH_RE.exec(window.location.pathname);
    if (m) {
      const fromPath = m[1];
      try { localStorage.setItem(STORAGE_KEY, fromPath); } catch {}
      return fromPath;
    }

    // 2) Subdominio: si hostname.split('.').length >= 3, asumimos
    //    {slug}.dominio.tld. Skip si el primer segmento es genérico (www, app).
    const host = window.location.hostname;
    const parts = host.split('.');
    if (parts.length >= 3 && !['www', 'app', 'admin', 'localhost'].includes(parts[0])) {
      const fromSub = parts[0].toLowerCase();
      try { localStorage.setItem(STORAGE_KEY, fromSub); } catch {}
      return fromSub;
    }

    // 3) Persistido de una sesión anterior
    try {
      const stored = (localStorage.getItem(STORAGE_KEY) || '').trim().toLowerCase();
      if (stored) return stored;
    } catch {}

    // 4) Default
    return DEFAULT_SLUG;
  }

  private _wasExplicitlyResolved(): boolean {
    if (typeof window === 'undefined') return false;
    if (PATH_RE.test(window.location.pathname)) return true;
    const parts = window.location.hostname.split('.');
    if (parts.length >= 3 && !['www', 'app', 'admin', 'localhost'].includes(parts[0])) return true;
    return false;
  }
}
