import {
    Component, OnDestroy, OnInit, ChangeDetectorRef, ViewChild, ElementRef, HostListener,
} from '@angular/core';
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

interface ActionItem {
    title: string;
    owner: string;
    due_date: string;
    status: string;
    source_sessions?: number[];
}

interface Decision {
    text: string;
    source_sessions?: number[];
}

interface StructuredAnswer {
    intro: string;
    /** El backend nuevo manda `Decision[]` con fuentes; toleramos string[]
     *  (formato viejo) por backward-compat con respuestas cacheadas. */
    decisions: Array<Decision | string>;
    action_items: ActionItem[];
}

interface AskResponse {
    answer: string;
    structured?: StructuredAnswer | null;
    citations: Citation[];
    model: string;
    chunks_used: number;
}
interface ChatTurn {
    /** id del backend (askhistory.id). 0 si todavía no se persistió. */
    id?: number;
    question: string;
    answer: string;
    structured?: StructuredAnswer | null;
    citations: Citation[];
    model: string;
    chunks_used: number;
    timestamp: number;
}

/** Lo que devuelve GET /api/ask/history. */
interface AskHistoryEntry {
    id: number;
    question: string;
    answer: string;
    structured?: StructuredAnswer | null;
    citations: Citation[];
    project_id?: number | null;
    model: string;
    chunks_used: number;
    created_at: string;
}

interface SuggestedCategory {
    label: string;
    icon: 'meeting' | 'risk' | 'decision';
    prompts: string[];
}

interface ModelOption {
    id: string;
    label: string;
    sublabel: string;
    disabled: boolean;
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

    /** Catálogo de modelos disponibles. Backend siempre usa Groq por ahora,
     *  pero exponemos un select preparado para cuando agreguemos OpenAI/Claude. */
    readonly modelOptions: ModelOption[] = [
        { id: 'groq-llama-3.3-70b', label: 'Acten AI', sublabel: 'Groq · Llama 3.3 70B', disabled: false },
        { id: 'gpt-4o',             label: 'GPT-4o',   sublabel: 'OpenAI · próximamente', disabled: true },
        { id: 'claude-sonnet',      label: 'Claude Sonnet', sublabel: 'Anthropic · próximamente', disabled: true },
    ];
    selectedModel: string = this.modelOptions[0].id;

    /** Controles avanzados (popover de sliders). */
    showAdvanced = false;
    topK: number = 8;

    /** Dropdown de Historial — abre/cierra el panel con las preguntas
     *  recientes de la sesión actual (in-memory). */
    showHistory = false;

    @ViewChild('attachInput') attachInput?: ElementRef<HTMLInputElement>;
    @ViewChild('advancedPanel') advancedPanel?: ElementRef<HTMLDivElement>;
    @ViewChild('historyPanel') historyPanel?: ElementRef<HTMLDivElement>;

    constructor(
        private http: HttpClient,
        private authService: AuthService,
        private toast: ToastService,
        private cdr: ChangeDetectorRef,
    ) {}

    ngOnInit(): void {
        this.loadProjects();
        this.loadSessionsMeta();
        this.loadHistory();
    }

