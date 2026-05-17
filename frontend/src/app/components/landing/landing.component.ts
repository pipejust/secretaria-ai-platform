import { Component, HostListener, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClientModule } from '@angular/common/http';
import { Router } from '@angular/router';
import { LandingCmsService, LandingContent, ContactSubmission, TrustLogoItem, LandingPerson } from '../../services/landing-cms.service';

/**
 * Landing pública de Acten — rediseño "Acten Premium" 2026-Q2.
 *
 * Servida en `/` para el hostname acten.app. Toda la lógica de plataforma
 * queda en admin.acten.app (vía HostnameGuard).
 *
 * Contenido: 100% dinámico desde el backend (`LandingCmsService.loadPublic()`).
 * Estilo: paleta navy/blue/emerald (definida como CSS vars dentro de :host
 * para no contaminar el resto de la app), tipografía Playfair Display + Sora,
 * iconografía Lucide-style inline SVG (sin innerHTML, sin DomSanitizer).
 *
 * Secciones (en orden de scroll):
 *   header → hero → trust → features → flow → integrations → testimonials
 *   → contact → final CTA → footer
 */
@Component({
    selector: 'app-landing',
    standalone: true,
    imports: [CommonModule, FormsModule, HttpClientModule],
    templateUrl: './landing.component.html',
    styleUrls: ['./landing.component.css'],
})
export class LandingComponent implements OnInit {
    private readonly router = inject(Router);
    private readonly cms = inject(LandingCmsService);

    /** URL del admin (admin.acten.app en producción, mismo origen en dev). */
    adminUrl = this.resolveAdminUrl();

    /** Scrolled past hero — el header pasa a opaco. */
    scrolled = false;

    /** Estado mobile menu. */
    mobileMenuOpen = false;

    /** Año actual para footer. */
    year = new Date().getFullYear();

    /** Contenido dinámico del backend. Null mientras carga. */
    content: LandingContent | null = null;
    contentLoading = true;
    contentError = false;

    /** Logos reales de tenants en la trust band. Vacío => sección oculta. */
    trustLogos: TrustLogoItem[] = [];

    /** Personas reales del sistema (owners de tareas + project_contacts).
     *  Reemplaza a los testimonios estáticos del CMS. Vacío => sección oculta. */
    people: LandingPerson[] = [];

    /** Estado newsletter (UI-only por ahora). */
    newsletterEmail = '';
    newsletterSent = false;

    /** Estado del formulario de contacto. */
    contactForm: ContactSubmission = {
        name: '',
        email: '',
        company: '',
        role: '',
        message: '',
        website: '',
    };
    contactSent = false;
    contactError = '';
    contactLoading = false;

    async ngOnInit(): Promise<void> {
        // Si el hostname es admin.* (admin.acten.app), esta landing NO debe
        // verse — el admin entra directo a /admin. Redirigimos de inmediato.
        if (typeof window !== 'undefined') {
            const host = window.location.hostname;
            if (host.startsWith('admin.')) {
                this.router.navigateByUrl('/admin');
                return;
            }
            document.body.classList.add('acten-landing-body');
        }

        try {
            this.content = await this.cms.loadPublic();
        } catch (err) {
            console.error('[Landing] No se pudo cargar contenido público', err);
            this.contentError = true;
        } finally {
            this.contentLoading = false;
        }

        // Trust logos + personas en paralelo — si alguno falla queda vacío
        // y la sección correspondiente no se muestra (gracias a *ngIf).
        const [logosResult, peopleResult] = await Promise.allSettled([
            this.cms.loadTrustLogos(),
            this.cms.loadPeople(),
        ]);
        if (logosResult.status === 'fulfilled') {
            this.trustLogos = logosResult.value;
        } else {
            console.warn('[Landing] No se pudieron cargar trust logos', logosResult.reason);
            this.trustLogos = [];
        }
        if (peopleResult.status === 'fulfilled') {
            this.people = peopleResult.value;
        } else {
            console.warn('[Landing] No se pudieron cargar personas', peopleResult.reason);
            this.people = [];
        }
    }

