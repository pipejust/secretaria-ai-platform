import { inject } from '@angular/core';
import { Router, type CanActivateFn } from '@angular/router';
import { AuthService } from '../services/auth.service';
import { TenantService } from '../services/tenant.service';

/**
 * En un dominio propio de una empresa (acten.softnexus.io, acten.singularlab.co)
 * la raíz no es el landing de Acten: es el espacio de esa empresa. Sin sesión,
 * su login; con sesión, su panel. En acten.app la raíz sigue siendo el landing.
 */
export const customDomainGuard: CanActivateFn = () => {
    const tenants = inject(TenantService);
    if (!tenants.resolvedFromHost()) {
        return true;
    }
    const router = inject(Router);
    const auth = inject(AuthService);
    return router.createUrlTree([auth.token ? '/admin/dashboard' : '/login']);
};
