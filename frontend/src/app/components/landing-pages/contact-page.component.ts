import { CommonModule } from '@angular/common';
import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { LandingCmsService, LandingContent, ContactSubmission } from '../../services/landing-cms.service';
import { LandingShellComponent } from '../landing-shell/landing-shell.component';

@Component({
    selector: 'app-contact-page',
    standalone: true,
    imports: [CommonModule, FormsModule, LandingShellComponent],
    template: `
<app-landing-shell>
  <div class="alct" *ngIf="content as c">
    <header class="alct-hero">
      <div class="al-container">
        <p class="alct-eyebrow">{{ c.contact_page?.eyebrow || 'Hablemos' }}</p>
        <h1 class="alct-title">{{ c.contact_page?.title }}</h1>
        <p class="alct-subtitle" *ngIf="c.contact_page?.subtitle">{{ c.contact_page?.subtitle }}</p>
      </div>
    </header>

    <section class="alct-body">
      <div class="al-container alct-grid">
        <aside class="alct-aside">
          <div class="alct-channels" *ngIf="c.contact_page?.channels?.length">
            <article *ngFor="let ch of c.contact_page?.channels" class="alct-channel">
              <span class="alct-channel__lbl">{{ ch.label }}</span>
              <span class="alct-channel__val">{{ ch.value }}</span>
            </article>
          </div>

          <div class="alct-offices" *ngIf="c.contact_page?.offices?.length">
            <h3>{{ c.contact_page?.offices_title || 'Oficinas' }}</h3>
            <ul>
              <li *ngFor="let o of c.contact_page?.offices">
                <strong>{{ o.city }}</strong>
                <span>{{ o.address }}</span>
              </li>
            </ul>
          </div>
        </aside>

        <form class="alct-form" (ngSubmit)="submit()" #f="ngForm" novalidate>
          <label>
            <span>{{ c.contact_page?.form_name_label || 'Nombre completo' }} *</span>
            <input type="text" name="name" [(ngModel)]="model.name" required minlength="2"/>
          </label>
          <label>
            <span>{{ c.contact_page?.form_email_label || 'Correo electrónico' }} *</span>
            <input type="email" name="email" [(ngModel)]="model.email" required
                   pattern="^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$"/>
          </label>
          <label>
            <span>{{ c.contact_page?.form_company_label || 'Empresa' }}</span>
            <input type="text" name="company" [(ngModel)]="model.company"/>
          </label>
          <label>
            <span>{{ c.contact_page?.form_message_label || 'Mensaje' }} *</span>
            <textarea name="message" rows="5" [(ngModel)]="model.message" required></textarea>
          </label>
          <!-- Honeypot -->
          <input type="text" name="website" [(ngModel)]="model.website" hidden tabindex="-1" autocomplete="off"/>

          <div class="alct-privacy" *ngIf="c.contact_page?.privacy_label">
            <label>
              <input type="checkbox" name="acceptPrivacy" [(ngModel)]="acceptPrivacy" required/>
              <span>{{ c.contact_page?.privacy_label }}</span>
            </label>
          </div>

          <button type="submit" class="alct-form__cta" [disabled]="isSubmitting || !canSubmit(f)">
            {{ isSubmitting ? 'Enviando…' : (c.contact_page?.form_cta_label || 'Enviar mensaje') }}
          </button>

          <div class="alct-result alct-result--ok" *ngIf="status === 'ok'">
            {{ c.contact_page?.form_success }}
          </div>
          <div class="alct-result alct-result--err" *ngIf="status === 'err'">
            {{ errorMsg || c.contact_page?.form_error }}
          </div>
        </form>
      </div>
    </section>
  </div>
</app-landing-shell>
`,
    styles: [`
    :host { display: block; }
    .al-container { max-width: 1200px; margin: 0 auto; padding: 0 24px; }
    .alct-hero { padding: 56px 0 32px; background: #F8FAFC; border-bottom: 1px solid #E2E8F0; }
    .alct-eyebrow { font-size: 13px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em;
                    color: #145DFF; margin: 0 0 16px; }
    .alct-title { font-size: clamp(2rem, 1rem + 3vw, 3rem); font-weight: 600; color: #0F172A;
                  letter-spacing: -0.02em; line-height: 1.15; margin: 0 0 12px; max-width: 760px; }
    .alct-subtitle { font-size: 1.05rem; color: #475569; margin: 0; }

    .alct-body { padding: 48px 0 64px; }
    .alct-grid { display: grid; grid-template-columns: 1fr 1.5fr; gap: 56px; align-items: start; }

    .alct-aside { display: flex; flex-direction: column; gap: 32px; }
    .alct-channels { display: flex; flex-direction: column; gap: 12px; }
    .alct-channel { padding: 16px; border: 1px solid #E2E8F0; border-radius: 12px;
                    display: flex; flex-direction: column; gap: 4px; background: #fff; }
    .alct-channel__lbl { font-size: 12.5px; color: #64748B; font-weight: 600;
                         text-transform: uppercase; letter-spacing: 0.05em; }
    .alct-channel__val { font-size: 14.5px; color: #0F172A; font-weight: 500; }

    .alct-offices h3 { font-size: 1rem; font-weight: 600; color: #0F172A; margin: 0 0 12px; }
    .alct-offices ul { list-style: none; margin: 0; padding: 0; display: flex;
                       flex-direction: column; gap: 12px; }
    .alct-offices li { padding: 12px 16px; background: #F8FAFC; border: 1px solid #E2E8F0;
                       border-radius: 10px; display: flex; flex-direction: column; gap: 4px; }
    .alct-offices strong { font-size: 14px; color: #0F172A; }
    .alct-offices span { font-size: 13px; color: #64748B; }

    .alct-form { background: #fff; border: 1px solid #E2E8F0; border-radius: 16px; padding: 32px;
                 box-shadow: 0 8px 24px -12px rgba(15,23,42,0.08);
                 display: flex; flex-direction: column; gap: 16px; }
    .alct-form label { display: flex; flex-direction: column; gap: 6px; font-size: 13.5px;
                       color: #334155; font-weight: 500; }
    .alct-form input, .alct-form textarea {
      width: 100%; padding: 11px 12px; border: 1px solid #CBD5E1; border-radius: 10px;
      font-family: inherit; font-size: 14px; color: #0F172A; background: #fff; box-sizing: border-box;
      transition: all 150ms cubic-bezier(0.4,0,0.2,1);
    }
    .alct-form input:focus, .alct-form textarea:focus {
      outline: none; border-color: #145DFF; box-shadow: 0 0 0 3px rgba(20,93,255,0.12);
    }
    .alct-form textarea { resize: vertical; min-height: 120px; font-family: inherit; }

    .alct-privacy label { flex-direction: row; align-items: center; gap: 10px; font-size: 13px; }
    .alct-privacy input { width: auto; }

    .alct-form__cta { padding: 13px 24px; background: #145DFF; color: #fff; border: 0;
                      border-radius: 10px; font-size: 14.5px; font-weight: 600; cursor: pointer; }
    .alct-form__cta:hover:not(:disabled) { background: #0F4ACC; }
    .alct-form__cta:disabled { opacity: 0.5; cursor: not-allowed; }

    .alct-result { padding: 12px 16px; border-radius: 10px; font-size: 14px; }
    .alct-result--ok { background: #DCFCE7; color: #166534; border: 1px solid #86EFAC; }
    .alct-result--err { background: #FEE2E2; color: #991B1B; border: 1px solid #FCA5A5; }

    @media (max-width: 900px) {
      .alct-grid { grid-template-columns: 1fr; gap: 32px; }
    }
  `],
})
export class ContactPageComponent implements OnInit {
    private readonly cms = inject(LandingCmsService);
    content: LandingContent | null = null;
    model: ContactSubmission = { name: '', email: '', company: '', message: '', website: '' };
    acceptPrivacy = false;
    isSubmitting = false;
    status: '' | 'ok' | 'err' = '';
    errorMsg = '';

    async ngOnInit(): Promise<void> {
        try { this.content = await this.cms.loadPublic(); } catch { /* shell skeleton */ }
    }

    canSubmit(_f: any): boolean {
        return !!(this.model.name?.trim() && this.model.email?.trim() && this.model.message?.trim()
            && (this.acceptPrivacy || !this.content?.contact_page?.privacy_label));
    }

    async submit(): Promise<void> {
        if (this.isSubmitting) return;
        this.status = '';
        this.errorMsg = '';
        this.isSubmitting = true;
        try {
            await this.cms.submitContact({ ...this.model });
            this.status = 'ok';
        } catch (e: any) {
            this.status = 'err';
            this.errorMsg = e?.error?.detail || '';
        } finally {
            this.isSubmitting = false;
        }
    }
}
