import { ChangeDetectorRef, Component, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterModule } from '@angular/router';
import { HttpErrorResponse } from '@angular/common/http';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { Subject, forkJoin, of } from 'rxjs';
import { catchError, takeUntil } from 'rxjs/operators';
import {
    AddOn, BillingMe, BillingService, Catalog, PaymentInfo, Plan, SubscriptionStatus,
} from '../../services/billing.service';
import { AuthService } from '../../services/auth.service';
import { LanguageService } from '../../services/language.service';
import { ToastService } from '../../services/toast.service';
import {
    MONTH_OPTIONS, clampMonths, dependentsOf, formatCop, formatUsd, toggleAddon, totalCopCents,
} from './billing-pricing';
import { loadWompiWidget, openWompiWidget } from './wompi-widget';

const MS_PER_DAY = 86_400_000;
const LOCALES: Record<string, string> = { es: 'es-CO', en: 'en-US', ca: 'ca-ES' };

@Component({
    selector: 'app-billing',
    standalone: true,
    imports: [CommonModule, FormsModule, RouterModule, TranslateModule],
    templateUrl: './billing.component.html',
    styleUrls: ['./billing.component.css'],
})
export class BillingComponent implements OnInit, OnDestroy {
    private readonly billing = inject(BillingService);
    private readonly auth = inject(AuthService);
    private readonly toast = inject(ToastService);
    private readonly translate = inject(TranslateService);
    private readonly lang = inject(LanguageService);
    private readonly route = inject(ActivatedRoute);
    private readonly router = inject(Router);
    private readonly cdr = inject(ChangeDetectorRef);
    private readonly destroy$ = new Subject<void>();

    readonly monthOptions = MONTH_OPTIONS;

    me: BillingMe | null = null;
    catalog: Catalog | null = null;
    payments: PaymentInfo[] = [];
    loading = true;
    loadError = '';

    selectedPlanKey: string | null = null;
    selectedAddons: ReadonlySet<string> = new Set<string>();
    months = 1;

    paying = false;
    syncing = false;
    /** Resultado del pago que llegó por redirección (?reference=&id=). */
    redirectPayment: PaymentInfo | null = null;

    cancelPrompt = false;
    cancelSaving = false;

    ngOnInit(): void {
        this.load();
    }

    ngOnDestroy(): void {
        this.destroy$.next();
        this.destroy$.complete();
    }

    // ── Carga ───────────────────────────────────────────────────────────

    load(): void {
        this.loading = true;
        this.loadError = '';
        forkJoin({
            me: this.billing.getMe(),
            catalog: this.billing.getCatalog(),
            payments: this.billing.getPayments().pipe(catchError(() => of({ items: [] as PaymentInfo[] }))),
        }).pipe(takeUntil(this.destroy$)).subscribe({
            next: ({ me, catalog, payments }) => {
                this.me = me;
                this.catalog = catalog;
                this.payments = payments.items;
                this.preselectCurrent();
                this.loading = false;
                this.cdr.detectChanges();
                this.syncFromRedirect();
            },
            error: (e: HttpErrorResponse) => {
                this.loading = false;
                this.loadError = this.detailMessage(e) || this.translate.instant('billing.msg_load_failed');
                this.cdr.detectChanges();
            },
        });
    }

    private preselectCurrent(): void {
        if (this.selectedPlanKey) { return; }
        const plans = this.catalog?.plans ?? [];
        const current = plans.find((p) => p.key === this.me?.entitlements.plan);
        this.selectedPlanKey = (current ?? plans[0])?.key ?? null;
        const known = new Set(this.addons.map((a) => a.key));
        this.selectedAddons = new Set((this.me?.entitlements.addons ?? []).filter((k) => known.has(k)));
    }

