/**
 * LanguageService — orquesta i18n de toda la plataforma.
 *
 * Idiomas soportados: 'es' (default), 'ca', 'en'.
 *
 * Prioridad para elegir idioma inicial:
 *   1. Preferencia guardada del user logueado (user.language en el perfil)
 *   2. localStorage['acten_lang'] de sesiones anteriores
 *   3. navigator.language (mapeado a 'es' | 'ca' | 'en')
 *   4. 'es' como fallback final
 *
 * Cuando el user cambia idioma desde el selector (login/landing/perfil):
 *  - Aplica inmediatamente vía TranslateService
 *  - Persiste en localStorage para próximas visitas sin login
 *  - Si está logueado, también persiste en server (PUT /auth/me { language })
 */
import { Injectable, inject, signal, computed } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { TranslateService } from '@ngx-translate/core';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';

export type SupportedLang = 'es' | 'ca' | 'en';

const STORAGE_KEY = 'acten_lang';
const SUPPORTED: SupportedLang[] = ['es', 'ca', 'en'];
const DEFAULT_LANG: SupportedLang = 'es';

@Injectable({ providedIn: 'root' })
export class LanguageService {
    private translate = inject(TranslateService);
    private http = inject(HttpClient);

    /** Idioma actualmente aplicado (signal reactivo para que las UIs se
     *  re-rendericen al cambiar). */
    readonly currentLang = signal<SupportedLang>(DEFAULT_LANG);

    /** Etiqueta human-friendly del idioma actual ("Español"/"Català"/"English"). */
    readonly currentLangLabel = computed(() => {
        const map: Record<SupportedLang, string> = {
            es: 'Español',
            ca: 'Català',
            en: 'English',
        };
        return map[this.currentLang()];
    });

    readonly supportedLangs: { code: SupportedLang; label: string; flag: string }[] = [
        { code: 'es', label: 'Español', flag: '🇪🇸' },
        { code: 'ca', label: 'Català', flag: '🇪🇸' },  // sin emoji oficial — usa el español
        { code: 'en', label: 'English', flag: '🇺🇸' },
    ];

    /**
     * Configura el TranslateService y aplica el idioma inicial. Llamar UNA
     * vez al arrancar la app (vía APP_INITIALIZER) ANTES de que los
     * componentes intenten traducir.
     */
    async bootstrap(): Promise<void> {
        this.translate.addLangs(SUPPORTED);
        this.translate.setFallbackLang(DEFAULT_LANG);
        const initial = this.resolveInitialLang();
        // CRITICAL: bootstrap NUNCA debe bloquear el render. Si los JSON
        // fallan (server devuelve HTML por SPA fallback, network error,
        // CORS, etc.) NO debemos lanzar — la app debe arrancar igual con
        // las claves literales como fallback ("login.title" si no se
        // resolvió). Esto evita pantalla blanca por un fallo de i18n.
        try {
            await this.applyLanguage(initial, { persistLocal: true, syncServer: false });
        } catch (err) {
            console.error('[LanguageService] bootstrap falló — la app arranca sin traducciones:', err);
        }
    }

    /**
     * Cambia el idioma activo y opcionalmente lo persiste.
     * - persistLocal: guarda en localStorage para próximas visitas (default true)
     * - syncServer: PUT /auth/me con la nueva preferencia (default true si el
     *   user está logueado; false en login/landing)
     */
    async setLanguage(
        lang: SupportedLang,
        opts: { persistLocal?: boolean; syncServer?: boolean } = {},
    ): Promise<void> {
        const safe = this.normalize(lang);
        await this.applyLanguage(safe, {
            persistLocal: opts.persistLocal ?? true,
            syncServer: opts.syncServer ?? this.hasAuthToken(),
        });
    }

    /** Lee el idioma persistido del usuario logueado (vía /auth/me) y lo
     *  aplica. Llamar después de un login exitoso. */
    async syncFromUserProfile(language: string | null | undefined): Promise<void> {
        if (!language) return;
        const safe = this.normalize(language);
        if (safe === this.currentLang()) return;
        await this.applyLanguage(safe, { persistLocal: true, syncServer: false });
    }

    // ─────────────────────────────────────────────────────────────────
    // Internos
    // ─────────────────────────────────────────────────────────────────

    private async applyLanguage(
        lang: SupportedLang,
        opts: { persistLocal: boolean; syncServer: boolean },
    ): Promise<void> {
        await firstValueFrom(this.translate.use(lang));
        this.currentLang.set(lang);
        // Sincronizar el <html lang="..."> para SEO/accesibilidad.
        if (typeof document !== 'undefined') {
            document.documentElement.lang = lang;
        }
        if (opts.persistLocal && typeof localStorage !== 'undefined') {
            try { localStorage.setItem(STORAGE_KEY, lang); } catch { /* ignore */ }
        }
        if (opts.syncServer && this.hasAuthToken()) {
            try {
                const token = localStorage.getItem('access_token') || '';
                const headers = new HttpHeaders({ Authorization: `Bearer ${token}` });
                await firstValueFrom(
                    this.http.put(`${environment.apiUrl}/auth/me`, { language: lang }, { headers }),
                );
            } catch (err) {
                // Best-effort: si falla, igual queda aplicado client-side.
                console.warn('LanguageService: no se pudo sincronizar idioma con server', err);
            }
        }
    }

    private resolveInitialLang(): SupportedLang {
        // 1) localStorage
        if (typeof localStorage !== 'undefined') {
            const stored = localStorage.getItem(STORAGE_KEY);
            if (stored && SUPPORTED.includes(stored as SupportedLang)) {
                return stored as SupportedLang;
            }
        }
        // 2) navigator.language
        if (typeof navigator !== 'undefined' && navigator.language) {
            return this.normalize(navigator.language);
        }
        return DEFAULT_LANG;
    }

    private normalize(raw: string): SupportedLang {
        const code = (raw || '').toLowerCase().split('-')[0];
        if (SUPPORTED.includes(code as SupportedLang)) return code as SupportedLang;
        return DEFAULT_LANG;
    }

    private hasAuthToken(): boolean {
        return typeof localStorage !== 'undefined' && !!localStorage.getItem('access_token');
    }
}
