import {
  ApplicationConfig,
  inject,
  provideAppInitializer,
  provideBrowserGlobalErrorListeners,
  provideZoneChangeDetection,
} from '@angular/core';
import { provideRouter } from '@angular/router';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { provideTranslateService } from '@ngx-translate/core';
import { provideTranslateHttpLoader } from '@ngx-translate/http-loader';
import { authInterceptor } from './interceptors/auth-interceptor';
import { errorInterceptor } from './interceptors/error.interceptor';
import { tenantInterceptor } from './interceptors/tenant.interceptor';
import { BrandingService } from './services/branding.service';
import { LanguageService } from './services/language.service';
import { TitleService } from './services/title.service';

import { routes } from './app.routes';

/**
 * Bug histórico fixed: el proyecto se había quedado SIN Zone.js y SIN
 * `provideZoneChangeDetection`/`provideZonelessChangeDetection`, así que la
 * detección de cambios no se disparaba dentro de los callbacks async (HTTP
 * subscribe, setTimeout, etc.). Síntoma reportado: "el botón de iniciar
 * sesión se queda en 'Verificando...' tras un 401" — el `error` callback sí
 * se ejecutaba pero la vista no se refrescaba.
 *
 * Activamos Zone.js + `eventCoalescing` para que TODA la app reaccione
 * automáticamente a mutaciones en callbacks async, evitando tener que
 * inyectar `ChangeDetectorRef` en cada componente.
 *
 * White-label: cargamos la marca antes de que arranque cualquier ruta para
 * evitar el flash de "Acten" → marca cliente y para tener los colores
 * aplicados desde la primera pintura.
 */
export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideZoneChangeDetection({ eventCoalescing: true }),
    provideRouter(routes),
    provideHttpClient(withInterceptors([tenantInterceptor, authInterceptor, errorInterceptor])),
    // i18n provider — registra TranslateService + el loader HTTP que lee
    // /assets/i18n/{lang}.json. El LanguageService.bootstrap() llamado vía
    // APP_INITIALIZER aplica el idioma elegido (user > localStorage >
    // navigator > 'es') antes de que arranquen las rutas.
    provideTranslateService({ fallbackLang: 'es' }),
    provideTranslateHttpLoader({ prefix: '/assets/i18n/', suffix: '.json' }),
    provideAppInitializer(() => inject(BrandingService).loadFromServer()),
    provideAppInitializer(() => inject(LanguageService).bootstrap()),
    // El TitleService se arranca DESPUÉS del LanguageService porque depende
    // del fallbackLang y de las traducciones cargadas para resolver los
    // labels de `route_titles.*`. No bloquea — solo registra listeners.
    provideAppInitializer(() => inject(TitleService).start()),
  ],
};
