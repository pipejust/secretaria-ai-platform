import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';

/**
 * Tipos del contenido del landing público.
 * El backend devuelve siempre la estructura completa (los defaults se
 * mezclan con lo guardado en `landing_content_service.DEFAULT_CONTENT`),
 * así que en el frontend podemos asumir que todas las claves existen.
 */

export interface NavItem { label: string; anchor: string; }
export interface FeatureItem { title: string; description: string; icon: string; }
export interface StepItem    { title: string; description: string; icon: string; }
export interface Testimonial {
  quote: string;
  name: string;
  role: string;
  company: string;
  brand_wordmark?: string;
  /** URL del avatar real (https:// o data:). Si vacío, se renderizan iniciales. */
  avatar_url?: string;
  /** URL del logo de la empresa para reemplazar el wordmark. Opcional. */
  company_logo_url?: string;
  initials: string;
}

/** Item de la trust band — endpoint /api/public/landing/trust-logos */
export interface TrustLogoItem {
  slug: string;
  name: string;
  /** Logo a renderizar — prioriza logo_dark_data_url del tenant para
   *  que se vea bien sobre el fondo navy de la trust band. Vacío si el
   *  tenant no subió ningún logo; en ese caso el frontend cae a wordmark. */
  logo_url: string;
  /** True si lo que viene en logo_url es la variante oscura del tenant
   *  (subida específicamente para fondos oscuros). False si tuvo que
   *  caer al logo regular. Permite ajustar estilos por tenant. */
  has_dark_variant?: boolean;
}

/** Persona real del sistema — endpoint /api/public/landing/people */
export interface LandingPerson {
  name: string;
  /** Role componiendo "Cargo · Empresa" si tenemos ambos. */
  role: string;
  /** Empresa/entity del project_contact si existe. Vacío si no aplica. */
  company?: string;
  /** URL del avatar (https/data) o vacío. */
  avatar_url: string;
  initials: string;
}
export interface HeroAttribute { label: string; icon: string; }
export interface StatusItem  { label: string; tone: string; }
export interface SocialLink  { label: string; icon: string; url: string; }
export interface LegalLink   { label: string; url: string; }
export interface PricingPlan {
  name: string;
  price: string;
  billing: string;
  description: string;
  features: string[];
  cta_label: string;
  cta_anchor: string;
  featured: boolean;
}
export interface ResourceItem { category: string; title: string; description: string; url: string; icon: string; }
export interface CompanyValue { title: string; description: string; }
export interface CompanyStat  { label: string; value: string; }
export interface FooterLink   { label: string; url: string; }
export interface FooterColumn { title: string; links: FooterLink[]; }

