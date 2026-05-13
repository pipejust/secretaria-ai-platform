import { Component, OnInit, OnDestroy, ChangeDetectorRef, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterModule, Router, NavigationEnd, ActivatedRoute } from '@angular/router';
import { Subject } from 'rxjs';
import { debounceTime, distinctUntilChanged, filter, switchMap, takeUntil } from 'rxjs/operators';
import { AuthService } from '../../services/auth.service';
import { BrandingService } from '../../services/branding.service';
import { NotificationService, AcnNotification } from '../../services/notification.service';
import { SearchService, SearchGroup } from '../../services/search.service';

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
    imports: [CommonModule, FormsModule, RouterModule],
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

    // ---- Notifications (bell del topbar) -----------------------------------
    private readonly notifSvc = inject(NotificationService);
    /** Contador del badge — se sincroniza vía suscripción al service. */
    notifUnread = 0;
    /** Lista renderizada en el panel cuando se abre. */
    notifItems: AcnNotification[] = [];
    notifLoading = false;

    // ---- Search global -----------------------------------------------------
    private readonly searchSvc = inject(SearchService);
    /** Texto del input. Cuando cambia, dispara la query con debounce. */
    searchQuery = '';
    /** Resultados agrupados que pinta el dropdown. */
    searchGroups: SearchGroup[] = [];
    searchOpen = false;
    searchLoading = false;
    private readonly searchInput$ = new Subject<string>();

    /** Título dinámico del topbar derivado de la ruta activa
     *  (route.data.title o route.title con sufijo " | Acten" recortado). */
    currentPageTitle = 'Resumen';

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

        // Suscripción al contador de notificaciones — emite cada cambio.
        this.notifSvc.unreadCount$
            .pipe(takeUntil(this.destroy$))
            .subscribe((n) => {
                this.notifUnread = n;
                this.cdr.detectChanges();
            });
        // Polling cada 60s del unread_count (sólo si hay token).
        this.notifSvc.startPolling(60_000)
            .pipe(takeUntil(this.destroy$))
            .subscribe();

        // Pipeline del input de búsqueda: debounce 300ms + dedup + HTTP.
        this.searchInput$
            .pipe(
                debounceTime(300),
                distinctUntilChanged(),
                switchMap((q) => {
                    this.searchLoading = true;
                    return this.searchSvc.search(q);
                }),
                takeUntil(this.destroy$),
            )
            .subscribe((res) => {
                this.searchGroups = res?.groups || [];
                this.searchLoading = false;
                // Solo abrimos el dropdown si hay query — si está vacío,
                // se cierra para no quedarse colgado en blanco.
                this.searchOpen = (this.searchQuery || '').trim().length >= 2;
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
                if (this.searchOpen) this.searchOpen = false;
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

    /** Abre/cierra el panel de notificaciones del topbar. Cuando se abre,
     *  carga la lista del backend (refresca el contador en la respuesta). */
    toggleNotifPanel(ev?: Event) {
        ev?.stopPropagation();
        this.showNotifPanel = !this.showNotifPanel;
        if (this.showNotifPanel) {
            this.isProfileDropdownOpen = false;
            this.searchOpen = false;
            this._loadNotifications();
        }
    }

    private _loadNotifications(): void {
        this.notifLoading = true;
        this.notifSvc.list(false, 20)
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (r) => {
                    this.notifItems = r?.items || [];
                    this.notifLoading = false;
                    this.cdr.detectChanges();
                },
                error: () => {
                    this.notifItems = [];
                    this.notifLoading = false;
                    this.cdr.detectChanges();
                },
            });
    }

    /** Click en una notificación: la marca como leída, cierra el panel y
     *  navega al deep-link asociado. Si no hay link, sólo la marca. */
    onNotificationClick(n: AcnNotification, ev?: Event): void {
        ev?.stopPropagation();
        // Optimistic UI: ya marcamos como leída en el array local.
        if (!n.is_read) {
            n.is_read = true;
            n.read_at = new Date().toISOString();
            this.notifUnread = Math.max(0, this.notifUnread - 1);
        }
        this.notifSvc.markRead(n.id)
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (r) => {
                    this.showNotifPanel = false;
                    if (r.link_to) this.router.navigateByUrl(r.link_to);
                },
                error: () => {
                    // Si falla el mark-read, igual cerramos y navegamos.
                    this.showNotifPanel = false;
                    if (n.link_to) this.router.navigateByUrl(n.link_to);
                },
            });
    }

    markAllNotificationsRead(ev?: Event): void {
        ev?.stopPropagation();
        this.notifSvc.markAllRead()
            .pipe(takeUntil(this.destroy$))
            .subscribe(() => {
                this.notifItems = this.notifItems.map((n) => ({
                    ...n,
                    is_read: true,
                    read_at: n.read_at || new Date().toISOString(),
                }));
                this.notifUnread = 0;
                this.cdr.detectChanges();
            });
    }

    /** Convierte 'session_processed' → label humano en español. */
    notifKindLabel(kind: string): string {
        const map: Record<string, string> = {
            session_processed: 'Sesión analizada',
            session_received:  'Sesión recibida',
            task_assigned:     'Tarea asignada',
            routing_failed:    'Error de sincronización',
            comment_mention:   'Mención en comentario',
            system:            'Sistema',
        };
        return map[kind] || 'Notificación';
    }

    /** "hace X" para los timestamps de las notificaciones. */
    timeAgo(iso: string): string {
        if (!iso) return '';
        const d = new Date(iso);
        if (isNaN(d.getTime())) return '';
        const diff = Date.now() - d.getTime();
        const mins = Math.floor(diff / 60000);
        if (mins < 1) return 'hace un momento';
        if (mins < 60) return `hace ${mins} min`;
        const hrs = Math.floor(mins / 60);
        if (hrs < 24) return `hace ${hrs} h`;
        const days = Math.floor(hrs / 24);
        if (days < 7) return `hace ${days} d`;
        return d.toLocaleDateString('es-CO', { day: '2-digit', month: 'short' });
    }

    // ==================== SEARCH ====================

    /** Disparado por el `(input)` del search del topbar. */
    onSearchInput(value: string): void {
        this.searchQuery = value;
        const trimmed = (value || '').trim();
        if (trimmed.length < 2) {
            this.searchOpen = false;
            this.searchGroups = [];
            return;
        }
        this.searchInput$.next(trimmed);
    }

    onSearchFocus(): void {
        if (this.searchGroups.length > 0 && (this.searchQuery || '').trim().length >= 2) {
            this.searchOpen = true;
        }
    }

    /** Click sobre un resultado del dropdown — navega y cierra. */
    onSearchHit(link: string): void {
        this.searchOpen = false;
        this.searchQuery = '';
        this.searchGroups = [];
        if (link) this.router.navigateByUrl(link);
    }

    closeSearchDropdown(): void {
        this.searchOpen = false;
    }

    searchTypeIcon(type: string): string {
        const map: Record<string, string> = {
            meeting: '🎙',
            project: '📁',
            task:    '✓',
            person:  '👤',
        };
        return map[type] || '•';
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

    /** "12 may 2026" — etiqueta que renderiza el date selector del topbar. */
    get todayLabel(): string {
        const d = new Date();
        const months = ['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'];
        return `${d.getDate()} ${months[d.getMonth()]} ${d.getFullYear()}`;
    }

    /** Año actual para el copy del footer. */
    get currentYear(): number { return new Date().getFullYear(); }

    /** Botón "+ New Meeting" del topbar — navega al dashboard con query
     *  param para que el componente abra el modal de upload existente.
     *  No introduce nueva lógica de negocio; sólo orquesta la UX. */
    goNewMeeting(): void {
        this.router.navigate(['/admin/dashboard'], { queryParams: { new: 'meeting' } });
    }
}
