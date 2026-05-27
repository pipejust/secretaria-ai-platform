/**
 * <app-password-input> — input de contraseña con toggle ojo/ojo-tachado.
 *
 * Reemplaza cualquier `<input type="password" [(ngModel)]="...">` regular
 * dándole al usuario la opción de ver/ocultar lo que escribe (o lo que
 * tiene pre-rellenado). Soporta ngModel para usarse igual que un input
 * nativo en formularios template-driven.
 *
 * Uso:
 *   <app-password-input [(ngModel)]="form.password"
 *                       placeholder="Mínimo 8 caracteres"
 *                       autocomplete="new-password"
 *                       [minlength]="8"
 *                       [required]="true"></app-password-input>
 *
 * También sirve para campos sensibles que NO son passwords (API keys,
 * tokens, secrets) — useCase="secret" no cambia el comportamiento pero
 * es semánticamente más claro en el call site.
 */
import {
    Component,
    Input,
    Output,
    EventEmitter,
    forwardRef,
    HostBinding,
    inject,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule, NG_VALUE_ACCESSOR, ControlValueAccessor } from '@angular/forms';
import { TranslateService } from '@ngx-translate/core';

@Component({
    selector: 'app-password-input',
    standalone: true,
    imports: [CommonModule, FormsModule],
    template: `
        <div class="pwi-wrap" [class.pwi-wrap--readonly]="readonly">
            <input
                [type]="visible ? 'text' : 'password'"
                [(ngModel)]="value"
                (ngModelChange)="onChange($event)"
                (blur)="onTouched()"
                [name]="name"
                [placeholder]="placeholder"
                [autocomplete]="autocomplete"
                [minlength]="minlength || null"
                [maxlength]="maxlength || null"
                [required]="required"
                [readonly]="readonly"
                [disabled]="disabled"
                [attr.aria-label]="ariaLabel || placeholder || defaultAriaLabel"
                class="pwi-input"
            />
            <button
                type="button"
                class="pwi-toggle"
                (click)="toggle()"
                [attr.aria-label]="visible ? hideLabel : showLabel"
                [attr.aria-pressed]="visible"
                tabindex="-1">
                <!-- Eye open: visible -->
                <svg *ngIf="visible" width="18" height="18" viewBox="0 0 24 24" fill="none"
                     stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
                    <circle cx="12" cy="12" r="3"></circle>
                </svg>
                <!-- Eye-off: hidden -->
                <svg *ngIf="!visible" width="18" height="18" viewBox="0 0 24 24" fill="none"
                     stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path>
                    <line x1="1" y1="1" x2="23" y2="23"></line>
                </svg>
            </button>
        </div>
    `,
    styles: [`
        :host { display: block; width: 100%; }
        .pwi-wrap {
            position: relative;
            display: flex;
            align-items: stretch;
            width: 100%;
        }
        .pwi-input {
            flex: 1;
            width: 100%;
            padding-right: 42px;  /* espacio para el botón */
            font-family: inherit;
            font-size: inherit;
            line-height: inherit;
            /* No imponemos color/border/background — heredamos del input host
               para que se integre con el estilo del form contenedor (login,
               settings, integrations, etc.). */
        }
        .pwi-toggle {
            position: absolute;
            right: 4px;
            top: 50%;
            transform: translateY(-50%);
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 34px;
            height: 34px;
            border: 0;
            background: transparent;
            color: #64748b;
            cursor: pointer;
            border-radius: 6px;
            transition: color 150ms, background 150ms;
            padding: 0;
        }
        .pwi-toggle:hover { color: #0f172a; background: rgba(15, 23, 42, 0.05); }
        .pwi-toggle:focus-visible {
            outline: 2px solid #2563eb;
            outline-offset: 1px;
        }
        .pwi-wrap--readonly .pwi-toggle { color: #94a3b8; }
    `],
    providers: [
        {
            provide: NG_VALUE_ACCESSOR,
            useExisting: forwardRef(() => PasswordInputComponent),
            multi: true,
        },
    ],
})
export class PasswordInputComponent implements ControlValueAccessor {
    /** Estado del toggle. Por default oculto (como un <input type=password> regular). */
    visible = false;

    /** Valor interno, sincronizado vía ngModel. */
    value = '';

    @Input() name = '';
    @Input() placeholder = '';
    @Input() autocomplete: string = 'new-password';
    @Input() minlength: number | null = null;
    @Input() maxlength: number | null = null;
    @Input() required = false;
    @Input() readonly = false;
    @Input() disabled = false;
    @Input() ariaLabel = '';
    /** Semántico — 'password' (default), 'secret' (API key / token).
     *  No cambia comportamiento pero documenta el call site. */
    @Input() useCase: 'password' | 'secret' = 'password';

    @Output() valueChange = new EventEmitter<string>();

    @HostBinding('class.pwi-host') hostClass = true;

    private readonly translate = inject(TranslateService);

    /** Labels accesibles traducidos en tiempo de uso. Usamos `instant`
     *  porque las traducciones están listas tras APP_INITIALIZER y el
     *  componente nunca se renderiza antes. Si en algún caso límite la
     *  key no resuelve, ngx-translate devuelve la propia key, lo cual
     *  sigue siendo aceptable para un screen-reader. */
    get showLabel(): string {
        return this.translate.instant('password_input.show_password');
    }
    get hideLabel(): string {
        return this.translate.instant('password_input.hide_password');
    }
    get defaultAriaLabel(): string {
        return this.translate.instant('password_input.password');
    }

    toggle(): void { this.visible = !this.visible; }

    // ── ControlValueAccessor — para integrarse con [(ngModel)] ─────────
    onChange: (v: string) => void = () => {};
    onTouched: () => void = () => {};

    writeValue(v: string | null): void {
        this.value = v ?? '';
    }
    registerOnChange(fn: (v: string) => void): void { this.onChange = fn; }
    registerOnTouched(fn: () => void): void { this.onTouched = fn; }
    setDisabledState(isDisabled: boolean): void { this.disabled = isDisabled; }
}
