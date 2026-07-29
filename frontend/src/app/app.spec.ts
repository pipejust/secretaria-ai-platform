import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { provideTranslateService } from '@ngx-translate/core';
import { App } from './app';

describe('App', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [
        // `App` inyecta Router/ActivatedRoute para el SEO reactivo por ruta.
        provideRouter([]),
        // `ToastComponent` (renderizado por la plantilla de `App`) usa
        // TranslateModule; sin loader explícito ngx-translate registra el
        // TranslateNoOpLoader, suficiente para el test.
        provideTranslateService(),
      ],
    }).compileComponents();
  });

  it('should create the app', () => {
    const fixture = TestBed.createComponent(App);
    const app = fixture.componentInstance;
    expect(app).toBeTruthy();
  });

  it('should render the router outlet and the toast host', async () => {
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('main.main-content')).toBeTruthy();
    expect(compiled.querySelector('router-outlet')).toBeTruthy();
    expect(compiled.querySelector('app-toast')).toBeTruthy();
  });
});
