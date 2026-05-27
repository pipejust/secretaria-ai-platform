/**
 * TitleService — gestiona el `<title>` del browser de forma i18n-consciente.
 *
 * Lee `route.data.titleKey` de la ruta activa, lo traduce vía ngx-translate,
 * y aplica `Platform — Translated Title` al document title. Se re-ejecuta
 * tanto en cada NavigationEnd como cuando el usuario cambia de idioma desde
 * el selector (vía signal `langService.currentLang`), garantizando que el
 * tab del browser SIEMPRE refleje el idioma activo sin necesidad de recargar.
 *
 * Registro: `provideAppInitializer(() => inject(TitleService).start())` en
 * app.config.ts — debe arrancar UNA vez tras el bootstrap de i18n.
 *
 * Patrón de uso desde rutas:
 *   {
 *     path: 'dashboard',
 *     component: DashboardComponent,
 *     data: { titleKey: 'route_titles.dashboard' }
 *   }
 *
 * Backward-compat: si una ruta no tiene `titleKey` pero sí tiene `title`
 * (string hardcoded del legacy), respetamos el `title` literal. Esto deja
 * que rutas no migradas sigan funcionando sin regresión.
 */
import { Injectable, effect, inject } from '@angular/core';
import { Title } from '@angular/platform-browser';
import { ActivatedRoute, NavigationEnd, Router } from '@angular/router';
import { TranslateService } from '@ngx-translate/core';
import { filter } from 'rxjs/operators';
import { LanguageService } from './language.service';

const PLATFORM_NAME = 'Acten';

@Injectable({ providedIn: 'root' })
export class TitleService {
    private readonly router = inject(Router);
    private readonly activatedRoute = inject(ActivatedRoute);
    private readonly title = inject(Title);
    private readonly translate = inject(TranslateService);
    private readonly langService = inject(LanguageService);

    private started = false;

    /**
     * Arranca el listener. Idempotente — llamadas repetidas son no-op.
     * Llamar UNA vez vía APP_INITIALIZER tras `LanguageService.bootstrap()`.
     */
    start(): void {
        if (this.started) return;
        this.started = true;

        // 1) Cada NavigationEnd → re-resuelve y aplica el título de la ruta activa.
        this.router.events
            .pipe(filter((e) => e instanceof NavigationEnd))
            .subscribe(() => this.applyForCurrentRoute());

        // 2) Cambio de idioma desde el selector → re-traduce sin esperar
        //    a una nueva navegación. Reactivo vía signal.
        effect(() => {
            // Leer la signal para que el effect se re-dispare al cambiar.
            this.langService.currentLang();
            // No corremos en el primer tick si todavía no hubo navigation;
            // applyForCurrentRoute() es safe y simplemente cae al fallback.
            this.applyForCurrentRoute();
        });

        // Aplicación inicial — antes de la primera NavigationEnd, el title
        // del index.html sigue válido. Lo refrescamos por si arrancamos en
        // una ruta concreta (deep link).
        this.applyForCurrentRoute();
    }

    /** Resuelve la ruta hoja activa y aplica el título traducido. */
    private applyForCurrentRoute(): void {
        const leaf = this.resolveLeafRoute();
        if (!leaf) return;
        const data = leaf.snapshot.data || {};
        const titleKey = (data as Record<string, unknown>)['titleKey'];
        const legacyTitle = leaf.snapshot.title;

        if (typeof titleKey === 'string' && titleKey) {
            const translated = this.translate.instant(titleKey);
            // Si la traducción falla, `instant` devuelve la propia key.
            // En ese caso caemos al legacy title o al platform name.
            const safe = translated && translated !== titleKey
                ? translated
                : (legacyTitle || PLATFORM_NAME);
            this.title.setTitle(`${PLATFORM_NAME} — ${safe}`);
            return;
        }

        if (legacyTitle) {
            // Legacy route — respetamos el string hardcoded.
            this.title.setTitle(legacyTitle);
            return;
        }

        // Sin nada definido → solo el nombre de la plataforma.
        this.title.setTitle(PLATFORM_NAME);
    }

    /** Camina hasta la ruta hoja desde ActivatedRoute.root. */
    private resolveLeafRoute(): ActivatedRoute | null {
        let route: ActivatedRoute | null = this.activatedRoute.root;
        while (route?.firstChild) {
            route = route.firstChild;
        }
        return route;
    }
}
