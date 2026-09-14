import { Injectable } from '@angular/core';
import { Observable, Subject } from 'rxjs';

export type ToastKind = 'success' | 'error' | 'info' | 'warning';

/** Enlace opcional al pie del toast (p. ej. «Ver suscripción»). */
export interface ToastAction {
    label: string;
    link: string;
}

export interface Toast {
    id: number;
    kind: ToastKind;
    message: string;
    durationMs: number;
    action?: ToastAction;
}

@Injectable({ providedIn: 'root' })
export class ToastService {
    private readonly subject = new Subject<Toast>();
    private nextId = 0;

    readonly toasts$: Observable<Toast> = this.subject.asObservable();

    show(message: string, kind: ToastKind = 'info', durationMs = 4000, action?: ToastAction): void {
        this.subject.next({
            id: ++this.nextId,
            kind,
            message,
            durationMs,
            ...(action ? { action } : {}),
        });
    }

    success(message: string, durationMs = 3500): void {
        this.show(message, 'success', durationMs);
    }

    error(message: string, durationMs = 6000): void {
        this.show(message, 'error', durationMs);
    }

    warning(message: string, durationMs = 4500, action?: ToastAction): void {
        this.show(message, 'warning', durationMs, action);
    }

    info(message: string, durationMs = 4000): void {
        this.show(message, 'info', durationMs);
    }
}
