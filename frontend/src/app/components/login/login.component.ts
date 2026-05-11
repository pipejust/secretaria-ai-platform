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

    /** Multi-tenant: empresa contra la que se loguea. */
    tenantSlug = 'acten';
    /** True si el slug viene de URL/host (no editable). */
    tenantLocked = false;
    /** Mostrar el campo solo si NO viene forzado por URL/host. */
    showTenantField = true;

    /** Marca white-label expuesta al template (logo, nombre, colores). */
    readonly branding = inject(BrandingService);
    private readonly tenants = inject(TenantService);
    readonly year = new Date().getFullYear();

    constructor(
        private authService: AuthService,
        private router: Router,
    ) {}

    ngOnInit() {
        // Pre-fill del tenant. Si viene de URL/host, lo bloqueamos para no
        // dejar al usuario equivocarse (ya está en el contexto correcto).
        this.tenantSlug = this.tenants.slug();
        this.tenantLocked = this.tenants.isExplicit();
        // Si ya estamos en el tenant default y nadie lo forzó, ocultamos el
        // campo. El usuario puede revelarlo con el link "Otra empresa".
        this.showTenantField = !this.tenants.isDefault() || this.tenantLocked;

        if (this.authService.token) {
            this.router.navigate(['/admin/dashboard']);
        }
    }

    /** Revela el campo "Empresa" para que el usuario pueda escribir un slug
     *  distinto al default (ej. para entrar a 'nexura' desde la URL default). */
    revealTenantField() {
        this.showTenantField = true;
        // Si el usuario está cambiando de empresa explícitamente, vaciamos
        // el slug por defecto para que tipee el suyo desde cero.
        if (this.tenantSlug === 'acten') this.tenantSlug = '';
        // Foco diferido al input cuando renderice.
        setTimeout(() => {
            const el = document.getElementById('tenantSlug') as HTMLInputElement | null;
            el?.focus();
        }, 50);
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
                    // Revelo el campo de empresa para que el usuario pueda
                    // probar con otro workspace (típico: credenciales de
                    // Nexura intentando entrar al tenant Acten por default).
                    if (!this.showTenantField || this.tenantSlug === 'acten') {
                        this.showTenantField = true;
                        this.errorMessage =
                            'Credenciales inválidas en este workspace. Si tu cuenta es de otra empresa, escríbela aquí abajo.';
                        setTimeout(() => {
                            (document.getElementById('tenantSlug') as HTMLInputElement | null)?.focus();
                        }, 60);
                    } else {
                        this.errorMessage = 'Correo o contraseña incorrectos';
                    }
                } else if (err.status === 404) {
                    this.errorMessage = `La empresa "${this.tenantSlug}" no existe o está inactiva.`;
                } else {
                    this.errorMessage = 'Error conectando al servidor. Inténtalo más tarde.';
                }
            }
        });
    }
}
