import { CommonModule } from '@angular/common';
import { Component, OnInit, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { LandingCmsService, LandingContent } from '../../services/landing-cms.service';
import { LandingShellComponent } from '../landing-shell/landing-shell.component';

@Component({
    selector: 'app-company-page',
    standalone: true,
    imports: [CommonModule, RouterLink, LandingShellComponent],
    template: `
<app-landing-shell>
  <div class="alc" *ngIf="content as c">
    <header class="alc-hero">
      <div class="al-container">
        <p class="alc-eyebrow">{{ c.case_studies?.eyebrow || 'Empresas' }}</p>
        <h1 class="alc-title">{{ c.case_studies?.title || 'Empresas que confían en Acten.' }}</h1>
      </div>
    </header>

    <section class="alc-grid-wrap">
      <div class="al-container">
        <div class="alc-grid">
          <article *ngFor="let cs of c.case_studies?.items || []" class="alc-case">
            <div class="alc-case__head">
              <h3>{{ cs.company }}</h3>
              <span class="alc-case__tag">{{ cs.tagline }}</span>
            </div>
            <p class="alc-case__summary">{{ cs.summary }}</p>

            <div class="alc-case__kpis" *ngIf="cs.kpis?.length">
              <div *ngFor="let k of cs.kpis" class="alc-case__kpi">
                <span class="alc-case__kpi-val">{{ k.value }}</span>
                <span class="alc-case__kpi-lbl">{{ k.label }}</span>
              </div>
            </div>

            <div class="alc-case__sectors" *ngIf="cs.sectors_served?.length">
              <span *ngFor="let s of cs.sectors_served" class="alc-case__sector">{{ s }}</span>
            </div>

            <a [routerLink]="['/casos', cs.slug]" class="alc-case__link">Ver caso completo →</a>
          </article>
        </div>

        <div *ngIf="!c.case_studies?.items?.length" class="alc-empty">
          Pronto compartiremos historias de empresas que ya confían en nosotros.
        </div>
      </div>
    </section>
  </div>
</app-landing-shell>
`,
    styles: [`
    :host { display: block; }
    .al-container { max-width: 1200px; margin: 0 auto; padding: 0 24px; }
    .alc-hero { padding: 72px 0 40px; background: #F8FAFC; border-bottom: 1px solid #E2E8F0; }
    .alc-eyebrow { font-size: 13px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em;
                   color: #145DFF; margin: 0 0 16px; }
    .alc-title { font-size: clamp(2rem, 1rem + 3vw, 3rem); font-weight: 600; color: #0F172A;
                 letter-spacing: -0.02em; line-height: 1.15; margin: 0; max-width: 760px; }

    .alc-grid-wrap { padding: 48px 0 64px; }
    .alc-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 24px; }
    .alc-case { background: #fff; border: 1px solid #E2E8F0; border-radius: 16px; padding: 28px;
                display: flex; flex-direction: column; gap: 16px;
                transition: all 200ms cubic-bezier(0.4,0,0.2,1); }
    .alc-case:hover { transform: translateY(-3px); border-color: #CBD5E1;
                      box-shadow: 0 12px 28px -12px rgba(15,23,42,0.18); }
    .alc-case__head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
    .alc-case__head h3 { font-size: 1.25rem; font-weight: 700; color: #0F172A; margin: 0; }
    .alc-case__tag { background: #DBEAFE; color: #145DFF; padding: 4px 10px; border-radius: 999px;
                     font-size: 11.5px; font-weight: 600; }
    .alc-case__summary { font-size: 14.5px; color: #475569; line-height: 1.5; margin: 0; }
    .alc-case__kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
                      gap: 12px; padding: 16px 0; border-top: 1px solid #F1F5F9;
                      border-bottom: 1px solid #F1F5F9; }
    .alc-case__kpi { display: flex; flex-direction: column; gap: 2px; }
    .alc-case__kpi-val { font-size: 1.4rem; font-weight: 700; color: #0F172A; letter-spacing: -0.02em; }
    .alc-case__kpi-lbl { font-size: 12px; color: #64748B; line-height: 1.2; }
    .alc-case__sectors { display: flex; gap: 6px; flex-wrap: wrap; }
    .alc-case__sector { background: #F1F5F9; color: #475569; padding: 4px 10px;
                        border-radius: 6px; font-size: 12px; }
    .alc-case__link { font-size: 14px; font-weight: 600; color: #145DFF; text-decoration: none;
                      margin-top: auto; }
    .alc-case__link:hover { text-decoration: underline; }
    .alc-empty { padding: 48px 0; text-align: center; color: #64748B; }
  `],
})
export class CompanyPageComponent implements OnInit {
    private readonly cms = inject(LandingCmsService);
    content: LandingContent | null = null;
    async ngOnInit(): Promise<void> {
        try { this.content = await this.cms.loadPublic(); } catch { /* shell skeleton */ }
    }
}
