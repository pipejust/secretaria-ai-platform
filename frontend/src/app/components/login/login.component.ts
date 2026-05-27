import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterModule } from '@angular/router';
import { AuthService } from '../../services/auth.service';
import { BrandingService } from '../../services/branding.service';
import { LanguageService } from '../../services/language.service';
import { LanguageSelectorComponent } from '../shared/language-selector/language-selector.component';
import { TranslateModule } from '@ngx-translate/core';
import { TenantService } from '../../services/tenant.service';

@Component({
    selector: 'app-login',
    standalone: true,
    imports: [CommonModule, FormsModule, RouterModule, TranslateModule, LanguageSelectorComponent],
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

    // ─── 2FA challenge ───────────────────────────────────────────
    awaiting2FA = false;
    code2FA = '';
    emailMasked = '';
    isVerifying2FA = false;

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
    readonly lang = inject(LanguageService);
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

        // Normalizar email INMEDIATAMENTE: trim + lowercase. Aunque el
        // backend ya lo hace vía Pydantic validator (defensa real), aquí
        // damos feedback visual instantáneo y evitamos que el campo
        // quede con "Felipe@SoftnexusIO" después de tipear con mayúsculas.
        // Refleja el cambio en el ngModel para que el input lo muestre.
        this.email = (this.email || '').trim().toLowerCase();

        this.isLoading = true;
        this.errorMessage = '';

        this.authService.login(this.email, this.password, this.tenantSlug).subscribe({
            next: (res) => {
                this.isLoading = false;
                // El backend pide 2FA antes de emitir el token.
                if (res?.two_factor_required) {
                    this.awaiting2FA = true;
                    this.emailMasked = res.email_masked || this.email;
                    this.code2FA = '';
                    return;
                }
                this.branding.loadFromServer();
                // Sync de idioma desde el perfil del user — si tiene una
                // preferencia distinta a la actual, la aplica antes de navegar.
                if (res?.language) this.lang.syncFromUserProfile(res.language);
                // Si el backend devuelve must_change_password=true, redirigir
                // a la pantalla de cambio obligatorio antes del dashboard.
                if (res?.must_change_password) {
                    this.router.navigate(['/change-password']);
                    return;
                }
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

    verify2FA() {
        if (!this.code2FA || this.code2FA.trim().length < 6) {
            this.errorMessage = 'Introduce el código de 6 dígitos que te enviamos por correo.';
            return;
        }
        this.isVerifying2FA = true;
        this.errorMessage = '';
        this.authService.verifyLogin2FA(this.email, this.code2FA.trim(), this.tenantSlug).subscribe({
            next: (res) => {
                this.isVerifying2FA = false;
                this.branding.loadFromServer();
                if (res?.must_change_password) {
                    this.router.navigate(['/change-password']);
                    return;
                }
                this.router.navigate(['/admin/dashboard']);
            },
            error: (err) => {
                this.isVerifying2FA = false;
                this.errorMessage = err?.error?.detail || 'Código incorrecto. Inténtalo nuevamente.';
            },
        });
    }

    cancel2FA() {
        this.awaiting2FA = false;
        this.code2FA = '';
        this.errorMessage = '';
    }

    resend2FA() {
        // Para reenviar simplemente repetimos el login (genera nuevo código).
        this.errorMessage = '';
        this.authService.login(this.email, this.password, this.tenantSlug).subscribe({
            next: () => { this.errorMessage = 'Se reenvió un nuevo código a tu correo.'; },
            error: () => { this.errorMessage = 'No se pudo reenviar el código.'; },
        });
    }
}
