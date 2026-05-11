import { Injectable, computed, inject, signal } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';

/**
 * White-label / Branding.
 *
 * La plataforma siempre se llama **Acten** (`platformName`, no editable).
 * Lo que cambia es la marca de la empresa cliente: nombre, tagline, logo,
 * colores, datos de contacto.
 *
 * Diseño:
 * - Cargamos la marca una vez al bootstrap vía APP_INITIALIZER (ver app.config.ts).
 * - Aplicamos los colores como CSS custom properties sobre `:root` para que
 *   todo el tema reaccione sin tocar componentes (botones, inputs, headers).
 * - Exponemos un signal `brand` que cualquier componente puede consumir.
 */

export interface Branding {
  platform_name: string;
  platform_tagline: string;
  company_name: string;
  company_tagline: string;
  company_email: string;
  company_address: string;
  company_website: string;
  company_phone: string;
  primary_color: string;
  secondary_color: string;
  accent_color: string;
  /** Logo "completo" — wordmark + monograma juntos. Usado en sidebar
   *  expandido, login, emails, header de exports. */
  logo_data_url: string;
  /** Imagologo / icono cuadrado — versión compacta de la marca para
   *  espacios chicos: sidebar colapsado, favicon visual, avatar default. */
  icon_data_url: string;
  favicon_data_url: string;
}

const DEFAULT_BRAND: Branding = {
  platform_name: 'Acten',
  platform_tagline: 'From conversation to clarity. From clarity to impact.',
  company_name: 'Acten',
  company_tagline: '',
  company_email: '',
  company_address: '',
  company_website: '',
  company_phone: '',
  // Paleta EXACTA del handoff Page 01 — Section 5 (Color System).
  primary_color: '#223148',
  secondary_color: '#1B7F67',
  accent_color: '#D9A441',
  logo_data_url: '',
  icon_data_url: '',
  favicon_data_url: '',
};

/** Defaults estáticos de Acten (assets en `/public/brand/`). Se usan si el
 *  tenant no tiene su propio logo subido. Mantenerlos sincronizados con
 *  los archivos físicos en `frontend/public/brand/`. */
const ACTEN_DEFAULT_LOGO_FULL = 'brand/acten-logo-full.png';
const ACTEN_DEFAULT_ICON      = 'brand/imagologo.png';

@Injectable({ providedIn: 'root' })
export class BrandingService {
  private http = inject(HttpClient);
  private apiUrl = `${environment.apiUrl}/api/branding`;

  /** Estado reactivo. Por defecto Acten — antes de que cargue la DB. */
  readonly brand = signal<Branding>(DEFAULT_BRAND);

  /** Helpers derivados que algunos componentes consumen directo. */
  readonly companyName = computed(() => this.brand().company_name || 'Acten');
  readonly platformName = computed(() => this.brand().platform_name || 'Acten');
  readonly logoUrl = computed(() => this.brand().logo_data_url || '');
  readonly hasLogo = computed(() => !!this.brand().logo_data_url);
  readonly iconUrl = computed(() => this.brand().icon_data_url || '');
  readonly hasIcon = computed(() => !!this.brand().icon_data_url);

  /** Resuelven SIEMPRE a algo renderizable: si el tenant subió su logo
   *  o icono lo usan; si no, caen al default visual de Acten. Estos son
   *  los que el sidebar y otros chrome elements consumen para nunca
   *  quedarse sin imagen aunque la marca no esté configurada. */
  readonly displayLogoUrl = computed(
    () => this.brand().logo_data_url || ACTEN_DEFAULT_LOGO_FULL,
  );
  readonly displayIconUrl = computed(
    () =>
      this.brand().icon_data_url ||
      this.brand().logo_data_url ||
      ACTEN_DEFAULT_ICON,
  );

