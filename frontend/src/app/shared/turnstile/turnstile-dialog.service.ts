/**
 * Servicio singleton para pedir un token de Turnstile bajo demanda.
 *
 * Caso de uso: formularios en los que montar el widget inline sería ruidoso
 * (settings, uploads, dispatches). El flujo:
 *
 *   const token = await this.turnstileDialog.verify('change_password');
 *   if (token) this.http.post(url, body, { headers: { 'cf-turnstile-token': token } })
 *
 * Levanta un overlay sencillo creado directamente en el DOM (sin Angular
 * componentes dinámicos) con el widget renderizado. Cuando Cloudflare entrega
 * el token, resuelve la promesa y limpia. Cancelar o un error rechazan.
 */

import { Injectable, inject } from '@angular/core';
import { environment } from '../../../environments/environment';
import { TurnstileLoaderService } from './turnstile.component';

const TEST_SITE_KEY = '1x00000000000000000000AA';

@Injectable({ providedIn: 'root' })
export class TurnstileDialogService {
  private loader = inject(TurnstileLoaderService);

  async verify(action: string): Promise<string> {
    await this.loader.load();
    if (!window.turnstile) throw new Error('turnstile_unavailable');
    return new Promise<string>((resolve, reject) => {
      const overlay = this.buildOverlay();
      const { host, cancelBtn, cleanup } = overlay;
      cancelBtn.onclick = () => { cleanup(); reject(new Error('cancelled')); };
      const sitekey = (environment.turnstileSiteKey || '').trim() || TEST_SITE_KEY;
      try {
        window.turnstile!.render(host, {
          sitekey,
          action,
          theme: 'auto',
          callback: (token: string) => { cleanup(); resolve(token); },
          'error-callback': (code?: string) => { cleanup(); reject(new Error(code || 'turnstile_error')); },
          'expired-callback': () => { cleanup(); reject(new Error('turnstile_expired')); },
        });
      } catch (e) {
        cleanup();
        reject(e);
      }
    });
  }

  private buildOverlay(): {
    host: HTMLDivElement;
    cancelBtn: HTMLButtonElement;
    cleanup: () => void;
  } {
    const overlay = document.createElement('div');
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.style.cssText =
      'position:fixed;inset:0;background:rgba(15,23,42,.55);display:flex;align-items:center;justify-content:center;z-index:10000;padding:24px;';
    const card = document.createElement('div');
    card.style.cssText =
      'background:#fff;padding:28px 24px;border-radius:16px;box-shadow:0 24px 60px rgba(0,0,0,.25);text-align:center;max-width:360px;width:100%;font-family:inherit;';
    const title = document.createElement('p');
    title.textContent = 'Verificación de seguridad';
    title.style.cssText = 'margin:0 0 6px;font-weight:600;color:#111827;font-size:16px;';
    const subtitle = document.createElement('p');
    subtitle.textContent = 'Confirma que no eres un bot para continuar.';
    subtitle.style.cssText = 'margin:0 0 20px;color:#6b7280;font-size:13px;line-height:1.4;';
    const host = document.createElement('div');
    host.style.cssText = 'display:flex;justify-content:center;min-height:65px;';
    const cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.textContent = 'Cancelar';
    cancelBtn.style.cssText =
      'margin-top:20px;background:none;border:none;color:#64748b;cursor:pointer;font-size:13px;font-weight:500;padding:6px 12px;';
    card.appendChild(title);
    card.appendChild(subtitle);
    card.appendChild(host);
    card.appendChild(cancelBtn);
    overlay.appendChild(card);
    document.body.appendChild(overlay);
    const cleanup = () => {
      try { overlay.remove(); } catch { /* DOM pudo irse con la navegación */ }
    };
    return { host, cancelBtn, cleanup };
  }
}
