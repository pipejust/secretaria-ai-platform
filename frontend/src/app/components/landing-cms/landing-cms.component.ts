import {
    ChangeDetectorRef,
    Component,
    OnInit,
    inject,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClientModule } from '@angular/common/http';
import { RouterModule } from '@angular/router';

import { AuthService } from '../../services/auth.service';
import { ToastService } from '../../services/toast.service';
import {
    FeatureItem,
    FooterColumn,
    FooterLink,
    LandingCmsService,
    LandingContent,
    PricingPlan,
    ResourceItem,
    StepItem,
    Testimonial,
    CompanyValue,
    CompanyStat,
    NavItem,
} from '../../services/landing-cms.service';

/**
 * CMS del landing público de acten.app.
 *
 * Solo visible al super-admin del tenant Acten (gating en sidebar +
 * gating en el backend con `_require_acten_tenant`).
 *
 * UX: navegador izquierdo con lista de secciones, panel derecho con el
 * editor de la sección seleccionada, barra inferior fija con
 * "Cancelar / Restablecer / Guardar". El formulario es template-driven
 * (NgModel) para no introducir ReactiveForms a un proyecto que no los usa.
 *
 * El estado `content` es el objeto editable completo. `originalSnapshot`
 * guarda la última versión guardada para poder cancelar / detectar dirty.
 */

type SectionKey =
    | 'hero'
    | 'nav'
    | 'trust'
    | 'features'
    | 'flow'
    | 'integrations'
    | 'testimonials'
    | 'pricing'
    | 'contact'
    | 'final_cta'
    | 'footer';

interface SectionMeta {
    key: SectionKey;
    label: string;
    hint: string;
    iconPaths: string[];
}

@Component({
    selector: 'app-landing-cms',
    standalone: true,
    imports: [CommonModule, FormsModule, HttpClientModule, RouterModule],
    templateUrl: './landing-cms.component.html',
    styleUrls: ['./landing-cms.component.css'],
})
export class LandingCmsComponent implements OnInit {
    private readonly cms = inject(LandingCmsService);
    private readonly auth = inject(AuthService);
    private readonly toast = inject(ToastService);
    private readonly cdr = inject(ChangeDetectorRef);

    /** Modelo editable. Empieza vacío hasta que `ngOnInit` lo carga. */
    content: LandingContent | null = null;

    /** Snapshot de la última versión guardada (JSON stringified). Sirve
     *  para detectar cambios sin tocar y permitir "Cancelar". */
    private originalSnapshot = '';

    /** Sección activa del navegador izquierdo. */
    activeSection: SectionKey = 'hero';

    isLoading = true;
    isSaving = false;
    isResetting = false;
    loadError = '';

