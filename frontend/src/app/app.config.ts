import {
  ApplicationConfig,
  inject,
  provideAppInitializer,
  provideBrowserGlobalErrorListeners,
  provideZoneChangeDetection,
} from '@angular/core';
import { provideRouter } from '@angular/router';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { authInterceptor } from './interceptors/auth-interceptor';
import { errorInterceptor } from './interceptors/error.interceptor';
import { BrandingService } from './services/branding.service';

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
    provideHttpClient(withInterceptors([authInterceptor, errorInterceptor])),
    provideAppInitializer(() => inject(BrandingService).loadFromServer()),
  ],
};
