import { Component, OnDestroy, OnInit, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { Subject } from 'rxjs';
import { takeUntil } from 'rxjs/operators';
import { AuthService } from '../../services/auth.service';
import { ToastService } from '../../services/toast.service';
import { environment } from '../../../environments/environment';
import { MdRenderPipe } from '../../pipes/md-render.pipe';

interface Citation {
    session_id: number;
    kind: string;
    snippet: string;
    distance: number;
}
interface AskResponse {
    answer: string;
    citations: Citation[];
    model: string;
    chunks_used: number;
}
interface ChatTurn {
    question: string;
    answer: string;
    citations: Citation[];
    model: string;
    chunks_used: number;
    timestamp: number;
}

interface SuggestedCategory {
    label: string;
    icon: 'meeting' | 'risk' | 'decision';
    prompts: string[];
}

@Component({
    selector: 'app-ask',
    standalone: true,
    imports: [CommonModule, FormsModule, RouterModule, MdRenderPipe],
    templateUrl: './ask.component.html',
    styleUrls: ['./ask.component.css'],
})
export class AskComponent implements OnInit, OnDestroy {
    question = '';
    history: ChatTurn[] = [];
    isAsking = false;
    projects: Array<{ id: number; name: string }> = [];
    /** Filtro por proyecto — preservado del componente original. */
    projectId: number | null = null;
    /** Sesiones cargadas para enriquecer el listado de Fuentes con metadatos. */
    private sessionMeta: Map<number, { title: string; date: string; duration?: string; participants?: number }> = new Map();
    private readonly destroy$ = new Subject<void>();

    constructor(
        private http: HttpClient,
        private authService: AuthService,
        private toast: ToastService,
        private cdr: ChangeDetectorRef,
    ) {}

    ngOnInit(): void {
        this.loadProjects();
        this.loadSessionsMeta();
    }

    ngOnDestroy(): void {
        this.destroy$.next();
        this.destroy$.complete();
    }

    loadProjects(): void {
        this.http.get<any[]>(`${environment.apiUrl}/api/projects/`)
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (data) => {
                    this.projects = (data || []).map(p => ({ id: p.id, name: p.name }));
                    this.cdr.detectChanges();
                },
                error: () => { /* permite preguntar sin filtro */ }
            });
    }

    /** Carga ligera de las últimas sesiones para poder enriquecer el panel
     *  de "Fuentes" con título/fecha cuando una citation referencia un
     *  session_id que conocemos. Si falla, las fuentes se muestran con su
     *  shape básico (Sesión #N). */
    loadSessionsMeta(): void {
        const headers = this.authService.getAuthHeaders();
        this.http.get<any>(`${environment.apiUrl}/api/sessions/?page=1&limit=100`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (data) => {
                    const items = (data?.items ?? data) || [];
                    for (const s of items) {
                        this.sessionMeta.set(s.id, {
                            title: s.title || `Sesión #${s.id}`,
                            date: this._formatSessionDate(s.date),
                        });
                    }
                    this.cdr.detectChanges();
                },
                error: () => { /* meta opcional */ }
            });
    }

    private _formatSessionDate(v: any): string {
        if (v == null) return '';
        const n = typeof v === 'string' && !isNaN(Number(v)) ? Number(v) : v;
        const d = new Date(n);
        if (isNaN(d.getTime())) return String(v);
        const months = ['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'];
        return `${d.getDate()} ${months[d.getMonth()]} ${d.getFullYear()}`;
    }

    onEnter(ev: Event): void {
        const e = ev as KeyboardEvent;
        if (e.shiftKey) return;
        e.preventDefault();
        this.submit();
    }

    submit(): void {
        const q = (this.question || '').trim();
        if (q.length < 3) {
            this.toast.warning('Escribe una pregunta de al menos 3 caracteres.');
            return;
        }
        this.isAsking = true;
        const headers = this.authService.getAuthHeaders();
        const body: any = { question: q, top_k: 8 };
        if (this.projectId) body.project_id = this.projectId;

        this.http.post<AskResponse>(`${environment.apiUrl}/api/ask`, body, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (res) => {
                    this.history = [
                        { question: q, ...res, timestamp: Date.now() },
                        ...this.history,
                    ];
                    this.question = '';
                    this.isAsking = false;
                    this.cdr.detectChanges();
                },
                error: (err) => {
                    this.isAsking = false;
                    const msg = err?.error?.detail || 'Error consultando a Acten.';
                    this.toast.error(msg);
                    this.cdr.detectChanges();
                },
            });
    }

    /** Click en una sugerencia: pone el texto en el input y dispara submit. */
    usePrompt(text: string): void {
        if (this.isAsking) return;
        this.question = text;
        this.submit();
    }

    /** Botón "Nueva pregunta" del topbar — limpia historial + input. */
    newQuestion(): void {
        this.history = [];
        this.question = '';
        this.cdr.detectChanges();
    }

    trackByTurn(_i: number, t: ChatTurn): number { return t.timestamp; }
    trackByCitation(_i: number, c: Citation): string { return `${c.session_id}-${c.kind}`; }
    trackBySource(_i: number, s: { session_id: number }): number { return s.session_id; }

    // ============================================================
    // Helpers de presentación
    // ============================================================

    /** Las 3 categorías de prompts del panel izquierdo. Mismo set para
     *  todos los users — el componente no persiste favoritos por ahora. */
    readonly suggestedPrompts: SuggestedCategory[] = [
        {
            label: 'Reuniones',
            icon: 'meeting',
            prompts: [
                '¿Cuáles fueron las decisiones clave de las reuniones recientes?',
                'Resume la última reunión con el equipo de producto.',
                '¿Qué tareas tengo asignadas?',
                'Muestra reuniones sobre el roadmap del producto.',
            ],
        },
        {
            label: 'Riesgos',
            icon: 'risk',
            prompts: [
                '¿Cuáles son los principales riesgos del roadmap Q2?',
                '¿Qué riesgos están vencidos o sin actualización?',
                'Muestra riesgos relacionados con integraciones.',
            ],
        },
        {
            label: 'Decisiones',
            icon: 'decision',
            prompts: [
                '¿Qué decisiones están pendientes de aprobación?',
                'Muestra decisiones tomadas sobre las prioridades Q2.',
                '¿Quién aprobó el presupuesto de los ítems del roadmap?',
            ],
        },
    ];

    /** Iniciales del usuario logueado (KP, etc.). */
    get userInitials(): string {
        const name = this.authService.currentUserValue?.full_name || '';
        if (!name) return '··';
        const parts = name.trim().split(/\s+/);
        return ((parts[0]?.[0] || '') + (parts[1]?.[0] || '')).toUpperCase() || '··';
    }

    /** "Hoy, 9:15 a. m." para el timestamp de la pregunta del usuario. */
    formatTurnTime(ts: number): string {
        const d = new Date(ts);
        if (isNaN(d.getTime())) return '';
        const now = new Date();
        const isToday = d.toDateString() === now.toDateString();
        const time = d.toLocaleTimeString('es-CO', { hour: 'numeric', minute: '2-digit' });
        return isToday ? `Hoy, ${time}` : d.toLocaleDateString('es-CO', { day: '2-digit', month: 'short' }) + `, ${time}`;
    }

    /** Confianza visual basada en la cantidad de chunks usados. */
    confidenceLabel(turn: ChatTurn): { tone: 'high' | 'medium' | 'low'; text: string } {
        const n = turn?.chunks_used || 0;
        if (n >= 5) return { tone: 'high',   text: 'Alta' };
        if (n >= 3) return { tone: 'medium', text: 'Media' };
        return { tone: 'low', text: 'Baja' };
    }

    /** Cantidad de fuentes únicas (deduplicadas por session_id). */
    uniqueSourceCount(turn: ChatTurn): number {
        const ids = new Set<number>();
        for (const c of (turn?.citations || [])) ids.add(c.session_id);
        return ids.size;
    }

    /** Lista de fuentes únicas enriquecida con metadata de la sesión.
     *  Una fuente por session_id (deduplicada) + agrupa los chunks. */
    sourceList(turn: ChatTurn): {
        session_id: number;
        title: string;
        date: string;
        kinds: string[];
    }[] {
        const map = new Map<number, { kinds: Set<string> }>();
        for (const c of (turn?.citations || [])) {
            const cur = map.get(c.session_id) || { kinds: new Set<string>() };
            cur.kinds.add(c.kind);
            map.set(c.session_id, cur);
        }
        return Array.from(map.entries()).map(([sid, v]) => {
            const meta = this.sessionMeta.get(sid);
            return {
                session_id: sid,
                title: meta?.title || `Sesión #${sid}`,
                date: meta?.date || '',
                kinds: Array.from(v.kinds),
            };
        });
    }

    /** Copia el texto de la respuesta al portapapeles. */
    copyAnswer(turn: ChatTurn): void {
        try {
            navigator.clipboard.writeText(turn.answer || '');
            this.toast.success('Respuesta copiada al portapapeles.');
        } catch {
            this.toast.warning('No se pudo copiar la respuesta.');
        }
    }

    /** Feedback placeholder (like/dislike) — no persiste todavía, sólo
     *  reconoce el click. Cuando exista endpoint, lo cableamos. */
    rateAnswer(turn: ChatTurn, score: 'up' | 'down'): void {
        this.toast.success(score === 'up' ? '¡Gracias por tu feedback!' : 'Gracias, tomaremos nota.');
    }
}
