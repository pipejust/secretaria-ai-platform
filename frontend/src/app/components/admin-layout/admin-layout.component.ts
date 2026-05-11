import { Component, OnInit, OnDestroy, ChangeDetectorRef, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule, Router, NavigationEnd, ActivatedRoute } from '@angular/router';
import { Subject } from 'rxjs';
import { filter, takeUntil } from 'rxjs/operators';
import { AuthService } from '../../services/auth.service';
import { BrandingService } from '../../services/branding.service';

interface CurrentUser {
    email?: string;
    full_name?: string;
    role?: string | null;
    is_superadmin?: boolean;
    tenant?: { id: number; slug: string; name: string };
}

@Component({
    selector: 'app-admin-layout',
    standalone: true,
    imports: [CommonModule, RouterModule],
    templateUrl: './admin-layout.component.html',
    styleUrls: ['./admin-layout.component.css']
})
export class AdminLayoutComponent implements OnInit, OnDestroy {
    user: CurrentUser | null = null;
    isAdmin = false;
    isSuperAdmin = false;
    isCollapsed = false;
    isMobileOpen = false;
    isProfileDropdownOpen = false;
    showNotifPanel = false;

    /** Título dinámico del topbar derivado de la ruta activa
     *  (route.data.title o route.title con sufijo " | Acten" recortado). */
    currentPageTitle = 'Overview';

    /** Marca white-label expuesta al template (logo, nombre, colores). */
    readonly branding = inject(BrandingService);

    private readonly destroy$ = new Subject<void>();

    constructor(
        private authService: AuthService,
        private router: Router,
        private activatedRoute: ActivatedRoute,
        private cdr: ChangeDetectorRef,
    ) { }

    /** Recorre el árbol de rutas hijas para encontrar la activa y leer su title. */
    private _resolveTitle(): string {
        let r = this.activatedRoute.firstChild;
        while (r?.firstChild) r = r.firstChild;
        const raw = (r?.snapshot?.title || '').toString();
        // Recorta " | Acten" final si está
        return raw.replace(/\s*\|\s*Acten\s*$/i, '').trim() || 'Acten';
    }

    ngOnInit(): void {
        this.authService.currentUser$
            .pipe(takeUntil(this.destroy$))
            .subscribe((u: CurrentUser | null) => {
                if (u === null && !this.authService.token) {
                    setTimeout(() => this.router.navigate(['/login']), 100);
                    return;
                }
                this.user = u;
                this.isAdmin = u?.role === 'admin';
                this.isSuperAdmin = !!u?.is_superadmin;
                this.cdr.detectChanges();
            });

        // Inicial — antes de la primera NavigationEnd
        this.currentPageTitle = this._resolveTitle();

        this.router.events
            .pipe(
                filter(event => event instanceof NavigationEnd),
                takeUntil(this.destroy$)
            )
            .subscribe(() => {
                this.currentPageTitle = this._resolveTitle();
                if (this.isMobileOpen) this.isMobileOpen = false;
                if (this.isProfileDropdownOpen) this.isProfileDropdownOpen = false;
                if (this.showNotifPanel) this.showNotifPanel = false;
                this.cdr.detectChanges();
            });
    }

    ngOnDestroy(): void {
        this.destroy$.next();
        this.destroy$.complete();
    }

    toggleProfileDropdown() {
        this.isProfileDropdownOpen = !this.isProfileDropdownOpen;
        if (this.isProfileDropdownOpen) this.showNotifPanel = false;
    }

    closeProfileDropdown() {
        this.isProfileDropdownOpen = false;
    }

    /** Abre/cierra el panel de notificaciones del topbar. */
    toggleNotifPanel(ev?: Event) {
        ev?.stopPropagation();
        this.showNotifPanel = !this.showNotifPanel;
        if (this.showNotifPanel) this.isProfileDropdownOpen = false;
    }

    toggleMobileMenu() {
        this.isMobileOpen = !this.isMobileOpen;
    }

    closeMobileMenu() {
        this.isMobileOpen = false;
    }

    toggleSidebar() {
        this.isCollapsed = !this.isCollapsed;
    }

    logout() {
        this.authService.logout();
    }

    getInitials(name: string | null | undefined): string {
        if (!name) return 'U';
        const words = name.trim().split(' ');
        if (words.length >= 2) {
            return (words[0].charAt(0) + words[1].charAt(0)).toUpperCase();
        }
        return name.substring(0, 2).toUpperCase();
    }
}
