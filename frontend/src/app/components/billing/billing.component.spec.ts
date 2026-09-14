import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting, HttpTestingController } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { provideTranslateService } from '@ngx-translate/core';
import { environment } from '../../../environments/environment';
import { AuthService } from '../../services/auth.service';
import { BillingMe, Catalog } from '../../services/billing.service';
import { BillingComponent } from './billing.component';

const BASE = `${environment.apiUrl}/api/billing`;

const catalog: Catalog = {
  currency: 'COP',
  features: { 'meetings.owned_bot': 'Bot propio', 'meetings.video': 'Vídeo' },
  plans: [
    { key: 'starter', name: 'Starter', price_usd_cents: 4900, price_cop_cents: 19_600_000, interval: 'month',
      features: [], meetings_per_month: 20, users_included: 5, is_public: true, is_active: true, sort_order: 1 },
    { key: 'business', name: 'Business', price_usd_cents: 14900, price_cop_cents: 59_600_000, interval: 'month',
      features: ['meetings.owned_bot'], meetings_per_month: 100, users_included: null, is_public: true, is_active: true, sort_order: 2 },
  ],
  addons: [
    { key: 'owned_bot', name: 'Bot propio', price_usd_cents: 2900, price_cop_cents: 11_600_000, features: ['meetings.owned_bot'], requires: [], is_active: true, sort_order: 1 },
    { key: 'video_recording', name: 'Vídeo', price_usd_cents: 1900, price_cop_cents: 7_600_000, features: ['meetings.video'], requires: ['owned_bot'], is_active: true, sort_order: 2 },
  ],
};

const me: BillingMe = {
  entitlements: { plan: 'business', status: 'active', features: [], addons: ['owned_bot'], period_end: '2030-01-01T00:00:00',
    trial_ends: null, meetings_per_month: 100, users_included: null, billing_mode: 'manual' },
  plan: catalog.plans[1],
  subscription: { status: 'active', billing_mode: 'manual', current_period_start: null, current_period_end: '2030-01-01T00:00:00',
    cancel_at_period_end: false, addons: ['owned_bot'], customer_email: null },
  usage: { meetings_this_period: 12, active_users: 4, period_start: '2026-09-01T00:00:00' },
  grace_days: 7, trial_days: 14, payments_enabled: false,
};

describe('BillingComponent', () => {
  let http: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [BillingComponent],
      providers: [
        provideHttpClient(), provideHttpClientTesting(), provideRouter([]), provideTranslateService(),
        { provide: AuthService, useValue: { getAuthHeaders: () => ({}), refreshCurrentUser: () => undefined } },
      ],
    }).compileComponents();
    http = TestBed.inject(HttpTestingController);
  });

  function render() {
    const fixture = TestBed.createComponent(BillingComponent);
    fixture.detectChanges();
    http.expectOne(`${BASE}/me`).flush(me);
    http.expectOne(`${BASE}/catalog`).flush(catalog);
    http.expectOne(`${BASE}/payments`).flush({ items: [] });
    fixture.detectChanges();
    return fixture;
  }

  it('preselects the current plan and add-ons, and computes the total for display', () => {
    const page = render().componentInstance;
    expect(page.selectedPlanKey).toBe('business');
    expect(page.isAddonSelected('owned_bot')).toBe(true);
    page.onMonthsChange(3);
    expect(page.totalCop).toBe((59_600_000 + 11_600_000) * 3);
  });

  it('marks the required add-on automatically and hides the pay button when payments are off', () => {
    const fixture = render();
    const page = fixture.componentInstance;
    page.onToggleAddon('owned_bot');
    expect(page.isAddonSelected('owned_bot')).toBe(false);
    page.onToggleAddon('video_recording');
    expect(page.isAddonSelected('owned_bot')).toBe(true);
    expect(page.canPay).toBe(false);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.bl-btn-primary')).toBeNull();
    expect(fixture.nativeElement.querySelector('.bl-alert-warn')).not.toBeNull();
  });

  it('asks for confirmation before cancelling at the end of the period', () => {
    const page = render().componentInstance;
    page.requestCancelToggle();
    expect(page.cancelPrompt).toBe(true);
    http.expectNone(`${BASE}/cancel`);
    page.confirmCancel();
    const req = http.expectOne(`${BASE}/cancel`);
    expect(req.request.body).toEqual({ cancel_at_period_end: true });
    req.flush({ ...me, subscription: { ...me.subscription!, cancel_at_period_end: true } });
    expect(page.cancelAtPeriodEnd).toBe(true);
    expect(page.cancelPrompt).toBe(false);
  });
});
