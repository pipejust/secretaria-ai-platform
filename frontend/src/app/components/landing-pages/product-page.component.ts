import { CommonModule } from '@angular/common';
import { Component, OnInit, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { LandingCmsService, LandingContent } from '../../services/landing-cms.service';
import { LandingShellComponent } from '../landing-shell/landing-shell.component';

@Component({
    selector: 'app-product-page',
    standalone: true,
    imports: [CommonModule, RouterLink, LandingShellComponent],
    template: `
<app-landing-shell>
  <div class="alp" *ngIf="content as c">
    <header class="alp-hero">
      <div class="al-container">
        <p class="alp-eyebrow">{{ c.product_page?.eyebrow }}</p>
        <h1 class="alp-title">{{ c.product_page?.title }}</h1>
        <p class="alp-subtitle">{{ c.product_page?.subtitle }}</p>
      </div>
    </header>
    <section class="alp-blocks">
      <div class="al-container">
        <article class="alp-block" *ngFor="let b of c.product_page?.blocks || []">
          <h2>{{ b.title }}</h2>
          <ul>
            <li *ngFor="let it of b.items">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                   stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="20 6 9 17 4 12"></polyline>
              </svg>
              <span>{{ it }}</span>
            </li>
          </ul>
        </article>
      </div>
    </section>
    <section class="alp-cta" *ngIf="c.product_page?.cta_title">
      <div class="al-container">
        <div class="alp-cta__card">
          <h2>{{ c.product_page?.cta_title }}</h2>
          <p>{{ c.product_page?.cta_subtitle }}</p>
          <a [routerLink]="c.product_page?.cta_url || '/demo'" class="alp-cta__btn">
            {{ c.product_page?.cta_label || 'Solicitar demo' }}
          </a>
        </div>
      </div>
    </section>
  </div>
</app-landing-shell>
`,
    styles: [`
    :host { display: block; }
    .al-container { max-width: 1200px; margin: 0 auto; padding: 0 24px; }
    .alp-hero { padding: 72px 0 40px; background: #F8FAFC; border-bottom: 1px solid #E2E8F0; }
    .alp-eyebrow { font-size: 13px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em;
                   color: #145DFF; margin: 0 0 16px; }
    .alp-title { font-size: clamp(2rem, 1rem + 3vw, 3rem); font-weight: 600; color: #0F172A; margin: 0 0 16px;
                 letter-spacing: -0.02em; line-height: 1.15; max-width: 760px; }
    .alp-subtitle { font-size: 1.1rem; color: #475569; max-width: 640px; margin: 0; line-height: 1.5; }
    .alp-blocks { padding: 56px 0 40px; }
    .alp-block { padding: 24px 0; border-bottom: 1px solid #E2E8F0; display: grid;
                 grid-template-columns: 1fr 2fr; gap: 32px; }
    .alp-block:last-child { border-bottom: 0; }
    .alp-block h2 { font-size: 1.25rem; font-weight: 600; color: #0F172A; margin: 0; letter-spacing: -0.01em; }
    .alp-block ul { list-style: none; margin: 0; padding: 0; display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; }
    .alp-block li { display: flex; align-items: flex-start; gap: 10px; font-size: 14.5px;
                    color: #475569; line-height: 1.4; }
    .alp-block li svg { color: #10B981; flex-shrink: 0; margin-top: 2px; }
    .alp-cta { padding: 56px 0 40px; }
    .alp-cta__card { background: #0F172A; border-radius: 18px; padding: 48px;
                     color: #fff; text-align: center; }
    .alp-cta__card h2 { font-size: 1.75rem; margin: 0 0 12px; letter-spacing: -0.02em; }
    .alp-cta__card p { color: #CBD5E1; margin: 0 0 24px; }
    .alp-cta__btn { display: inline-block; padding: 12px 28px; background: #145DFF; color: #fff;
                    border-radius: 10px; text-decoration: none; font-weight: 600; font-size: 14px; }
    @media (max-width: 768px) {
      .alp-block { grid-template-columns: 1fr; gap: 16px; }
      .alp-cta__card { padding: 32px 20px; }
    }
  `],
})
export class ProductPageComponent implements OnInit {
    private readonly cms = inject(LandingCmsService);
    content: LandingContent | null = null;
    async ngOnInit(): Promise<void> {
        try { this.content = await this.cms.loadPublic(); } catch { /* shell muestra skeleton */ }
    }
}
