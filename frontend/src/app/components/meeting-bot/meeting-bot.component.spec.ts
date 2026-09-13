import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting, HttpTestingController } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { vi } from 'vitest';
import { AuthService } from '../../services/auth.service';
import { MeetingBotComponent } from './meeting-bot.component';

describe('Meeting bot screen', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [MeetingBotComponent], providers: [
      provideHttpClient(), provideHttpClientTesting(), provideRouter([]),
      { provide: AuthService, useValue: { currentUserValue: { tenant_id: 1, id: 2, role: 'admin' } } },
    ] }).compileComponents();
  });
  it('renders the three capture paths and prevents leaving an active recording', async () => {
    const fixture = TestBed.createComponent(MeetingBotComponent);
    const page = fixture.componentInstance;
    vi.spyOn(page, 'ngOnInit').mockImplementation(async () => {});
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Invitar por correo');
    expect(fixture.nativeElement.textContent).toContain('Añadir en vivo');
    expect(fixture.nativeElement.textContent).toContain('Grabar desde la web');
    page.recorder.active = true;
    expect(page.canLeave()).toBe(false);
    page.recorder.active = false;
    expect(page.canLeave()).toBe(true);
  });
  it('does not request a bot without the recording authorization', async () => {
    const page = TestBed.createComponent(MeetingBotComponent).componentInstance;
    await page.join();
    expect(page.error).toContain('Confirma');
    TestBed.inject(HttpTestingController).expectNone(request => request.method === 'POST');
  });
  it('filters transcript by voice including zero without guessing names', () => {
    const page = TestBed.createComponent(MeetingBotComponent).componentInstance;
    page.result = { transcript: [{ text: 'Piloto aprobado', speaker_id: '0' }, { text: 'Gracias', speaker_id: '1' }] };
    page.speakerFilter = '0';
    expect(page.segments).toHaveLength(1);
    page.search = 'rechazado';
    expect(page.segments).toHaveLength(0);
    expect(page.time(14400)).toBe('4:00:00');
  });
  it('reuses the request identity after an uncertain response', async () => {
    const page = TestBed.createComponent(MeetingBotComponent).componentInstance;
    const http = TestBed.inject(HttpTestingController);
    sessionStorage.clear();
    const first = (page as any).startCapture('meeting', { external_id: 'first-id', title: 'Prueba' });
    const failed = expect(first).rejects.toBeTruthy();
    http.expectOne(request => request.method === 'POST').flush({}, { status: 502, statusText: 'Bad Gateway' });
    await failed;
    const second = (page as any).startCapture('meeting', { external_id: 'would-duplicate', title: 'Prueba' });
    const request = http.expectOne(request => request.method === 'POST');
    expect(request.request.body.external_id).toBe('first-id');
    request.flush({ id: 'remote-id' });
    await second;
    http.verify();
    sessionStorage.clear();
  });
});
