import { Component, inject } from '@angular/core';
import { CommonModule, Location } from '@angular/common';
import { Router, RouterModule } from '@angular/router';
import { TranslateModule } from '@ngx-translate/core';

interface Section { id: string; title: string; }

/**
 * Términos y Condiciones — espejo estructural del PrivacyComponent.
 * Comparte estilos para mantener consistencia visual de la familia "legal".
 */
@Component({
    selector: 'app-terms',
    standalone: true,
    imports: [CommonModule, RouterModule, TranslateModule],
    templateUrl: './terms.component.html',
    // Reutiliza la hoja de estilos de privacy — la página legal es
    // visualmente idéntica, sólo cambia el contenido. Evita duplicar CSS.
    styleUrls: ['../privacy/privacy.component.css'],
})
export class TermsComponent {
    private readonly location = inject(Location);
    private readonly router = inject(Router);

    readonly year = new Date().getFullYear();
    readonly lastUpdated = '15 de mayo de 2026';

    /** Vuelve al origen del visitante (footer del landing o donde sea). */
    goBack(event?: Event): void {
        if (event) event.preventDefault();
        if (typeof window !== 'undefined' && window.history.length > 1) {
            this.location.back();
        } else {
            this.router.navigateByUrl('/');
        }
    }

    readonly sections: Section[] = [
        { id: 'intro',        title: '1. Introducción' },
        { id: 'cuenta',       title: '2. Tu cuenta' },
        { id: 'uso',          title: '3. Uso aceptable' },
        { id: 'contenido',    title: '4. Tu contenido' },
        { id: 'ia',           title: '5. Servicios de IA' },
        { id: 'integraciones', title: '6. Integraciones de terceros' },
        { id: 'planes',       title: '7. Planes y facturación' },
        { id: 'propiedad',    title: '8. Propiedad intelectual' },
        { id: 'garantia',     title: '9. Limitación de garantías' },
        { id: 'responsabilidad', title: '10. Limitación de responsabilidad' },
        { id: 'terminacion',  title: '11. Terminación' },
        { id: 'ley',          title: '12. Ley aplicable' },
        { id: 'cambios',      title: '13. Cambios a estos términos' },
        { id: 'contacto',     title: '14. Contacto' },
    ];
}
