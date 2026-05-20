/**
 * LandingShellComponent — header + footer envoltorio para páginas
 * dedicadas (/producto, /soluciones, /precios, /recursos, /empresa, etc).
 *
 * La página actual (LandingComponent / acten.app/) tiene su propio header
 * y footer inline porque su layout (hero+secciones) es atómico. Las
 * páginas nuevas son más simples y se benefician de un shell común.
 *
 * Uso:
 *   <app-landing-shell>
 *     <div class="al-page-body">...contenido...</div>
 *   </app-landing-shell>
 *
 * El shell carga el content del CMS y lo expone vía template ref para
 * que el contenido proyectado pueda referenciarlo si lo necesita.
 */
import { CommonModule } from '@angular/common';
import { Component, HostListener, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { HttpClientModule } from '@angular/common/http';
import { Router, RouterLink } from '@angular/router';
import { LandingCmsService, LandingContent } from '../../services/landing-cms.service';

@Component({
    selector: 'app-landing-shell',
    standalone: true,
    imports: [CommonModule, FormsModule, HttpClientModule, RouterLink],
    templateUrl: './landing-shell.component.html',
    styleUrls: ['./landing-shell.component.css'],
})
export class LandingShellComponent implements OnInit {
    private readonly router = inject(Router);
    private readonly cms = inject(LandingCmsService);

    content: LandingContent | null = null;
    contentLoading = true;
    scrolled = false;
    mobileMenuOpen = false;
    year = new Date().getFullYear();

    get loginUrl(): string {
        if (typeof window === 'undefined') return '/login';
        const host = window.location.hostname;
        if (host === 'localhost' || host.startsWith('127.') || host.endsWith('.local')) {
            return `${window.location.origin}/login`;
        }
        return 'https://admin.acten.app/login';
    }

    async ngOnInit(): Promise<void> {
        try {
            this.content = await this.cms.loadPublic();
        } catch {
            // si falla, los componentes hijos pueden tener fallbacks
        } finally {
            this.contentLoading = false;
        }
    }

    @HostListener('window:scroll')
    onScroll(): void {
        this.scrolled = window.pageYOffset > 30;
    }

    toggleMobileMenu(): void {
        this.mobileMenuOpen = !this.mobileMenuOpen;
    }
    closeMobileMenu(): void {
        this.mobileMenuOpen = false;
    }

    /** Navegar a un item del menú: anchor (#section) ó route (/path). */
    navigateNav(item: { anchor: string }, event?: Event): void {
        if (event) event.preventDefault();
        this.closeMobileMenu();
        const target = (item.anchor || '').trim();
        if (!target) return;
        if (target.startsWith('#')) {
            // Scroll a section dentro de la home — si estamos en otra ruta,
            // navegamos a / con fragment.
            if (this.router.url === '/' || this.router.url.startsWith('/?')) {
                const id = target.slice(1);
                const el = document.getElementById(id);
                if (el) {
                    const top = el.getBoundingClientRect().top + window.pageYOffset - 80;
                    window.scrollTo({ top, behavior: 'smooth' });
                }
            } else {
                this.router.navigate(['/'], { fragment: target.slice(1) });
            }
        } else if (target.startsWith('http')) {
            window.location.href = target;
        } else {
            this.router.navigateByUrl(target);
        }
    }

    /** Compone línea de copyright sin duplicar © ni el año. */
    copyrightLine(raw: string | null | undefined): string {
        const body = (raw ?? '').replace(/^\s*©\s*\d{4}\s*/u, '').replace(/^\s*©\s*/u, '').trim();
        return body ? `© ${this.year} ${body}` : `© ${this.year}`;
    }
}
