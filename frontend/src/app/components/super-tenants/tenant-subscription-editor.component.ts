import { ChangeDetectorRef, Component, Input, OnChanges, OnDestroy, OnInit, SimpleChanges, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { Subject, forkJoin } from 'rxjs';
import { takeUntil } from 'rxjs/operators';
import {
    AddOn, BillingService, Catalog, Plan, TenantSubscriptionInput, TenantSubscriptionRow, VocemInput } from '../../services/billing.service';
import { ToastService } from '../../services/toast.service';

interface EditorForm {
    plan_key: string;
    addons: Set<string>;
    status: 'active' | 'cancelled';
    /** yyyy-mm-dd para el <input type="date">; vacío = sin vencimiento. */
    period_end: string;
    notes: string;
    meeting_source: 'fireflies' | 'owned_bot' | 'both';
    /** Vacío = los días del plan; 0 = sin límite. */
    video_days: number | null;
    /** Cuentas autenticadas de Skribby propias de la empresa; vacío = las de Acten. */
    auth_meet: string;
    auth_teams: string;
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
    savingVocem = false;
    vocem: { homeserver: string; user_id: string; call_base_url: string; chat_base_url: string; access_token: string } =
        { homeserver: '', user_id: '', call_base_url: '', chat_base_url: '', access_token: '' };
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
        return { plan_key: '', addons: new Set<string>(), status: 'active', period_end: '', notes: '',
                 meeting_source: 'fireflies', video_days: null, auth_meet: '', auth_teams: '' };
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
            meeting_source: (r?.meeting_source as EditorForm['meeting_source']) ?? 'fireflies',
            video_days: r?.video_retention_override ?? null,
            auth_meet: r?.auth_accounts?.['gmeet'] ?? '',
            auth_teams: r?.auth_accounts?.['teams'] ?? '',
        };
        this.vocem = {
            homeserver: r?.vocem?.homeserver ?? '', user_id: r?.vocem?.user_id ?? '',
            call_base_url: r?.vocem?.call_base_url ?? '', chat_base_url: r?.vocem?.chat_base_url ?? '', access_token: '',
        };
        this.cdr.detectChanges();
    }

    isAddonOn(key: string): boolean { return this.form.addons.has(key); }

    saveVocem(): void {
        if (this.savingVocem) { return; }
        const body: VocemInput = {
            homeserver: this.vocem.homeserver.trim(), user_id: this.vocem.user_id.trim(),
            call_base_url: this.vocem.call_base_url.trim(), chat_base_url: this.vocem.chat_base_url.trim(),
            ...(this.vocem.access_token.trim() ? { access_token: this.vocem.access_token.trim() } : {}),
        };
        this.savingVocem = true;
        this.billing.updateTenantVocem(this.tenantId, body).pipe(takeUntil(this.destroy$)).subscribe({
            next: (st) => {
                this.savingVocem = false;
                this.vocem.access_token = '';
                this.rows = this.rows.map((r) => r.tenant_id === this.tenantId ? { ...r, vocem: st } : r);
                this.row = this.rows.find((r) => r.tenant_id === this.tenantId) ?? null;
                this.toast.success(this.translate.instant('tenants.billing_vocem_saved'));
                this.cdr.detectChanges();
            },
            error: (e) => {
                this.savingVocem = false;
                this.toast.error(e?.error?.detail || this.translate.instant('common.error_generic'));
                this.cdr.detectChanges();
            },
        });
    }

    clearVocem(): void {
        this.savingVocem = true;
        this.billing.updateTenantVocem(this.tenantId, { clear: true }).pipe(takeUntil(this.destroy$)).subscribe({
            next: (st) => {
                this.savingVocem = false;
                this.rows = this.rows.map((r) => r.tenant_id === this.tenantId ? { ...r, vocem: st } : r);
                this.row = this.rows.find((r) => r.tenant_id === this.tenantId) ?? null;
                this.vocem = { homeserver: '', user_id: '', call_base_url: '', chat_base_url: '', access_token: '' };
                this.cdr.detectChanges();
            },
            error: () => { this.savingVocem = false; this.cdr.detectChanges(); },
        });
    }

    private authAccounts(): Record<string, string> {
        const out: Record<string, string> = {};
        if (this.form.auth_meet.trim()) { out['gmeet'] = this.form.auth_meet.trim(); }
        if (this.form.auth_teams.trim()) { out['teams'] = this.form.auth_teams.trim(); }
        return out;
    }

    /** El bot como origen exige su add-on (o un plan que ya lo traiga). */
    get botAvailable(): boolean {
        const plan = this.plans.find((p) => p.key === this.form.plan_key);
        return this.form.addons.has('owned_bot') || !!plan?.features.includes('meetings.owned_bot');
    }

    get planVideoDays(): number | null {
        return this.plans.find((p) => p.key === this.form.plan_key)?.video_retention_days ?? null;
    }

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
            meeting_source: this.botAvailable ? this.form.meeting_source : 'fireflies',
            video_retention_days: this.videoDaysValue(),
            ...(this.botAvailable ? { auth_accounts: this.authAccounts() } : {}),
        };
        this.saving = true;
        this.billing.updateTenantSubscription(this.tenantId, body).pipe(takeUntil(this.destroy$)).subscribe({
            next: (me) => {
                this.saving = false;
                this.rows = this.rows.map((r) => r.tenant_id === this.tenantId
                    ? { ...r, plan: me.entitlements.plan, status: me.entitlements.status, addons: me.entitlements.addons,
                        billing_mode: me.entitlements.billing_mode, period_end: me.entitlements.period_end, notes: body.notes,
                        meeting_source: body.meeting_source ?? r.meeting_source,
                        video_retention_override: body.video_retention_days ?? null,
                        auth_accounts: body.auth_accounts ?? r.auth_accounts,
                        video_retention_days: me.entitlements.video_retention_days }
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

    private videoDaysValue(): number | null {
        const v = this.form.video_days;
        if (v === null || v === undefined || String(v) === '' || Number.isNaN(Number(v))) { return null; }
        return Math.max(0, Math.trunc(Number(v)));
    }

    private detail(e: HttpErrorResponse): string {
        const d: unknown = e?.error?.detail;
        return typeof d === 'string' ? d : '';
    }
}
