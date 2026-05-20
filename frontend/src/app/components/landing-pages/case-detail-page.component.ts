import { CommonModule } from '@angular/common';
import { Component, OnInit, inject } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { LandingCmsService, LandingContent } from '../../services/landing-cms.service';
import { LandingShellComponent } from '../landing-shell/landing-shell.component';

@Component({
    selector: 'app-case-detail-page',
    standalone: true,
    imports: [CommonModule, RouterLink, LandingShellComponent],
    template: `
<app-landing-shell>
  <div class="alcd" *ngIf="content as c">
    <ng-container *ngIf="caseStudy as cs; else notFoundTpl">
      <header class="alcd-hero">
        <div class="al-container">
          <a routerLink="/empresa" class="alcd-back">← Volver a empresas</a>
          <p class="alcd-eyebrow">{{ c.case_study_detail?.eyebrow || 'Caso de uso' }}</p>
          <h1 class="alcd-title">{{ cs.company }}</h1>
          <p class="alcd-tagline">{{ cs.tagline }}</p>
        </div>
      </header>

      <section class="alcd-body">
        <div class="al-container">
          <article class="alcd-block">
            <h2>{{ c.case_study_detail?.challenge_label || 'El desafío' }}</h2>
            <p>{{ cs.summary }}</p>
          </article>

          <article class="alcd-block">
            <h2>{{ c.case_study_detail?.results_label || 'Resultados' }}</h2>
            <div class="alcd-kpis" *ngIf="cs.kpis?.length">
              <div *ngFor="let k of cs.kpis" class="alcd-kpi">
                <span class="alcd-kpi__val">{{ k.value }}</span>
                <span class="alcd-kpi__lbl">{{ k.label }}</span>
              </div>
            </div>
            <p *ngIf="!cs.kpis?.length" class="alcd-empty">Métricas próximamente.</p>
          </article>

          <article class="alcd-block" *ngIf="cs.sectors_served?.length">
            <h2>Sectores impactados</h2>
            <div class="alcd-sectors">
              <span *ngFor="let s of cs.sectors_served">{{ s }}</span>
            </div>
          </article>
        </div>
      </section>

      <section class="alcd-others" *ngIf="otherCases.length">
        <div class="al-container">
          <h2 class="alcd-others__title">
            {{ c.case_study_detail?.other_cases_label || 'Otros casos de uso' }}
          </h2>
          <div class="alcd-others__grid">
            <a *ngFor="let o of otherCases"
               [routerLink]="['/casos', o.slug]"
               class="alcd-others__card">
              <h3>{{ o.company }}</h3>
              <p>{{ o.tagline }}</p>
            </a>
          </div>
        </div>
      </section>
    </ng-container>

    <ng-template #notFoundTpl>
      <section class="alcd-notfound">
        <div class="al-container">
          <h1>Caso no encontrado</h1>
          <p>El caso de uso que buscás no existe o fue movido.</p>
          <a routerLink="/empresa" class="alcd-back">← Volver a empresas</a>
        </div>
      </section>
    </ng-template>
  </div>
</app-landing-shell>
`,
    styles: [`
    :host { display: block; }
    .al-container { max-width: 960px; margin: 0 auto; padding: 0 24px; }
    .alcd-back { font-size: 13.5px; color: #145DFF; text-decoration: none; }
    .alcd-back:hover { text-decoration: underline; }
    .alcd-hero { padding: 56px 0 32px; background: #F8FAFC; border-bottom: 1px solid #E2E8F0; }
    .alcd-eyebrow { font-size: 13px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em;
                    color: #145DFF; margin: 16px 0 12px; }
    .alcd-title { font-size: clamp(2rem, 1rem + 3vw, 3rem); font-weight: 600; color: #0F172A;
                  letter-spacing: -0.02em; line-height: 1.15; margin: 0 0 12px; }
    .alcd-tagline { font-size: 1.15rem; color: #475569; margin: 0; }

    .alcd-body { padding: 48px 0; }
    .alcd-block { padding: 28px 0; border-bottom: 1px solid #E2E8F0; }
    .alcd-block:last-child { border-bottom: 0; }
    .alcd-block h2 { font-size: 1.25rem; font-weight: 600; color: #0F172A; margin: 0 0 14px; }
    .alcd-block p { font-size: 15px; color: #475569; line-height: 1.6; margin: 0; }
    .alcd-kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 16px; }
    .alcd-kpi { background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 12px; padding: 16px;
                display: flex; flex-direction: column; gap: 4px; }
    .alcd-kpi__val { font-size: 1.6rem; font-weight: 700; color: #0F172A; letter-spacing: -0.02em; }
    .alcd-kpi__lbl { font-size: 12.5px; color: #64748B; }
    .alcd-sectors { display: flex; gap: 8px; flex-wrap: wrap; }
    .alcd-sectors span { background: #DBEAFE; color: #145DFF; padding: 6px 14px;
                        border-radius: 999px; font-size: 13px; font-weight: 500; }
    .alcd-empty { font-size: 14px; color: #64748B; font-style: italic; }

    .alcd-others { padding: 48px 0 64px; background: #F8FAFC; border-top: 1px solid #E2E8F0; }
    .alcd-others__title { font-size: 1.25rem; font-weight: 600; color: #0F172A; margin: 0 0 24px; }
    .alcd-others__grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
                         gap: 16px; }
    .alcd-others__card { background: #fff; border: 1px solid #E2E8F0; border-radius: 12px;
                         padding: 20px; text-decoration: none; display: block;
                         transition: all 150ms cubic-bezier(0.4,0,0.2,1); }
    .alcd-others__card:hover { border-color: #145DFF; transform: translateY(-2px); }
    .alcd-others__card h3 { font-size: 1rem; font-weight: 600; color: #0F172A; margin: 0 0 4px; }
    .alcd-others__card p { font-size: 13px; color: #64748B; margin: 0; }

    .alcd-notfound { padding: 96px 0; text-align: center; }
    .alcd-notfound h1 { font-size: 2rem; color: #0F172A; margin: 0 0 12px; }
    .alcd-notfound p { font-size: 1rem; color: #64748B; margin: 0 0 24px; }
  `],
})
export class CaseDetailPageComponent implements OnInit {
    private readonly cms = inject(LandingCmsService);
    private readonly route = inject(ActivatedRoute);
    content: LandingContent | null = null;
    slug = '';

    async ngOnInit(): Promise<void> {
        this.route.paramMap.subscribe(p => this.slug = p.get('slug') || '');
        try { this.content = await this.cms.loadPublic(); } catch { /* shell skeleton */ }
    }

    get caseStudy() {
        return this.content?.case_studies?.items.find(c => c.slug === this.slug);
    }

    get otherCases() {
        return (this.content?.case_studies?.items || [])
            .filter(c => c.slug !== this.slug)
            .slice(0, 3);
    }
}
