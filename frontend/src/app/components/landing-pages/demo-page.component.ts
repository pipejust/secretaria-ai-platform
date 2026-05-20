import { CommonModule } from '@angular/common';
import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { LandingCmsService, LandingContent, ContactSubmission } from '../../services/landing-cms.service';
import { LandingShellComponent } from '../landing-shell/landing-shell.component';

@Component({
    selector: 'app-demo-page',
    standalone: true,
    imports: [CommonModule, FormsModule, LandingShellComponent],
    template: `
<app-landing-shell>
  <div class="ald" *ngIf="content as c">
    <header class="ald-hero">
      <div class="al-container ald-hero__grid">
        <div>
          <p class="ald-eyebrow">{{ c.demo_page?.eyebrow }}</p>
          <h1 class="ald-title">{{ c.demo_page?.title }}</h1>
          <p class="ald-subtitle" *ngIf="c.demo_page?.subtitle">{{ c.demo_page?.subtitle }}</p>

          <ul class="ald-bullets" *ngIf="c.demo_page?.bullets?.length">
            <li *ngFor="let b of c.demo_page?.bullets">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                   stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="20 6 9 17 4 12"></polyline>
              </svg>
              <span>{{ b }}</span>
            </li>
          </ul>
        </div>

        <form class="ald-form" (ngSubmit)="submit()" #f="ngForm" novalidate>
          <div class="ald-row">
            <label>
              <span>{{ c.demo_page?.form_name_label || 'Nombre completo' }} *</span>
              <input type="text" name="name" [(ngModel)]="model.name" required minlength="2"/>
            </label>
          </div>
          <div class="ald-row">
            <label>
              <span>{{ c.demo_page?.form_email_label || 'Correo corporativo' }} *</span>
              <input type="email" name="email" [(ngModel)]="model.email" required
                     pattern="^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$"/>
            </label>
          </div>
          <div class="ald-row ald-row--2">
            <label>
              <span>{{ c.demo_page?.form_company_label || 'Empresa' }}</span>
              <input type="text" name="company" [(ngModel)]="model.company"/>
            </label>
            <label>
              <span>{{ c.demo_page?.form_role_label || 'Cargo' }}</span>
              <input type="text" name="role" [(ngModel)]="model.role"/>
            </label>
          </div>
          <div class="ald-row" *ngIf="c.demo_page?.form_team_size_options?.length">
            <label>
              <span>{{ c.demo_page?.form_team_size_label || 'Tamaño del equipo' }}</span>
              <select name="teamSize" [(ngModel)]="teamSize">
                <option value="">Seleccionar…</option>
                <option *ngFor="let o of c.demo_page?.form_team_size_options" [value]="o">{{ o }}</option>
              </select>
            </label>
          </div>
          <div class="ald-row">
            <label>
              <span>{{ c.demo_page?.form_message_label || 'Mensaje (opcional)' }}</span>
              <textarea name="message" rows="4" [(ngModel)]="model.message"></textarea>
            </label>
          </div>
          <!-- Honeypot — bots lo rellenan, users no lo ven -->
          <input type="text" name="website" [(ngModel)]="model.website" hidden tabindex="-1" autocomplete="off"/>

          <div class="ald-privacy" *ngIf="c.demo_page?.privacy_label">
            <label>
              <input type="checkbox" name="acceptPrivacy" [(ngModel)]="acceptPrivacy" required/>
              <span>{{ c.demo_page?.privacy_label }}</span>
            </label>
          </div>

          <button type="submit" class="ald-form__cta" [disabled]="isSubmitting || !canSubmit(f)">
            {{ isSubmitting ? 'Enviando…' : (c.demo_page?.form_cta_label || 'Solicitar demo') }}
          </button>

          <div class="ald-result ald-result--ok" *ngIf="status === 'ok'">
            {{ c.demo_page?.form_success }}
          </div>
          <div class="ald-result ald-result--err" *ngIf="status === 'err'">
            {{ errorMsg || c.demo_page?.form_error }}
          </div>
        </form>
      </div>
    </header>
  </div>
</app-landing-shell>
`,
    styles: [`
    :host { display: block; }
    .al-container { max-width: 1200px; margin: 0 auto; padding: 0 24px; }
    .ald-hero { padding: 72px 0; background: #F8FAFC; min-height: calc(100vh - 200px); }
    .ald-hero__grid { display: grid; grid-template-columns: 1fr 1fr; gap: 64px; align-items: start; }
    .ald-eyebrow { font-size: 13px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em;
                   color: #145DFF; margin: 0 0 16px; }
    .ald-title { font-size: clamp(1.85rem, 1rem + 2.5vw, 2.5rem); font-weight: 600; color: #0F172A;
                 letter-spacing: -0.02em; line-height: 1.2; margin: 0 0 16px; }
    .ald-subtitle { font-size: 1.05rem; color: #475569; line-height: 1.5; margin: 0 0 24px; }
    .ald-bullets { list-style: none; margin: 24px 0 0; padding: 0; display: flex;
                   flex-direction: column; gap: 12px; }
    .ald-bullets li { display: flex; align-items: flex-start; gap: 10px; font-size: 15px;
                      color: #334155; line-height: 1.4; }
    .ald-bullets svg { color: #10B981; flex-shrink: 0; margin-top: 2px; }

    .ald-form { background: #fff; border: 1px solid #E2E8F0; border-radius: 16px; padding: 32px;
                box-shadow: 0 8px 24px -12px rgba(15,23,42,0.08);
                display: flex; flex-direction: column; gap: 16px; }
    .ald-row { display: flex; flex-direction: column; gap: 8px; }
    .ald-row--2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    .ald-form label { display: flex; flex-direction: column; gap: 6px; font-size: 13.5px;
                      color: #334155; font-weight: 500; }
    .ald-form input, .ald-form select, .ald-form textarea {
      width: 100%; padding: 11px 12px; border: 1px solid #CBD5E1; border-radius: 10px;
      font-family: inherit; font-size: 14px; color: #0F172A; background: #fff; box-sizing: border-box;
      transition: all 150ms cubic-bezier(0.4,0,0.2,1);
    }
    .ald-form input:focus, .ald-form select:focus, .ald-form textarea:focus {
      outline: none; border-color: #145DFF; box-shadow: 0 0 0 3px rgba(20,93,255,0.12); }
    .ald-form textarea { resize: vertical; min-height: 92px; font-family: inherit; }

    .ald-privacy label { flex-direction: row; align-items: center; gap: 10px; font-size: 13px; }
    .ald-privacy input { width: auto; }

    .ald-form__cta { padding: 13px 24px; background: #145DFF; color: #fff; border: 0;
                     border-radius: 10px; font-size: 14.5px; font-weight: 600; cursor: pointer;
                     transition: background 150ms cubic-bezier(0.4,0,0.2,1); }
    .ald-form__cta:hover:not(:disabled) { background: #0F4ACC; }
    .ald-form__cta:disabled { opacity: 0.5; cursor: not-allowed; }

    .ald-result { padding: 12px 16px; border-radius: 10px; font-size: 14px; }
    .ald-result--ok { background: #DCFCE7; color: #166534; border: 1px solid #86EFAC; }
    .ald-result--err { background: #FEE2E2; color: #991B1B; border: 1px solid #FCA5A5; }

    @media (max-width: 900px) {
      .ald-hero__grid { grid-template-columns: 1fr; gap: 40px; }
      .ald-row--2 { grid-template-columns: 1fr; }
    }
  `],
})
export class DemoPageComponent implements OnInit {
    private readonly cms = inject(LandingCmsService);
    content: LandingContent | null = null;
    model: ContactSubmission = { name: '', email: '', company: '', role: '', message: '', website: '' };
    teamSize = '';
    acceptPrivacy = false;
    isSubmitting = false;
    status: '' | 'ok' | 'err' = '';
    errorMsg = '';

    async ngOnInit(): Promise<void> {
        try { this.content = await this.cms.loadPublic(); } catch { /* shell skeleton */ }
    }

    canSubmit(f: any): boolean {
        return !!(this.model.name?.trim() && this.model.email?.trim()
            && (this.acceptPrivacy || !this.content?.demo_page?.privacy_label));
    }

    async submit(): Promise<void> {
        if (this.isSubmitting) return;
        this.status = '';
        this.errorMsg = '';
        this.isSubmitting = true;
        try {
            const message = [
                this.model.message || '',
                this.teamSize ? `\n[Tamaño del equipo: ${this.teamSize}]` : '',
            ].join('');
            await this.cms.submitContact({ ...this.model, message: message.trim() });
            this.status = 'ok';
        } catch (e: any) {
            this.status = 'err';
            this.errorMsg = e?.error?.detail || '';
        } finally {
            this.isSubmitting = false;
        }
    }
}