export interface LandingContent {
  nav: {
    items: NavItem[];
    cta_label: string;
    login_label: string;
  };
  hero: {
    badge: string;
    title: string;
    title_lead?: string;
    title_highlight_prefix?: string;
    title_highlight_word?: string;
    subtitle: string;
    cta_primary_label: string;
    cta_primary_anchor: string;
    cta_secondary_label: string;
    cta_secondary_anchor: string;
    attributes?: HeroAttribute[];
    mockup_eyebrow: string;
    mockup_title: string;
    mockup_meta: string;
  };
  trust: {
    eyebrow: string;
    logos: string[];
  };
  features: {
    eyebrow: string;
    title: string;
    title_lead?: string;
    title_highlight?: string;
    subtitle: string;
    link_label?: string;
    link_anchor?: string;
    items: FeatureItem[];
  };
  flow: {
    eyebrow: string;
    title: string;
    title_lead?: string;
    title_highlight?: string;
    subtitle: string;
    link_label?: string;
    link_anchor?: string;
    steps: StepItem[];
  };
  integrations: {
    eyebrow: string;
    title: string;
    subtitle: string;
    see_all_label?: string;
    see_all_anchor?: string;
    items: string[];
  };
  testimonials: {
    eyebrow: string;
    title: string;
    /** Pool de frases rotativas que se asignan a cada persona por índice.
     *  El landing usa quotes[i % quotes.length]. Editable desde el CMS. */
    quotes?: string[];
    /** Legacy — testimonios estáticos. Frontend nuevo NO los usa (muestra
     *  personas reales de /api/public/landing/people). Conservado por
     *  backwards-compat. */
    items: Testimonial[];
  };
  pricing: {
    eyebrow: string;
    title: string;
    subtitle: string;
    plans: PricingPlan[];
    footnote: string;
  };
  resources: {
    eyebrow: string;
    title: string;
    subtitle: string;
    items: ResourceItem[];
  };
  company: {
    eyebrow: string;
    title: string;
    subtitle: string;
    story: string;
    mission: string;
    values: CompanyValue[];
    stats: CompanyStat[];
  };
  contact: {
    eyebrow: string;
    title: string;
    subtitle: string;
    email: string;
    phone: string;
    address: string;
    form_name_label: string;
    form_email_label: string;
    form_company_label: string;
    form_role_label: string;
    form_message_label: string;
    form_cta_label: string;
    form_success: string;
    form_error: string;
  };
  final_cta: {
    eyebrow: string;
    title: string;
    title_lead?: string;
    title_highlight?: string;
    subtitle: string;
    cta_label: string;
    cta_anchor: string;
    secondary_label: string;
    secondary_anchor: string;
    status_items?: StatusItem[];
  };
  footer: {
    tagline: string;
    newsletter_title: string;
    newsletter_subtitle: string;
    newsletter_placeholder?: string;
    newsletter_cta: string;
    newsletter_success: string;
    social_links?: SocialLink[];
    columns: FooterColumn[];
    legal_links?: LegalLink[];
    language_label?: string;
    copyright: string;
  };
  // ─── Páginas dedicadas (rutas /producto, /soluciones, etc.) ─────────
  product_page?: {
    eyebrow: string;
    title: string;
    subtitle: string;
    blocks: { title: string; items: string[] }[];
    cta_title?: string;
    cta_subtitle?: string;
    cta_label?: string;
    cta_url?: string;
  };
  solutions_page?: {
    eyebrow: string;
    title: string;
    subtitle: string;
    audiences: { key: string; title: string; description: string; icon: string }[];
    use_cases_title?: string;
    use_cases: { title: string; description: string }[];
    industries_title?: string;
    industries: { key: string; title: string; icon: string }[];
  };
  case_studies?: {
    eyebrow: string;
    title: string;
    subtitle: string;
    items: {
      slug: string;
      company: string;
      logo_url?: string;
      tagline: string;
      summary: string;
      kpis?: { label: string; value: string }[];
      sectors_served?: string[];
    }[];
  };
  case_study_detail?: {
    eyebrow: string;
    challenge_label: string;
    solution_label: string;
    results_label: string;
    other_cases_label: string;
  };
  demo_page?: {
    eyebrow: string;
    title: string;
    subtitle: string;
    bullets: string[];
    form_name_label: string;
    form_email_label: string;
    form_company_label: string;
    form_role_label: string;
    form_team_size_label?: string;
    form_team_size_options?: string[];
    form_use_case_label?: string;
    form_message_label: string;
    form_cta_label: string;
    form_success: string;
    form_error: string;
    privacy_label?: string;
    privacy_url?: string;
  };
  contact_page?: {
    eyebrow: string;
    title: string;
    subtitle: string;
    channels: { label: string; value: string; icon: string }[];
    offices_title?: string;
    offices: { city: string; address: string }[];
    form_name_label: string;
    form_email_label: string;
    form_company_label: string;
    form_message_label: string;
    form_cta_label: string;
    form_success: string;
    form_error: string;
    privacy_label?: string;
  };
}

export interface ContactSubmission {
  name: string;
  email: string;
  company?: string;
  role?: string;
  message: string;
  /** Honeypot — debe ir vacío. Bots lo rellenan. */
  website?: string;
}


@Injectable({ providedIn: 'root' })
export class LandingCmsService {
  private readonly http = inject(HttpClient);
  private readonly adminUrl  = `${environment.apiUrl}/api/landing-cms`;
  private readonly publicUrl = `${environment.apiUrl}/api/public/landing`;

  /** GET público — usado por el landing en acten.app. Sin token. */
  async loadPublic(): Promise<LandingContent> {
    return firstValueFrom(this.http.get<LandingContent>(this.publicUrl));
  }

  /** GET público — tenants reales del sistema para la trust band. */
  async loadTrustLogos(): Promise<TrustLogoItem[]> {
    const resp = await firstValueFrom(
      this.http.get<{ items: TrustLogoItem[] }>(`${this.publicUrl}/trust-logos`),
    );
    return resp?.items ?? [];
  }

  /** GET público — personas reales (owners de tareas + project_contacts). */
  async loadPeople(): Promise<LandingPerson[]> {
    const resp = await firstValueFrom(
      this.http.get<{ items: LandingPerson[] }>(`${this.publicUrl}/people`),
    );
    return resp?.items ?? [];
  }

  /** GET admin — devuelve el mismo shape que loadPublic. Requiere token + tenant 'acten'. */
  async loadAdmin(token: string): Promise<LandingContent> {
    const headers = new HttpHeaders({ Authorization: `Bearer ${token}` });
    return firstValueFrom(this.http.get<LandingContent>(`${this.adminUrl}/`, { headers }));
  }

  /** PUT admin — patch parcial. El backend deep-mergea sobre lo que ya estaba. */
  async save(patch: Partial<LandingContent>, token: string): Promise<LandingContent> {
    const headers = new HttpHeaders({ Authorization: `Bearer ${token}` });
    return firstValueFrom(this.http.put<LandingContent>(`${this.adminUrl}/`, patch, { headers }));
  }

  /** Reset duro al contenido default. */
  async reset(token: string): Promise<LandingContent> {
    const headers = new HttpHeaders({ Authorization: `Bearer ${token}` });
    return firstValueFrom(this.http.post<LandingContent>(`${this.adminUrl}/reset`, {}, { headers }));
  }

  /** POST público — formulario de contacto del landing. */
  async submitContact(payload: ContactSubmission): Promise<{ status: string }> {
    return firstValueFrom(
      this.http.post<{ status: string }>(`${this.publicUrl}/contact`, payload),
    );
  }
}
