import { ChangeDetectionStrategy, ChangeDetectorRef, Component, Input, OnChanges, OnDestroy, OnInit, SimpleChanges, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Subject } from 'rxjs';
import { takeUntil } from 'rxjs/operators';
import { UserDirectoryService, UserSummary } from '../../../services/user-directory.service';
import { environment } from '../../../../environments/environment';

/**
 * Chip / avatar unificado para mostrar un usuario por correo.
 *
 * Comportamiento:
 * - Si el `email` matchea un User del tenant → muestra `full_name`
 *   (nombre editado en perfil) y `avatar_url` (foto subida por el user).
 * - Si NO matchea → muestra `fallbackName` o el local-part del email.
 *
 * Recibe siempre el email como ancla; el resolver hace el lookup.
 * Si el caller ya tiene el User resuelto (porque el endpoint enriqueció
 * la fila), puede pasarlo como `[primed]` para evitar el round-trip.
 */
@Component({
    selector: 'app-user-chip',
    standalone: true,
    imports: [CommonModule],
    changeDetection: ChangeDetectionStrategy.OnPush,
    template: `
      <span class="uchip" [class.uchip--sm]="size === 'sm'" [class.uchip--xs]="size === 'xs'"
            [attr.title]="tooltip">
        <span class="uchip-avatar" [style.background]="avatarBg">
          <img *ngIf="avatarSrc" [src]="avatarSrc" [alt]="displayName">
          <span *ngIf="!avatarSrc" class="uchip-initials">{{ initials }}</span>
        </span>
        <span class="uchip-text" *ngIf="!hideName">
          <span class="uchip-name">{{ displayName }}</span>
          <span class="uchip-email" *ngIf="showEmail && (email || '')">{{ email }}</span>
        </span>
      </span>
    `,
    styles: [`
      .uchip {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        min-width: 0;
        max-width: 100%;
      }
      .uchip-avatar {
        width: 28px;
        height: 28px;
        border-radius: 50%;
        background: linear-gradient(135deg, var(--brand-primary, #223148), var(--brand-secondary, #1B7F67));
        color: #FFFFFF;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.02em;
        flex-shrink: 0;
        overflow: hidden;
        position: relative;
      }
      .uchip-avatar img {
        width: 100%;
        height: 100%;
        object-fit: cover;
      }
      .uchip-initials { line-height: 1; user-select: none; }
      .uchip-text {
        display: inline-flex;
        flex-direction: column;
        min-width: 0;
        line-height: 1.2;
      }
      .uchip-name {
        font-size: 13px;
        font-weight: 600;
        color: var(--color-fg-default, #0F172A);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .uchip-email {
        font-size: 11px;
        color: var(--color-fg-muted, #64748B);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .uchip--sm .uchip-avatar { width: 24px; height: 24px; font-size: 10px; }
      .uchip--sm .uchip-name { font-size: 12px; }
      .uchip--xs .uchip-avatar { width: 20px; height: 20px; font-size: 9px; }
      .uchip--xs .uchip-name { font-size: 11px; }
    `],
})
export class UserChipComponent implements OnInit, OnChanges, OnDestroy {
    private readonly directory = inject(UserDirectoryService);
    private readonly cdr = inject(ChangeDetectorRef);

    @Input() email: string | null | undefined;
    /** Nombre a mostrar si el email no matchea un user del tenant. */
    @Input() fallbackName: string | null | undefined;
    /** Si pasas el user resuelto inline (endpoint enriquecido), evita el lookup. */
    @Input() primed: UserSummary | null | undefined;
    @Input() size: 'xs' | 'sm' | 'md' = 'md';
    @Input() hideName = false;
    @Input() showEmail = false;

    user: UserSummary | null = null;

    private readonly destroy$ = new Subject<void>();

    ngOnInit(): void {
        this.directory.directory$
            .pipe(takeUntil(this.destroy$))
            .subscribe(() => {
                // Cuando el directorio resuelve nuestro email, re-render.
                const u = this.directory.peek(this.email);
                if (u !== undefined && u !== this.user) {
                    this.user = u;
                    this.cdr.markForCheck();
                }
            });
        this._refresh();
    }

    ngOnChanges(changes: SimpleChanges): void {
        if (changes['primed'] && this.primed !== undefined) {
            this.directory.primeCache(this.email, this.primed);
        }
        if (changes['email'] || changes['primed']) this._refresh();
    }

    ngOnDestroy(): void {
        this.destroy$.next();
        this.destroy$.complete();
    }

    private _refresh(): void {
        if (this.primed !== undefined) {
            this.user = this.primed;
            this.cdr.markForCheck();
            return;
        }
        // Matching ESTRICTO por email. No usamos nombre como fallback porque
        // dos personas distintas pueden tener el mismo nombre con correos
        // diferentes — mostrar la foto equivocada es un error grave.
        const cached = this.directory.peek(this.email);
        if (cached !== undefined) {
            this.user = cached;
            this.cdr.markForCheck();
            return;
        }
        this.user = null;
        this.directory.lookup(this.email).pipe(takeUntil(this.destroy$)).subscribe();
    }

    get displayName(): string {
        if (this.user?.full_name) return this.user.full_name;
        if (this.fallbackName) return this.fallbackName;
        const e = (this.email || '').trim();
        return e ? e.split('@')[0] : 'Sin asignar';
    }

    get tooltip(): string {
        const parts: string[] = [this.displayName];
        if (this.email) parts.push(this.email);
        if (this.user?.role) parts.push(`Rol: ${this.user.role}`);
        if (this.user?.department) parts.push(this.user.department);
        return parts.join(' · ');
    }

    get avatarSrc(): string | null {
        const raw = this.user?.avatar_url;
        if (!raw) return null;
        if (raw.startsWith('http')) return raw;
        return `${environment.apiUrl}${raw}`;
    }

    get initials(): string {
        const name = this.displayName.trim();
        if (!name) return '?';
        const words = name.split(/\s+/);
        if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
        return name.substring(0, 2).toUpperCase();
    }

    /** Color de fondo derivado del email para que contactos externos no
     *  tengan todos el mismo gradient — pequeño hash → hue. */
    get avatarBg(): string | null {
        if (this.user?.avatar_url || this.user?.id) return null;
        const key = (this.email || this.displayName || '').toLowerCase();
        if (!key) return null;
        let h = 0;
        for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) % 360;
        // Saturación/luminosidad estables para contraste con texto blanco.
        return `linear-gradient(135deg, hsl(${h}, 55%, 38%), hsl(${(h + 30) % 360}, 60%, 45%))`;
    }
}