  /** Carga inicial — invocado por APP_INITIALIZER y por la pantalla de admin. */
  async loadFromServer(): Promise<void> {
    try {
      const data = await firstValueFrom(this.http.get<Partial<Branding>>(`${this.apiUrl}/`));
      const merged: Branding = { ...DEFAULT_BRAND, ...data };
      this.brand.set(merged);
      this.applyToRoot(merged);
      this.applyDocumentMeta(merged);
    } catch (err) {
      // Si el backend está caído, nos quedamos con DEFAULT_BRAND. No bloqueamos boot.
      console.warn('BrandingService: no se pudo cargar /api/branding/, uso defaults.', err);
      this.applyToRoot(DEFAULT_BRAND);
    }
  }

  /**
   * Patch parcial — admin only en el backend.
   * El backend devuelve la marca actualizada completa.
   */
  async update(patch: Partial<Branding>, token: string | null): Promise<Branding> {
    const headers = new HttpHeaders({ Authorization: `Bearer ${token || ''}` });
    const updated = await firstValueFrom(
      this.http.put<Branding>(`${this.apiUrl}/`, patch, { headers }),
    );
    const merged: Branding = { ...DEFAULT_BRAND, ...updated };
    this.brand.set(merged);
    this.applyToRoot(merged);
    this.applyDocumentMeta(merged);
    return merged;
  }

  /** Sube un logo (multipart). Devuelve la marca actualizada. */
  async uploadLogo(file: File, token: string | null): Promise<Branding> {
    const headers = new HttpHeaders({ Authorization: `Bearer ${token || ''}` });
    const form = new FormData();
    form.append('file', file);
    const updated = await firstValueFrom(
      this.http.post<Branding>(`${this.apiUrl}/logo`, form, { headers }),
    );
    const merged: Branding = { ...DEFAULT_BRAND, ...updated };
    this.brand.set(merged);
    this.applyToRoot(merged);
    this.applyDocumentMeta(merged);
    return merged;
  }

  async deleteLogo(token: string | null): Promise<Branding> {
    const headers = new HttpHeaders({ Authorization: `Bearer ${token || ''}` });
    const updated = await firstValueFrom(
      this.http.delete<Branding>(`${this.apiUrl}/logo`, { headers }),
    );
    const merged: Branding = { ...DEFAULT_BRAND, ...updated };
    this.brand.set(merged);
    this.applyToRoot(merged);
    this.applyDocumentMeta(merged);
    return merged;
  }

  /**
   * Escribe los colores en `:root` como CSS custom properties.
   * Esto reaplica el tema sin recargar la página.
   */
  private applyToRoot(b: Branding): void {
    if (typeof document === 'undefined') return;
    const root = document.documentElement;
    root.style.setProperty('--brand-primary', b.primary_color);
    root.style.setProperty('--brand-secondary', b.secondary_color);
    root.style.setProperty('--brand-accent', b.accent_color);
    // Mapeamos también a las vars históricas que ya consumen los componentes.
    root.style.setProperty('--accent-color', b.primary_color);
    root.style.setProperty('--accent-hover', this.shade(b.primary_color, -10));
    root.style.setProperty('--topbar-bg', b.primary_color);
  }

  /** Actualiza `<title>` y favicon dinámico. */
  private applyDocumentMeta(b: Branding): void {
    if (typeof document === 'undefined') return;
    const platform = b.platform_name || 'Acten';
    const company = b.company_name || platform;
    document.title = company === platform ? platform : `${company} · ${platform}`;
    if (b.favicon_data_url) {
      let link = document.querySelector("link[rel='icon']") as HTMLLinkElement | null;
      if (!link) {
        link = document.createElement('link');
        link.rel = 'icon';
        document.head.appendChild(link);
      }
      link.href = b.favicon_data_url;
    }
  }

  /** Oscurece/aclara un hex sin libs externas (para hover states). */
  private shade(hex: string, amount: number): string {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex);
    if (!m) return hex;
    const num = parseInt(m[1], 16);
    let r = (num >> 16) + amount;
    let g = ((num >> 8) & 0xff) + amount;
    let b = (num & 0xff) + amount;
    r = Math.max(0, Math.min(255, r));
    g = Math.max(0, Math.min(255, g));
    b = Math.max(0, Math.min(255, b));
    return `#${((r << 16) | (g << 8) | b).toString(16).padStart(6, '0')}`;
  }
}
