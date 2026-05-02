import { Component, OnInit, OnDestroy, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule, Router, NavigationEnd } from '@angular/router';
import { Subject } from 'rxjs';
import { filter, takeUntil } from 'rxjs/operators';
import { AuthService } from '../../services/auth.service';

interface CurrentUser {
    email?: string;
    full_name?: string;
    role?: string | null;
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
    isCollapsed = false;
    isMobileOpen = false;
    isProfileDropdownOpen = false;

    private readonly destroy$ = new Subject<void>();

    constructor(private authService: AuthService, private router: Router, private cdr: ChangeDetectorRef) { }

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
                this.cdr.detectChanges();
            });

        this.router.events
            .pipe(
                filter(event => event instanceof NavigationEnd),
                takeUntil(this.destroy$)
            )
            .subscribe(() => {
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

    ngOnDestroy(): void {
        this.destroy$.next();
        this.destroy$.complete();
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
