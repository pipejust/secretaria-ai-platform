import { Component, inject } from '@angular/core';
import { CommonModule, Location } from '@angular/common';
import { Router, RouterModule } from '@angular/router';

interface Section { id: string; title: string; }

@Component({
  selector: 'app-privacy',
  standalone: true,
  imports: [CommonModule, RouterModule],
  templateUrl: './privacy.component.html',
  styleUrls: ['./privacy.component.css'],
})
export class PrivacyComponent {
  private readonly location = inject(Location);
  private readonly router = inject(Router);

  readonly year = new Date().getFullYear();
  readonly lastUpdated = '15 de mayo de 2026';

  /** Volver al lugar de origen. Si hay history previo, usamos back();
   *  si no, vamos al landing (`/`). Evita "Volver al login" cuando el
   *  visitante venía desde el footer de acten.app. */
  goBack(event?: Event): void {
    if (event) event.preventDefault();
    // Si hay historia previa dentro de la app (más de la entrada actual),
    // usamos back. En SSR / direct-link no hay historia → vamos al raíz.
    if (typeof window !== 'undefined' && window.history.length > 1) {
      this.location.back();
    } else {
      this.router.navigateByUrl('/');
    }
  }

  readonly sections: Section[] = [
    { id: 'intro',         title: '1. Introducción' },
    { id: 'datos',         title: '2. Datos que recopilamos' },
    { id: 'uso',           title: '3. Cómo usamos tus datos' },
    { id: 'aislamiento',   title: '4. Aislamiento entre empresas' },
    { id: 'compartir',     title: '5. Con quién compartimos datos' },
    { id: 'seguridad',     title: '6. Seguridad' },
    { id: 'derechos',      title: '7. Tus derechos (GDPR)' },
    { id: 'retencion',     title: '8. Retención y borrado' },
    { id: 'cookies',       title: '9. Cookies y tracking' },
    { id: 'cambios',       title: '10. Cambios a esta política' },
    { id: 'contacto',      title: '11. Contacto' },
  ];
}
