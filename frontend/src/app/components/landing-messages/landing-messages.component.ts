import {
    ChangeDetectorRef,
    Component,
    OnInit,
    inject,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { TranslateModule } from '@ngx-translate/core';
import { FormsModule } from '@angular/forms';
import { HttpClientModule } from '@angular/common/http';
import { RouterModule } from '@angular/router';

import { AuthService } from '../../services/auth.service';
import { ToastService } from '../../services/toast.service';
import {
    ContactMessage,
    ContactMessageStatus,
    LandingMessagesService,
} from '../../services/landing-messages.service';

/**
 * Bandeja de mensajes del landing público.
 *
 * Layout estilo "inbox": panel izquierdo con la lista de mensajes, panel
 * derecho con el detalle. En mobile la lista cubre toda la pantalla y al
 * abrir un mensaje se desliza el detalle por encima.
 *
 * El filtro y el contador se calculan a partir del payload del backend.
 * Cada vez que se abre un mensaje "new" hacemos PATCH a status='read'
 * para que el contador de no-leídos baje en tiempo real.
 */

type FilterKey = '' | ContactMessageStatus;

interface FilterChip {
    key: FilterKey;
    label: string;
}

@Component({
    selector: 'app-landing-messages',
    standalone: true,
    imports: [CommonModule, FormsModule, HttpClientModule, RouterModule, TranslateModule],
    templateUrl: './landing-messages.component.html',
    styleUrls: ['./landing-messages.component.css'],
})
export class LandingMessagesComponent implements OnInit {
    private readonly api = inject(LandingMessagesService);
    private readonly auth = inject(AuthService);
    private readonly toast = inject(ToastService);
    private readonly cdr = inject(ChangeDetectorRef);

    messages: ContactMessage[] = [];
    selected: ContactMessage | null = null;
    unreadCount = 0;

    isLoading = true;
    isUpdating = false;
    loadError = '';

    activeFilter: FilterKey = '';

    readonly filters: FilterChip[] = [
        { key: '', label: 'Todos' },
        { key: 'new', label: 'Nuevos' },
        { key: 'read', label: 'Leídos' },
        { key: 'replied', label: 'Respondidos' },
        { key: 'archived', label: 'Archivados' },
    ];

    /** Mobile only: cuando se abre un mensaje en pantalla pequeña, la vista
     *  de detalle ocupa la pantalla completa. Este flag lo controla. */
    isDetailOpenMobile = false;

    // ────────────────────────────────────────────────────────────────────
    // Lifecycle
    // ────────────────────────────────────────────────────────────────────

    async ngOnInit(): Promise<void> {
        await this.reload();
    }

    // ────────────────────────────────────────────────────────────────────
    // Carga / filtros
    // ────────────────────────────────────────────────────────────────────

    async reload(): Promise<void> {
        const token = this.auth.token;
        if (!token) {
            this.loadError = 'Tu sesión expiró. Vuelve a iniciar sesión.';
            this.isLoading = false;
            return;
        }
        this.isLoading = true;
        this.loadError = '';
        try {
            const page = await this.api.loadMessages(token, this.activeFilter);
            this.messages = page.items ?? [];
            this.unreadCount = page.unread_count ?? 0;

            // Si el seleccionado todavía existe en la lista nueva, lo
            // refrescamos. Si no, lo limpiamos para evitar mostrar stale.
            if (this.selected) {
                const fresh = this.messages.find(m => m.id === this.selected!.id);
                this.selected = fresh ?? null;
            }
        } catch (err: any) {
            const detail = err?.error?.detail || err?.message || 'Error desconocido.';
            this.loadError = `No se pudieron cargar los mensajes: ${detail}`;
            this.toast.error(this.loadError);
        } finally {
            this.isLoading = false;
            this.cdr.detectChanges();
        }
    }

    async selectFilter(key: FilterKey): Promise<void> {
        if (this.activeFilter === key) return;
        this.activeFilter = key;
        await this.reload();
    }

    // ────────────────────────────────────────────────────────────────────
    // Selección de mensaje
    // ────────────────────────────────────────────────────────────────────

    async openMessage(msg: ContactMessage): Promise<void> {
        this.selected = msg;
        this.isDetailOpenMobile = true;

        // Si es nuevo, lo marcamos como leído automáticamente para que el
        // badge desaparezca y baje el contador. Si falla, no rompemos la UX.
        if (msg.status === 'new') {
            const token = this.auth.token;
            if (!token) return;
            try {
                await this.api.updateStatus(token, msg.id, 'read');
                msg.status = 'read';
                msg.read_at = new Date().toISOString();
                this.unreadCount = Math.max(0, this.unreadCount - 1);
                this.cdr.detectChanges();
            } catch {
                /* silencioso — el cambio de estado de "new"→"read" es
                 * cosmético; el usuario igual ve el contenido. */
            }
        }
    }

    closeMobileDetail(): void {
        this.isDetailOpenMobile = false;
    }

    // ────────────────────────────────────────────────────────────────────
    // Acciones sobre el mensaje seleccionado
    // ────────────────────────────────────────────────────────────────────

    async setStatus(status: ContactMessageStatus): Promise<void> {
        if (!this.selected || this.isUpdating) return;
        const token = this.auth.token;
        if (!token) {
            this.toast.error('Tu sesión expiró.');
            return;
        }
        this.isUpdating = true;
        const previous = this.selected.status;
        try {
            await this.api.updateStatus(token, this.selected.id, status);
            this.selected.status = status;
            if (status === 'replied' && !this.selected.replied_at) {
                this.selected.replied_at = new Date().toISOString();
            }
            if (status === 'read' && !this.selected.read_at) {
                this.selected.read_at = new Date().toISOString();
            }
            // Recalcular contador local si pasamos de/hacia "new".
            if (previous === 'new' && status !== 'new') {
                this.unreadCount = Math.max(0, this.unreadCount - 1);
            } else if (previous !== 'new' && status === 'new') {
                this.unreadCount += 1;
            }
            this.toast.success(`Mensaje marcado como ${this.statusLabel(status).toLowerCase()}.`);

            // Si el filtro activo ya no incluye este estado, recargamos
            // para que desaparezca del listado.
            if (this.activeFilter && this.activeFilter !== status) {
                await this.reload();
            }
        } catch (err: any) {
            const detail = err?.error?.detail || err?.message || 'Error desconocido.';
            this.toast.error(`No se pudo actualizar: ${detail}`);
        } finally {
            this.isUpdating = false;
            this.cdr.detectChanges();
        }
    }

    async deleteSelected(): Promise<void> {
        if (!this.selected || this.isUpdating) return;
        const target = this.selected;
        if (!confirm(`¿Eliminar el mensaje de ${target.name}? Esta acción no se puede deshacer.`)) {
            return;
        }
        const token = this.auth.token;
        if (!token) {
            this.toast.error('Tu sesión expiró.');
            return;
        }
        this.isUpdating = true;
        try {
            await this.api.delete(token, target.id);
            // Quitar de la lista y limpiar selección.
            this.messages = this.messages.filter(m => m.id !== target.id);
            if (target.status === 'new') {
                this.unreadCount = Math.max(0, this.unreadCount - 1);
            }
            this.selected = null;
            this.isDetailOpenMobile = false;
            this.toast.success('Mensaje eliminado.');
        } catch (err: any) {
            const detail = err?.error?.detail || err?.message || 'Error desconocido.';
            this.toast.error(`No se pudo eliminar: ${detail}`);
        } finally {
            this.isUpdating = false;
            this.cdr.detectChanges();
        }
    }

    // ────────────────────────────────────────────────────────────────────
    // Helpers de UI
    // ────────────────────────────────────────────────────────────────────

    mailtoLink(msg: ContactMessage): string {
        const subject = encodeURIComponent('Re: contacto desde acten.app');
        const body = encodeURIComponent(`Hola ${msg.name},\n\n`);
        return `mailto:${msg.email}?subject=${subject}&body=${body}`;
    }

    statusLabel(status: ContactMessageStatus): string {
        switch (status) {
            case 'new': return 'Nuevo';
            case 'read': return 'Leído';
            case 'replied': return 'Respondido';
            case 'archived': return 'Archivado';
        }
    }

    /** Conteo derivado para el header — local, no llama al backend. */
    get readCount(): number {
        return this.messages.filter(m => m.status === 'read').length;
    }
    get archivedCount(): number {
        return this.messages.filter(m => m.status === 'archived').length;
    }
    get repliedCount(): number {
        return this.messages.filter(m => m.status === 'replied').length;
    }

    /** Formato "hace X" simple, suficiente para una inbox liviana. */
    timeAgo(iso: string | null): string {
        if (!iso) return '';
        const then = new Date(iso).getTime();
        if (isNaN(then)) return '';
        const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
        if (seconds < 60) return 'ahora';
        const minutes = Math.floor(seconds / 60);
        if (minutes < 60) return `hace ${minutes} min`;
        const hours = Math.floor(minutes / 60);
        if (hours < 24) return `hace ${hours} h`;
        const days = Math.floor(hours / 24);
        if (days < 7) return `hace ${days} d`;
        const weeks = Math.floor(days / 7);
        if (weeks < 5) return `hace ${weeks} sem`;
        const months = Math.floor(days / 30);
        if (months < 12) return `hace ${months} mes${months === 1 ? '' : 'es'}`;
        const years = Math.floor(days / 365);
        return `hace ${years} año${years === 1 ? '' : 's'}`;
    }

    /** Fecha completa legible para el panel de detalle. */
    formatDateTime(iso: string | null): string {
        if (!iso) return '';
        const d = new Date(iso);
        if (isNaN(d.getTime())) return '';
        try {
            return d.toLocaleString('es-ES', {
                day: '2-digit', month: 'short', year: 'numeric',
                hour: '2-digit', minute: '2-digit',
            });
        } catch {
            return d.toISOString();
        }
    }

    /** Snippet de 2 líneas: colapsa saltos y trunca a ~140 caracteres. */
    snippet(text: string, max = 140): string {
        const collapsed = (text || '').replace(/\s+/g, ' ').trim();
        if (collapsed.length <= max) return collapsed;
        return collapsed.slice(0, max).trimEnd() + '…';
    }

    trackById(_index: number, item: ContactMessage): number {
        return item.id;
    }
}
