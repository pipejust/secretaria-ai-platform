import {
    Component, OnDestroy, OnInit, ChangeDetectorRef, ViewChild, ElementRef, HostListener,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
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
    /** Sesiones cuyo contenido literal substancia el `intro`. El backend
     *  las puebla; el frontend las usa para separar "fuentes con la
     *  respuesta" (primarias) de "otras consultadas" (resto de citations).
     *  Opcional por backward-compat con respuestas viejas. */
    intro_source_sessions?: number[];
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
    imports: [CommonModule, FormsModule, RouterModule, MdRenderPipe, TranslateModule],
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
        private translate: TranslateService,
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
            this.toast.warning(this.translate.instant('ask.toast_question_too_short'));
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

        // Contexto conversacional: enviamos los últimos N turnos del hilo
        // actual para que el LLM pueda resolver referencias como "eso",
        // "lo anterior", o seguir hablando del mismo tema.
        // `history` está en orden cronológico (más antiguo arriba, más
        // reciente abajo) tras el fix del append.
        if (this.history.length > 0) {
            body.prior_turns = this.history.slice(-8).map(t => ({
                question: t.question,
                // El backend espera answer como string. Compactamos intro +
                // primeras decisiones para que el LLM tenga el "qué dijiste"
                // sin inflar tokens.
                answer: this._compactAnswer(t),
            }));
        }

        this.http.post<AskResponse>(`${environment.apiUrl}/api/ask`, body, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (res) => {
                    // APPEND al final del chat (no prepend). Así el último
                    // turno queda visualmente abajo y el scroll natural
                    // baja al nuevo mensaje, como en cualquier chat.
                    const newTurn: ChatTurn = {
                        id: 0,
                        question: q,
                        ...res,
                        timestamp: Date.now(),
                    };
                    this.history = [...this.history, newTurn];
                    // El historial-dropdown (lista lateral) mantiene orden
                    // descendente por timestamp para que lo más nuevo aparezca
                    // primero al desplegarlo — uso distinto al chat.
                    this.historyList = [newTurn, ...this.historyList];
                    this.question = '';
                    this.isAsking = false;
                    this.cdr.detectChanges();
                    this._refreshHistoryIds();
                    this._scrollToBottom();
                },
                error: (err) => {
                    this.isAsking = false;
                    const msg = err?.error?.detail || this.translate.instant('ask.toast_ask_error');
                    this.toast.error(msg);
                    this.cdr.detectChanges();
                },
            });
    }

    /** Compacta una respuesta del turno previo para mandarla como contexto
     *  al LLM. Usa la respuesta plana + extracto de las decisiones/tareas
     *  del payload `structured` si existen. Limita a ~600 chars. */
    private _compactAnswer(t: ChatTurn): string {
        const parts: string[] = [];
        if (t.answer) parts.push(t.answer);
        const s = t.structured;
        if (s) {
            if (s.intro && !parts.length) parts.push(s.intro);
            if (s.decisions && s.decisions.length) {
                parts.push('Decisiones: ' + s.decisions.slice(0, 3)
                    .map((d: any) => typeof d === 'string' ? d : (d.text || '')).join('; '));
            }
            if (s.action_items && s.action_items.length) {
                parts.push('Tareas: ' + s.action_items.slice(0, 3)
                    .map((a: any) => a.title || '').join('; '));
            }
        }
        return parts.join('\n').slice(0, 600);
    }

    /** Scroll al final del contenedor del chat tras agregar un nuevo turn.
     *  Se ejecuta en doble RAF para asegurar que el DOM ya pintó la card. */
    private _scrollToBottom(): void {
        const tryScroll = () => {
            const el = document.querySelector('.acten-ask .ask-chat-scroll')
                    || document.querySelector('.acten-ask .ask-thread')
                    || document.querySelector('.acten-ask main')
                    || document.scrollingElement;
            if (el) {
                (el as HTMLElement).scrollTop = (el as HTMLElement).scrollHeight;
            }
        };
        requestAnimationFrame(() => requestAnimationFrame(tryScroll));
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
                    // Ahora `history` está en orden cronológico (más nuevo
                    // al FINAL), así que el último elemento es el recién
                    // creado. El backend siempre devuelve más reciente primero.
                    if (this.history.length && fromBE.length) {
                        const top = fromBE[0];
                        const lastIdx = this.history.length - 1;
                        const last = this.history[lastIdx];
                        if (top.question === last.question && !last.id) {
                            this.history = [
                                ...this.history.slice(0, lastIdx),
                                { ...last, id: top.id },
                            ];
                        }
                    }
                    this.cdr.detectChanges();
                },
                error: () => { /* mejor no molestar al usuario */ }
            });
    }

    /** Click en una sugerencia: resuelve la KEY i18n contra el idioma
     *  activo y la pone en el input antes de enviar. Las prompts son
     *  KEYS (`ask.prompt_meetings_1`) no texto literal — ver
     *  `suggestedPrompts` abajo. */
    usePrompt(keyOrText: string): void {
        if (this.isAsking) return;
        const resolved = this.translate.instant(keyOrText);
        // Si la traducción no existe, `instant` devuelve la key tal cual:
        // detectamos ese caso y caemos al input original para no enviar
        // "ask.prompt_meetings_1" como pregunta literal al backend.
        this.question = (resolved && resolved !== keyOrText) ? resolved : keyOrText;
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
                    this.toast.success(this.translate.instant('ask.toast_entry_deleted'));
                    this.cdr.detectChanges();
                },
                error: () => this.toast.error(this.translate.instant('ask.toast_entry_delete_error')),
            });
    }

    /** Borra TODO el historial (backend + ambas listas locales). */
    clearAllHistory(): void {
        if (!this.historyList.length) return;
        const ok = confirm(this.translate.instant('ask.confirm_clear_history', { count: this.historyList.length }));
        if (!ok) return;
        const headers = this.authService.getAuthHeaders();
        this.http.delete(`${environment.apiUrl}/api/ask/history`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: () => {
                    this.history = [];
                    this.historyList = [];
                    this.showHistory = false;
                    this.toast.success(this.translate.instant('ask.toast_history_cleared'));
                    this.cdr.detectChanges();
                },
                error: () => this.toast.error(this.translate.instant('ask.toast_history_clear_error')),
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
        this.toast.success(this.translate.instant('ask.toast_session_pinned', { title: this.pinnedSession.title }));
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
            this.toast.warning(this.translate.instant('ask.toast_image_bad_format'));
            input.value = '';
            return;
        }
        if (file.size > 4 * 1024 * 1024) {
            this.toast.warning(this.translate.instant('ask.toast_image_too_large'));
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
                    this.toast.warning(this.translate.instant('ask.toast_image_no_text'));
                    this.cdr.detectChanges();
                    return;
                }
                const prefix = this.question ? this.question + '\n\n' : '';
                this.question = `${prefix}${this.translate.instant('ask.toast_image_extracted_prefix', { name: file.name, text: txt })}`;
                this.toast.success(this.translate.instant('ask.toast_image_processed', { chars: res.chars }));
                this.cdr.detectChanges();
            },
            error: (err) => {
                this.isOcr = false;
                input.value = '';
                const msg = err?.error?.detail || this.translate.instant('ask.toast_image_error');
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
            this.toast.warning(this.translate.instant('ask.toast_attach_too_large'));
            input.value = '';
            return;
        }
        const okTypes = ['text/plain', 'text/markdown', 'application/json', ''];
        const ext = file.name.toLowerCase().split('.').pop() || '';
        const looksText = okTypes.includes(file.type) || ['txt','md','json','csv'].includes(ext);
        if (!looksText) {
            this.toast.warning(this.translate.instant('ask.toast_attach_bad_format'));
            input.value = '';
            return;
        }
        const reader = new FileReader();
        reader.onload = () => {
            const txt = String(reader.result || '').slice(0, 4000);
            const prefix = this.question ? this.question + '\n\n' : '';
            this.question = `${prefix}${this.translate.instant('ask.toast_attached_context_prefix', { name: file.name, text: txt })}`;
            this.toast.success(this.translate.instant('ask.toast_attached_success', { name: file.name }));
            this.cdr.detectChanges();
        };
        reader.onerror = () => this.toast.error(this.translate.instant('ask.toast_attach_read_error'));
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

    /** Las 3 categorías de prompts del panel izquierdo. Los `label` y
     *  los `prompts` son CLAVES i18n — se resuelven en el template via
     *  `| translate`. Antes estaban hardcoded en español, lo que dejaba
     *  el panel entero en ES aunque el usuario estuviera en CA/EN. */
    readonly suggestedPrompts: SuggestedCategory[] = [
        {
            label: 'ask.cat_meetings',
            icon: 'meeting',
            prompts: [
                'ask.prompt_meetings_1',
                'ask.prompt_meetings_2',
                'ask.prompt_meetings_3',
                'ask.prompt_meetings_4',
            ],
        },
        {
            label: 'ask.cat_risks',
            icon: 'risk',
            prompts: [
                'ask.prompt_risks_1',
                'ask.prompt_risks_2',
                'ask.prompt_risks_3',
            ],
        },
        {
            label: 'ask.cat_decisions',
            icon: 'decision',
            prompts: [
                'ask.prompt_decisions_1',
                'ask.prompt_decisions_2',
                'ask.prompt_decisions_3',
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
        if (n >= 5) return { tone: 'high',   text: this.translate.instant('ask.confidence_high') };
        if (n >= 3) return { tone: 'medium', text: this.translate.instant('ask.confidence_medium') };
        return { tone: 'low', text: this.translate.instant('ask.confidence_low') };
    }

    /** Cantidad de fuentes únicas (deduplicadas por session_id). */
    uniqueSourceCount(turn: ChatTurn): number {
        const ids = new Set<number>();
        for (const c of (turn?.citations || [])) ids.add(c.session_id);
        return ids.size;
    }

    /** IDs de sesiones que el LLM citó como soporte directo de la respuesta:
     *  intro_source_sessions + decisions/action_items/risks/agreements
     *  source_sessions. Estos son los chips visibles destacados. */
    private primarySessionIds(turn: ChatTurn): Set<number> {
        const ids = new Set<number>();
        const s = turn?.structured;
        if (!s) return ids;
        for (const sid of (s.intro_source_sessions || [])) ids.add(sid);
        const collect = (arr?: Array<Decision | string>) => {
            for (const d of (arr || [])) {
                if (typeof d === 'string') continue;
                for (const sid of (d?.source_sessions || [])) ids.add(sid);
            }
        };
        collect(s.decisions);
        collect(s.risks);
        collect(s.agreements);
        for (const it of (s.action_items || [])) {
            for (const sid of (it?.source_sessions || [])) ids.add(sid);
        }
        return ids;
    }

    /** Fuentes PRIMARIAS: las que el LLM cita como soporte de la respuesta. */
    primarySourceList(turn: ChatTurn): {
        session_id: number;
        title: string;
        date: string;
        kinds: string[];
    }[] {
        const primary = this.primarySessionIds(turn);
        return this._buildSourceList(turn, sid => primary.has(sid));
    }

    /** Fuentes CONSULTADAS pero no citadas: estaban en el RAG pero el LLM
     *  no las usó para construir la respuesta. Se muestran colapsadas. */
    otherSourceList(turn: ChatTurn): {
        session_id: number;
        title: string;
        date: string;
        kinds: string[];
    }[] {
        const primary = this.primarySessionIds(turn);
        return this._buildSourceList(turn, sid => !primary.has(sid));
    }

    /** Helper compartido: dedup por session_id, agrupa kinds, aplica filtro. */
    private _buildSourceList(
        turn: ChatTurn,
        keep: (sid: number) => boolean,
    ): { session_id: number; title: string; date: string; kinds: string[] }[] {
        const map = new Map<number, { kinds: Set<string> }>();
        for (const c of (turn?.citations || [])) {
            if (!keep(c.session_id)) continue;
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

    /** Backward-compat: el template viejo usaba `sourceList(turn)` con todas
     *  las fuentes. Lo mantenemos para no romper lo cacheado. */
    sourceList(turn: ChatTurn): {
        session_id: number;
        title: string;
        date: string;
        kinds: string[];
    }[] {
        return this._buildSourceList(turn, () => true);
    }

    /** UI: si hay primarias, las mostramos arriba destacadas y el bloque
     *  "Otras consultadas" se colapsa por default. Toggle por turn.id. */
    private _otherSourcesExpanded = new Set<number>();
    isOtherSourcesExpanded(turn: ChatTurn): boolean {
        return this._otherSourcesExpanded.has(turn?.id || 0);
    }
    toggleOtherSources(turn: ChatTurn): void {
        const id = turn?.id || 0;
        if (this._otherSourcesExpanded.has(id)) {
            this._otherSourcesExpanded.delete(id);
        } else {
            this._otherSourcesExpanded.add(id);
        }
    }

    /** Copia el texto de la respuesta al portapapeles. */
    copyAnswer(turn: ChatTurn): void {
        try {
            navigator.clipboard.writeText(turn.answer || '');
            this.toast.success(this.translate.instant('ask.toast_answer_copied'));
        } catch {
            this.toast.warning(this.translate.instant('ask.toast_answer_copy_error'));
        }
    }

    /** Feedback placeholder (like/dislike) — no persiste todavía, sólo
     *  reconoce el click. Cuando exista endpoint, lo cableamos. */
    rateAnswer(turn: ChatTurn, score: 'up' | 'down'): void {
        this.toast.success(score === 'up'
            ? this.translate.instant('ask.toast_feedback_thanks_up')
            : this.translate.instant('ask.toast_feedback_thanks_down'));
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

    /** Convierte las referencias «#42» / «sesión #42» del intro en enlaces
     *  markdown hacia la curación de esa sesión (`/admin/curation/<id>`),
     *  para que el pipe `mdRender` los pinte como anclas clicables. Solo
     *  enlaza ids que existen en las citations del turn (evita linkear
     *  números que no son sesiones). */
    linkifySessions(turn: ChatTurn): string {
        const intro = turn?.structured?.intro || '';
        if (!intro) return intro;
        const ids = new Set<number>();
        for (const c of (turn?.citations || [])) ids.add(c.session_id);
        for (const s of (turn?.structured?.intro_source_sessions || [])) ids.add(s);
        if (!ids.size) return intro;
        return intro.replace(
            /(?:sesi[oó]n(?:es)?\s+)?#(\d+)/gi,
            (full: string, num: string) => {
                const id = parseInt(num, 10);
                if (!ids.has(id)) return full;
                return `[${full}](/admin/curation/${id})`;
            },
        );
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
            case 'in_progress': return this.translate.instant('ask.status_in_progress');
            case 'pending':     return this.translate.instant('ask.status_pending');
            case 'not_started': return this.translate.instant('ask.status_not_started');
            case 'done':        return this.translate.instant('ask.status_done');
            default:            return s ? s : this.translate.instant('ask.status_pending');
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
