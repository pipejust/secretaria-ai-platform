import { Injectable } from '@angular/core';
import { BehaviorSubject, Observable } from 'rxjs';

export interface UiPrefs {
    dateFormat: string;
    timeZone: string;
    language: string;
    landingPage: string;
    darkMode: boolean;
    workspaceName: string;
    weekStartsOn: 'monday' | 'sunday';
    defaultMeetingDuration: number;
    defaultTaskAssignee: string;
    defaultProjectPrivacy: 'workspace' | 'private' | 'public';
    autoArchiveDays: number;
    defaultCalendar: string;
    defaultFileStorage: string;
    defaultCommunicationChannel: string;
    defaultDocEditor: string;
}

export const PREFS_LS_KEY = 'acten:ui-prefs:v1';

export const DEFAULT_PREFS: UiPrefs = {
    dateFormat: 'DD/MM/YYYY',
    timeZone: 'America/Bogota',
    language: 'es-CO',
    landingPage: '/admin/dashboard',
    darkMode: false,
    workspaceName: '',
    weekStartsOn: 'monday',
    defaultMeetingDuration: 60,
    defaultTaskAssignee: '',
    defaultProjectPrivacy: 'workspace',
    autoArchiveDays: 90,
    defaultCalendar: 'google',
    defaultFileStorage: 'google-drive',
    defaultCommunicationChannel: '',
    defaultDocEditor: 'google-docs',
};

/**
 * Servicio central de preferencias de UX por usuario.
 *
 * Diseño:
 *   1) Una sola fuente de verdad → localStorage (no hay endpoint backend).
 *   2) BehaviorSubject expone cambios en tiempo real al resto de la app.
 *   3) `applySideEffects()` aplica efectos visibles inmediatos (clase
 *      `dark-mode` en body, atributo `data-week-start` en `<html>`, etc).
 *   4) Consumidores pueden:
 *        - leer una vez con `get()`
 *        - suscribirse con `prefs$` para reaccionar a cambios
 */
@Injectable({ providedIn: 'root' })
export class PreferencesService {
    private readonly _subject = new BehaviorSubject<UiPrefs>(this._load());
    /** Stream que emite cada vez que cambia alguna preferencia. */
    readonly prefs$: Observable<UiPrefs> = this._subject.asObservable();

    constructor() {
        // Aplicar efectos al boot inicial.
        this.applySideEffects();
    }

    get(): UiPrefs { return this._subject.value; }

    /** Reemplaza el bloque completo de prefs y persiste. */
    setAll(next: UiPrefs): void {
        const merged = { ...DEFAULT_PREFS, ...next };
        this._subject.next(merged);
        this._save(merged);
        this.applySideEffects();
    }

    /** Actualiza un único campo y persiste. */
    set<K extends keyof UiPrefs>(key: K, value: UiPrefs[K]): void {
        const next = { ...this._subject.value, [key]: value };
        this._subject.next(next);
        this._save(next);
        this.applySideEffects();
    }

    /** Resetea TODO a los defaults. */
    reset(): void { this.setAll({ ...DEFAULT_PREFS }); }

    // ============================================================
    // Side effects que se aplican a la página entera.
    // ============================================================
    applySideEffects(): void {
        if (typeof document === 'undefined') return;
        const p = this._subject.value;
        // Dark mode → clase global del body.
        document.body.classList.toggle('acten-dark', !!p.darkMode);
        // Semana inicia → atributo legible por CSS / componentes.
        document.documentElement.setAttribute('data-week-start', p.weekStartsOn);
        // Idioma → atributo del documento (futuro i18n).
        document.documentElement.setAttribute('lang', (p.language || 'es-CO').split('-')[0]);
    }

    // ============================================================
    // Helpers de formato. Otros componentes pueden importar el servicio
    // y formatear fechas/horas en base a las prefs actuales.
    // ============================================================
    formatDate(value: Date | string | number | null | undefined): string {
        if (value == null || value === '') return '—';
        const d = (value instanceof Date) ? value : new Date(value);
        if (isNaN(d.getTime())) return String(value);
        const tz = this._subject.value.timeZone || undefined;
        const fmt = this._subject.value.dateFormat || 'DD/MM/YYYY';
        const parts = new Intl.DateTimeFormat('en-CA', {
            timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit',
        }).formatToParts(d);
        const get = (t: string) => parts.find(p => p.type === t)?.value || '';
        const y = get('year'), m = get('month'), dd = get('day');
        switch (fmt) {
            case 'MM/DD/YYYY': return `${m}/${dd}/${y}`;
            case 'YYYY-MM-DD': return `${y}-${m}-${dd}`;
            case 'D MMM YYYY': {
                const month = new Intl.DateTimeFormat('es-CO', { timeZone: tz, month: 'short' }).format(d);
                return `${parseInt(dd, 10)} ${month} ${y}`;
            }
            default:           return `${dd}/${m}/${y}`;
        }
    }

    // ============================================================
    // Persistencia.
    // ============================================================
    private _load(): UiPrefs {
        try {
            const raw = localStorage.getItem(PREFS_LS_KEY);
            if (!raw) return { ...DEFAULT_PREFS };
            const parsed = JSON.parse(raw);
            return { ...DEFAULT_PREFS, ...(typeof parsed === 'object' ? parsed : {}) };
        } catch { return { ...DEFAULT_PREFS }; }
    }

    private _save(prefs: UiPrefs): void {
        try { localStorage.setItem(PREFS_LS_KEY, JSON.stringify(prefs)); }
        catch { /* quota / private mode */ }
    }
}
