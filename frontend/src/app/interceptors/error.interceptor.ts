import { HttpInterceptorFn, HttpErrorResponse } from '@angular/common/http';
import { Injector, inject } from '@angular/core';
import { TranslateService } from '@ngx-translate/core';
import { throwError } from 'rxjs';
import { catchError } from 'rxjs/operators';
import { PlanGateDetail } from '../services/billing.service';
import { ToastService } from '../services/toast.service';

/**
 * Maneja errores HTTP globales. Cuando el backend devuelve 401 sobre un
 * endpoint PROTEGIDO (no el propio /auth/login ni recovery), interpretamos
 * que la sesión expiró: limpiamos el token y mandamos al usuario a /login.
 *
 * Bug histórico fixed: antes este interceptor redirigía en CUALQUIER 401,
 * incluso los del propio endpoint de login. Eso causaba que un intento de
 * login con credenciales incorrectas recargara la página antes de que el
 * componente pudiera mostrar el mensaje de error → la UI parecía "no
 * responder" (síntoma reportado por el usuario).
 *
 * 402 = la función no está en el plan de la empresa. Solo actuamos cuando
 * el cuerpo trae `detail.feature` (forma de `require_feature`); los 402
 * con `detail` de texto los maneja el componente que hizo la llamada.
 */
const AUTH_BYPASS_PATHS = [
  '/auth/login',
  '/auth/forgot-password',
  '/auth/reset-password',
];

const BILLING_ROUTE = '/admin/billing';

function isAuthEndpoint(url: string): boolean {
  return AUTH_BYPASS_PATHS.some((p) => url.includes(p));
}

function planGateDetail(error: HttpErrorResponse): PlanGateDetail | null {
  const detail: unknown = error.error?.detail;
  if (!detail || typeof detail !== 'object' || !('feature' in detail)) { return null; }
  const d = detail as Partial<PlanGateDetail>;
  return typeof d.feature === 'string' ? { ...d, feature: d.feature } as PlanGateDetail : null;
}

export const errorInterceptor: HttpInterceptorFn = (req, next) => {
  // Solo el Injector, nunca TranslateService directamente: el loader de
  // traducciones usa HttpClient, HttpClient pasa por este interceptor, y
  // pedir aquí TranslateService cerraba el ciclo. La carga de
  // /assets/i18n/*.json fallaba sin ruido y toda la app mostraba claves.
  const injector = inject(Injector);
  return next(req).pipe(
    catchError((error: HttpErrorResponse) => {
      if (error.status === 401 && !isAuthEndpoint(req.url)) {
        localStorage.removeItem('access_token');
        setTimeout(() => { window.location.href = '/login'; }, 100);
      }
      if (error.status === 402) {
        const gate = planGateDetail(error);
        if (gate) {
          const toast = injector.get(ToastService);
          const translate = injector.get(TranslateService);
          toast.warning(
            translate.instant('billing.feature_not_included', { label: gate.label || gate.feature }),
            6000,
            { label: translate.instant('billing.see_subscription'), link: BILLING_ROUTE },
          );
        }
      }
      return throwError(() => error);
    }),
  );
};
