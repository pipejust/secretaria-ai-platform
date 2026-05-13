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
    /** Riesgos y acuerdos siguen el mismo shape que decisions. */
    risks?: Array<Decision | string>;
    agreements?: Array<Decision | string>;
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
    /** Vista actual del chat. Empieza VACÍA al cargar la página y al
     *  apretar "Nueva pregunta". El historial completo vive en
     *  `historyList` y solo se muestra desde el dropdown. */
    history: ChatTurn[] = [];
    /** Historial persistido en backend (todas las preguntas del usuario).
     *  Se usa solo para poblar el dropdown del botón "Historial". */
    historyList: ChatTurn[] = [];
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

    /** Menú del botón Adjuntar (texto/imagen/sesión). */
    showAttachMenu = false;
    /** Modal selector de sesión a pinear. */
    showSessionPicker = false;
    sessionPickerQuery = '';
    /** Sesión actualmente pinneada como filtro de búsqueda RAG. */
    pinnedSession: { id: number; title: string; date?: string } | null = null;
    /** Loading state mientras OCR procesa la imagen. */
    isOcr = false;

    @ViewChild('attachInput')   attachInput?:   ElementRef<HTMLInputElement>;
    @ViewChild('imageInput')    imageInput?:    ElementRef<HTMLInputElement>;
    @ViewChild('advancedPanel') advancedPanel?: ElementRef<HTMLDivElement>;
    @ViewChild('historyPanel')  historyPanel?:  ElementRef<HTMLDivElement>;
    @ViewChild('attachMenu')    attachMenu?:    ElementRef<HTMLDivElement>;

    constructor(
        private http: HttpClient,
        private authService: AuthService,
        private toast: ToastService,
        private cdr: ChangeDetectorRef,
    ) {}

    ngOnInit(): void {
        this.loadProjects();
        this.loadSessionsMeta();
        // Sólo cargamos la LISTA del historial para el dropdown.
        // La vista del chat empieza vacía intencionalmente.
        this.loadHistoryList();
    }

    /** Carga el historial del backend al iniciar. Cada entry se transforma
     *  a `ChatTurn` y se vuelca SÓLO en `historyList` (para el dropdown).
     *  El chat principal (`history`) permanece vacío hasta que el usuario
     *  haga una pregunta nueva o seleccione una del dropdown. */
    loadHistoryList(): void {
        const headers = this.authService.getAuthHeaders();
        this.http.get<AskHistoryEntry[]>(`${environment.apiUrl}/api/ask/history?limit=30`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (entries) => {
                    this.historyList = (entries || []).map(e => this._entryToTurn(e));
                    this.cdr.detectChanges();
                },
                error: () => { /* historial es opcional */ }
            });
    }

    private _entryToTurn(e: AskHistoryEntry): ChatTurn {
        return {
            id: e.id,
            question: e.question,
            answer: e.answer,
            structured: e.structured,
            citations: e.citations || [],
            model: e.model,
            chunks_used: e.chunks_used,
            timestamp: this._parseDateMs(e.created_at),
        };
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
        // Si el usuario pinneó una sesión específica, restringimos la
        // búsqueda RAG a ella (evita contaminación de otras reuniones).
        if (this.pinnedSession) body.session_ids = [this.pinnedSession.id];

        this.http.post<AskResponse>(`${environment.apiUrl}/api/ask`, body, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (res) => {
                    // Insertamos el nuevo turn al tope de AMBAS listas:
                    // - `history` (vista del chat actual)
                    // - `historyList` (dropdown de Historial)
                    // El id queda en 0 momentáneamente; lo sincronizamos
                    // con un refresh del historial para que delete funcione.
                    const newTurn: ChatTurn = {
                        id: 0,
                        question: q,
                        ...res,
                        timestamp: Date.now(),
                    };
                    this.history = [newTurn, ...this.history];
                    this.historyList = [newTurn, ...this.historyList];
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
     *  (backend devuelve más reciente primero). Patcheamos el id en
     *  `history` (vista) y reconstruimos `historyList` (dropdown). */
    private _refreshHistoryIds(): void {
        const headers = this.authService.getAuthHeaders();
        this.http.get<AskHistoryEntry[]>(`${environment.apiUrl}/api/ask/history?limit=30`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (entries) => {
                    const fromBE = entries || [];
                    // historyList completo viene del backend (ya enriquecido).
                    this.historyList = fromBE.map(e => this._entryToTurn(e));
                    // Para `history` (vista actual), parchamos sólo el id
                    // del turn más reciente sin pisar el resto del estado.
                    if (this.history.length && fromBE.length) {
                        const top = fromBE[0];
                        if (top.question === this.history[0].question && !this.history[0].id) {
                            this.history = [
                                { ...this.history[0], id: top.id },
                                ...this.history.slice(1),
                            ];
                        }
                    }
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

    /** Botón "Nueva pregunta" — RESETEA la vista del chat por completo
     *  para empezar limpio. El historial persistido en backend NO se toca
     *  y sigue accesible desde el dropdown. */
    newQuestion(): void {
        this.history = [];
        this.question = '';
        this.showHistory = false;
        this.showAdvanced = false;
        this.cdr.detectChanges();
    }

    /** Borra UNA entrada del historial. Quita de ambas listas. */
    deleteHistoryEntry(t: ChatTurn, ev: Event): void {
        ev.stopPropagation();
        if (!t.id) {
            this.history = this.history.filter(x => x.timestamp !== t.timestamp);
            this.historyList = this.historyList.filter(x => x.timestamp !== t.timestamp);
            this.cdr.detectChanges();
            return;
        }
        const headers = this.authService.getAuthHeaders();
        this.http.delete(`${environment.apiUrl}/api/ask/history/${t.id}`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: () => {
                    this.history = this.history.filter(x => x.id !== t.id);
                    this.historyList = this.historyList.filter(x => x.id !== t.id);
                    this.toast.success('Pregunta borrada del historial.');
                    this.cdr.detectChanges();
                },
                error: () => this.toast.error('No se pudo borrar la entrada.'),
            });
    }

    /** Borra TODO el historial (backend + ambas listas locales). */
    clearAllHistory(): void {
        if (!this.historyList.length) return;
        const ok = confirm(`¿Borrar las ${this.historyList.length} preguntas del historial? Esta acción no se puede deshacer.`);
        if (!ok) return;
        const headers = this.authService.getAuthHeaders();
        this.http.delete(`${environment.apiUrl}/api/ask/history`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: () => {
                    this.history = [];
                    this.historyList = [];
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

    // ============================================================
    // Menú "Adjuntar" (texto / imagen / sesión)
    // ============================================================

    toggleAttachMenu(): void {
        this.showAttachMenu = !this.showAttachMenu;
        this.cdr.detectChanges();
    }

    /** Click en "Adjuntar texto" — abre el file picker de texto plano. */
    pickTextFile(): void {
        this.showAttachMenu = false;
        this.attachInput?.nativeElement?.click();
    }

    /** Click en "Adjuntar imagen" — abre el file picker de imágenes,
     *  el archivo se sube al backend para OCR y el texto resultado se
     *  inyecta en el input de pregunta. */
    pickImage(): void {
        this.showAttachMenu = false;
        this.imageInput?.nativeElement?.click();
    }

    /** Click en "Enfocar en una reunión" — abre el modal selector de
     *  sesión. Una vez elegida, se "pinea" como filtro RAG. */
    openSessionPicker(): void {
        this.showAttachMenu = false;
        this.sessionPickerQuery = '';
        this.showSessionPicker = true;
        this.cdr.detectChanges();
    }

    closeSessionPicker(): void {
        this.showSessionPicker = false;
        this.cdr.detectChanges();
    }

    /** Aplica el pin sobre la sesión elegida. Las próximas preguntas
     *  irán con `session_ids: [sid]` para no mezclar otras actas. */
    pinSession(sid: number): void {
        const meta = this.sessionMeta.get(sid);
        this.pinnedSession = {
            id: sid,
            title: meta?.title || `Sesión #${sid}`,
            date: meta?.date,
        };
        this.showSessionPicker = false;
        this.toast.success(`Búsqueda enfocada en: ${this.pinnedSession.title}`);
        this.cdr.detectChanges();
    }

    /** Quita el pin (vuelve a buscar en todas las sesiones). */
    unpinSession(): void {
        this.pinnedSession = null;
        this.cdr.detectChanges();
    }

    /** Lista de sesiones filtrada por el query del picker. */
    get filteredSessions(): Array<{ id: number; title: string; date: string }> {
        const q = this.sessionPickerQuery.trim().toLowerCase();
        const all: Array<{ id: number; title: string; date: string }> = [];
        this.sessionMeta.forEach((meta, id) => {
            all.push({ id, title: meta.title, date: meta.date });
        });
        all.sort((a, b) => b.id - a.id); // más reciente primero
        if (!q) return all.slice(0, 50);
        return all.filter(s =>
            s.title.toLowerCase().includes(q) || String(s.id).includes(q)
        ).slice(0, 50);
    }

    onImageSelected(ev: Event): void {
        const input = ev.target as HTMLInputElement;
        const file = input?.files?.[0];
        if (!file) return;
        const okTypes = ['image/jpeg', 'image/jpg', 'image/png', 'image/webp'];
        if (!okTypes.includes(file.type)) {
            this.toast.warning('Solo se admiten imágenes JPEG, PNG o WebP.');
            input.value = '';
            return;
        }
        if (file.size > 4 * 1024 * 1024) {
            this.toast.warning('La imagen es muy grande (máx 4 MB).');
            input.value = '';
            return;
        }

        this.isOcr = true;
        this.cdr.detectChanges();
        const headers = this.authService.getAuthHeaders();
        const form = new FormData();
        form.append('file', file);
        this.http.post<{ text: string; chars: number }>(
            `${environment.apiUrl}/api/ask/extract-image`, form, { headers }
        ).pipe(takeUntil(this.destroy$)).subscribe({
            next: (res) => {
                this.isOcr = false;
                input.value = '';
                const txt = (res.text || '').slice(0, 4000);
                if (!txt.trim()) {
                    this.toast.warning('No se detectó texto en la imagen.');
                    this.cdr.detectChanges();
                    return;
                }
                const prefix = this.question ? this.question + '\n\n' : '';
                this.question = `${prefix}--- Texto extraído de imagen (${file.name}) ---\n${txt}`;
                this.toast.success(`Imagen procesada: ${res.chars} caracteres extraídos.`);
                this.cdr.detectChanges();
            },
            error: (err) => {
                this.isOcr = false;
                input.value = '';
                const msg = err?.error?.detail || 'No se pudo procesar la imagen.';
                this.toast.error(msg);
                this.cdr.detectChanges();
            },
        });
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
        if (this.showAttachMenu) {
            const panel = this.attachMenu?.nativeElement;
            if (panel && target && !panel.contains(target)) {
                this.showAttachMenu = false;
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

    /** Click en una pregunta del historial: la trae a la vista del chat.
     *  - Si ya está visible: hace scroll y la flashea.
     *  - Si no está: la PREPENDE a la vista (no reemplaza el resto, así
     *    el usuario puede mezclar preguntas viejas con la sesión actual). */
    jumpToTurn(turn: ChatTurn): void {
        this.showHistory = false;
        const alreadyVisible = this.history.some(t =>
            (turn.id && t.id === turn.id) || t.timestamp === turn.timestamp
        );
        if (!alreadyVisible) {
            this.history = [turn, ...this.history];
        }
        this.cdr.detectChanges();
        // Diferido para que Angular pinte el nuevo turn y el DOM exista.
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
        return !!(s.intro || s.decisions?.length || s.action_items?.length
                  || s.risks?.length || s.agreements?.length);
    }

    /** Genera los queryParams para focus en una sección del curation
     *  cuando se hace click en un chip. La página destino los lee y hace
     *  scroll + highlight al item correspondiente. */
    focusParams(focus: 'decisions' | 'risks' | 'agreements' | 'task' | 'summary',
                text: string = ''): {[k: string]: string} {
        const out: {[k: string]: string} = { focus };
        if (text) out['text'] = text;
        return out;
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
