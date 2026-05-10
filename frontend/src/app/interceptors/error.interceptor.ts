import { HttpInterceptorFn, HttpErrorResponse } from '@angular/common/http';
import { throwError } from 'rxjs';
import { catchError } from 'rxjs/operators';

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
 */
const AUTH_BYPASS_PATHS = [
  '/auth/login',
  '/auth/forgot-password',
  '/auth/reset-password',
];

function isAuthEndpoint(url: string): boolean {
  return AUTH_BYPASS_PATHS.some((p) => url.includes(p));
}

export const errorInterceptor: HttpInterceptorFn = (req, next) => {
  return next(req).pipe(
    catchError((error: HttpErrorResponse) => {
      if (error.status === 401 && !isAuthEndpoint(req.url)) {
        localStorage.removeItem('access_token');
        setTimeout(() => { window.location.href = '/login'; }, 100);
      }
      return throwError(() => error);
    }),
  );
};
