import { CommonModule } from '@angular/common';
import { Component, OnInit, inject } from '@angular/core';
import { LandingCmsService, LandingContent, ResourceItem } from '../../services/landing-cms.service';
import { LandingShellComponent } from '../landing-shell/landing-shell.component';

type CategoryFilter = 'todos' | string;

@Component({
    selector: 'app-resources-page',
    standalone: true,
    imports: [CommonModule, LandingShellComponent],
    template: `
<app-landing-shell>
  <div class="alr" *ngIf="content as c">
    <header class="alr-hero">
      <div class="al-container">
        <p class="alr-eyebrow">{{ c.resources?.eyebrow }}</p>
        <h1 class="alr-title">{{ c.resources?.title }}</h1>
        <p class="alr-subtitle">{{ c.resources?.subtitle }}</p>
      </div>
    </header>

    <section class="alr-tabs">
      <div class="al-container">
        <div class="alr-tab-list">
          <button (click)="filter = 'todos'"
                  [class.is-active]="filter === 'todos'">Todos</button>
          <button *ngFor="let cat of categories"
                  (click)="filter = cat"
                  [class.is-active]="filter === cat">{{ cat }}</button>
        </div>
      </div>
    </section>

    <section class="alr-grid-wrap">
      <div class="al-container">
        <div class="alr-grid">
          <article *ngFor="let r of filtered" class="alr-card">
            <span class="alr-card__cat">{{ r.category }}</span>
            <h3 class="alr-card__title">{{ r.title }}</h3>
            <p class="alr-card__desc">{{ r.description }}</p>
            <a [href]="r.url" class="alr-card__link" *ngIf="r.url && r.url !== '#'">
              Leer más →
            </a>
          </article>
        </div>
        <p class="alr-empty" *ngIf="!filtered.length">
          No hay recursos en esta categoría todavía. Volvé pronto.
        </p>
      </div>
    </section>
  </div>
</app-landing-shell>
`,
    styles: [`
    :host { display: block; }
    .al-container { max-width: 1200px; margin: 0 auto; padding: 0 24px; }
    .alr-hero { padding: 72px 0 32px; background: #F8FAFC; border-bottom: 1px solid #E2E8F0; }
    .alr-eyebrow { font-size: 13px; font-weight: 700; text-transform: uppercase;
                   letter-spacing: 0.08em; color: #145DFF; margin: 0 0 16px; }
    .alr-title { font-size: clamp(2rem, 1rem + 3vw, 3rem); font-weight: 600; color: #0F172A;
                 letter-spacing: -0.02em; line-height: 1.15; margin: 0 0 16px; max-width: 760px; }
    .alr-subtitle { font-size: 1.1rem; color: #475569; max-width: 640px; margin: 0; line-height: 1.5; }

    .alr-tabs { padding: 24px 0 16px; border-bottom: 1px solid #E2E8F0; background: #fff;
                position: sticky; top: 72px; z-index: 10; }
    .alr-tab-list { display: flex; gap: 8px; flex-wrap: wrap; }
    .alr-tab-list button {
      padding: 8px 16px; background: #fff; border: 1px solid #E2E8F0; border-radius: 999px;
      font-size: 13.5px; font-weight: 500; color: #475569; cursor: pointer;
      transition: all 150ms cubic-bezier(0.4,0,0.2,1); }
    .alr-tab-list button:hover { border-color: #CBD5E1; color: #0F172A; }
    .alr-tab-list button.is-active { background: #145DFF; border-color: #145DFF; color: #fff; }

    .alr-grid-wrap { padding: 32px 0 64px; }
    .alr-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 20px; }
    .alr-card { background: #fff; border: 1px solid #E2E8F0; border-radius: 14px; padding: 24px;
                display: flex; flex-direction: column; gap: 10px;
                transition: all 200ms cubic-bezier(0.4,0,0.2,1); }
    .alr-card:hover { border-color: #145DFF; transform: translateY(-2px);
                      box-shadow: 0 8px 24px -10px rgba(15,23,42,0.12); }
    .alr-card__cat { display: inline-block; padding: 4px 10px; background: #DBEAFE; color: #145DFF;
                     font-size: 11.5px; font-weight: 600; border-radius: 999px;
                     text-transform: uppercase; letter-spacing: 0.05em; width: fit-content; }
    .alr-card__title { font-size: 1.1rem; font-weight: 600; color: #0F172A; margin: 0;
                       line-height: 1.3; }
    .alr-card__desc { font-size: 14px; color: #64748B; line-height: 1.5; margin: 0; flex: 1; }
    .alr-card__link { font-size: 14px; font-weight: 600; color: #145DFF;
                      text-decoration: none; margin-top: 4px; }
    .alr-card__link:hover { text-decoration: underline; }
    .alr-empty { text-align: center; padding: 48px 0; color: #64748B; }
  `],
})
export class ResourcesPageComponent implements OnInit {
    private readonly cms = inject(LandingCmsService);
    content: LandingContent | null = null;
    filter: CategoryFilter = 'todos';

    async ngOnInit(): Promise<void> {
        try { this.content = await this.cms.loadPublic(); } catch { /* shell skeleton */ }
    }

    get categories(): string[] {
        const cats = new Set((this.content?.resources?.items || []).map((r: ResourceItem) => r.category));
        return Array.from(cats).sort();
    }

    get filtered(): ResourceItem[] {
        const items = this.content?.resources?.items || [];
        if (this.filter === 'todos') return items;
        return items.filter(r => r.category === this.filter);
    }
}
