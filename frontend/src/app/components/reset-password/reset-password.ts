import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { ActivatedRoute, Router, RouterModule } from '@angular/router';
import { environment } from '../../../environments/environment';
import { BrandingService } from '../../services/branding.service';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { LanguageSelectorComponent } from '../shared/language-selector/language-selector.component';

/**
 * Reset Password — réplica del layout split del login.
 * Lee el token de la URL (?token=...), valida que las dos contraseñas
 * coincidan localmente, y POSTea a /auth/reset-password.
 */
@Component({
    selector: 'app-reset-password',
    standalone: true,
    imports: [CommonModule, FormsModule, RouterModule, TranslateModule, LanguageSelectorComponent],
    templateUrl: './reset-password.html',
    styleUrl: './reset-password.css',
})
export class ResetPassword implements OnInit {
    token = '';
    newPassword = '';
    confirmPassword = '';
    showPassword = false;

    isLoading = false;
    successMessage = '';
    errorMessage = '';
    /** True si el token ni siquiera vino en la URL — caso terminal. */
    tokenMissing = false;

    readonly branding = inject(BrandingService);
    private readonly http = inject(HttpClient);
    private readonly route = inject(ActivatedRoute);
    private readonly router = inject(Router);
    private readonly translate = inject(TranslateService);
    readonly year = new Date().getFullYear();

    ngOnInit() {
        this.route.queryParams.subscribe((params) => {
            this.token = params['token'] || '';
            if (!this.token) {
                this.tokenMissing = true;
                this.errorMessage = this.translate.instant('reset_password.errors.invalid_link');
            }
        });
    }

    togglePassword() {
        this.showPassword = !this.showPassword;
    }

    /** Diagnóstico simple para mostrar en UI sin lib externa. Aplica al
     *  `newPassword`; no es la fuente de verdad — el backend re-valida.
     *  Las labels se traducen vía i18n para que el medidor cambie con el
     *  idioma seleccionado. */
    get passwordStrength(): { score: number; label: string } {
        const pwd = this.newPassword;
        if (!pwd) return { score: 0, label: '' };
        let score = 0;
        if (pwd.length >= 8) score++;
        if (/[A-Z]/.test(pwd)) score++;
        if (/[0-9]/.test(pwd)) score++;
        if (/[^A-Za-z0-9]/.test(pwd)) score++;
        const keys = [
            'reset_password.strength.very_weak',
            'reset_password.strength.weak',
            'reset_password.strength.acceptable',
            'reset_password.strength.good',
            'reset_password.strength.excellent',
        ];
        return { score, label: this.translate.instant(keys[score]) };
    }

    onSubmit() {
        if (!this.token || !this.newPassword || !this.confirmPassword) return;

        if (this.newPassword.length < 8) {
            this.errorMessage = this.translate.instant('reset_password.errors.min_length');
            return;
        }

        if (this.newPassword !== this.confirmPassword) {
            this.errorMessage = this.translate.instant('reset_password.errors.mismatch_simple');
            return;
        }

        this.isLoading = true;
        this.successMessage = '';
        this.errorMessage = '';

        const payload = { token: this.token, new_password: this.newPassword };

        this.http
            .post<{ msg: string }>(`${environment.apiUrl}/auth/reset-password`, payload)
            .subscribe({
                next: (response) => {
                    this.isLoading = false;
                    this.successMessage = response.msg;
                    // Limpio inputs por seguridad/visual.
                    this.newPassword = '';
                    this.confirmPassword = '';
                    // Redirección automática al login a los 3.5s. El usuario
                    // sigue viendo el éxito y puede usar el botón si prefiere.
                    setTimeout(() => this.router.navigate(['/login']), 3500);
                },
                error: (err) => {
                    this.isLoading = false;
                    this.errorMessage =
                        err.error?.detail ||
                        this.translate.instant('reset_password.errors.expired_link');
                    console.error('Reset password error:', err);
                },
            });
    }
}
