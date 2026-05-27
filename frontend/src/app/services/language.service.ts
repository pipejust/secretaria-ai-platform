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
/**
 * Flag de sessionStorage que marca si el user PICKED un idioma en este
 * tab/sesión actual desde el selector (landing/login/perfil). Cuando es
 * true y el user inicia sesión, el idioma elegido en el cliente PRIMA
 * sobre `user.language` que viene del backend — el caller hace push del
 * idioma local al server. Esto permite que una persona seleccione 'ca'
 * en la landing, inicie sesión, y el primer login adopte 'ca' como su
 * preferencia persistente.
 *
 * Se limpia automáticamente al hacer logout y al consumir el flag tras
 * el login. Usamos sessionStorage (no localStorage) para que NO sobreviva
 * a cierres de pestaña — la "explicitud" muere con la sesión del browser.
 */
const SESSION_PICKED_KEY = 'acten_lang_picked_session';
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
     *
     * Marca el flag `SESSION_PICKED_KEY` para que el siguiente login sepa
     * que el cliente eligió un idioma explícitamente y deba priorizarlo
     * sobre el `user.language` del backend.
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
        // Marcamos que el user eligió en este browser session — gana
        // sobre user.language en el siguiente login si difiere.
        try {
            sessionStorage.setItem(SESSION_PICKED_KEY, '1');
        } catch { /* ignore */ }
    }

    /**
     * Reconcilia el idioma del cliente con el del perfil del user tras login.
     *
     * Reglas:
     *  - Si el user eligió un idioma en esta sesión del browser (flag
     *    `SESSION_PICKED_KEY`) Y difiere de `user.language` del backend →
     *    el local PRIMA: lo pusheamos al server via setLanguage (que hace
     *    PUT /auth/me). Esto cumple la regla "si elige idioma en
     *    landing/login, su primer login adopta ese idioma".
     *  - En caso contrario, aplicamos `user.language` localmente — la
     *    config del perfil prevalece para logins recurrentes.
     *  - Consumimos el flag SIEMPRE (incluso si no hubo cambio).
     */
    async syncFromUserProfile(language: string | null | undefined): Promise<void> {
        const userPickedThisSession = this.consumeSessionPickFlag();
        const serverLang = language ? this.normalize(language) : null;
        const localLang = this.currentLang();

        if (userPickedThisSession && (!serverLang || serverLang !== localLang)) {
            // El user eligió en la landing/login y/o el server no tiene
            // valor: pusheamos el local. setLanguage hace PUT /auth/me.
            await this.setLanguage(localLang, { persistLocal: true, syncServer: true });
            return;
        }

        // Login recurrente o el user no tocó el selector — gana la config
        // del perfil del backend.
        if (!serverLang) return;
        if (serverLang === localLang) return;
        await this.applyLanguage(serverLang, { persistLocal: true, syncServer: false });
    }

    /** Limpia el flag de "pick en sesión". Llamar en logout para que la
     *  siguiente cuenta no herede la elección de la anterior. */
    clearSessionPickFlag(): void {
        try { sessionStorage.removeItem(SESSION_PICKED_KEY); } catch { /* ignore */ }
    }

    private consumeSessionPickFlag(): boolean {
        try {
            const v = sessionStorage.getItem(SESSION_PICKED_KEY);
            sessionStorage.removeItem(SESSION_PICKED_KEY);
            return v === '1';
        } catch {
            return false;
        }
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
        // 1) localStorage — preferencia explícita del usuario en este browser
        if (typeof localStorage !== 'undefined') {
            const stored = localStorage.getItem(STORAGE_KEY);
            if (stored && SUPPORTED.includes(stored as SupportedLang)) {
                return stored as SupportedLang;
            }
        }
        // 2) navigator.language SOLO si es 'ca' (catalán) — el caso único
        // donde detectar autoidioma agrega valor. Para 'en' ignoramos porque
        // la mayoría de visitantes hispanos pueden tener navegadores en
        // inglés y la plataforma es hispana por default. Esto evita que la
        // landing arranque en inglés para usuarios de español/catalán que
        // tienen Chrome configurado en inglés.
        if (typeof navigator !== 'undefined' && navigator.language) {
            const detected = this.normalize(navigator.language);
            if (detected === 'ca') return 'ca';
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
