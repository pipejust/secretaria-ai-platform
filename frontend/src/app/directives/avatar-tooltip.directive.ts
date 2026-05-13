import {
    AfterViewInit,
    Directive,
    ElementRef,
    HostListener,
    OnDestroy,
    Renderer2,
} from '@angular/core';

/**
 * Avatar tooltip directive
 * ------------------------
 * Aplicada al host con clase `.avatar-tip-host`. Busca el `.avatar-tip`
 * adentro y, en hover/focus, lo posiciona usando coordenadas del viewport
 * (`position: fixed`) — así nunca lo recortan contenedores con `overflow:
 * hidden` o `overflow-y: auto` (panel de detalle, filas de tabla, etc).
 *
 * Diseño:
 * - Cero JS extra mientras el usuario no hace hover (las reglas CSS
 *   globales siguen siendo el fallback para casos sin overflow).
 * - Al hacer hover/focus, re-toma `getBoundingClientRect()` del avatar y
 *   del tooltip y lo pinta encima (o debajo si no cabe arriba) con
 *   `position: fixed`.
 * - Al salir, devuelve los inline styles a su estado original para que el
 *   tooltip "cierre" sin parpadeo y no acumule estilos sucios.
 */
@Directive({
    selector: '.avatar-tip-host',
    standalone: true,
})
export class AvatarTooltipDirective implements AfterViewInit, OnDestroy {
    private tipEl: HTMLElement | null = null;
    /** Backup de los inline styles originales del tooltip — los restauramos
     *  cuando el usuario sale del hover, así el CSS-only fallback sigue
     *  funcionando si la directiva no estuviera. */
    private styleSnapshot: Record<string, string> = {};

    constructor(
        private host: ElementRef<HTMLElement>,
        private renderer: Renderer2,
    ) {}

    ngAfterViewInit(): void {
        this.tipEl = this.host.nativeElement.querySelector('.avatar-tip') as HTMLElement | null;
        if (this.tipEl) {
            // Aria + relación visual: cuando el avatar tiene `tabindex` la
            // directiva ya recibe focus. No tocamos más nada.
            this.tipEl.setAttribute('aria-hidden', 'true');
        }
    }

    ngOnDestroy(): void {
        this.restoreSnapshot();
        this.tipEl = null;
    }

    @HostListener('mouseenter') onMouseEnter(): void { this.position(); }
    @HostListener('focusin')   onFocusIn(): void   { this.position(); }
    @HostListener('mouseleave') onMouseLeave(): void { this.restoreSnapshot(); }
    @HostListener('focusout')  onFocusOut(): void  { this.restoreSnapshot(); }

    private position(): void {
        if (!this.tipEl) return;
        const tip = this.tipEl;

        // Snapshot inicial (una sola vez por hover) para poder revertir.
        this.styleSnapshot = {
            position: tip.style.position,
            top: tip.style.top,
            left: tip.style.left,
            right: tip.style.right,
            bottom: tip.style.bottom,
            transform: tip.style.transform,
            zIndex: tip.style.zIndex,
        };

        // Hacemos visible en posición temporal para medir tamaño real.
        this.renderer.setStyle(tip, 'position', 'fixed');
        this.renderer.setStyle(tip, 'top', '-9999px');
        this.renderer.setStyle(tip, 'left', '-9999px');
        this.renderer.setStyle(tip, 'right', 'auto');
        this.renderer.setStyle(tip, 'bottom', 'auto');
        this.renderer.setStyle(tip, 'transform', 'none');
        this.renderer.setStyle(tip, 'zIndex', '9999');

        // Forzamos reflow leyendo width/height.
        const tipRect = tip.getBoundingClientRect();
        const hostRect = this.host.nativeElement.getBoundingClientRect();
        const vw = window.innerWidth;
        const vh = window.innerHeight;

        // Default: arriba del avatar, centrado.
        let top = hostRect.top - tipRect.height - 8;
        let left = hostRect.left + (hostRect.width / 2) - (tipRect.width / 2);

        // Si no cabe arriba → debajo.
        if (top < 8) {
            top = hostRect.bottom + 8;
            this.renderer.addClass(tip, 'tip-below');
        } else {
            this.renderer.removeClass(tip, 'tip-below');
        }

        // Clamp horizontal a la viewport con margen 8px.
        if (left < 8) left = 8;
        if (left + tipRect.width > vw - 8) left = vw - 8 - tipRect.width;

        // Clamp vertical (por si tampoco cabe debajo).
        if (top + tipRect.height > vh - 8) top = vh - 8 - tipRect.height;

        this.renderer.setStyle(tip, 'top', `${Math.round(top)}px`);
        this.renderer.setStyle(tip, 'left', `${Math.round(left)}px`);
    }

    private restoreSnapshot(): void {
        if (!this.tipEl) return;
        const tip = this.tipEl;
        for (const [key, value] of Object.entries(this.styleSnapshot)) {
            // Si el style original estaba vacío, lo removemos para volver al
            // estado del CSS global; si tenía valor, lo re-aplicamos.
            if (value) {
                this.renderer.setStyle(tip, key, value);
            } else {
                tip.style.removeProperty(key);
            }
        }
        // Limpia la flag tip-below para el próximo hover.
        this.renderer.removeClass(tip, 'tip-below');
        this.styleSnapshot = {};
    }
}
