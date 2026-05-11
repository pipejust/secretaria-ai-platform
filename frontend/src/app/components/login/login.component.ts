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

    /** Multi-tenant: empresa contra la que se loguea. */
    tenantSlug = 'acten';
    /** True si el slug viene de URL/host (no editable). */
    tenantLocked = false;
    /** Mostrar el campo solo si NO viene forzado por URL/host. */
    showTenantField = true;

    /** Marca white-label expuesta al template (logo, nombre, colores). */
    readonly branding = inject(BrandingService);
    private readonly tenants = inject(TenantService);

    constructor(private authService: AuthService, private router: Router) { }

    ngOnInit() {
        // Pre-fill del tenant. Si viene de URL/host, lo bloqueamos para no
        // dejar al usuario equivocarse (ya está en el contexto correcto).
        this.tenantSlug = this.tenants.slug();
        this.tenantLocked = this.tenants.isExplicit();
        // Si ya estamos en el tenant default y nadie lo forzó, ocultamos el
        // campo para no abrumar — el usuario típico ni sabe lo que es.
        this.showTenantField = !this.tenants.isDefault() || this.tenantLocked;

        if (this.authService.token) {
            this.router.navigate(['/admin/dashboard']);
        }
    }

    togglePassword() {
        this.showPassword = !this.showPassword;
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
                    this.errorMessage = 'Correo o contraseña incorrectos';
                } else {
                    this.errorMessage = 'Error conectando al servidor. Inténtalo más tarde.';
                }
            }
        });
    }
}