    /** Catálogo de secciones del landing. Mantiene el orden visual del
     *  landing real para que el admin pueda recorrerlo de arriba a abajo. */
    readonly sections: SectionMeta[] = [
        {
            key: 'nav',
            label: 'Navegación',
            hint: 'Menú superior y CTAs del header',
            iconPaths: ['M3 12h18', 'M3 6h18', 'M3 18h18'],
        },
        {
            key: 'hero',
            label: 'Hero',
            hint: 'Título principal, subtítulo y CTAs',
            iconPaths: ['M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z', 'M9 22V12h6v10'],
        },
        {
            key: 'trust',
            label: 'Confianza',
            hint: 'Logos de empresas que usan Acten',
            iconPaths: ['M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z'],
        },
        {
            key: 'features',
            label: 'Capacidades',
            hint: 'Grid de 6 características',
            iconPaths: ['M4 4h6v6H4z', 'M14 4h6v6h-6z', 'M14 14h6v6h-6z', 'M4 14h6v6H4z'],
        },
        {
            key: 'flow',
            label: 'Flujo',
            hint: '4 pasos: captura → IA → estructura → acción',
            iconPaths: ['M5 12h14', 'M12 5l7 7-7 7'],
        },
        {
            key: 'integrations',
            label: 'Integraciones',
            hint: 'Lista de plataformas conectadas',
            iconPaths: [
                'M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71',
                'M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71',
            ],
        },
        {
            key: 'testimonials',
            label: 'Testimonios',
            hint: 'Citas de clientes reales',
            iconPaths: [
                'M3 21c3 0 7-1 7-8V5c0-1.25-.756-2.017-2-2H4c-1.25 0-2 .75-2 1.972V11c0 1.25.75 2 2 2 1 0 1 0 1 1v1c0 1-1 2-2 2s-1 .008-1 1.031V20c0 1 0 1 1 1z',
                'M15 21c3 0 7-1 7-8V5c0-1.25-.757-2.017-2-2h-4c-1.25 0-2 .75-2 1.972V11c0 1.25.75 2 2 2h.75c0 2.25.25 4-2.75 4v3c0 1 0 1 1 1z',
            ],
        },
        {
            key: 'pricing',
            label: 'Precios',
            hint: 'Planes Starter / Business / Enterprise',
            iconPaths: [
                'M12 1v22',
                'M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6',
            ],
        },
        {
            key: 'contact',
            label: 'Contacto',
            hint: 'Email, teléfono, formulario',
            iconPaths: [
                'M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z',
                'M22 6l-10 7L2 6',
            ],
        },
        {
            key: 'final_cta',
            label: 'CTA final',
            hint: 'Banner de cierre antes del footer',
            iconPaths: [
                'M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z',
            ],
        },
        {
            key: 'footer',
            label: 'Footer',
            hint: 'Columnas de enlaces y newsletter',
            iconPaths: [
                'M3 18h18',
                'M3 12h18',
                'M3 6h18',
            ],
        },
    ];

    // ────────────────────────────────────────────────────────────────────
    // Lifecycle
    // ────────────────────────────────────────────────────────────────────

    /** Idioma de EDICIÓN del CMS. Independiente del idioma de la UI del
     *  admin — el admin puede editar el landing en catalán mientras la
     *  interfaz está en español. Default 'es'. */
    editLang: 'es' | 'ca' | 'en' = 'es';
    readonly availableEditLangs: { code: 'es' | 'ca' | 'en'; label: string }[] = [
        { code: 'es', label: 'Español' },
        { code: 'ca', label: 'Català' },
        { code: 'en', label: 'English' },
    ];

    async ngOnInit(): Promise<void> {
        const token = this.auth.token;
        if (!token) {
            this.loadError = 'Tu sesión expiró. Vuelve a iniciar sesión.';
            this.isLoading = false;
            return;
        }
        await this._reloadForLang(token);
    }

    private async _reloadForLang(token: string): Promise<void> {
        this.isLoading = true;
        try {
            const data = await this.cms.loadAdmin(token, this.editLang);
            this.content = data;
            this.originalSnapshot = JSON.stringify(data);
        } catch (err: any) {
            const detail = err?.error?.detail || err?.message || 'Error desconocido.';
            this.loadError = `No se pudo cargar el contenido: ${detail}`;
            this.toast.error(this.loadError);
        } finally {
            this.isLoading = false;
            this.cdr.detectChanges();
        }
    }

    /** Cambia el idioma de edición. Si hay cambios sin guardar, pide
     *  confirmación antes de descartar (el GET re-carga el form con las
     *  traducciones del nuevo idioma desde server). */
    async changeEditLang(newLang: 'es' | 'ca' | 'en'): Promise<void> {
        if (newLang === this.editLang) return;
        if (this.isDirty && !confirm(
            'Tenés cambios sin guardar en este idioma. Si cambiás de idioma se descartarán. ¿Continuar?',
        )) return;
        this.editLang = newLang;
        const token = this.auth.token;
        if (token) await this._reloadForLang(token);
    }

    // ────────────────────────────────────────────────────────────────────
    // Estado / dirty
    // ────────────────────────────────────────────────────────────────────

