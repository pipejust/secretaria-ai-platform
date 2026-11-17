import { Component, OnInit, ViewChild, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { RouterModule } from '@angular/router';
import { environment } from '../../../environments/environment';
import { BrandingService } from '../../services/branding.service';
import { TenantService } from '../../services/tenant.service';
import { TranslateModule } from '@ngx-translate/core';
import { LanguageSelectorComponent } from '../shared/language-selector/language-selector.component';
import { TurnstileComponent } from '../../shared/turnstile/turnstile.component';

/**
 * Forgot Password — réplica del layout split del login.
 * Aside navy izq + main cream der. Multi-tenant: envía el slug junto con
 * el email para que el backend resuelva el workspace correcto.
 */
@Component({
    selector: 'app-forgot-password',
    standalone: true,
    imports: [CommonModule, FormsModule, RouterModule, TranslateModule, LanguageSelectorComponent, TurnstileComponent],
    templateUrl: './forgot-password.html',
    styleUrl: './forgot-password.css',
})
export class ForgotPassword implements OnInit {
    email = '';
    isLoading = false;
    successMessage = '';
    errorMessage = '';

    /** Multi-tenant: empresa contra la que se solicita el reseteo. */
    tenantSlug = 'acten';
    tenantLocked = false;
    showTenantField = false;

    // Turnstile
    turnstileToken = '';
    @ViewChild('ts') ts?: TurnstileComponent;

    readonly branding = inject(BrandingService);
    private readonly tenants = inject(TenantService);
    private readonly http = inject(HttpClient);
    readonly year = new Date().getFullYear();

    ngOnInit() {
        this.tenantSlug = this.tenants.slug();
        this.tenantLocked = this.tenants.isExplicit();
        this.showTenantField = !this.tenants.isDefault() || this.tenantLocked;
    }

    revealTenantField() {
        this.showTenantField = true;
        if (this.tenantSlug === 'acten') this.tenantSlug = '';
        setTimeout(() => {
            const el = document.getElementById('tenantSlug') as HTMLInputElement | null;
            el?.focus();
        }, 50);
    }

    onSubmit() {
        if (!this.email) return;
        if (!this.turnstileToken) {
            this.errorMessage = 'Espera un momento a que la verificación de seguridad termine de cargar.';
            return;
        }

        this.isLoading = true;
        this.successMessage = '';
        this.errorMessage = '';

        const payload = {
            email: this.email,
            tenant_slug: this.tenantSlug || undefined,
        };
        const headers = new HttpHeaders({ 'cf-turnstile-token': this.turnstileToken });

        this.http
            .post<{ msg: string }>(`${environment.apiUrl}/auth/forgot-password`, payload, { headers })
            .subscribe({
                next: (response) => {
                    this.isLoading = false;
                    this.successMessage = response.msg;
                    // Limpio el email para evitar reenvío accidental al hacer
                    // doble click. El user-flow correcto es revisar la bandeja.
                    this.email = '';
                },
                error: (err) => {
                    this.isLoading = false;
                    this.ts?.reset();
                    this.turnstileToken = '';
                    const code = err?.error?.detail?.error_code;
                    if (code === 'turnstile_required' || code === 'turnstile_failed' || code === 'turnstile_action_mismatch') {
                        this.errorMessage = 'La verificación de seguridad no pasó. Inténtalo de nuevo.';
                    } else if (err.status === 404) {
                        this.errorMessage = `La empresa "${this.tenantSlug}" no existe o está inactiva.`;
                    } else {
                        this.errorMessage =
                            'No pudimos procesar tu solicitud. Inténtalo de nuevo en unos minutos.';
                    }
                },
            });
    }
}
