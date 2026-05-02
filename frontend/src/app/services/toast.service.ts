import { Injectable } from '@angular/core';
import { Observable, Subject } from 'rxjs';

export type ToastKind = 'success' | 'error' | 'info' | 'warning';

export interface Toast {
    id: number;
    kind: ToastKind;
    message: string;
    durationMs: number;
}

@Injectable({ providedIn: 'root' })
export class ToastService {
    private readonly subject = new Subject<Toast>();
    private nextId = 0;

    readonly toasts$: Observable<Toast> = this.subject.asObservable();

    show(message: string, kind: ToastKind = 'info', durationMs = 4000): void {
        this.subject.next({
            id: ++this.nextId,
            kind,
            message,
            durationMs,
        });
    }

    success(message: string, durationMs = 3500): void {
        this.show(message, 'success', durationMs);
    }

    error(message: string, durationMs = 6000): void {
        this.show(message, 'error', durationMs);
    }

    warning(message: string, durationMs = 4500): void {
        this.show(message, 'warning', durationMs);
    }

    info(message: string, durationMs = 4000): void {
        this.show(message, 'info', durationMs);
    }
}
