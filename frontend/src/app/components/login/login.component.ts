import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterModule } from '@angular/router';
import { AuthService } from '../../services/auth.service';
import { BrandingService } from '../../services/branding.service';
import { TenantService } from '../../services/tenant.service';

@Component({
    selector: 'app-login',
    standalone: true,
    imports: [CommonModule, FormsModule, RouterModule],
    templateUrl: './login.component.html',
    styleUrls: ['./login.component.css']
})
export class LoginComponent implements OnInit {
    email = '';
    password = '';
    showPassword = false;
    isLoading = false;
    errorMessage = '';
    /** Visual-only: el SSO real no está cableado todavía. */
    rememberMe = true;

    /** Multi-tenant: derivado SIEMPRE de la URL (TenantService).
     *  El usuario NUNCA edita esto desde el form — para cambiar de
     *  empresa tiene que navegar a /t/{otro}/login. */
    tenantSlug = 'acten';
    /** True si la URL incluye un slug explícito (/t/:slug/ o subdominio).
     *  Cuando es true mostramos el chip readonly en el form como
     *  contexto, no como campo editable. */
    isTenantUrl = false;

    /** Marca white-label expuesta al template (logo, nombre, colores). */
    readonly branding = inject(BrandingService);
    private readonly tenants = inject(TenantService);
    readonly year = new Date().getFullYear();

    constructor(
        private authService: AuthService,
        private router: Router,
    ) {}

    ngOnInit() {
        // El slug viene exclusivamente de la URL (path /t/:slug/, subdominio
        // o dominio custom). Nunca lo edita el usuario desde el form.
        this.tenantSlug = this.tenants.slug();
        this.isTenantUrl = this.tenants.isExplicit();

        if (this.authService.token) {
            this.router.navigate(['/admin/dashboard']);
        }
    }

    /** Link "Olvidaste contraseña" — preserva el prefix de URL del tenant
     *  para que la pantalla de recuperación siga estando en el mismo
     *  workspace. Ej: /t/nexura/login → /t/nexura/forgot-password. */
    get forgotPasswordUrl(): string {
        if (this.isTenantUrl && this.tenantSlug && this.tenantSlug !== 'acten') {
            return `/t/${this.tenantSlug}/forgot-password`;
        }
        return '/forgot-password';
    }

    togglePassword() {
        this.showPassword = !this.showPassword;
    }

    /** SSO + alta de cuenta: visualmente presentes para coincidir con el
     *  mockup, pero el flujo todavía no está cableado. Mostramos un mensaje
     *  efímero en pantalla en lugar de un toast (para no añadir dependencias). */
    ssoUnavailable(ev?: Event) {
        ev?.preventDefault();
        this.errorMessage = 'SSO y registro auto-servicio aún no disponibles. Pide acceso a tu administrador.';
        setTimeout(() => { if (this.errorMessage.startsWith('SSO')) this.errorMessage = ''; }, 4000);
    }

    onSubmit() {
        if (!this.email || !this.password) return;

        this.isLoading = true;
        this.errorMessage = '';

        this.authService.login(this.email, this.password, this.tenantSlug).subscribe({
            next: (res) => {
                this.isLoading = false;
                // Refrescar marca con la del tenant en el que entró.
                this.branding.loadFromServer();
                this.router.navigate(['/admin/dashboard']);
            },
            error: (err) => {
                this.isLoading = false;
                if (err.status === 401) {
                    this.errorMessage = 'Correo o contraseña incorrectos.';
                } else if (err.status === 404) {
                    this.errorMessage = `La empresa "${this.tenantSlug}" no existe o está inactiva.`;
                } else {
                    this.errorMessage = 'Error conectando al servidor. Inténtalo más tarde.';
                }
            }
        });
    }
}
