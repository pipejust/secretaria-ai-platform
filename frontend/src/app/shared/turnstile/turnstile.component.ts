/**
 * Cloudflare Turnstile — widget que emite un token verificable por el backend.
 *
 * Cada envío sensible (login, contacto, forgot-password, uploads, etc.)
 * renderiza un <app-turnstile [action]="..."> y espera a (tokenChange) antes
 * de habilitar el botón de submit. El token caduca a los 5 minutos y es de
 * un solo uso; renew() pide uno nuevo tras un fallo del servidor.
 *
 * El script del widget se inyecta una sola vez (TurnstileLoaderService) y
 * se reutiliza. Si `environment.turnstileSiteKey` está vacío, se usa la
 * site key oficial de prueba de Cloudflare (siempre pasa, nunca falla) para
 * no bloquear dev local y CI.
 */

import {
  AfterViewInit,
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  EventEmitter,
  Injectable,
  Input,
  NgZone,
  OnDestroy,
  Output,
  ViewChild,
} from '@angular/core';
import { environment } from '../../../environments/environment';

/** Site key oficial de Cloudflare "always passes" para dev/CI. */
const TEST_SITE_KEY = '1x00000000000000000000AA';

interface TurnstileGlobal {
  render(
    container: HTMLElement,
    opts: {
      sitekey: string;
      action?: string;
      callback?: (token: string) => void;
      'error-callback'?: (code?: string) => void;
      'expired-callback'?: () => void;
      'timeout-callback'?: () => void;
      theme?: 'light' | 'dark' | 'auto';
      size?: 'normal' | 'compact' | 'flexible';
    },
  ): string;
  reset(widgetId: string): void;
  remove(widgetId: string): void;
}

declare global {
  interface Window {
    turnstile?: TurnstileGlobal;
  }
}

@Injectable({ providedIn: 'root' })
export class TurnstileLoaderService {
  private loading: Promise<void> | null = null;

  load(): Promise<void> {
    if (window.turnstile) return Promise.resolve();
    if (this.loading) return this.loading;
    this.loading = new Promise<void>((resolve, reject) => {
      const s = document.createElement('script');
      s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
      s.async = true;
      s.defer = true;
      s.onload = () => resolve();
      s.onerror = () => {
        this.loading = null;
        reject(new Error('turnstile_script_failed'));
      };
      document.head.appendChild(s);
    });
    return this.loading;
  }
}

@Component({
  selector: 'app-turnstile',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<div #host class="turnstile-host"></div>`,
  styles: [
    `.turnstile-host { min-height: 65px; display: inline-block; }`,
  ],
})
export class TurnstileComponent implements AfterViewInit, OnDestroy {
  /** Acción declarada por el flujo (debe coincidir con la que exige el backend). */
  @Input({ required: true }) action!: string;
  /** Tema del widget. */
  @Input() theme: 'light' | 'dark' | 'auto' = 'auto';
  /** Emite el token cuando Cloudflare lo entrega. Vacío si expira. */
  @Output() tokenChange = new EventEmitter<string>();
  /** Emite si Cloudflare rechaza el challenge. */
  @Output() error = new EventEmitter<string>();

  @ViewChild('host', { static: true }) hostRef!: ElementRef<HTMLDivElement>;
  private widgetId: string | null = null;

  constructor(private loader: TurnstileLoaderService, private zone: NgZone) {}

  async ngAfterViewInit(): Promise<void> {
    try {
      await this.loader.load();
    } catch (e) {
      this.zone.run(() => this.error.emit('script_failed'));
      return;
    }
    this.renderWidget();
  }

  /** Vuelve a pedir un token al widget: usar tras un fallo de Turnstile en el servidor. */
  reset(): void {
    if (this.widgetId && window.turnstile) {
      window.turnstile.reset(this.widgetId);
      this.tokenChange.emit('');
    }
  }

  ngOnDestroy(): void {
    if (this.widgetId && window.turnstile) {
      try { window.turnstile.remove(this.widgetId); } catch { /* el widget pudo morir con el DOM */ }
    }
  }

  private renderWidget(): void {
    if (!window.turnstile) return;
    const sitekey = (environment.turnstileSiteKey || '').trim() || TEST_SITE_KEY;
    this.widgetId = window.turnstile.render(this.hostRef.nativeElement, {
      sitekey,
      action: this.action,
      theme: this.theme,
      callback: (token: string) => this.zone.run(() => this.tokenChange.emit(token)),
      'expired-callback': () => this.zone.run(() => this.tokenChange.emit('')),
      'timeout-callback': () => this.zone.run(() => this.tokenChange.emit('')),
      'error-callback': (code?: string) => this.zone.run(() => this.error.emit(code || 'error')),
    });
  }
}
