import { ChangeDetectorRef, Component, Input, OnChanges, OnDestroy, OnInit, SimpleChanges, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { Subject, forkJoin } from 'rxjs';
import { takeUntil } from 'rxjs/operators';
import {
    AddOn, BillingService, Catalog, Plan, TenantSubscriptionInput, TenantSubscriptionRow,
} from '../../services/billing.service';
import { ToastService } from '../../services/toast.service';

interface EditorForm {
    plan_key: string;
    addons: Set<string>;
    status: 'active' | 'cancelled';
    /** yyyy-mm-dd para el <input type="date">; vacío = sin vencimiento. */
    period_end: string;
    notes: string;
}

/** Asignación manual de plan por empresa (contratos, cortesías, Enterprise). */
@Component({
    selector: 'app-tenant-subscription-editor',
    standalone: true,
    imports: [CommonModule, FormsModule, TranslateModule],
    templateUrl: './tenant-subscription-editor.component.html',
    styleUrls: ['./billing-admin.css'],
})
export class TenantSubscriptionEditorComponent implements OnInit, OnChanges, OnDestroy {
    @Input({ required: true }) tenantId!: number;

    private readonly billing = inject(BillingService);
    private readonly toast = inject(ToastService);
    private readonly translate = inject(TranslateService);
    private readonly cdr = inject(ChangeDetectorRef);
    private readonly destroy$ = new Subject<void>();

    catalog: Catalog | null = null;
    rows: TenantSubscriptionRow[] = [];
    row: TenantSubscriptionRow | null = null;
    form: EditorForm = this.emptyForm();
    loading = true;
    saving = false;
    error = '';

    ngOnInit(): void {
        forkJoin({ catalog: this.billing.getCatalogAll(), tenants: this.billing.getTenantSubscriptions() })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: ({ catalog, tenants }) => {
                    this.catalog = catalog;
                    this.rows = tenants.items;
                    this.loading = false;
                    this.pickRow();
                },
                error: (e: HttpErrorResponse) => {
                    this.loading = false;
                    this.error = this.detail(e) || this.translate.instant('tenants.billing_load_failed');
                    this.cdr.detectChanges();
                },
            });
    }

    ngOnChanges(changes: SimpleChanges): void {
        if (changes['tenantId'] && !changes['tenantId'].firstChange) { this.pickRow(); }
    }

    ngOnDestroy(): void {
        this.destroy$.next();
        this.destroy$.complete();
    }

    get plans(): Plan[] { return this.catalog?.plans ?? []; }
    get addons(): AddOn[] { return this.catalog?.addons ?? []; }

    private emptyForm(): EditorForm {
        return { plan_key: '', addons: new Set<string>(), status: 'active', period_end: '', notes: '' };
    }

    private pickRow(): void {
        this.row = this.rows.find((r) => r.tenant_id === this.tenantId) ?? null;
        const r = this.row;
        this.form = {
            plan_key: r?.plan ?? this.plans[0]?.key ?? '',
            addons: new Set(r?.addons ?? []),
            status: r?.status === 'expired' ? 'cancelled' : 'active',
            period_end: r?.period_end ? r.period_end.slice(0, 10) : '',
            notes: r?.notes ?? '',
        };
        this.cdr.detectChanges();
    }

    isAddonOn(key: string): boolean { return this.form.addons.has(key); }

    toggleAddon(key: string): void {
        const next = new Set(this.form.addons);
        if (next.has(key)) { next.delete(key); } else { next.add(key); }
        this.form = { ...this.form, addons: next };
    }

    statusTone(status: string): 'green' | 'amber' | 'red' | 'gray' {
        if (status === 'active' || status === 'trialing') { return 'green'; }
        if (status === 'past_due') { return 'amber'; }
        if (status === 'expired') { return 'red'; }
        return 'gray';
    }

    save(): void {
        if (!this.form.plan_key || this.saving) { return; }
        const body: TenantSubscriptionInput = {
            plan_key: this.form.plan_key,
            addons: [...this.form.addons],
            status: this.form.status,
            current_period_end: this.form.period_end ? `${this.form.period_end}T23:59:59` : null,
            notes: this.form.notes.trim(),
        };
        this.saving = true;
        this.billing.updateTenantSubscription(this.tenantId, body).pipe(takeUntil(this.destroy$)).subscribe({
            next: (me) => {
                this.saving = false;
                this.rows = this.rows.map((r) => r.tenant_id === this.tenantId
                    ? { ...r, plan: me.entitlements.plan, status: me.entitlements.status, addons: me.entitlements.addons,
                        billing_mode: me.entitlements.billing_mode, period_end: me.entitlements.period_end, notes: body.notes }
                    : r);
                this.row = this.rows.find((r) => r.tenant_id === this.tenantId) ?? null;
                this.toast.success(this.translate.instant('tenants.billing_saved'));
                this.cdr.detectChanges();
            },
            error: (e: HttpErrorResponse) => {
                this.saving = false;
                this.toast.error(this.detail(e) || this.translate.instant('tenants.billing_save_failed'));
                this.cdr.detectChanges();
            },
        });
    }

    private detail(e: HttpErrorResponse): string {
        const d: unknown = e?.error?.detail;
        return typeof d === 'string' ? d : '';
    }
}
