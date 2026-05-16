import { Component, HostListener, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClientModule } from '@angular/common/http';
import { Router } from '@angular/router';
import { LandingCmsService, LandingContent, ContactSubmission } from '../../services/landing-cms.service';

/**
 * Landing pública de Acten. Servida en `/` para el hostname acten.app.
 * Toda la lógica de plataforma queda en admin.acten.app (vía HostnameGuard).
 *
 * Contenido: 100% dinámico desde el backend (`LandingCmsService.loadPublic()`).
 * Diseño: tokens oficiales del proyecto (charcoal / cream / ink-blue /
 * deep-indigo / emerald / amber), tipografía Playfair Display + Sora,
 * iconografía Lucide-style inline SVG.
 *
 * Secciones (en orden de scroll):
 *   header → hero → trust → features → flow → integrations → testimonials
 *   → pricing → resources → company → contact → final CTA → footer
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
        } catch (err) {
            console.error('[Landing] Error enviando contacto', err);
            this.contactError = this.content?.contact.form_error
                || 'No se pudo enviar el mensaje. Intenta de nuevo en unos minutos.';
        } finally {
            this.contactLoading = false;
        }
    }
}
