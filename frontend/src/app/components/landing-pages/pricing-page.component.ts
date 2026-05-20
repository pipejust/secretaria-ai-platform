import { CommonModule } from '@angular/common';
import { Component, OnInit, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { LandingCmsService, LandingContent } from '../../services/landing-cms.service';
import { LandingShellComponent } from '../landing-shell/landing-shell.component';

@Component({
    selector: 'app-pricing-page',
    standalone: true,
    imports: [CommonModule, RouterLink, LandingShellComponent],
    template: `
<app-landing-shell>
  <div class="alpr" *ngIf="content as c">
    <header class="alpr-hero">
      <div class="al-container">
        <p class="alpr-eyebrow">{{ c.pricing?.eyebrow }}</p>
        <h1 class="alpr-title">{{ c.pricing?.title }}</h1>
        <p class="alpr-subtitle">{{ c.pricing?.subtitle }}</p>
      </div>
    </header>

    <section class="alpr-plans">
      <div class="al-container">
        <div class="alpr-grid">
          <article *ngFor="let p of c.pricing?.plans || []"
                   class="alpr-plan" [class.is-featured]="p.featured">
            <header>
              <h3>{{ p.name }}</h3>
              <p class="alpr-plan__desc">{{ p.description }}</p>
            </header>
            <div class="alpr-plan__price">
              <span class="alpr-plan__amount">{{ p.price }}</span>
              <span class="alpr-plan__billing">{{ p.billing }}</span>
            </div>
            <ul class="alpr-plan__features">
              <li *ngFor="let f of p.features">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                     stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                  <polyline points="20 6 9 17 4 12"></polyline>
                </svg>
                <span>{{ f }}</span>
              </li>
            </ul>
            <a [routerLink]="resolveCta(p.cta_anchor)" class="alpr-plan__cta"
               [class.is-featured]="p.featured">
              {{ p.cta_label }}
            </a>
          </article>
        </div>
        <p class="alpr-footnote" *ngIf="c.pricing?.footnote">{{ c.pricing?.footnote }}</p>
      </div>
    </section>
  </div>
</app-landing-shell>
`,
    styles: [`
    :host { display: block; }
    .al-container { max-width: 1200px; margin: 0 auto; padding: 0 24px; }
    .alpr-hero { padding: 72px 0 32px; background: #F8FAFC; border-bottom: 1px solid #E2E8F0;
                 text-align: center; }
    .alpr-eyebrow { font-size: 13px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em;
                    color: #145DFF; margin: 0 0 16px; }
    .alpr-title { font-size: clamp(2rem, 1rem + 3vw, 3rem); font-weight: 600; color: #0F172A;
                  letter-spacing: -0.02em; line-height: 1.15; margin: 0 0 16px; }
    .alpr-subtitle { font-size: 1.1rem; color: #475569; max-width: 640px;
                     margin: 0 auto; line-height: 1.5; }

    .alpr-plans { padding: 48px 0 64px; }
    .alpr-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
                 gap: 20px; align-items: stretch; }
    .alpr-plan { display: flex; flex-direction: column; background: #fff;
                 border: 1px solid #E2E8F0; border-radius: 16px; padding: 28px;
                 transition: all 200ms cubic-bezier(0.4,0,0.2,1); }
    .alpr-plan.is-featured { border-color: #145DFF; box-shadow: 0 12px 32px -8px rgba(20,93,255,0.18);
                             transform: scale(1.02); }
    .alpr-plan header h3 { font-size: 1.25rem; font-weight: 700; color: #0F172A; margin: 0 0 6px; }
    .alpr-plan__desc { font-size: 13.5px; color: #64748B; line-height: 1.4; margin: 0 0 20px; }
    .alpr-plan__price { display: flex; align-items: baseline; gap: 8px;
                        padding: 16px 0; border-bottom: 1px solid #F1F5F9; margin-bottom: 20px; }
    .alpr-plan__amount { font-size: 2.25rem; font-weight: 700; color: #0F172A; letter-spacing: -0.02em; }
    .alpr-plan__billing { font-size: 13px; color: #64748B; }
    .alpr-plan__features { list-style: none; margin: 0 0 24px; padding: 0;
                           display: flex; flex-direction: column; gap: 10px; flex: 1; }
    .alpr-plan__features li { display: flex; align-items: flex-start; gap: 8px;
                              font-size: 14px; color: #475569; line-height: 1.4; }
    .alpr-plan__features svg { color: #10B981; flex-shrink: 0; margin-top: 3px; }
    .alpr-plan__cta { display: inline-flex; align-items: center; justify-content: center;
                      padding: 12px 16px; border-radius: 10px; text-decoration: none;
                      font-weight: 600; font-size: 14px;
                      background: #F1F5F9; color: #0F172A; border: 1px solid #E2E8F0;
                      transition: all 150ms cubic-bezier(0.4,0,0.2,1); }
    .alpr-plan__cta:hover { background: #E2E8F0; }
    .alpr-plan__cta.is-featured { background: #145DFF; color: #fff; border-color: #145DFF; }
    .alpr-plan__cta.is-featured:hover { background: #0F4ACC; }
    .alpr-footnote { text-align: center; font-size: 13px; color: #64748B;
                     margin: 32px 0 0; padding-top: 24px; border-top: 1px solid #E2E8F0; }
  `],
})
export class PricingPageComponent implements OnInit {
    private readonly cms = inject(LandingCmsService);
    content: LandingContent | null = null;
    async ngOnInit(): Promise<void> {
        try { this.content = await this.cms.loadPublic(); } catch { /* shell skeleton */ }
    }
    /** Resuelve cta_anchor a una ruta absoluta válida para routerLink. */
    resolveCta(anchor: string | undefined): string {
        const v = (anchor || '/demo').trim();
        if (v.startsWith('/')) return v;
        if (v.startsWith('#')) return '/demo';
        return '/demo';
    }
}