    get isDirty(): boolean {
        if (!this.content) return false;
        return JSON.stringify(this.content) !== this.originalSnapshot;
    }

    selectSection(key: SectionKey): void {
        this.activeSection = key;
        // Scroll al top del panel al cambiar de sección — el formulario
        // puede ser largo y queremos consistencia.
        const panel = document.querySelector('.lcms-panel');
        if (panel) panel.scrollTo({ top: 0, behavior: 'smooth' });
    }

    // ────────────────────────────────────────────────────────────────────
    // Acciones del bottom bar
    // ────────────────────────────────────────────────────────────────────

    async save(): Promise<void> {
        if (!this.content || this.isSaving) return;
        const token = this.auth.token;
        if (!token) {
            this.toast.error('Tu sesión expiró. Vuelve a iniciar sesión.');
            return;
        }
        this.isSaving = true;
        try {
            const updated = await this.cms.save(this.content, token, this.editLang);
            this.content = updated;
            this.originalSnapshot = JSON.stringify(updated);
            this.toast.success(`Contenido del landing guardado en ${this.editLang.toUpperCase()}.`);
        } catch (err: any) {
            const detail = err?.error?.detail || err?.message || 'Error desconocido.';
            this.toast.error(`No se pudo guardar: ${detail}`);
        } finally {
            this.isSaving = false;
            this.cdr.detectChanges();
        }
    }

    cancelChanges(): void {
        if (!this.originalSnapshot) return;
        if (this.isDirty && !confirm('Vas a descartar los cambios sin guardar. ¿Continuar?')) {
            return;
        }
        this.content = JSON.parse(this.originalSnapshot);
        this.toast.info('Cambios descartados.');
    }

    async resetToDefaults(): Promise<void> {
        if (!confirm('Esto reemplaza TODO el contenido del landing con los defaults de fábrica. ¿Seguro?')) {
            return;
        }
        const token = this.auth.token;
        if (!token) {
            this.toast.error('Tu sesión expiró.');
            return;
        }
        this.isResetting = true;
        try {
            const fresh = await this.cms.reset(token);
            this.content = fresh;
            this.originalSnapshot = JSON.stringify(fresh);
            this.toast.success('Contenido restaurado a los defaults de fábrica.');
        } catch (err: any) {
            const detail = err?.error?.detail || err?.message || 'Error desconocido.';
            this.toast.error(`No se pudo restablecer: ${detail}`);
        } finally {
            this.isResetting = false;
            this.cdr.detectChanges();
        }
    }

    openLanding(): void {
        const host = window.location.hostname;
        // En prod, el landing vive en acten.app. En dev (localhost), abre
        // mismo origen — el guard del LandingComponent decide qué mostrar.
        const url = host.endsWith('acten.app')
            ? 'https://acten.app/'
            : window.location.origin + '/';
        window.open(url, '_blank', 'noopener');
    }

    // ────────────────────────────────────────────────────────────────────
    // Helpers para arrays dinámicos (Add / Remove)
    // ────────────────────────────────────────────────────────────────────

    addNavItem(): void {
        if (!this.content) return;
        this.content.nav.items.push({ label: 'Nuevo enlace', anchor: '#' });
    }
    removeNavItem(idx: number): void {
        this.content?.nav.items.splice(idx, 1);
    }

    addTrustLogo(): void {
        if (!this.content) return;
        this.content.trust.logos.push('Nueva empresa');
    }
    removeTrustLogo(idx: number): void {
        this.content?.trust.logos.splice(idx, 1);
    }

    addFeature(): void {
        if (!this.content) return;
        const item: FeatureItem = {
            title: 'Nueva capacidad',
            description: 'Describe lo que esta capacidad hace por el cliente.',
            icon: 'capture',
        };
        this.content.features.items.push(item);
    }
    removeFeature(idx: number): void {
        this.content?.features.items.splice(idx, 1);
    }

