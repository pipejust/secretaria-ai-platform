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
export interface Testimonial { quote: string; name: string; role: string; company: string; initials: string; }
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
    subtitle: string;
    cta_primary_label: string;
    cta_primary_anchor: string;
    cta_secondary_label: string;
    cta_secondary_anchor: string;
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
    subtitle: string;
    items: FeatureItem[];
  };
  flow: {
    eyebrow: string;
    title: string;
    subtitle: string;
    steps: StepItem[];
  };
  integrations: {
    eyebrow: string;
    title: string;
    subtitle: string;
    items: string[];
  };
  testimonials: {
    eyebrow: string;
    title: string;
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
    subtitle: string;
    cta_label: string;
    cta_anchor: string;
    secondary_label: string;
    secondary_anchor: string;
  };
  footer: {
    tagline: string;
    newsletter_title: string;
    newsletter_subtitle: string;
    newsletter_cta: string;
    newsletter_success: string;
    columns: FooterColumn[];
    copyright: string;
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
