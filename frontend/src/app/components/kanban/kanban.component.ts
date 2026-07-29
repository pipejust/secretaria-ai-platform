import { Component, OnInit, OnDestroy, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { Subject } from 'rxjs';
import { takeUntil } from 'rxjs/operators';
import { AuthService } from '../../services/auth.service';
import { ToastService } from '../../services/toast.service';
import { environment } from '../../../environments/environment';
import { parseLocalDate } from '../../shared/dates';

interface Persona {
    clave: string;
    nombre: string | null;
    correo: string | null;
    tarjetas?: number;
}

interface Tarjeta {
    id: number;
    titulo: string;
    descripcion: string;
    prioridad: 'alta' | 'media' | 'baja';
    vence: string | null;
    hora: string | null;
    session_id: number;
    proyecto: string;
    project_id: number | null;
    persona: Persona;
    sin_dueno: boolean;
    orden: number;
}

interface Columna {
    key: string;
    titulo: string;
    tarjetas: Tarjeta[];
    total: number;
}

interface Tablero {
    columnas: Columna[];
    personas: Persona[];
    total: number;
}

interface ProyectoLite { id: number; name: string; }

/** Una calle del tablero: la persona y sus tarjetas por columna. */
interface Calle {
    persona: Persona;
    porColumna: Record<string, Tarjeta[]>;
    total: number;
}

const SIN_DUENO = '__sin_dueno__';

@Component({
    selector: 'app-kanban',
    standalone: true,
    imports: [CommonModule, FormsModule, RouterModule, TranslateModule],
    templateUrl: './kanban.component.html',
    styleUrls: ['./kanban.component.css'],
})
export class KanbanComponent implements OnInit, OnDestroy {
    tablero: Tablero | null = null;
    proyectos: ProyectoLite[] = [];
    cargando = false;

    // Filtros
    proyectoId: number | null = null;
    soloMias = false;
    incluirCerradas = false;
    busqueda = '';

    /** Calles por persona. Es lo que se pidió: ver de un vistazo qué lleva
     *  cada uno. Apagado da un tablero clásico de cuatro columnas. */
    porPersona = true;
    private static readonly VISTA_KEY = 'acten.kanban.porPersona';

    arrastrando: Tarjeta | null = null;
    columnaSobre: string | null = null;

    private readonly destroy$ = new Subject<void>();

    constructor(
        private http: HttpClient,
        private auth: AuthService,
        private toast: ToastService,
        private cdr: ChangeDetectorRef,
        private translate: TranslateService,
    ) {}

    ngOnInit(): void {
        try {
            const v = localStorage.getItem(KanbanComponent.VISTA_KEY);
            if (v !== null) this.porPersona = v === '1';
        } catch { /* modo privado */ }
        this.cargar();
        this.cargarProyectos();
    }

    ngOnDestroy(): void {
        this.destroy$.next();
        this.destroy$.complete();
    }

    // ══════════════════════════════════════════════════════════════════
    // Carga
    // ══════════════════════════════════════════════════════════════════

    cargar(): void {
        this.cargando = true;
        const params = new URLSearchParams();
        if (this.proyectoId != null) params.set('project_id', String(this.proyectoId));
        if (this.soloMias) params.set('solo_mias', 'true');
        if (this.incluirCerradas) params.set('incluir_cerradas', 'true');

        this.http.get<Tablero>(
            `${environment.apiUrl}/api/kanban?${params.toString()}`,
            { headers: this.auth.getAuthHeaders() },
        ).pipe(takeUntil(this.destroy$)).subscribe({
            next: (t) => { this.tablero = t; this.cargando = false; this.cdr.detectChanges(); },
            error: () => {
                this.cargando = false;
                this.toast.error(this.translate.instant('kanban.load_error'));
            },
        });
    }

    cargarProyectos(): void {
        this.http.get<any[]>(`${environment.apiUrl}/api/projects/`,
            { headers: this.auth.getAuthHeaders() })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (d) => {
                    this.proyectos = (d || []).map(p => ({ id: p.id, name: p.name }));
                    this.cdr.detectChanges();
                },
                error: () => { /* el tablero funciona sin el selector */ },
            });
    }

    // ══════════════════════════════════════════════════════════════════
    // Vistas derivadas
    // ══════════════════════════════════════════════════════════════════

    private coincide(t: Tarjeta): boolean {
        const q = this.busqueda.trim().toLowerCase();
        if (!q) return true;
        return (`${t.titulo} ${t.descripcion} ${t.proyecto} ${t.persona.nombre || ''}`)
            .toLowerCase().includes(q);
    }

    tarjetasDe(col: Columna): Tarjeta[] {
        return (col.tarjetas || []).filter(t => this.coincide(t));
    }

    /** Una calle por persona, en el orden que manda el backend (sin dueño
     *  al final). Se omiten las calles que la búsqueda deja vacías. */
    get calles(): Calle[] {
        if (!this.tablero) return [];
        const cols = this.tablero.columnas.map(c => c.key);
        const mapa = new Map<string, Calle>();
        for (const p of this.tablero.personas) {
            const vacio: Record<string, Tarjeta[]> = {};
            for (const k of cols) vacio[k] = [];
            mapa.set(p.clave, { persona: p, porColumna: vacio, total: 0 });
        }
        for (const col of this.tablero.columnas) {
            for (const t of this.tarjetasDe(col)) {
                const calle = mapa.get(t.persona.clave);
                if (!calle) continue;
                calle.porColumna[col.key].push(t);
                calle.total++;
            }
        }
        return [...mapa.values()].filter(c => c.total > 0);
    }

    nombrePersona(p: Persona): string {
        if (p.clave === SIN_DUENO) return this.translate.instant('kanban.unassigned');
        return p.nombre || p.correo || '—';
    }

    iniciales(p: Persona): string {
        if (p.clave === SIN_DUENO) return '?';
        const base = (p.nombre || p.correo || '?').trim();
        const partes = base.split(/[\s@.]+/).filter(Boolean);
        return ((partes[0]?.[0] || '') + (partes[1]?.[0] || '')).toUpperCase() || '?';
    }

    esSinDueno(p: Persona): boolean { return p.clave === SIN_DUENO; }

    /** Color estable por persona: el mismo nombre siempre el mismo tono. */
    tono(p: Persona): string {
        if (p.clave === SIN_DUENO) return 'hsl(215 16% 55%)';
        let h = 0;
        for (const ch of p.clave) h = (h * 31 + ch.charCodeAt(0)) % 360;
        return `hsl(${h} 52% 45%)`;
    }

    venceTarde(t: Tarjeta): boolean {
        if (!t.vence) return false;
        const d = parseLocalDate(t.vence);
        if (!d) return false;
        const hoy = new Date();
        hoy.setHours(0, 0, 0, 0);
        return d.getTime() < hoy.getTime();
    }

    // ══════════════════════════════════════════════════════════════════
    // Arrastrar y soltar
    // ══════════════════════════════════════════════════════════════════

    empezarArrastre(t: Tarjeta, ev: DragEvent): void {
        this.arrastrando = t;
        ev.dataTransfer?.setData('text/plain', String(t.id));
        if (ev.dataTransfer) ev.dataTransfer.effectAllowed = 'move';
    }

    terminarArrastre(): void {
        this.arrastrando = null;
        this.columnaSobre = null;
        this.cdr.detectChanges();
    }

    sobreColumna(key: string, ev: DragEvent): void {
        ev.preventDefault();
        if (ev.dataTransfer) ev.dataTransfer.dropEffect = 'move';
        if (this.columnaSobre !== key) {
            this.columnaSobre = key;
            this.cdr.detectChanges();
        }
    }

    soltar(destino: string, ev: DragEvent): void {
        ev.preventDefault();
        const t = this.arrastrando;
        this.columnaSobre = null;
        this.arrastrando = null;
        if (!t || !this.tablero) return;

        const origen = this.tablero.columnas.find(
            c => c.tarjetas.some(x => x.id === t.id),
        );
        if (!origen || origen.key === destino) { this.cdr.detectChanges(); return; }

        // Movimiento optimista: la tarjeta salta ya y se revierte si el
        // servidor la rechaza. Esperar la respuesta para pintar hace que
        // arrastrar se sienta roto en cuanto hay latencia.
        const idx = origen.tarjetas.findIndex(x => x.id === t.id);
        origen.tarjetas.splice(idx, 1);
        origen.total--;
        const col = this.tablero.columnas.find(c => c.key === destino)!;
        col.tarjetas.unshift(t);
        col.total++;
        this.cdr.detectChanges();

        this.http.patch<{ id: number }>(
            `${environment.apiUrl}/api/kanban/${t.id}`,
            { columna: destino, orden: 0 },
            { headers: this.auth.getAuthHeaders() },
        ).pipe(takeUntil(this.destroy$)).subscribe({
            next: () => {
                this.http.post(`${environment.apiUrl}/api/kanban/reordenar`,
                    { columna: destino, ids: col.tarjetas.map(x => x.id) },
                    { headers: this.auth.getAuthHeaders() },
                ).pipe(takeUntil(this.destroy$)).subscribe({ error: () => { /* el orden es cosmético */ } });
            },
            error: (e) => {
                // Devolver la tarjeta a su sitio: dejarla donde el usuario
                // la soltó mentiría sobre lo que hay guardado.
                col.tarjetas = col.tarjetas.filter(x => x.id !== t.id);
                col.total--;
                origen.tarjetas.splice(idx, 0, t);
                origen.total++;
                this.cdr.detectChanges();
                this.toast.error(e?.error?.detail || this.translate.instant('kanban.move_error'));
            },
        });
    }

    alternarPorPersona(): void {
        this.porPersona = !this.porPersona;
        try {
            localStorage.setItem(KanbanComponent.VISTA_KEY, this.porPersona ? '1' : '0');
        } catch { /* modo privado */ }
        this.cdr.detectChanges();
    }

    trackTarjeta = (_: number, t: Tarjeta) => t.id;
    trackColumna = (_: number, c: Columna) => c.key;
    trackCalle = (_: number, c: Calle) => c.persona.clave;
}
