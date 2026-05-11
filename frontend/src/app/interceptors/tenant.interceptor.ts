import { HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { TenantService } from '../services/tenant.service';

/**
 * Multi-tenant: inyecta `X-Tenant-Slug: <slug>` en TODAS las peticiones a
 * nuestro backend (no a APIs externas). Esto permite que endpoints públicos
 * como `/api/branding/` resuelvan el tenant antes de tener token, y que
 * llamadas a tenants alternos (super-admin viendo otra empresa) usen el
 * slug correcto sin tener que pasar `?tenant=` en cada URL.
 *
 * Si el JWT del usuario apunta a otro tenant, el backend ignora el header
 * — la auth siempre gana. El header solo es decisivo para endpoints sin
 * autenticación o para resolver conflictos de host vs path.
 */
export const tenantInterceptor: HttpInterceptorFn = (req, next) => {
  const ts = inject(TenantService);
  const slug = ts.slug();

  // Sólo añadimos el header a llamadas a nuestro propio API (backend).
  // Detectamos por `/api/` o `/auth/` en la URL relativa o absoluta.
  const url = req.url;
  const isOurApi = /\/api\//.test(url) || /\/auth\//.test(url);
  if (!isOurApi || !slug) {
    return next(req);
  }

  return next(
    req.clone({
      setHeaders: { 'X-Tenant-Slug': slug },
    }),
  );
};
