/**
 * <app-language-selector> — dropdown para cambiar idioma (es | ca | en).
 *
 * Variantes:
 *   variant="compact" — solo bandera/código (header del login/landing)
 *   variant="full"    — bandera + nombre completo (settings)
 *
 * Al cambiar, llama a LanguageService.setLanguage() que:
 *  - aplica el idioma globalmente
 *  - persiste en localStorage
 *  - sincroniza con server si hay token (PATCH /auth/me)
 */
import { Component, Input, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { TranslateModule } from '@ngx-translate/core';
import { LanguageService, SupportedLang } from '../../../services/language.service';

@Component({
    selector: 'app-language-selector',
    standalone: true,
    imports: [CommonModule, FormsModule, TranslateModule],
    template: `
        <div class="lang-selector" [class.lang-selector--compact]="variant === 'compact'"
             [class.lang-selector--full]="variant === 'full'">
            <label *ngIf="variant === 'full'" class="lang-selector__label">
                {{ 'lang.selector_label' | translate }}
            </label>
            <div class="lang-selector__wrap">
                <svg class="lang-selector__icon" width="16" height="16" viewBox="0 0 24 24"
                     fill="none" stroke="currentColor" stroke-width="2"
                     stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <circle cx="12" cy="12" r="10"></circle>
                    <line x1="2" y1="12" x2="22" y2="12"></line>
                    <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path>
                </svg>
                <select [ngModel]="lang.currentLang()"
                        (ngModelChange)="onChange($event)"
                        class="lang-selector__select"
                        [attr.aria-label]="'lang.selector_label' | translate">
                    <option *ngFor="let l of lang.supportedLangs" [value]="l.code">
                        <ng-container *ngIf="variant === 'compact'">{{ l.code | uppercase }}</ng-container>
                        <ng-container *ngIf="variant !== 'compact'">{{ l.label }}</ng-container>
                    </option>
                </select>
                <svg class="lang-selector__chev" width="12" height="12" viewBox="0 0 24 24"
                     fill="none" stroke="currentColor" stroke-width="2"
                     stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <polyline points="6 9 12 15 18 9"></polyline>
                </svg>
            </div>
        </div>
    `,
    styles: [`
        :host { display: inline-block; }
        .lang-selector__label {
            display: block;
            font-size: 13px;
            font-weight: 600;
            color: #334155;
            margin-bottom: 6px;
        }
        .lang-selector__wrap {
            position: relative;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 6px 28px 6px 10px;
            border: 1px solid rgba(255,255,255,0.18);
            border-radius: 8px;
            background: transparent;
            color: inherit;
            transition: background 150ms, border-color 150ms;
        }
        .lang-selector__wrap:hover {
            background: rgba(255,255,255,0.06);
            border-color: rgba(255,255,255,0.30);
        }
        .lang-selector__icon { color: inherit; opacity: 0.72; flex-shrink: 0; }
        .lang-selector__select {
            -webkit-appearance: none;
            appearance: none;
            background: transparent;
            border: 0;
            color: inherit;
            font: inherit;
            font-size: 13px;
            font-weight: 500;
            outline: none;
            cursor: pointer;
            padding-right: 4px;
        }
        .lang-selector__select option { color: #0f172a; background: #ffffff; }
        .lang-selector__chev {
            position: absolute;
            right: 8px; top: 50%;
            transform: translateY(-50%);
            color: inherit; opacity: 0.6;
            pointer-events: none;
        }
        /* Variante full: para forms (claros). Más espacio, look de input. */
        .lang-selector--full .lang-selector__wrap {
            padding: 10px 32px 10px 12px;
            border: 1px solid #cbd5e1;
            background: #ffffff;
            color: #0f172a;
        }
        .lang-selector--full .lang-selector__wrap:hover { border-color: #94a3b8; background: #f8fafc; }
        .lang-selector--full .lang-selector__select { font-size: 14px; }
    `],
})
export class LanguageSelectorComponent {
    readonly lang = inject(LanguageService);
    @Input() variant: 'compact' | 'full' = 'compact';

    onChange(code: SupportedLang): void {
        this.lang.setLanguage(code);
    }
}