    /** Carga el historial del backend al iniciar. Cada entry se transforma
     *  a `ChatTurn` y se vuelca en `this.history`. Se preserva el orden
     *  desc del backend (más reciente primero). */
    loadHistory(): void {
        const headers = this.authService.getAuthHeaders();
        this.http.get<AskHistoryEntry[]>(`${environment.apiUrl}/api/ask/history?limit=30`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (entries) => {
                    this.history = (entries || []).map(e => ({
                        id: e.id,
                        question: e.question,
                        answer: e.answer,
                        structured: e.structured,
                        citations: e.citations || [],
                        model: e.model,
                        chunks_used: e.chunks_used,
                        timestamp: this._parseDateMs(e.created_at),
                    }));
                    this.cdr.detectChanges();
                },
                error: () => { /* historial es opcional */ }
            });
    }

    private _parseDateMs(iso: string): number {
        const t = Date.parse(iso || '');
        return isNaN(t) ? Date.now() : t;
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
        const topK = Math.max(3, Math.min(20, Math.round(this.topK || 8)));
        const body: any = { question: q, top_k: topK };
        if (this.projectId) body.project_id = this.projectId;

        this.http.post<AskResponse>(`${environment.apiUrl}/api/ask`, body, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (res) => {
                    // Insertamos optimistamente el turn al tope con id=0
                    // (placeholder). Disparamos un reload del historial para
                    // sincronizar el id real de askhistory (necesario para
                    // que delete funcione).
                    this.history = [
                        { id: 0, question: q, ...res, timestamp: Date.now() },
                        ...this.history,
                    ];
                    this.question = '';
                    this.isAsking = false;
                    this.cdr.detectChanges();
                    this._refreshHistoryIds();
                },
                error: (err) => {
                    this.isAsking = false;
                    const msg = err?.error?.detail || 'Error consultando a Acten.';
                    this.toast.error(msg);
                    this.cdr.detectChanges();
                },
            });
    }

    /** Tras un submit exitoso, recargamos el historial para sincronizar
     *  los ids reales de askhistory. La diferencia de orden es estable
     *  (backend devuelve más reciente primero, igual que mostramos). */
    private _refreshHistoryIds(): void {
        const headers = this.authService.getAuthHeaders();
        this.http.get<AskHistoryEntry[]>(`${environment.apiUrl}/api/ask/history?limit=30`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (entries) => {
                    // Reemplazamos solo los IDs preservando el resto del estado
                    // (importante por si el usuario borró localmente algo
                    // que aún existe en backend). Como el orden coincide
                    // (desc por id), parchamos por índice.
                    const fromBE = entries || [];
                    this.history = this.history.map((t, i) => {
                        const backendEntry = fromBE[i];
                        if (backendEntry && backendEntry.question === t.question) {
                            return { ...t, id: backendEntry.id };
                        }
                        return t;
                    });
                    this.cdr.detectChanges();
                },
                error: () => { /* mejor no molestar al usuario */ }
            });
    }

    /** Click en una sugerencia: pone el texto en el input y dispara submit. */
    usePrompt(text: string): void {
        if (this.isAsking) return;
        this.question = text;
        this.submit();
    }

    /** Botón "Nueva pregunta" del topbar — solo limpia el input. El
     *  historial se mantiene (vive en backend). Para borrar el historial,
     *  el usuario debe usar el botón explícito en el dropdown. */
    newQuestion(): void {
        this.question = '';
        this.cdr.detectChanges();
    }

    /** Borra UNA entrada del historial. */
    deleteHistoryEntry(t: ChatTurn, ev: Event): void {
        ev.stopPropagation();
        if (!t.id) {
            // Sin id (edge case), borrar localmente nomás.
            this.history = this.history.filter(x => x.timestamp !== t.timestamp);
            this.cdr.detectChanges();
            return;
        }
        const headers = this.authService.getAuthHeaders();
        this.http.delete(`${environment.apiUrl}/api/ask/history/${t.id}`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: () => {
                    this.history = this.history.filter(x => x.id !== t.id);
                    this.toast.success('Pregunta borrada del historial.');
                    this.cdr.detectChanges();
                },
                error: () => this.toast.error('No se pudo borrar la entrada.'),
            });
    }

    /** Borra TODO el historial. Pide confirmación. */
    clearAllHistory(): void {
        if (!this.history.length) return;
        const ok = confirm(`¿Borrar las ${this.history.length} preguntas del historial? Esta acción no se puede deshacer.`);
        if (!ok) return;
        const headers = this.authService.getAuthHeaders();
        this.http.delete(`${environment.apiUrl}/api/ask/history`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: () => {
                    this.history = [];
                    this.showHistory = false;
                    this.toast.success('Historial borrado.');
                    this.cdr.detectChanges();
                },
                error: () => this.toast.error('No se pudo borrar el historial.'),
            });
    }

    // ============================================================
    // Tool buttons (paperclip + sliders) del input chat
    // ============================================================

    /** Abre el file picker. Acepta texto plano para inyectarlo como
     *  contexto adicional dentro de la pregunta. */
    triggerAttach(): void {
        this.attachInput?.nativeElement?.click();
    }

    onAttachFile(ev: Event): void {
        const input = ev.target as HTMLInputElement;
        const file = input?.files?.[0];
        if (!file) return;
        // Limitamos a 200kb de texto plano para no sobrepasar el contexto.
        if (file.size > 200_000) {
            this.toast.warning('El archivo es muy grande (máx 200 KB de texto).');
            input.value = '';
            return;
        }
        const okTypes = ['text/plain', 'text/markdown', 'application/json', ''];
        const ext = file.name.toLowerCase().split('.').pop() || '';
        const looksText = okTypes.includes(file.type) || ['txt','md','json','csv'].includes(ext);
        if (!looksText) {
            this.toast.warning('Solo se admite texto plano (.txt, .md, .json, .csv).');
            input.value = '';
            return;
        }
        const reader = new FileReader();
        reader.onload = () => {
            const txt = String(reader.result || '').slice(0, 4000);
            const prefix = this.question ? this.question + '\n\n' : '';
            this.question = `${prefix}--- Contexto adjunto (${file.name}) ---\n${txt}`;
            this.toast.success(`Adjunté ${file.name} como contexto.`);
            this.cdr.detectChanges();
        };
        reader.onerror = () => this.toast.error('No se pudo leer el archivo.');
        reader.readAsText(file);
        input.value = '';
    }

    /** Toggle del popover de ajustes avanzados (top_k, etc.). */
    toggleAdvanced(): void {
        this.showAdvanced = !this.showAdvanced;
        this.cdr.detectChanges();
    }

    /** Resuelve el ModelOption activo a partir del id. Lo usamos en el
     *  template para mostrar el label corto en la pill y el tooltip largo. */
    selectedModelObj(): ModelOption | undefined {
        return this.modelOptions.find(m => m.id === this.selectedModel);
    }

    /** Cerrar popovers al click fuera. */
    @HostListener('document:click', ['$event'])
    onDocumentClick(ev: MouseEvent): void {
        const target = ev.target as Node | null;
        if (this.showAdvanced) {
            const panel = this.advancedPanel?.nativeElement;
            if (panel && target && !panel.contains(target)) {
                this.showAdvanced = false;
            }
        }
        if (this.showHistory) {
            const panel = this.historyPanel?.nativeElement;
            if (panel && target && !panel.contains(target)) {
                this.showHistory = false;
            }
        }
        this.cdr.detectChanges();
    }

    // ============================================================
    // Historial — dropdown con las preguntas de la sesión actual
    // ============================================================

    toggleHistory(): void {
        this.showHistory = !this.showHistory;
        this.cdr.detectChanges();
    }

    /** Click en una pregunta del historial: hace scroll al turn correspondiente. */
    jumpToTurn(turn: ChatTurn): void {
        this.showHistory = false;
        this.cdr.detectChanges();
        // Se difiere al siguiente tick para que Angular pinte y exista el DOM.
        setTimeout(() => {
            const el = document.querySelector(`[data-turn-id="${turn.timestamp}"]`) as HTMLElement | null;
            if (el) {
                el.scrollIntoView({ behavior: 'smooth', block: 'start' });
                el.classList.add('aa-turn-flash');
                setTimeout(() => el.classList.remove('aa-turn-flash'), 1400);
            }
        }, 30);
    }

    trackByTurn(_i: number, t: ChatTurn): number { return t.timestamp; }
    trackByCitation(_i: number, c: Citation): string { return `${c.session_id}-${c.kind}`; }
    trackBySource(_i: number, s: { session_id: number }): number { return s.session_id; }
    trackByDecision(i: number, _d: Decision | string): number { return i; }
    trackByActionItem(i: number, _it: ActionItem): number { return i; }
    trackBySid(_i: number, sid: number): number { return sid; }

    /** Normaliza una Decisión para que el template tenga siempre la misma forma. */
    decisionText(d: Decision | string): string {
        return typeof d === 'string' ? d : (d?.text || '');
    }
    decisionSources(d: Decision | string): number[] {
        return typeof d === 'string' ? [] : (d?.source_sessions || []);
    }

    /** Devuelve el título (o "Sesión #N") de una sesión por su id, usando
     *  la metadata cargada en `loadSessionsMeta`. Si no la tenemos cargada,
     *  cae al placeholder. Útil para mostrar chips legibles. */
    sessionLabel(sid: number): string {
        const m = this.sessionMeta.get(sid);
        return m?.title || `Sesión #${sid}`;
    }

    /** Versión truncada del nombre de la sesión para usar en chips/badges
     *  donde el espacio es limitado. Si no hay título, devuelve "#N". */
    sessionShortName(sid: number, maxLen: number = 28): string {
        const m = this.sessionMeta.get(sid);
        const title = m?.title;
        if (!title) return `#${sid}`;
        return title.length > maxLen ? title.slice(0, maxLen - 1).trimEnd() + '…' : title;
    }

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

    // ============================================================
    // Helpers para la respuesta estructurada
    // ============================================================

    /** ¿Tiene el turn datos estructurados que valga la pena renderizar? */
    hasStructured(turn: ChatTurn): boolean {
        const s = turn?.structured;
        if (!s) return false;
        return !!(s.intro || s.decisions?.length || s.action_items?.length);
    }

    /** Iniciales del owner para el avatar circular (mismo helper que
     *  usa la lista de usuarios global). */
    ownerInitials(name: string): string {
        const n = (name || '').trim();
        if (!n) return '··';
        const parts = n.split(/\s+/);
        return ((parts[0]?.[0] || '') + (parts[1]?.[0] || '')).toUpperCase();
    }

    /** Etiqueta legible para el badge de status. */
    statusLabel(status: string): string {
        const s = (status || '').toLowerCase();
        switch (s) {
            case 'in_progress': return 'En progreso';
            case 'pending':     return 'Pendiente';
            case 'not_started': return 'No iniciado';
            case 'done':        return 'Completado';
            default:            return s ? s : 'Pendiente';
        }
    }

    /** Tono del badge — debe coincidir con [data-tone] en el CSS. */
    statusTone(status: string): 'blue' | 'amber' | 'gray' | 'green' {
        const s = (status || '').toLowerCase();
        if (s === 'in_progress') return 'blue';
        if (s === 'done')        return 'green';
        if (s === 'not_started') return 'gray';
        return 'amber'; // pending o desconocido
    }
}
