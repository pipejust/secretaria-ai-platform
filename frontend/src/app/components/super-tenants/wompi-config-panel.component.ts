import { ChangeDetectorRef, Component, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpErrorResponse } from '@angular/common/http';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { Subject } from 'rxjs';
import { takeUntil } from 'rxjs/operators';
import { BillingService, WompiConfigInput, WompiConfigState, WompiEnvironment } from '../../services/billing.service';
import { ToastService } from '../../services/toast.service';

interface WompiForm {
    environment: WompiEnvironment;
    public_key: string;
    /** Vacío = conservar el secreto guardado. */
    private_key: string;
    events_secret: string;
    integrity_secret: string;
}

/** Llaves de Wompi de la plataforma. Los secretos nunca vuelven al navegador. */
@Component({
    selector: 'app-wompi-config-panel',
    standalone: true,
    imports: [CommonModule, FormsModule, TranslateModule],
    templateUrl: './wompi-config-panel.component.html',
    styleUrls: ['./billing-admin.css'],
})
export class WompiConfigPanelComponent implements OnInit, OnDestroy {
    private readonly billing = inject(BillingService);
    private readonly toast = inject(ToastService);
    private readonly translate = inject(TranslateService);
    private readonly cdr = inject(ChangeDetectorRef);
    private readonly destroy$ = new Subject<void>();

    state: WompiConfigState | null = null;
    form: WompiForm = { environment: 'sandbox', public_key: '', private_key: '', events_secret: '', integrity_secret: '' };
    loading = true;
    saving = false;
    error = '';

    ngOnInit(): void {
        this.billing.getWompiConfig().pipe(takeUntil(this.destroy$)).subscribe({
            next: (s) => { this.apply(s); this.loading = false; this.cdr.detectChanges(); },
            error: (e: HttpErrorResponse) => {
                this.loading = false;
                this.error = this.detail(e) || this.translate.instant('tenants.wompi_load_failed');
                this.cdr.detectChanges();
            },
        });
    }

    ngOnDestroy(): void {
        this.destroy$.next();
        this.destroy$.complete();
    }

    private apply(s: WompiConfigState): void {
        this.state = s;
        this.form = { ...this.form, environment: s.environment, public_key: s.public_key,
            private_key: '', events_secret: '', integrity_secret: '' };
    }

    get canSave(): boolean { return !this.saving && this.form.public_key.trim().length >= 8; }

    save(): void {
        if (!this.canSave) { return; }
        const body: WompiConfigInput = {
            environment: this.form.environment,
            public_key: this.form.public_key.trim(),
            ...(this.form.private_key.trim() ? { private_key: this.form.private_key.trim() } : {}),
            ...(this.form.events_secret.trim() ? { events_secret: this.form.events_secret.trim() } : {}),
            ...(this.form.integrity_secret.trim() ? { integrity_secret: this.form.integrity_secret.trim() } : {}),
        };
        this.saving = true;
        this.billing.updateWompiConfig(body).pipe(takeUntil(this.destroy$)).subscribe({
            next: (s) => {
                this.saving = false;
                this.apply(s);
                this.toast.success(this.translate.instant('tenants.wompi_saved'));
                this.cdr.detectChanges();
            },
            error: (e: HttpErrorResponse) => {
                this.saving = false;
                this.toast.error(this.detail(e) || this.translate.instant('tenants.wompi_save_failed'));
                this.cdr.detectChanges();
            },
        });
    }

    private detail(e: HttpErrorResponse): string {
        const d: unknown = e?.error?.detail;
        return typeof d === 'string' ? d : '';
    }
}
