import { ChangeDetectionStrategy, ChangeDetectorRef, Component, OnDestroy, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Subject } from 'rxjs';
import { takeUntil } from 'rxjs/operators';
import { Toast, ToastService } from '../../services/toast.service';

@Component({
    selector: 'app-toast',
    standalone: true,
    imports: [CommonModule],
    changeDetection: ChangeDetectionStrategy.OnPush,
    templateUrl: './toast.component.html',
    styleUrls: ['./toast.component.css'],
})
export class ToastComponent implements OnInit, OnDestroy {
    toasts: Toast[] = [];

    private readonly destroy$ = new Subject<void>();

    constructor(private toastService: ToastService, private cdr: ChangeDetectorRef) {}

    ngOnInit(): void {
        this.toastService.toasts$
            .pipe(takeUntil(this.destroy$))
            .subscribe((toast) => {
                this.toasts = [...this.toasts, toast];
                this.cdr.markForCheck();
                window.setTimeout(() => this.dismiss(toast.id), toast.durationMs);
            });
    }

    ngOnDestroy(): void {
        this.destroy$.next();
        this.destroy$.complete();
    }

    dismiss(id: number): void {
        this.toasts = this.toasts.filter((t) => t.id !== id);
        this.cdr.markForCheck();
    }

    trackById(_index: number, toast: Toast): number {
        return toast.id;
    }
}