    addStep(): void {
        if (!this.content) return;
        const item: StepItem = {
            title: 'Nuevo paso',
            description: 'Describe el paso del flujo.',
            icon: 'mic',
        };
        this.content.flow.steps.push(item);
    }
    removeStep(idx: number): void {
        this.content?.flow.steps.splice(idx, 1);
    }

    addIntegration(): void {
        if (!this.content) return;
        this.content.integrations.items.push('Nueva integración');
    }
    removeIntegration(idx: number): void {
        this.content?.integrations.items.splice(idx, 1);
    }

    // ── Testimonios ────────────────────────────────────────────────────
    // El landing público ya NO usa testimonios estáticos del CMS — pinta
    // personas reales del sistema con las frases rotativas de `quotes[]`.
    // Conservamos add/removeTestimonial por backwards-compat para tenants
    // con items legacy guardados, pero el form admin nuevo solo expone el
    // editor de frases rotativas (más simple y alineado con lo que el
    // landing realmente renderiza).

    addTestimonialQuote(): void {
        if (!this.content) return;
        if (!this.content.testimonials.quotes) this.content.testimonials.quotes = [];
        this.content.testimonials.quotes.push('');
    }
    removeTestimonialQuote(idx: number): void {
        this.content?.testimonials.quotes?.splice(idx, 1);
    }
    /** NgModel two-way no funciona bien sobre un primitive en un array;
     *  usamos one-way + setter explícito por índice. */
    updateTestimonialQuote(idx: number, value: string): void {
        if (!this.content?.testimonials.quotes) return;
        this.content.testimonials.quotes[idx] = value;
    }

    // Legacy — se mantienen por backwards-compat (no se exponen en el
    // form actual, pero el modelo todavía los acepta).
    addTestimonial(): void {
        if (!this.content) return;
        const item: Testimonial = {
            quote: 'Lo que cambió para nosotros desde que usamos Acten…',
            name: 'Nombre Apellido',
            role: 'Cargo',
            company: 'Empresa',
            initials: 'NA',
        };
        this.content.testimonials.items.push(item);
    }
    removeTestimonial(idx: number): void {
        this.content?.testimonials.items.splice(idx, 1);
    }

    addPlan(): void {
        if (!this.content) return;
        const item: PricingPlan = {
            name: 'Nuevo plan',
            price: '$0',
            billing: 'USD / mes',
            description: 'Describe a quién va dirigido este plan.',
            features: ['Beneficio 1', 'Beneficio 2'],
            cta_label: 'Empezar',
            cta_anchor: '#contact',
            featured: false,
        };
        this.content.pricing.plans.push(item);
    }
    removePlan(idx: number): void {
        this.content?.pricing.plans.splice(idx, 1);
    }
    addPlanFeature(planIdx: number): void {
        this.content?.pricing.plans[planIdx]?.features.push('Nuevo beneficio');
    }
    removePlanFeature(planIdx: number, featIdx: number): void {
        this.content?.pricing.plans[planIdx]?.features.splice(featIdx, 1);
    }

    addResource(): void {
        if (!this.content) return;
        const item: ResourceItem = {
            category: 'Guía',
            title: 'Nuevo recurso',
            description: 'Resumen breve del recurso.',
            url: '#',
            icon: 'guide',
        };
        this.content.resources.items.push(item);
    }
    removeResource(idx: number): void {
        this.content?.resources.items.splice(idx, 1);
    }

    addValue(): void {
        if (!this.content) return;
        const item: CompanyValue = {
            title: 'Nuevo valor',
            description: 'Explica qué significa este valor para el equipo.',
        };
        this.content.company.values.push(item);
    }
    removeValue(idx: number): void {
        this.content?.company.values.splice(idx, 1);
    }

    addStat(): void {
        if (!this.content) return;
        const item: CompanyStat = { label: 'Nueva métrica', value: '0' };
        this.content.company.stats.push(item);
    }
    removeStat(idx: number): void {
        this.content?.company.stats.splice(idx, 1);
    }

