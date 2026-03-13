import { HttpInterceptorFn, HttpErrorResponse } from '@angular/common/http';
import { inject } from '@angular/core';
import { throwError } from 'rxjs';
import { catchError } from 'rxjs/operators';
import { Router } from '@angular/router';

export const errorInterceptor: HttpInterceptorFn = (req, next) => {
  const router = inject(Router);

  return next(req).pipe(
    catchError((error: HttpErrorResponse) => {
      // Si el backend devuelve 401 Unauthorized (token expirado o inválido)
      if (error.status === 401) {
        localStorage.removeItem('access_token');
        setTimeout(() => {
            window.location.href = '/login';
        }, 100);
      }
      return throwError(() => error);
    })
  );
};
