import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting, HttpTestingController } from '@angular/common/http/testing';
import { HttpHeaders } from '@angular/common/http';
import { environment } from '../../environments/environment';
import { AuthService } from './auth.service';
import { BillingService } from './billing.service';

const BASE = `${environment.apiUrl}/api/billing`;

describe('BillingService', () => {
  let service: BillingService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(), provideHttpClientTesting(),
        { provide: AuthService, useValue: { getAuthHeaders: () => new HttpHeaders({ Authorization: 'Bearer t' }) } },
      ],
    });
    service = TestBed.inject(BillingService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('loads the subscription state with the auth header', () => {
    let received: unknown;
    service.getMe().subscribe((r) => (received = r));
    const req = http.expectOne(`${BASE}/me`);
    expect(req.request.method).toBe('GET');
    expect(req.request.headers.get('Authorization')).toBe('Bearer t');
    req.flush({ payments_enabled: false });
    expect(received).toEqual({ payments_enabled: false });
  });

  it('posts the checkout selection as the backend expects it', () => {
    service.checkout({ plan_key: 'business', addons: ['owned_bot'], months: 3 }).subscribe();
    const req = http.expectOne(`${BASE}/checkout`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ plan_key: 'business', addons: ['owned_bot'], months: 3 });
    req.flush({ reference: 'acten-1-abc' }, { status: 201, statusText: 'Created' });
  });

  it('syncs a payment by reference with the Wompi transaction id', () => {
    service.syncPayment('acten-1-abc', '1234-5678').subscribe();
    const req = http.expectOne(`${BASE}/payments/acten-1-abc/sync`);
    expect(req.request.body).toEqual({ transaction_id: '1234-5678' });
    req.flush({ status: 'approved' });
  });

  it('sends the cancel-at-period-end flag', () => {
    service.cancel(true).subscribe();
    const req = http.expectOne(`${BASE}/cancel`);
    expect(req.request.body).toEqual({ cancel_at_period_end: true });
    req.flush({});
  });

  it('updates a tenant subscription and Wompi config on the superadmin endpoints', () => {
    service.updateTenantSubscription(7, {
      plan_key: 'enterprise', addons: [], status: 'active', current_period_end: null, notes: 'contrato',
    }).subscribe();
    const sub = http.expectOne(`${BASE}/tenants/7`);
    expect(sub.request.method).toBe('PUT');
    expect(sub.request.body.current_period_end).toBeNull();
    sub.flush({});

    service.updateWompiConfig({ environment: 'sandbox', public_key: 'pub_test_x' }).subscribe();
    const cfg = http.expectOne(`${BASE}/wompi-config`);
    expect(cfg.request.method).toBe('PUT');
    expect(cfg.request.body).toEqual({ environment: 'sandbox', public_key: 'pub_test_x' });
    cfg.flush({ configured: false });
  });
});