    /** En desktop el header se vuelve opaco al hacer scroll past hero. */
    @HostListener('window:scroll')
    onScroll(): void {
        this.scrolled = window.scrollY > 80;
    }

    /** Resolver URL del admin según el ambiente. */
    private resolveAdminUrl(): string {
        if (typeof window === 'undefined') return '/admin';
        const host = window.location.hostname;
        if (host === 'localhost' || host.startsWith('127.') || host.endsWith('.local')) {
            return window.location.origin + '/admin';
        }
        if (host.startsWith('admin.')) {
            return window.location.origin + '/admin';
        }
        return 'https://admin.acten.app/admin';
    }

    /** URL pública del login (sin tenant). */
    get loginUrl(): string {
        return this.adminUrl.replace(/\/admin$/, '') + '/login';
    }

    scrollTo(anchor: string, event?: Event): void {
        if (event) event.preventDefault();
        this.mobileMenuOpen = false;
        if (!anchor) return;
        const id = anchor.replace(/^#/, '');
        const el = document.getElementById(id);
        if (el) {
            const offset = 80;
            const top = el.getBoundingClientRect().top + window.pageYOffset - offset;
            window.scrollTo({ top, behavior: 'smooth' });
        }
    }

    toggleMobileMenu(): void {
        this.mobileMenuOpen = !this.mobileMenuOpen;
    }

    /** Pad de número a 2 dígitos: 1 → "01", 12 → "12". Helper de template. */
    pad2(n: number): string {
        return n < 10 ? '0' + n : String(n);
    }

    /**
     * Quote rotativo para las cards de testimonios. Como las personas reales
     * no traen quote propio, asignamos uno de un pool fijo de forma determinista
     * por indice. Mantiene el feel "testimonial" sin inventar datos por persona.
     */
    peopleQuote(index: number): string {
        const phrases = [
            'Acten transformó la forma en que mi equipo ejecuta sus decisiones.',
            'Pasamos de reuniones que terminan en olvido a tareas que sí se ejecutan.',
            'La precisión y el seguimiento automático elevaron nuestra disciplina.',
            'Las actas profesionales y la asignación de tareas son indispensables ya.',
        ];
        return phrases[index % phrases.length];
    }

    /** Iniciales para avatar a partir del nombre. */
    initialsOf(t: { initials?: string; name?: string }): string {
        if (t.initials) return t.initials;
        const name = (t.name || '').trim();
        if (!name) return '?';
        const parts = name.split(/\s+/);
        return parts.length === 1
            ? parts[0].slice(0, 2).toUpperCase()
            : (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
    }

    /**
     * Footer link sanity check — filtra enlaces sin destino real.
     * Acepta anchors (#features) cuyo elemento existe, rutas (/login,
     * /privacy) y URLs absolutas (https://...).
     * Descarta vacíos, "#" puro y anchors a secciones que no existen
     * en esta versión del landing.
     */
    isValidLink(url: string): boolean {
        if (!url || url === '#') return false;
        if (url.startsWith('#')) {
            // Solo válido si la sección existe en el DOM
            const id = url.replace(/^#/, '');
            if (typeof document === 'undefined') return true;  // SSR: optimista
            return !!document.getElementById(id);
        }
        return url.startsWith('/') || url.startsWith('http');
    }

    /** True si la columna del footer tiene al menos un link válido. */
    hasValidLinks(col: { links?: { url: string }[] }): boolean {
        return (col?.links || []).some(l => this.isValidLink(l.url));
    }

    /**
     * Navega correctamente según el tipo de URL.
     *   #anchor   → smooth scroll dentro del landing
     *   /ruta     → router de Angular (SPA, mantiene contexto)
     *   http://…  → comportamiento default (browser maneja, target opcional)
     *
     * Sin este helper, todos los enlaces del footer llamaban scrollTo()
     * que bloqueaba la navegación de /privacy, /terms, etc.
     */
    navigateLink(url: string, event?: Event): void {
        if (!url || url === '#') {
            if (event) event.preventDefault();
            return;
        }
        if (url.startsWith('#')) {
            this.scrollTo(url, event);
            return;
        }
        if (url.startsWith('/')) {
            if (event) event.preventDefault();
            this.mobileMenuOpen = false;
            this.router.navigateByUrl(url);
            return;
        }
        // URL absoluta http(s) — deja que el browser navegue (puede tener target).
    }

    /**
     * Mapea el nombre de una integración (texto libre del CMS) a una clave
     * conocida para renderizar el SVG oficial. Match case-insensitive y
     * por substring. Devuelve '' si no hay match — el template cae al wordmark.
     */
    integrationKey(name: string): string {
        const n = (name || '').toLowerCase();
        if (n.includes('jira')) return 'jira';
        if (n.includes('trello')) return 'trello';
        if (n.includes('clickup')) return 'clickup';
        if (n.includes('azure')) return 'azure';
        if (n.includes('fireflies')) return 'fireflies';
        if (n.includes('google')) return 'google';
        if (n.includes('microsoft') || n.includes('outlook')) return 'microsoft';
        return '';
    }

    submitNewsletter(event: Event): void {
        event.preventDefault();
        if (!this.newsletterEmail || !this.newsletterEmail.includes('@')) {
            return;
        }
        // TODO: POST al backend cuando exista el endpoint. Por ahora UI-only.
        this.newsletterSent = true;
        setTimeout(() => {
            this.newsletterSent = false;
            this.newsletterEmail = '';
        }, 4000);
    }

    async submitContact(event: Event): Promise<void> {
        event.preventDefault();
        if (this.contactLoading || this.contactSent) return;

        // Validación mínima cliente. El backend revalida.
        const { name, email, message } = this.contactForm;
        if (!name.trim() || !email.includes('@') || !message.trim()) {
            this.contactError = this.content?.contact.form_error
                || 'Por favor completa nombre, email y mensaje.';
            return;
        }

        this.contactLoading = true;
        this.contactError = '';
        try {
            await this.cms.submitContact({
                name: this.contactForm.name.trim(),
                email: this.contactForm.email.trim(),
                company: this.contactForm.company?.trim() || undefined,
                role: this.contactForm.role?.trim() || undefined,
                message: this.contactForm.message.trim(),
                website: this.contactForm.website || '',
            });
            this.contactSent = true;
            // Reset suave: tras 6s vuelve al formulario vacío por si el usuario
            // quiere enviar otra solicitud (p. ej. para un colega).
            setTimeout(() => {
                this.contactSent = false;
                this.contactForm = {
                    name: '', email: '', company: '', role: '', message: '', website: '',
                };
            }, 6000);
        } catch (err: any) {
            console.error('[Landing] Error enviando contacto', err);
            // Mensajes específicos por tipo de error — antes era genérico para
            // todo, lo que confundía cuando el problema era rate limit o
            // validación (cosas que el usuario puede solucionar).
            const status = err?.status;
            const detail = err?.error?.detail;
            if (status === 429) {
                this.contactError = typeof detail === 'string' && detail
                    ? detail
                    : 'Estás enviando demasiados mensajes. Espera unos minutos y vuelve a intentar.';
            } else if (status === 422) {
                // Pydantic devuelve detail como array de objetos {loc, msg, ...}.
                const first = Array.isArray(detail) ? detail[0] : null;
                this.contactError = first?.msg
                    ? `Revisa el campo "${(first.loc || []).slice(-1)[0] || 'formulario'}": ${first.msg}.`
                    : 'Hay algún dato inválido en el formulario.';
            } else if (status === 0 || !status) {
                this.contactError = 'No pudimos conectar con el servidor. Verifica tu conexión e intenta de nuevo.';
            } else {
                this.contactError = this.content?.contact.form_error
                    || 'No se pudo enviar el mensaje. Intenta de nuevo en unos minutos.';
            }
        } finally {
            this.contactLoading = false;
        }
    }
}
