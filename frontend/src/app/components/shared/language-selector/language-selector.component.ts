/**
 * <app-language-selector> — dropdown custom para cambiar idioma (es | ca | en).
 *
 * Variantes:
 *   variant="compact" — botón con código de idioma, dropdown flotante
 *                       (header del login/landing/admin)
 *   variant="full"    — input bordeado con etiqueta completa (settings/perfil)
 *
 * NO usa <select> nativo (que se ve mal sobre fondos oscuros y depende
 * del SO). Implementa su propio dropdown con buen contraste sobre cualquier
 * fondo. Cierra al click afuera o al elegir un idioma.
 */
import {
    Component, Input, inject, HostListener, ElementRef, signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { TranslateModule } from '@ngx-translate/core';
import { LanguageService, SupportedLang } from '../../../services/language.service';

@Component({
    selector: 'app-language-selector',
    standalone: true,
    imports: [CommonModule, FormsModule, TranslateModule],
    template: `
        <div class="lang-sel"
             [class.lang-sel--compact]="variant === 'compact'"
             [class.lang-sel--full]="variant === 'full'"
             [class.is-open]="open()">
            <label *ngIf="variant === 'full'" class="lang-sel__label">
                {{ 'lang.selector_label' | translate }}
            </label>
            <button type="button"
                    class="lang-sel__trigger"
                    (click)="toggle($event)"
                    [attr.aria-haspopup]="'listbox'"
                    [attr.aria-expanded]="open()"
                    [attr.aria-label]="'lang.selector_label' | translate">
                <svg class="lang-sel__icon" width="14" height="14" viewBox="0 0 24 24"
                     fill="none" stroke="currentColor" stroke-width="2"
                     stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <circle cx="12" cy="12" r="10"></circle>
                    <line x1="2" y1="12" x2="22" y2="12"></line>
                    <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path>
                </svg>
                <span class="lang-sel__text">
                    <ng-container *ngIf="variant === 'compact'">{{ currentCode().toUpperCase() }}</ng-container>
                    <ng-container *ngIf="variant !== 'compact'">{{ currentLabel() }}</ng-container>
                </span>
                <svg class="lang-sel__chev" [class.flipped]="open()"
                     width="10" height="10" viewBox="0 0 24 24" fill="none"
                     stroke="currentColor" stroke-width="2"
                     stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <polyline points="6 9 12 15 18 9"></polyline>
                </svg>
            </button>

            <div class="lang-sel__menu" *ngIf="open()" role="listbox">
                <button *ngFor="let l of lang.supportedLangs"
                        type="button" role="option"
                        class="lang-sel__option"
                        [class.is-active]="lang.currentLang() === l.code"
                        [attr.aria-selected]="lang.currentLang() === l.code"
                        (click)="select(l.code, $event)">
                    <span class="lang-sel__opt-code">{{ l.code.toUpperCase() }}</span>
                    <span class="lang-sel__opt-label">{{ l.label }}</span>
                    <svg *ngIf="lang.currentLang() === l.code"
                         class="lang-sel__check"
                         width="14" height="14" viewBox="0 0 24 24" fill="none"
                         stroke="currentColor" stroke-width="2.5"
                         stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="20 6 9 17 4 12"></polyline>
                    </svg>
                </button>
            </div>
        </div>
    `,
    styles: [`
        :host { display: inline-block; position: relative; }
        .lang-sel { position: relative; display: inline-block; }
        .lang-sel__label {
            display: block; font-size: 13px; font-weight: 600;
            color: #334155; margin-bottom: 6px;
        }
        .lang-sel__trigger {
            display: inline-flex; align-items: center; gap: 8px;
            padding: 7px 10px 7px 12px;
            background: rgba(255,255,255,0.06);
            border: 1px solid rgba(255,255,255,0.14);
            border-radius: 10px;
            color: inherit; font: inherit;
            font-size: 13px; font-weight: 500;
            cursor: pointer; outline: none;
            transition: background 150ms, border-color 150ms;
        }
        .lang-sel__trigger:hover {
            background: rgba(255,255,255,0.12);
            border-color: rgba(255,255,255,0.28);
        }
        .lang-sel.is-open .lang-sel__trigger {
            background: rgba(255,255,255,0.14);
            border-color: rgba(255,255,255,0.34);
        }
        .lang-sel__trigger:focus-visible {
            outline: 2px solid currentColor;
            outline-offset: 1px;
        }
        .lang-sel__icon { opacity: 0.72; flex-shrink: 0; }
        .lang-sel__text { letter-spacing: 0.02em; }
        .lang-sel__chev {
            opacity: 0.7; transition: transform 180ms;
        }
        .lang-sel__chev.flipped { transform: rotate(180deg); }

        /* Variante full (settings, perfil — fondo claro) */
        .lang-sel--full .lang-sel__trigger {
            background: #ffffff;
            border-color: #cbd5e1;
            color: #0f172a;
            padding: 10px 12px 10px 14px;
            font-size: 14px;
            min-width: 220px;
            justify-content: space-between;
        }
        .lang-sel--full .lang-sel__trigger:hover {
            background: #f8fafc;
            border-color: #94a3b8;
        }
        .lang-sel--full.is-open .lang-sel__trigger {
            border-color: #2563eb;
            box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12);
        }

        /* Dropdown menu */
        .lang-sel__menu {
            position: absolute; top: calc(100% + 6px); right: 0;
            min-width: 180px; padding: 6px;
            background: #ffffff;
            border: 1px solid rgba(15, 23, 42, 0.08);
            border-radius: 12px;
            box-shadow:
                0 12px 32px -8px rgba(15, 23, 42, 0.28),
                0 4px 12px -4px rgba(15, 23, 42, 0.12);
            z-index: 1000;
            animation: lang-sel-fade 140ms cubic-bezier(0.16, 1, 0.3, 1);
        }
        @keyframes lang-sel-fade {
            from { opacity: 0; transform: translateY(-4px); }
            to   { opacity: 1; transform: translateY(0); }
        }
        .lang-sel__option {
            display: flex; align-items: center; gap: 10px;
            width: 100%;
            padding: 9px 12px;
            background: transparent; border: 0;
            font: inherit; font-size: 13.5px;
            color: #334155; cursor: pointer;
            border-radius: 8px;
            text-align: left;
            transition: background 120ms;
        }
        .lang-sel__option:hover {
            background: #f1f5f9; color: #0f172a;
        }
        .lang-sel__option.is-active {
            background: #eff6ff; color: #1d4ed8; font-weight: 600;
        }
        .lang-sel__opt-code {
            display: inline-flex; align-items: center; justify-content: center;
            min-width: 28px; padding: 2px 6px;
            background: #f1f5f9; color: #475569;
            border-radius: 4px;
            font-size: 11px; font-weight: 700; letter-spacing: 0.06em;
        }
        .lang-sel__option.is-active .lang-sel__opt-code {
            background: #dbeafe; color: #1d4ed8;
        }
        .lang-sel__opt-label { flex: 1; }
        .lang-sel__check { color: #1d4ed8; flex-shrink: 0; }

        /* Full variant: menu posicionado a la izquierda para alinear con label */
        .lang-sel--full .lang-sel__menu { left: 0; right: auto; min-width: 100%; }
    `],
})
export class LanguageSelectorComponent {
    readonly lang = inject(LanguageService);
    private readonly host = inject(ElementRef<HTMLElement>);
    @Input() variant: 'compact' | 'full' = 'compact';

    readonly open = signal(false);

    currentCode(): SupportedLang { return this.lang.currentLang(); }
    currentLabel(): string {
        const code = this.lang.currentLang();
        return this.lang.supportedLangs.find(l => l.code === code)?.label ?? code;
    }

    toggle(ev: Event): void {
        ev.stopPropagation();
        this.open.update(o => !o);
    }

    select(code: SupportedLang, ev: Event): void {
        ev.stopPropagation();
        this.open.set(false);
        // Si cambia idioma, aplicar + sync con server (LanguageService decide).
        if (code !== this.lang.currentLang()) {
            this.lang.setLanguage(code);
        }
    }

    /** Cerrar dropdown al click fuera. */
    @HostListener('document:click', ['$event'])
    onDocClick(ev: MouseEvent): void {
        if (!this.open()) return;
        if (!this.host.nativeElement.contains(ev.target as Node)) {
            this.open.set(false);
        }
    }

    /** Cerrar con Escape. */
    @HostListener('document:keydown.escape')
    onEsc(): void { this.open.set(false); }
}
