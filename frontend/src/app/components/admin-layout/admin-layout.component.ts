import { Component, OnInit, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule, Router, NavigationEnd } from '@angular/router';
import { filter } from 'rxjs/operators';
import { AuthService } from '../../services/auth.service';

@Component({
    selector: 'app-admin-layout',
    standalone: true,
    imports: [CommonModule, RouterModule],
    templateUrl: './admin-layout.component.html',
    styleUrls: ['./admin-layout.component.css']
})
export class AdminLayoutComponent implements OnInit {
    user: any = null;
    isAdmin = false;
    isCollapsed = false;
    isMobileOpen = false;
    isProfileDropdownOpen = false;

    constructor(private authService: AuthService, private router: Router, private cdr: ChangeDetectorRef) { }

    ngOnInit() {
        this.authService.currentUser$.subscribe(u => {
            console.log('User from AuthService:', u);
            this.user = u;
            this.isAdmin = u?.role === 'admin';
            console.log('isAdmin computed:', this.isAdmin);
            this.cdr.detectChanges(); // Forzar el update visual
        });

        // Cerrar los menús al navegar a otra ruta
        this.router.events.pipe(
            filter(event => event instanceof NavigationEnd)
        ).subscribe(() => {
            if (this.isMobileOpen) {
                this.isMobileOpen = false;
                this.cdr.detectChanges();
            }
            if (this.isProfileDropdownOpen) {
                this.isProfileDropdownOpen = false;
                this.cdr.detectChanges();
            }
        });
    }

    toggleProfileDropdown() {
        this.isProfileDropdownOpen = !this.isProfileDropdownOpen;
    }

    closeProfileDropdown() {
        this.isProfileDropdownOpen = false;
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

    getInitials(name: string): string {
        if (!name) return 'U';
        const words = name.trim().split(' ');
        if (words.length >= 2) {
            return (words[0].charAt(0) + words[1].charAt(0)).toUpperCase();
        }
        return name.substring(0, 2).toUpperCase();
    }
}
