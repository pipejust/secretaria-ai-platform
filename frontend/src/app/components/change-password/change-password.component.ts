/**
 * /change-password — pantalla de cambio obligatorio de contraseña tras el
 * primer login con must_change_password=true. NO permite navegar al
 * dashboard hasta completar el cambio (route guard hace cumplir).
 *
 * También sirve para el caso voluntario (link "Cambiar contraseña" en
 * Mi Perfil) — en ese modo exige `current_password`.
 */
import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { AuthService } from '../../services/auth.service';
import { ToastService } from '../../services/toast.service';
import { BrandingService } from '../../services/branding.service';
import { PasswordInputComponent } from '../shared/password-input/password-input.component';

@Component({
    selector: 'app-change-password',
    standalone: true,
    imports: [CommonModule, FormsModule, PasswordInputComponent],
    template: `
        <div class="cp-wrap">
            <div class="cp-card">
                <header class="cp-head">
                    <img *ngIf="branding.hasLogo()" [src]="branding.logoUrl()"
                         alt="" class="cp-logo" />
                    <h1>Establecé tu contraseña</h1>
                    <p class="cp-sub">
                        Por seguridad, tenés que reemplazar la contraseña temporal
                        por una propia antes de continuar.
                    </p>
                </header>

                <form (ngSubmit)="submit()" class="cp-form">
                    <div class="cp-field">
                        <label>Nueva contraseña</label>
                        <app-password-input name="new_password"
                                            [(ngModel)]="newPassword"
                                            placeholder="Mínimo 8 caracteres"
                                            autocomplete="new-password"
                                            [minlength]="8"
                                            [required]="true"></app-password-input>
                        <small class="cp-hint">Usá al menos 8 caracteres. Mezclá letras, números y símbolos para mayor seguridad.</small>
                    </div>

                    <div class="cp-field">
                        <label>Confirmar nueva contraseña</label>
                        <app-password-input name="confirm_password"
                                            [(ngModel)]="confirmPassword"
                                            placeholder="Repetí la nueva contraseña"
                                            autocomplete="new-password"
                                            [minlength]="8"
                                            [required]="true"></app-password-input>
                    </div>

                    <div *ngIf="errorMessage" class="cp-error">{{ errorMessage }}</div>

                    <button type="submit" class="cp-btn" [disabled]="isSubmitting">
                        {{ isSubmitting ? 'Guardando…' : 'Cambiar contraseña y continuar' }}
                    </button>
                </form>
            </div>
        </div>
    `,
    styles: [`
        :host { display: block; min-height: 100vh; background: #f8fafc; }
        .cp-wrap {
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 24px;
        }
        .cp-card {
            width: 100%;
            max-width: 460px;
            background: #ffffff;
            border-radius: 14px;
            box-shadow: 0 20px 60px -20px rgba(15,23,42,0.18);
            padding: 36px 32px;
        }
        .cp-head { text-align: center; margin-bottom: 24px; }
        .cp-logo { height: 48px; width: auto; max-width: 220px; object-fit: contain;
                   margin: 0 auto 16px; display: block; }
        .cp-head h1 { margin: 0 0 6px; font-size: 22px; color: #0f172a; }
        .cp-sub { margin: 0; color: #64748b; font-size: 14px; line-height: 1.5; }
        .cp-form { display: flex; flex-direction: column; gap: 16px; }
        .cp-field label {
            display: block;
            font-size: 13px;
            font-weight: 600;
            color: #334155;
            margin-bottom: 6px;
        }
        .cp-field ::ng-deep .pwi-input {
            width: 100%;
            padding: 11px 42px 11px 12px;
            border: 1px solid #cbd5e1;
            border-radius: 8px;
            background: #ffffff;
            font-size: 14px;
            color: #0f172a;
            transition: border-color 150ms;
            box-sizing: border-box;
        }
        .cp-field ::ng-deep .pwi-input:focus {
            outline: none;
            border-color: #2563eb;
            box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12);
        }
        .cp-hint { display: block; margin-top: 6px; font-size: 12px; color: #64748b; }
        .cp-error {
            background: #fef2f2; border: 1px solid #fecaca;
            color: #991b1b; padding: 10px 12px; border-radius: 6px;
            font-size: 13px;
        }
        .cp-btn {
            margin-top: 6px;
            padding: 12px 16px;
            background: #2563eb;
            color: #ffffff;
            border: 0;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: background 150ms;
        }
        .cp-btn:hover:not(:disabled) { background: #1d4ed8; }
        .cp-btn:disabled { background: #94a3b8; cursor: not-allowed; }
    `],
})
export class ChangePasswordComponent {
    private auth = inject(AuthService);
    private router = inject(Router);
    private toast = inject(ToastService);
    readonly branding = inject(BrandingService);

    newPassword = '';
    confirmPassword = '';
    isSubmitting = false;
    errorMessage = '';

    submit(): void {
        this.errorMessage = '';
        const pw = (this.newPassword || '').trim();
        const cf = (this.confirmPassword || '').trim();
        if (pw.length < 8) {
            this.errorMessage = 'La contraseña debe tener al menos 8 caracteres.';
            return;
        }
        if (pw !== cf) {
            this.errorMessage = 'La confirmación no coincide.';
            return;
        }
        this.isSubmitting = true;
        // Modo forzado: NO mandamos current_password. El backend acepta porque
        // el user llegó acá con must_change_password=true.
        this.auth.changePasswordForced(pw).subscribe({
            next: () => {
                this.toast.success('Contraseña actualizada. ¡Listo!');
                this.router.navigate(['/admin/dashboard']);
            },
            error: (err) => {
                this.isSubmitting = false;
                this.errorMessage = err?.error?.detail || 'No se pudo cambiar la contraseña. Reintentá.';
            },
        });
    }
}
