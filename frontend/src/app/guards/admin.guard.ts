import { inject } from '@angular/core';
import { Router, type CanActivateFn } from '@angular/router';
import { filter, map, take } from 'rxjs/operators';
import { AuthService } from '../services/auth.service';

interface UserLike {
    role?: string | null;
    is_superadmin?: boolean;
}

/** Solo administradores de empresa (o superadmin). Espera al perfil, que
 *  llega por HTTP tras el bootstrap; si no cumple, manda al resumen. */
export const adminGuard: CanActivateFn = () => {
    const router = inject(Router);
    const authService = inject(AuthService);
    if (!authService.token) {
        return router.createUrlTree(['/login']);
    }
    return authService.currentUser$.pipe(
        filter((u): u is UserLike => u !== null && u !== undefined),
        take(1),
        map((u) => {
            const isAdmin = (u.role || '').toLowerCase() === 'admin' || !!u.is_superadmin;
            return isAdmin ? true : router.createUrlTree(['/admin/dashboard']);
        }),
    );
};