    addFooterColumn(): void {
        if (!this.content) return;
        const col: FooterColumn = {
            title: 'Nueva columna',
            links: [{ label: 'Nuevo enlace', url: '#' }],
        };
        this.content.footer.columns.push(col);
    }
    removeFooterColumn(idx: number): void {
        this.content?.footer.columns.splice(idx, 1);
    }
    addFooterLink(colIdx: number): void {
        const link: FooterLink = { label: 'Nuevo enlace', url: '#' };
        this.content?.footer.columns[colIdx]?.links.push(link);
    }
    removeFooterLink(colIdx: number, linkIdx: number): void {
        this.content?.footer.columns[colIdx]?.links.splice(linkIdx, 1);
    }

    // ────────────────────────────────────────────────────────────────────
    // Opciones para selects (iconos disponibles por sección)
    // ────────────────────────────────────────────────────────────────────

    readonly featureIcons: { value: string; label: string }[] = [
        { value: 'capture', label: 'Captura' },
        { value: 'decisions', label: 'Decisiones' },
        { value: 'tasks', label: 'Tareas' },
        { value: 'docs', label: 'Documentos' },
        { value: 'email', label: 'Correos' },
        { value: 'integrations', label: 'Integraciones' },
    ];

    readonly stepIcons: { value: string; label: string }[] = [
        { value: 'mic', label: 'Micrófono (captura)' },
        { value: 'brain', label: 'Cerebro (IA)' },
        { value: 'list', label: 'Lista (estructura)' },
        { value: 'send', label: 'Enviar (acción)' },
    ];

    readonly resourceIcons: { value: string; label: string }[] = [
        { value: 'guide', label: 'Guía' },
        { value: 'video', label: 'Video' },
        { value: 'blog', label: 'Blog' },
        { value: 'case', label: 'Caso de uso' },
    ];

    /** trackBy para *ngFor en arrays editables — necesario para que NgModel
     *  no pierda el foco del input al rerenderizarse. */
    trackByIndex(index: number): number {
        return index;
    }

    // ────────────────────────────────────────────────────────────────────
    // Helpers para case_studies (sección #company de la home one-page).
    // ensureCaseStudies garantiza shape antes de mutar (tenants viejos
    // pueden no tener la key en su landing_content_json todavía).
    // ────────────────────────────────────────────────────────────────────
    get caseStudies(): NonNullable<LandingContent['case_studies']> { return this.ensureCaseStudies(); }


    // ── Helpers case_studies ──────────────────────────────────────
    private ensureCaseStudies(): NonNullable<LandingContent['case_studies']> {
        if (!this.content!.case_studies) {
            this.content!.case_studies = { eyebrow: '', title: '', subtitle: '', items: [] };
        }
        return this.content!.case_studies!;
    }
    addCaseStudy(): void {
        this.ensureCaseStudies().items.push({
            slug: 'nuevo-' + Date.now(),
            company: 'Nueva empresa',
            tagline: '',
            summary: '',
            kpis: [],
            sectors_served: [],
        });
    }
    removeCaseStudy(idx: number): void { this.ensureCaseStudies().items.splice(idx, 1); }
    addCaseKpi(caseIdx: number): void {
        const cs = this.ensureCaseStudies().items[caseIdx];
        if (!cs) return;
        cs.kpis = cs.kpis || [];
        cs.kpis.push({ label: 'Nueva métrica', value: '0' });
    }
    removeCaseKpi(caseIdx: number, kpiIdx: number): void {
        this.ensureCaseStudies().items[caseIdx]?.kpis?.splice(kpiIdx, 1);
    }
    addCaseSector(caseIdx: number): void {
        const cs = this.ensureCaseStudies().items[caseIdx];
        if (!cs) return;
        cs.sectors_served = cs.sectors_served || [];
        cs.sectors_served.push('');
    }
    removeCaseSector(caseIdx: number, secIdx: number): void {
        this.ensureCaseStudies().items[caseIdx]?.sectors_served?.splice(secIdx, 1);
    }
}
