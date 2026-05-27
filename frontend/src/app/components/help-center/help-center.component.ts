import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
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
  private readonly translate = inject(TranslateService);

  /**
   * Categorías rápidas — links a secciones internas o a /admin/* cuando aplica.
   *
   * Los textos vienen de i18n (`help_center.topics.<key>.title|description`)
   * para que cambien con el idioma seleccionado. La fuente declarativa abajo
   * usa keys estables; el template resuelve cada `translate` en render.
   */
  readonly topics: Topic[] = [
    { title: 'help_center.topics.getting_started.title',  description: 'help_center.topics.getting_started.description',  icon: 'rocket'   },
    { title: 'help_center.topics.curation.title',         description: 'help_center.topics.curation.description',         icon: 'edit'     },
    { title: 'help_center.topics.integrations.title',     description: 'help_center.topics.integrations.description',     icon: 'link'     },
    { title: 'help_center.topics.multi_tenant.title',     description: 'help_center.topics.multi_tenant.description',     icon: 'building' },
    { title: 'help_center.topics.branding.title',         description: 'help_center.topics.branding.description',         icon: 'palette'  },
    { title: 'help_center.topics.privacy_security.title', description: 'help_center.topics.privacy_security.description', icon: 'shield'   },
  ];

  /**
   * FAQ — 6 entradas. Como con los topics, los strings son keys i18n y se
   * traducen en el template vía `translate` pipe (los call sites del HTML
   * existente leen `f.q` / `f.a` literalmente — ver `_t()` helper).
   */
  readonly faqs: FaqEntry[] = [
    { q: 'help_center.faqs.forgot_password.q',   a: 'help_center.faqs.forgot_password.a'   },
    { q: 'help_center.faqs.audio_storage.q',     a: 'help_center.faqs.audio_storage.a'     },
    { q: 'help_center.faqs.custom_smtp.q',       a: 'help_center.faqs.custom_smtp.a'       },
    { q: 'help_center.faqs.who_sees_minutes.q',  a: 'help_center.faqs.who_sees_minutes.a'  },
    { q: 'help_center.faqs.invite_member.q',     a: 'help_center.faqs.invite_member.a'     },
    { q: 'help_center.faqs.stuck_processing.q', a: 'help_center.faqs.stuck_processing.a' },
  ];

  /** Helper de template — traduce una key. Mantiene los strings en JSON y
   *  permite cambiar idioma sin recargar el componente. */
  t(key: string): string {
    return this.translate.instant(key);
  }
}