    private refreshState(): void {
        forkJoin({ me: this.billing.getMe(), payments: this.billing.getPayments() })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: ({ me, payments }) => {
                    this.me = me;
                    this.payments = payments.items;
                    this.auth.refreshCurrentUser();
                    this.cdr.detectChanges();
                },
                error: () => { /* la pantalla conserva el estado anterior */ },
            });
    }

    // ── Estado actual ───────────────────────────────────────────────────

    get status(): SubscriptionStatus { return this.me?.entitlements.status ?? 'none'; }
    get currentPlan(): Plan | null { return this.me?.plan ?? null; }
    get plans(): Plan[] { return this.catalog?.plans ?? []; }
    get addons(): AddOn[] { return this.catalog?.addons ?? []; }

    get trialDaysLeft(): number { return this.daysUntil(this.me?.entitlements.trial_ends); }
    get graceDaysLeft(): number {
        const grace = this.me?.grace_days ?? 0;
        return Math.max(0, this.daysUntil(this.me?.entitlements.period_end) + grace);
    }

    private daysUntil(iso: string | null | undefined): number {
        if (!iso) { return 0; }
        const end = new Date(iso).getTime();
        if (Number.isNaN(end)) { return 0; }
        return Math.max(0, Math.ceil((end - Date.now()) / MS_PER_DAY));
    }

    get meetingsLimit(): number | null { return this.me?.entitlements.meetings_per_month ?? null; }
    get usersLimit(): number | null { return this.me?.entitlements.users_included ?? null; }

    usagePercent(used: number, limit: number | null): number {
        if (!limit) { return 0; }
        return Math.min(100, Math.round((used / limit) * 100));
    }

    // ── Selección ───────────────────────────────────────────────────────

    get selectedPlan(): Plan | null {
        return this.plans.find((p) => p.key === this.selectedPlanKey) ?? null;
    }

    get selectedAddonList(): AddOn[] {
        return this.addons.filter((a) => this.selectedAddons.has(a.key));
    }

    get totalCop(): number {
        return totalCopCents(this.selectedPlan, this.selectedAddonList, this.months);
    }

    selectPlan(key: string): void { this.selectedPlanKey = key; }

    isAddonSelected(key: string): boolean { return this.selectedAddons.has(key); }

    onToggleAddon(key: string): void {
        this.selectedAddons = toggleAddon(this.selectedAddons, key, this.addons);
    }

    /** Nombres de los add-ons seleccionados que exigen a este. */
    dependentNames(key: string): string {
        return dependentsOf(key, this.selectedAddons, this.addons)
            .map((k) => this.addonName(k)).join(', ');
    }

    requiresNames(addon: AddOn): string {
        return addon.requires.map((k) => this.addonName(k)).join(', ');
    }

    addonName(key: string): string {
        return this.addons.find((a) => a.key === key)?.name ?? key;
    }

    featureLabel(key: string): string { return this.catalog?.features[key] ?? key; }

    onMonthsChange(value: number | string): void {
        this.months = clampMonths(Number(value));
    }

    // ── Pago con Wompi ──────────────────────────────────────────────────

    get canPay(): boolean {
        return !!this.me?.payments_enabled && !!this.selectedPlan && this.totalCop > 0 && !this.paying;
    }

    pay(): void {
        const plan = this.selectedPlan;
        if (!plan || !this.canPay) { return; }
        this.paying = true;
        this.billing.checkout({
            plan_key: plan.key, addons: [...this.selectedAddons], months: this.months,
        }).pipe(takeUntil(this.destroy$)).subscribe({
            next: async (checkout) => {
                try {
                    await loadWompiWidget(checkout.widget_script);
                    openWompiWidget({
                        currency: checkout.currency,
                        amountInCents: checkout.amount_in_cents,
                        reference: checkout.reference,
                        publicKey: checkout.public_key,
                        signature: { integrity: checkout.signature_integrity },
                        redirectUrl: checkout.redirect_url,
                        customerData: { email: checkout.customer_email },
                    }, (result) => this.onWidgetResult(checkout.reference, result.transaction?.id));
                } catch {
                    this.toast.error(this.translate.instant('billing.msg_widget_failed'));
                } finally {
                    this.paying = false;
                    this.cdr.detectChanges();
                }
            },
            error: (e: HttpErrorResponse) => {
                this.paying = false;
                this.toast.error(this.detailMessage(e) || this.translate.instant('billing.msg_checkout_failed'));
                this.cdr.detectChanges();
            },
        });
    }

    private onWidgetResult(reference: string, transactionId: string | undefined): void {
        if (!transactionId) {
            this.refreshState();
            return;
        }
        this.sync(reference, transactionId);
    }

    /** Wompi vuelve a `/admin/billing?reference=...&id=...` tras pagar. */
    private syncFromRedirect(): void {
        const params = this.route.snapshot.queryParamMap;
        const reference = params.get('reference');
        const transactionId = params.get('id');
        if (!reference) { return; }
        this.router.navigate([], { relativeTo: this.route, queryParams: {}, replaceUrl: true });
        if (transactionId) {
            this.sync(reference, transactionId);
            return;
        }
        this.billing.getPayment(reference).pipe(takeUntil(this.destroy$)).subscribe({
            next: (p) => { this.redirectPayment = p; this.announcePayment(p); this.cdr.detectChanges(); },
            error: () => { /* referencia desconocida: no hay nada que mostrar */ },
        });
    }

    private sync(reference: string, transactionId: string): void {
        this.syncing = true;
        this.billing.syncPayment(reference, transactionId).pipe(takeUntil(this.destroy$)).subscribe({
            next: (p) => {
                this.syncing = false;
                this.redirectPayment = p;
                this.announcePayment(p);
                this.refreshState();
            },
            error: (e: HttpErrorResponse) => {
                this.syncing = false;
                this.toast.error(this.detailMessage(e) || this.translate.instant('billing.msg_sync_failed'));
                this.cdr.detectChanges();
            },
        });
    }

    private announcePayment(p: PaymentInfo): void {
        if (p.status === 'approved') {
            this.toast.success(this.translate.instant('billing.msg_payment_approved'));
        } else if (p.status === 'pending') {
            this.toast.info(this.translate.instant('billing.msg_payment_pending'));
        } else {
            this.toast.warning(this.translate.instant('billing.msg_payment_declined'));
        }
    }

    // ── Cancelación ─────────────────────────────────────────────────────

    get cancelAtPeriodEnd(): boolean { return !!this.me?.subscription?.cancel_at_period_end; }
    get canCancel(): boolean { return !!this.me?.subscription && !this.cancelSaving; }

    requestCancelToggle(): void {
        if (!this.canCancel) { return; }
        if (this.cancelAtPeriodEnd) {
            this.saveCancel(false);
            return;
        }
        this.cancelPrompt = true;
    }

    confirmCancel(): void { this.saveCancel(true); }
    dismissCancel(): void { this.cancelPrompt = false; }

    private saveCancel(value: boolean): void {
        this.cancelSaving = true;
        this.billing.cancel(value).pipe(takeUntil(this.destroy$)).subscribe({
            next: (me) => {
                this.me = me;
                this.cancelPrompt = false;
                this.cancelSaving = false;
                this.toast.success(this.translate.instant(value ? 'billing.msg_cancel_saved' : 'billing.msg_resume_saved'));
                this.cdr.detectChanges();
            },
            error: (e: HttpErrorResponse) => {
                this.cancelSaving = false;
                this.toast.error(this.detailMessage(e) || this.translate.instant('billing.msg_cancel_failed'));
                this.cdr.detectChanges();
            },
        });
    }

    // ── Formato ─────────────────────────────────────────────────────────

    private get locale(): string { return LOCALES[this.lang.currentLang()] ?? 'es-CO'; }

    cop(cents: number): string { return formatCop(cents, this.locale); }
    usd(cents: number): string { return formatUsd(cents); }

    date(iso: string | null | undefined): string {
        if (!iso) { return '—'; }
        const d = new Date(iso);
        if (Number.isNaN(d.getTime())) { return '—'; }
        return new Intl.DateTimeFormat(this.locale, { day: 'numeric', month: 'short', year: 'numeric' }).format(d);
    }

    trackByKey(_i: number, item: { key: string }): string { return item.key; }
    trackByReference(_i: number, p: PaymentInfo): string { return p.reference; }

    private detailMessage(e: HttpErrorResponse): string {
        const detail: unknown = e?.error?.detail;
        if (typeof detail === 'string') { return detail; }
        if (detail && typeof detail === 'object' && 'message' in detail) {
            return String((detail as { message: unknown }).message);
        }
        return '';
    }
}
