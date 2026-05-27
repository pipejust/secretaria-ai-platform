import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { TranslateModule } from '@ngx-translate/core';
import { RouterModule } from '@angular/router';

interface FaqEntry { q: string; a: string; }
interface Topic { title: string; description: string; icon: string; }

@Component({
  selector: 'app-help-center',
  standalone: true,
  imports: [CommonModule, RouterModule, TranslateModule],
  templateUrl: './help-center.component.html',
  styleUrls: ['./help-center.component.css'],
})
export class HelpCenterComponent {
  readonly year = new Date().getFullYear();

  /** Categorías rápidas — links a secciones internas o a /admin/* cuando aplica. */
  readonly topics: Topic[] = [
    {
      title: 'Empezar con Acten',
      description: 'Crea tu cuenta, conecta Fireflies, sube tu primera reunión y revisa el acta generada por IA.',
      icon: 'rocket',
    },
    {
      title: 'Curación de actas',
      description: 'Revisa las decisiones, riesgos y compromisos extraídos por la IA antes de despachar tareas.',
      icon: 'edit',
    },
    {
      title: 'Integraciones',
      description: 'Conecta Trello, Jira, ClickUp, Azure DevOps, Slack, Notion y más desde Configuraciones.',
      icon: 'link',
    },
    {
      title: 'Multi-empresa (tenants)',
      description: 'Cada empresa cliente vive en un espacio aislado. Aprende a crear tenants y administrar accesos.',
      icon: 'building',
    },
    {
      title: 'Marca / White-label',
      description: 'Personaliza el logo, los colores primario/secundario/acento y los datos de contacto de tu empresa.',
      icon: 'palette',
    },
    {
      title: 'Privacidad y seguridad',
      description: 'Cómo Acten cifra tus datos, cumple con GDPR y mantiene aislada la información entre empresas.',
      icon: 'shield',
    },
  ];

  readonly faqs: FaqEntry[] = [
    {
      q: '¿Cómo recupero mi contraseña?',
      a: 'En la pantalla de inicio de sesión, haz clic en "¿Olvidaste tu contraseña?". Recibirás un correo con un enlace de recuperación válido por 30 minutos.',
    },
    {
      q: '¿Acten guarda el audio original de mis reuniones?',
      a: 'No. Acten procesa el audio para extraer la transcripción y los insights, y luego descarta el archivo. Solo conservamos el texto y los metadatos asociados.',
    },
    {
      q: '¿Puedo conectar Acten con mi propio servidor SMTP?',
      a: 'Sí. En Configuraciones → Integraciones puedes pegar tu API Key de Resend o configurar un SMTP custom. Las credenciales viven sólo en tu empresa.',
    },
    {
      q: '¿Quién puede ver las actas de mi empresa?',
      a: 'Solo los usuarios que pertenecen a tu tenant. Otra empresa NUNCA verá tus sesiones, proyectos, integraciones ni configuraciones — el aislamiento es a nivel de base de datos.',
    },
    {
      q: '¿Cómo invito a un nuevo miembro?',
      a: 'Si eres administrador del tenant, ve a Usuarios → Crear usuario. Comparte la contraseña inicial; el usuario podrá cambiarla en su primer login.',
    },
    {
      q: '¿Qué hago si una sesión queda en estado "Procesando"?',
      a: 'El cron de auto-recuperación reintenta cada 5 minutos. Si después de 30 minutos sigue atascada, contacta soporte: incluye el ID de la sesión y la fecha aproximada.',
    },
  ];
}
