import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';
import { AuthService } from './auth.service';

/** Espejo de backend/routers/billing.py y services/entitlements.py. */

export type SubscriptionStatus = 'trialing' | 'active' | 'past_due' | 'expired' | 'none';
export type BillingMode = 'manual' | 'wompi';
export type PaymentStatus = 'pending' | 'approved' | 'declined' | 'voided' | 'error';
export type WompiEnvironment = 'sandbox' | 'production';

export interface Plan {
    key: string;
    name: string;
    price_usd_cents: number;
    price_cop_cents: number;
    interval: string;
    features: string[];
    meetings_per_month: number | null;
    users_included: number | null;
    is_public: boolean;
    is_active: boolean;
    sort_order: number;
}

export interface AddOn {
    key: string;
    name: string;
    price_usd_cents: number;
    price_cop_cents: number;
    features: string[];
    /** Claves de otros add-ons que deben ir en la misma selección. */
    requires: string[];
    is_active: boolean;
    sort_order: number;
}

export interface Catalog {
    plans: Plan[];
    addons: AddOn[];
    /** clave de función → etiqueta legible (backend FEATURES). */
    features: Record<string, string>;
    currency: 'COP';
}

export interface Entitlements {
    plan: string | null;
    status: SubscriptionStatus;
    features: string[];
    addons: string[];
    period_end: string | null;
    trial_ends: string | null;
    meetings_per_month: number | null;
    users_included: number | null;
    billing_mode: BillingMode;
}

export interface SubscriptionInfo {
    status: string;
    billing_mode: BillingMode;
    current_period_start: string | null;
    current_period_end: string | null;
    cancel_at_period_end: boolean;
    addons: string[];
    customer_email: string | null;
}

export interface BillingUsage {
    meetings_this_period: number;
    active_users: number;
    period_start: string;
}

export interface BillingMe {
    entitlements: Entitlements;
    plan: Plan | null;
    subscription: SubscriptionInfo | null;
    usage: BillingUsage;
    grace_days: number;
    trial_days: number;
    payments_enabled: boolean;
}

export interface CheckoutInput {
    plan_key: string;
    addons: string[];
    months: number;
    customer_email?: string;
}

export interface CheckoutResponse {
    reference: string;
    amount_in_cents: number;
    currency: 'COP';
    public_key: string;
    signature_integrity: string;
    redirect_url: string;
    customer_email: string;
    widget_script: string;
    checkout_url: string;
    environment: WompiEnvironment;
    summary: { plan: Plan; addons: AddOn[]; months: number };
}

export interface PaymentInfo {
    reference: string;
    plan_key: string;
    addons: string[];
    months: number;
    amount_in_cents: number;
    currency: string;
    status: PaymentStatus;
    wompi_transaction_id: string | null;
    payment_method: string | null;
    created_at: string;
    approved_at: string | null;
}

export interface TenantSubscriptionRow {
    tenant_id: number;
    slug: string;
    name: string;
    plan: string | null;
    status: SubscriptionStatus;
    addons: string[];
    billing_mode: BillingMode;
    period_end: string | null;
    trial_ends: string | null;
    meeting_source: string;
    notes: string;
}

export interface TenantSubscriptionInput {
    plan_key: string;
    addons: string[];
    status: 'active' | 'cancelled';
    current_period_end: string | null;
    notes: string;
}

export interface WompiConfigState {
    environment: WompiEnvironment;
    public_key: string;
    has_private_key: boolean;
    has_events_secret: boolean;
    has_integrity_secret: boolean;
    configured: boolean;
}

/** Los secretos que no se envían se conservan en el backend. */
export interface WompiConfigInput {
    environment: WompiEnvironment;
    public_key: string;
    private_key?: string;
    events_secret?: string;
    integrity_secret?: string;
}

export type PlanUpdate = Partial<Pick<Plan,
    'name' | 'price_usd_cents' | 'price_cop_cents' | 'features' | 'meetings_per_month' | 'users_included' | 'is_public' | 'is_active'>>;
export type AddOnUpdate = Partial<Pick<AddOn, 'name' | 'price_usd_cents' | 'price_cop_cents' | 'is_active'>>;

/** Cuerpo del 402 que devuelve cualquier endpoint protegido por plan. */
export interface PlanGateDetail {
    message: string;
    feature: string;
    label: string;
    plan: string | null;
    status: SubscriptionStatus;
}

@Injectable({ providedIn: 'root' })
export class BillingService {
    private readonly http = inject(HttpClient);
    private readonly authService = inject(AuthService);
    private readonly baseUrl = `${environment.apiUrl}/api/billing`;

    private opts() {
        return { headers: this.authService.getAuthHeaders() };
    }

    // ── Administrador de empresa ────────────────────────────────────────

    getCatalog(): Observable<Catalog> {
        return this.http.get<Catalog>(`${this.baseUrl}/catalog`, this.opts());
    }

    getMe(): Observable<BillingMe> {
        return this.http.get<BillingMe>(`${this.baseUrl}/me`, this.opts());
    }

    checkout(body: CheckoutInput): Observable<CheckoutResponse> {
        return this.http.post<CheckoutResponse>(`${this.baseUrl}/checkout`, body, this.opts());
    }

    getPayments(): Observable<{ items: PaymentInfo[] }> {
        return this.http.get<{ items: PaymentInfo[] }>(`${this.baseUrl}/payments`, this.opts());
    }

    getPayment(reference: string): Observable<PaymentInfo> {
        return this.http.get<PaymentInfo>(`${this.baseUrl}/payments/${encodeURIComponent(reference)}`, this.opts());
    }

    syncPayment(reference: string, transactionId: string): Observable<PaymentInfo> {
        return this.http.post<PaymentInfo>(
            `${this.baseUrl}/payments/${encodeURIComponent(reference)}/sync`,
            { transaction_id: transactionId },
            this.opts(),
        );
    }

    cancel(cancelAtPeriodEnd: boolean): Observable<BillingMe> {
        return this.http.post<BillingMe>(`${this.baseUrl}/cancel`, { cancel_at_period_end: cancelAtPeriodEnd }, this.opts());
    }

    // ── Superadministrador ──────────────────────────────────────────────

    getWompiConfig(): Observable<WompiConfigState> {
        return this.http.get<WompiConfigState>(`${this.baseUrl}/wompi-config`, this.opts());
    }

    updateWompiConfig(body: WompiConfigInput): Observable<WompiConfigState> {
        return this.http.put<WompiConfigState>(`${this.baseUrl}/wompi-config`, body, this.opts());
    }

    getCatalogAll(): Observable<Catalog> {
        return this.http.get<Catalog>(`${this.baseUrl}/catalog/all`, this.opts());
    }

    updatePlan(key: string, body: PlanUpdate): Observable<Plan> {
        return this.http.put<Plan>(`${this.baseUrl}/catalog/plans/${encodeURIComponent(key)}`, body, this.opts());
    }

    updateAddOn(key: string, body: AddOnUpdate): Observable<AddOn> {
        return this.http.put<AddOn>(`${this.baseUrl}/catalog/addons/${encodeURIComponent(key)}`, body, this.opts());
    }

    getTenantSubscriptions(): Observable<{ items: TenantSubscriptionRow[] }> {
        return this.http.get<{ items: TenantSubscriptionRow[] }>(`${this.baseUrl}/tenants`, this.opts());
    }

    updateTenantSubscription(tenantId: number, body: TenantSubscriptionInput): Observable<BillingMe> {
        return this.http.put<BillingMe>(`${this.baseUrl}/tenants/${tenantId}`, body, this.opts());
    }
}
