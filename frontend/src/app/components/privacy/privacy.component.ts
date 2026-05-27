import { Component, inject } from '@angular/core';
import { CommonModule, Location } from '@angular/common';
import { Router, RouterModule } from '@angular/router';
import { TranslateModule } from '@ngx-translate/core';

/** TOC entry — la `titleKey` se traduce en el template vía `translate` pipe. */
interface Section { id: string; titleKey: string; }

@Component({
  selector: 'app-privacy',
  standalone: true,
  imports: [CommonModule, RouterModule, TranslateModule],
  templateUrl: './privacy.component.html',
  styleUrls: ['./privacy.component.css'],
})
export class PrivacyComponent {
  private readonly location = inject(Location);
  private readonly router = inject(Router);

  readonly year = new Date().getFullYear();
  /** Fecha de la última actualización legal. La key i18n permite mostrar
   *  "15 de mayo de 2026" / "May 15, 2026" / "15 de maig de 2026" según
   *  el idioma activo sin hardcodear el formato. */
  readonly lastUpdatedKey = 'privacy.last_updated_date';

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
    { id: 'intro',       titleKey: 'privacy.s1_title' },
    { id: 'datos',       titleKey: 'privacy.s2_title' },
    { id: 'uso',         titleKey: 'privacy.s3_title' },
    { id: 'aislamiento', titleKey: 'privacy.s4_title' },
    { id: 'compartir',   titleKey: 'privacy.s5_title' },
    { id: 'seguridad',   titleKey: 'privacy.s6_title' },
    { id: 'derechos',    titleKey: 'privacy.s7_title' },
    { id: 'retencion',   titleKey: 'privacy.s8_title' },
    { id: 'cookies',     titleKey: 'privacy.s9_title' },
    { id: 'cambios',     titleKey: 'privacy.s10_title' },
    { id: 'contacto',    titleKey: 'privacy.s11_title' },
  ];
}
