import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';

interface Section { id: string; title: string; }

@Component({
  selector: 'app-privacy',
  standalone: true,
  imports: [CommonModule, RouterModule],
  templateUrl: './privacy.component.html',
  styleUrls: ['./privacy.component.css'],
})
export class PrivacyComponent {
  readonly year = new Date().getFullYear();
  readonly lastUpdated = '15 de mayo de 2026';

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
