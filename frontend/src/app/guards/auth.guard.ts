import { inject } from '@angular/core';
import { Router, type CanActivateFn } from '@angular/router';
import { AuthService } from '../services/auth.service';

export const authGuard: CanActivateFn = (route, state) => {
    const router = inject(Router);
    const authService = inject(AuthService);

    if (authService.token) {
        return true;
    }

    // Not logged in, redirect to login page
    router.navigate(['/login'], { queryParams: { returnUrl: state.url } });
    return false;
};
