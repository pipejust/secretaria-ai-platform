import { AddOn, Plan } from '../../services/billing.service';

export const MIN_MONTHS = 1;
export const MAX_MONTHS = 12;

/** Meses válidos para el selector (1–12). */
export const MONTH_OPTIONS: readonly number[] = Array.from(
    { length: MAX_MONTHS - MIN_MONTHS + 1 }, (_, i) => MIN_MONTHS + i,
);

export function clampMonths(months: number): number {
    if (!Number.isFinite(months)) { return MIN_MONTHS; }
    return Math.min(MAX_MONTHS, Math.max(MIN_MONTHS, Math.trunc(months)));
}

/** Total en centavos de COP, solo para mostrar: el backend recalcula. */
export function totalCopCents(plan: Plan | null, addons: readonly AddOn[], months: number): number {
    if (!plan) { return 0; }
    const addonsCents = addons.reduce((sum, a) => sum + a.price_cop_cents, 0);
    return (plan.price_cop_cents + addonsCents) * clampMonths(months);
}

/** Marca un add-on: al activarlo se activan también los que exige (en
 *  cadena); al desactivarlo caen los que dependían de él. Devuelve un
 *  conjunto nuevo. */
export function toggleAddon(selected: ReadonlySet<string>, key: string, catalog: readonly AddOn[]): Set<string> {
    const next = new Set(selected);
    const byKey = new Map(catalog.map((a) => [a.key, a]));
    if (next.has(key)) {
        next.delete(key);
        removeDependents(next, key, catalog);
        return next;
    }
    next.add(key);
    addRequirements(next, key, byKey);
    return next;
}

function addRequirements(next: Set<string>, key: string, byKey: ReadonlyMap<string, AddOn>): void {
    for (const req of byKey.get(key)?.requires ?? []) {
        if (!next.has(req)) {
            next.add(req);
            addRequirements(next, req, byKey);
        }
    }
}

function removeDependents(next: Set<string>, removed: string, catalog: readonly AddOn[]): void {
    for (const a of catalog) {
        if (next.has(a.key) && a.requires.includes(removed)) {
            next.delete(a.key);
            removeDependents(next, a.key, catalog);
        }
    }
}

/** Claves de add-ons seleccionados que exigen a `key` (para explicar por
 *  qué no se puede quitar sin arrastrar otros). */
export function dependentsOf(key: string, selected: ReadonlySet<string>, catalog: readonly AddOn[]): string[] {
    return catalog.filter((a) => selected.has(a.key) && a.requires.includes(key)).map((a) => a.key);
}

export function formatCop(cents: number, locale = 'es-CO'): string {
    return new Intl.NumberFormat(locale, { style: 'currency', currency: 'COP', maximumFractionDigits: 0 })
        .format(Math.round(cents / 100));
}

export function formatUsd(cents: number, locale = 'en-US'): string {
    return new Intl.NumberFormat(locale, { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
        .format(cents / 100);
}
