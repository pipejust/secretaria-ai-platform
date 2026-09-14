import { ChangeDetectorRef, Component, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { Subject } from 'rxjs';
import { takeUntil } from 'rxjs/operators';
import { AddOn, AddOnUpdate, BillingService, Plan, PlanUpdate } from '../../services/billing.service';
import { ToastService } from '../../services/toast.service';

/** Fila editable: precios en unidades (USD / COP), no en centavos. */
export interface PlanRow {
    key: string;
    name: string;
    price_usd: number;
    price_cop: number;
    meetings_per_month: number | null;
    users_included: number | null;
    is_public: boolean;
    is_active: boolean;
}

export interface AddOnRow {
    key: string;
    name: string;
    price_usd: number;
    price_cop: number;
    is_active: boolean;
}

const toPlanRow = (p: Plan): PlanRow => ({
    key: p.key, name: p.name, price_usd: p.price_usd_cents / 100, price_cop: p.price_cop_cents / 100,
    meetings_per_month: p.meetings_per_month, users_included: p.users_included,
    is_public: p.is_public, is_active: p.is_active,
});

const toAddOnRow = (a: AddOn): AddOnRow => ({
    key: a.key, name: a.name, price_usd: a.price_usd_cents / 100, price_cop: a.price_cop_cents / 100, is_active: a.is_active,
});

const cents = (units: number | null | undefined): number => Math.max(0, Math.round((Number(units) || 0) * 100));
const intOrNull = (v: number | null | undefined): number | null =>
    v === null || v === undefined || Number.isNaN(Number(v)) || String(v) === '' ? null : Math.max(0, Math.trunc(Number(v)));

/** Precios y visibilidad de planes y add-ons (solo superadmin). */
@Component({
    selector: 'app-catalog-editor',
    standalone: true,
    imports: [CommonModule, FormsModule, TranslateModule],
    templateUrl: './catalog-editor.component.html',
    styleUrls: ['./billing-admin.css'],
})
export class CatalogEditorComponent implements OnInit, OnDestroy {
    private readonly billing = inject(BillingService);
    private readonly toast = inject(ToastService);
    private readonly translate = inject(TranslateService);
    private readonly cdr = inject(ChangeDetectorRef);
    private readonly destroy$ = new Subject<void>();

    plans: PlanRow[] = [];
    addons: AddOnRow[] = [];
    loading = true;
    savingKey: string | null = null;
    error = '';

    ngOnInit(): void {
        this.billing.getCatalogAll().pipe(takeUntil(this.destroy$)).subscribe({
            next: (c) => {
                this.plans = c.plans.map(toPlanRow);
                this.addons = c.addons.map(toAddOnRow);
                this.loading = false;
                this.cdr.detectChanges();
            },
            error: (e: HttpErrorResponse) => {
                this.loading = false;
                this.error = this.detail(e) || this.translate.instant('tenants.catalog_load_failed');
                this.cdr.detectChanges();
            },
        });
    }

    ngOnDestroy(): void {
        this.destroy$.next();
        this.destroy$.complete();
    }

    trackByKey(_i: number, row: { key: string }): string { return row.key; }

    savePlan(row: PlanRow): void {
        const body: PlanUpdate = {
            name: row.name.trim() || row.key,
            price_usd_cents: cents(row.price_usd),
            price_cop_cents: cents(row.price_cop),
            is_public: row.is_public,
            is_active: row.is_active,
        };
        // `null` = ilimitado; el backend ignora los campos ausentes, así que
        // solo mandamos los límites cuando tienen número.
        const meetings = intOrNull(row.meetings_per_month);
        const users = intOrNull(row.users_included);
        if (meetings !== null) { body.meetings_per_month = meetings; }
        if (users !== null) { body.users_included = users; }
        this.savingKey = row.key;
        this.billing.updatePlan(row.key, body).pipe(takeUntil(this.destroy$)).subscribe({
            next: (p) => {
                this.plans = this.plans.map((r) => r.key === p.key ? toPlanRow(p) : r);
                this.done(true);
            },
            error: (e: HttpErrorResponse) => this.done(false, e),
        });
    }

    saveAddOn(row: AddOnRow): void {
        const body: AddOnUpdate = {
            name: row.name.trim() || row.key,
            price_usd_cents: cents(row.price_usd),
            price_cop_cents: cents(row.price_cop),
            is_active: row.is_active,
        };
        this.savingKey = row.key;
        this.billing.updateAddOn(row.key, body).pipe(takeUntil(this.destroy$)).subscribe({
            next: (a) => {
                this.addons = this.addons.map((r) => r.key === a.key ? toAddOnRow(a) : r);
                this.done(true);
            },
            error: (e: HttpErrorResponse) => this.done(false, e),
        });
    }

    private done(ok: boolean, e?: HttpErrorResponse): void {
        this.savingKey = null;
        if (ok) {
            this.toast.success(this.translate.instant('tenants.catalog_saved'));
        } else {
            this.toast.error((e && this.detail(e)) || this.translate.instant('tenants.catalog_save_failed'));
        }
        this.cdr.detectChanges();
    }

    private detail(e: HttpErrorResponse): string {
        const d: unknown = e?.error?.detail;
        return typeof d === 'string' ? d : '';
    }
}
