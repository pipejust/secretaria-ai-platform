import { CommonModule } from '@angular/common';
import { Component, OnInit, inject } from '@angular/core';
import { LandingCmsService, LandingContent } from '../../services/landing-cms.service';
import { LandingShellComponent } from '../landing-shell/landing-shell.component';

@Component({
    selector: 'app-solutions-page',
    standalone: true,
    imports: [CommonModule, LandingShellComponent],
    template: `
<app-landing-shell>
  <div class="als" *ngIf="content as c">
    <header class="als-hero">
      <div class="al-container">
        <p class="als-eyebrow">{{ c.solutions_page?.eyebrow }}</p>
        <h1 class="als-title">{{ c.solutions_page?.title }}</h1>
      </div>
    </header>

    <section class="als-audiences">
      <div class="al-container">
        <div class="als-aud-grid">
          <article class="als-aud" *ngFor="let a of c.solutions_page?.audiences || []">
            <span class="als-aud__icon">{{ iconFor(a.icon) }}</span>
            <h3>{{ a.title }}</h3>
            <p>{{ a.description }}</p>
            <a class="als-aud__link">Ver más →</a>
          </article>
        </div>
      </div>
    </section>

    <section class="als-cases">
      <div class="al-container">
        <h2 class="als-section-title">{{ c.solutions_page?.use_cases_title || 'Casos de uso populares' }}</h2>
        <div class="als-case-grid">
          <article class="als-case" *ngFor="let u of c.solutions_page?.use_cases || []">
            <h4>{{ u.title }}</h4>
            <p>{{ u.description }}</p>
          </article>
        </div>
      </div>
    </section>

    <section class="als-industries">
      <div class="al-container">
        <h2 class="als-section-title als-section-title--dark">
          {{ c.solutions_page?.industries_title || 'Soluciones para cada industria' }}
        </h2>
        <div class="als-ind-grid">
          <article class="als-ind" *ngFor="let i of c.solutions_page?.industries || []">
            <span class="als-ind__icon">{{ iconFor(i.icon) }}</span>
            <span class="als-ind__title">{{ i.title }}</span>
          </article>
        </div>
      </div>
    </section>
  </div>
</app-landing-shell>
`,
    styles: [`
    :host { display: block; }
    .al-container { max-width: 1200px; margin: 0 auto; padding: 0 24px; }
    .als-hero { padding: 72px 0 40px; background: #F8FAFC; border-bottom: 1px solid #E2E8F0; }
    .als-eyebrow { font-size: 13px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em;
                   color: #145DFF; margin: 0 0 16px; }
    .als-title { font-size: clamp(2rem, 1rem + 3vw, 3rem); font-weight: 600; color: #0F172A;
                 letter-spacing: -0.02em; line-height: 1.15; margin: 0; max-width: 760px; }

    .als-audiences { padding: 56px 0 32px; }
    .als-aud-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 24px; }
    .als-aud { background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 14px; padding: 28px; }
    .als-aud__icon { display: inline-flex; align-items: center; justify-content: center;
                     width: 44px; height: 44px; border-radius: 12px; background: #DBEAFE;
                     font-size: 20px; margin-bottom: 16px; }
    .als-aud h3 { font-size: 1.15rem; font-weight: 600; color: #0F172A; margin: 0 0 8px; }
    .als-aud p { font-size: 14.5px; color: #475569; line-height: 1.5; margin: 0 0 12px; }
    .als-aud__link { font-size: 14px; color: #145DFF; font-weight: 600; cursor: pointer; }

    .als-cases { padding: 32px 0 56px; }
    .als-section-title { font-size: 1.5rem; font-weight: 600; color: #0F172A;
                         margin: 0 0 24px; letter-spacing: -0.01em; }
    .als-section-title--dark { color: #fff; }
    .als-case-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 16px; }
    .als-case { padding: 20px; border: 1px solid #E2E8F0; border-radius: 12px; background: #fff; }
    .als-case h4 { font-size: 1rem; font-weight: 600; color: #0F172A; margin: 0 0 6px; }
    .als-case p { font-size: 13.5px; color: #64748B; line-height: 1.4; margin: 0; }

    .als-industries { background: #0F172A; padding: 56px 0; margin-top: 24px; }
    .als-ind-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
                    gap: 16px; }
    .als-ind { background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.1);
               border-radius: 12px; padding: 20px; display: flex; flex-direction: column;
               gap: 10px; align-items: center; text-align: center; }
    .als-ind__icon { font-size: 24px; }
    .als-ind__title { color: #fff; font-size: 14px; font-weight: 500; }
    @media (max-width: 640px) { .als-aud { padding: 22px; } }
  `],
})
export class SolutionsPageComponent implements OnInit {
    private readonly cms = inject(LandingCmsService);
    content: LandingContent | null = null;
    async ngOnInit(): Promise<void> {
        try { this.content = await this.cms.loadPublic(); } catch { /* shell skeleton */ }
    }
    iconFor(name: string): string {
        const map: Record<string, string> = {
            users: '👥', building: '🏢', shield: '🛡️',
            code: '💻', chart: '📊', health: '🩺',
            book: '📚', factory: '🏭', gov: '🏛️',
        };
        return map[name] || '•';
    }
}
