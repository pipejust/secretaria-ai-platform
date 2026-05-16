import { Component, HostListener, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { environment } from '../../../environments/environment';

interface Feature {
    title: string;
    description: string;
    icon: 'capture' | 'decisions' | 'tasks' | 'docs' | 'email' | 'integrations';
}

interface Step {
    title: string;
    description: string;
    icon: 'mic' | 'brain' | 'list' | 'send';
}

interface Testimonial {
    quote: string;
    name: string;
    role: string;
    company: string;
    initials: string;
}

interface NavItem {
    label: string;
    anchor: string;
}

/**
 * Landing pública de Acten. Servida en `/` para el hostname acten.app.
 * Toda la lógica de plataforma queda en admin.acten.app (vía HostnameGuard).
 *
 * Stack visual:
 *  - Hero oscuro charcoal/ink-blue con badge, título editorial, CTA, mockup
 *  - Bloques claros con grid de capacidades (6 cards)
 *  - Flujo de 4 pasos (Captura → Inteligencia → Estructura → Acción)
 *  - Integraciones (logos texto)
 *  - Testimonios (3 cards)
 *  - CTA final oscuro
 *  - Footer multi-columna + newsletter
 */
@Component({
    selector: 'app-landing',
    standalone: true,
    imports: [CommonModule, FormsModule],
    templateUrl: './landing.component.html',
    styleUrls: ['./landing.component.css'],
})
export class LandingComponent implements OnInit {
    /** URL del admin (admin.acten.app en producción, mismo origen en dev). */
    adminUrl = this.resolveAdminUrl();

    /** Scrolled past hero — el header pasa a opaco. */
    scrolled = false;

    /** Estado mobile menu. */
    mobileMenuOpen = false;

    /** Año actual para footer. */
    year = new Date().getFullYear();

    /** Estado de envío del formulario newsletter (solo UI). */
    newsletterEmail = '';
    newsletterSent = false;

    /** Navegación principal del header. */
    navItems: NavItem[] = [
        { label: 'Producto',      anchor: '#features' },
        { label: 'Soluciones',    anchor: '#flow' },
        { label: 'Integraciones', anchor: '#integrations' },
        { label: 'Precios',       anchor: '#pricing' },
        { label: 'Recursos',      anchor: '#resources' },
        { label: 'Empresa',       anchor: '#company' },
    ];

    /** Logos de confianza (texto/wordmark, no imágenes — más portable). */
    trustLogos: string[] = [
        'Microsoft', 'Google', 'Siemens', 'BBVA', 'Santander', 'Deloitte',
    ];

    /** 6 capacidades principales. */
    features: Feature[] = [
        {
            title: 'Captura e interpreta',
            description: 'Transcripción precisa multilenguaje con identificación de hablantes, temas y contexto.',
            icon: 'capture',
        },
        {
            title: 'Decisiones y riesgos',
            description: 'Extracción automática de decisiones clave, riesgos y bloqueos con responsable y severidad.',
            icon: 'decisions',
        },
        {
            title: 'Tareas y responsables',
            description: 'Acciones concretas con responsable, fecha límite y prioridad — listas para asignar.',
            icon: 'tasks',
        },
        {
            title: 'Documentos profesionales',
            description: 'Actas en Word y PDF con tu marca, tipografía y plantilla custom por proyecto.',
            icon: 'docs',
        },
        {
            title: 'Correos personalizados',
            description: 'Envío automático a cada responsable con sus tareas, contexto y fechas. Sin reenvíos manuales.',
            icon: 'email',
        },
        {
            title: 'Integraciones nativas',
            description: 'Trello, Jira, ClickUp, Azure DevOps, Slack y calendarios. Acten sincroniza sin esfuerzo.',
            icon: 'integrations',
        },
    ];

    /** Flujo de 4 pasos. */
    steps: Step[] = [
        {
            title: 'Captura',
            description: 'Conecta Fireflies, Meet, Teams o sube grabaciones. Acten transcribe automáticamente.',
            icon: 'mic',
        },
        {
            title: 'Inteligencia',
            description: 'IA analiza el contexto, identifica decisiones, riesgos y compromisos.',
            icon: 'brain',
        },
        {
            title: 'Estructura',
            description: 'Genera acta profesional, lista de tareas y correos personalizados por responsable.',
            icon: 'list',
        },
        {
            title: 'Acción',
            description: 'Despacha a tus plataformas: Trello, Jira, ClickUp, Azure. Seguimiento automático.',
            icon: 'send',
        },
    ];

    /** Integraciones soportadas. */
    integrations: string[] = [
        'Jira', 'Trello', 'ClickUp', 'Azure DevOps',
        'Microsoft 365', 'Google Workspace', 'Slack', 'Fireflies',
    ];

    /** Testimonios. */
    testimonials: Testimonial[] = [
        {
            quote: 'Acten redujo en 4 horas semanales el trabajo de seguimiento de mi equipo. Las actas salen solas y los responsables saben qué hacer.',
            name: 'Lady Edith Ardila',
            role: 'Líder de Producto',
            company: 'Colpensiones',
            initials: 'LA',
        },
        {
            quote: 'Pasamos de tener decisiones perdidas en chats a un acta formal con trazabilidad. Lo más útil: la integración con Azure DevOps.',
            name: 'Felipe Cortés',
            role: 'CTO',
            company: 'Acten',
            initials: 'FC',
        },
        {
            quote: 'La IA capta hasta los detalles que se nos escapan. El resumen ejecutivo es exactamente lo que necesita la dirección.',
            name: 'Christian Muñoz',
            role: 'SCRUM Master',
            company: 'Softnexus',
            initials: 'CM',
        },
    ];

    ngOnInit(): void {
        // Si el hostname es admin.* (admin.acten.app), esta landing NO debe
        // verse — el admin entra directo a /admin. Redirigimos de inmediato.
        if (typeof window !== 'undefined') {
            const host = window.location.hostname;
            if (host.startsWith('admin.')) {
                this.router.navigateByUrl('/admin');
                return;
            }
        }
        // Aplicar clase al body para que el landing tenga su propio fondo
        document.body.classList.add('acten-landing-body');
    }

    constructor(private router: Router) {}

    /** En desktop el header se vuelve opaco al hacer scroll past hero. */
    @HostListener('window:scroll')
    onScroll(): void {
        this.scrolled = window.scrollY > 80;
    }

    /** Resolver URL del admin según el ambiente. En dev usa la URL actual
     *  (mismo origen). En producción usa admin.acten.app explícito. */
    private resolveAdminUrl(): string {
        if (typeof window === 'undefined') return '/admin';
        const host = window.location.hostname;
        // En localhost / desarrollo: mismo origen + /admin
        if (host === 'localhost' || host.startsWith('127.') || host.endsWith('.local')) {
            return window.location.origin + '/admin';
        }
        // En producción: forzar admin.acten.app
        // Si ya estamos en admin.* → mismo origen + /admin
        if (host.startsWith('admin.')) {
            return window.location.origin + '/admin';
        }
        // En acten.app → cambiar a admin.acten.app
        return 'https://admin.acten.app/admin';
    }

    /** URL pública del login (sin tenant). */
    get loginUrl(): string {
        return this.adminUrl.replace(/\/admin$/, '') + '/login';
    }

    scrollTo(anchor: string, event?: Event): void {
        if (event) event.preventDefault();
        this.mobileMenuOpen = false;
        const id = anchor.replace(/^#/, '');
        const el = document.getElementById(id);
        if (el) {
            const offset = 70;
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
}
