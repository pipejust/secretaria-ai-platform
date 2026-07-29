import { Component, OnInit, OnDestroy, ChangeDetectorRef, HostListener } from '@angular/core';
import { CommonModule } from '@angular/common';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterModule } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { Subject } from 'rxjs';
import { takeUntil } from 'rxjs/operators';
import { AuthService } from '../../services/auth.service';
import { ToastService } from '../../services/toast.service';
import { environment } from '../../../environments/environment';
import { UserDirectoryService } from '../../services/user-directory.service';
import { LanguageService } from '../../services/language.service';
import { isRealDueDate, toIsoDateOrNull } from '../../shared/due-date';
import { parseLocalDate } from '../../shared/dates';

interface CalAccount {
    id: number;
    provider: 'google' | 'microsoft';
    account_email: string;
    is_active: boolean;
    created_at: string;
}

interface CalEvent {
    id: number;
    title: string;
    start_at: string;
    end_at: string;
    attendees: string[];
    meeting_url: string | null;
    session_id: number | null;
}

interface PendingTask {
    id: number;
    session_id: number;
    title: string;
    description: string;
    owner_name: string;
    owner_email: string;
    due_date: string | null;
    status: 'pending' | 'done' | 'blocked' | 'cancelled';
    completed_at: string | null;
    is_approved: boolean;
    project_name: string;
    /** null = la tarea no cuelga de ningún proyecto. */
    owner_in_project?: boolean | null;
    bucket: 'vencido' | 'proximo' | 'pendiente' | 'sin_fecha' | 'completado' | 'cancelado' | 'bloqueado';
}

interface DayCell {
    date: Date;
    iso: string;
    day: number;
    inMonth: boolean;
    isToday: boolean;
    isSelected: boolean;
    weekday: number;
    events: CalEvent[];
    tasks: PendingTask[];
}

interface DeadlineItem {
    id: string;
    title: string;
    when: string;
    urgent: boolean;
}

interface NoteItem {
    id: string;
    text: string;
    created_at: string;
}

type ViewMode = 'month' | 'week' | 'agenda';

// Etiquetas de día/mes por idioma. El calendario antes hardcodeaba ES,
// así que un usuario en CA o EN veía "Mayo 2026" y "Miércoles" como
// títulos. Ahora resolvemos el array según el idioma activo del cliente.
const DAY_LABELS_BY_LANG: Record<string, string[]> = {
    es: ['DOM', 'LUN', 'MAR', 'MIÉ', 'JUE', 'VIE', 'SÁB'],
    ca: ['DG', 'DL', 'DT', 'DC', 'DJ', 'DV', 'DS'],
    en: ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'],
};
const DAY_LABELS_FULL_BY_LANG: Record<string, string[]> = {
    es: ['Domingo', 'Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado'],
    ca: ['Diumenge', 'Dilluns', 'Dimarts', 'Dimecres', 'Dijous', 'Divendres', 'Dissabte'],
    en: ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'],
};
const MONTH_LABELS_BY_LANG: Record<string, string[]> = {
    es: ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
         'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'],
    ca: ['Gener', 'Febrer', 'Març', 'Abril', 'Maig', 'Juny',
         'Juliol', 'Agost', 'Setembre', 'Octubre', 'Novembre', 'Desembre'],
    en: ['January', 'February', 'March', 'April', 'May', 'June',
         'July', 'August', 'September', 'October', 'November', 'December'],
};
// Default 'es' para callers que no quieran lidiar con el idioma.
// NB: bracket access en vez de dot — `Record<string, ...>` con
// `noPropertyAccessFromIndexSignature: true` (strict config de prod)
// prohibe el `.es`.
const DAY_LABELS = DAY_LABELS_BY_LANG['es'];
const DAY_LABELS_FULL = DAY_LABELS_FULL_BY_LANG['es'];
const MONTH_LABELS = MONTH_LABELS_BY_LANG['es'];
// "de" conector entre día y mes en formato largo. CA y EN no usan
// el mismo patrón pero damos algo razonable para los 3.
const DATE_CONNECTORS: Record<string, { de: string }> = {
    es: { de: 'de' },
    ca: { de: 'de' },
    en: { de: '' },  // En inglés "Wednesday, 27 May 2026" — sin "of".
};
const EVENT_PALETTE = ['indigo', 'emerald', 'amber', 'rose', 'sky', 'violet'];
const NOTES_STORAGE_KEY = 'acten.calendar.notes.v1';

@Component({
    selector: 'app-calendar',
    standalone: true,
    imports: [CommonModule, FormsModule, RouterModule, TranslateModule],
    templateUrl: './calendar.component.html',
    styleUrls: ['./calendar.component.css'],
})
export class CalendarComponent implements OnInit, OnDestroy {
    // Backend state
    accounts: CalAccount[] = [];
    events: CalEvent[] = [];
    tasks: PendingTask[] = [];
    isSyncing = false;
    isLoading = true;
    isLoadingTasks = false;

    // Layer visibility toggles
    showMeetings = true;
    showTasks = true;
    showCompletedTasks = false;

    /** Pintar tareas de gente que no es del proyecto.
     *
     * En una reunión se reparten encargos a quien esté delante, y a veces
     * esa persona no es del equipo. La tarea es real y no se toca; lo que
     * estorba es verla en el calendario del proyecto. Por defecto se ve:
     * ocultar trabajo pendiente sin avisar es peor que el ruido. */
    showExternalOwners = true;
    private static readonly EXTERNOS_KEY = 'acten.calendario.verExternos';

    // Por solicitud del usuario: ocultar la tarjeta de reuniones del panel derecho.
    // Mantenemos la data + filtros para no romper la grilla.
    readonly showMeetingsInPanel = false;

    // Tarea con foco (al hacer click en una pill del calendario).
    focusedTaskId: number | null = null;
    private focusClearTimer: ReturnType<typeof setTimeout> | null = null;

    // Calendar state
    today = new Date();
    viewYear = this.today.getFullYear();
    viewMonth = this.today.getMonth();
    selected: Date = this.startOfDay(this.today);
    viewMode: ViewMode = 'month';

    // UI state
    showFilters = false;
    showSourcesMenu = false;
    showDetailPanel = true;
    showNewMeetingHint = false;

    // Filters
    filterProvider: 'all' | 'google' | 'microsoft' = 'all';
    filterStatus: 'all' | 'linked' | 'pending' = 'all';
    filterSearch = '';

    // Notes (client-side persistence)
    notes: NoteItem[] = [];
    newNoteText = '';
    showNoteEditor = false;

    /** Etiquetas reactivas al idioma del cliente — usadas por el template
     *  (`*ngFor="let dayName of DAY_LABELS"`). Getter en vez de constante
     *  para que un cambio de idioma sin reload se propague en el próximo
     *  change-detection cycle. */
    get DAY_LABELS(): string[] {
        const lang = (this.lang?.currentLang() || 'es');
        return DAY_LABELS_BY_LANG[lang] || DAY_LABELS_BY_LANG['es'];
    }
    get MONTH_LABELS(): string[] {
        const lang = (this.lang?.currentLang() || 'es');
        return MONTH_LABELS_BY_LANG[lang] || MONTH_LABELS_BY_LANG['es'];
    }
    readonly EVENT_PALETTE = EVENT_PALETTE;

    // ============================================================
    // Estado del modal "Nueva tarea desde calendario"
    // ============================================================
    showNewTaskModal = false;
    isCreatingTask = false;
    newTaskSessions: { id: number; title: string }[] = [];
    newTask = {
        session_id: null as number | null,
        title: '',
        owner_name: '',
        owner_email: '',
        due_date: '',
        due_time: '',
        priority: 'media' as 'alta' | 'media' | 'baja',
        description: '',
    };

    private readonly destroy$ = new Subject<void>();

    constructor(
        private http: HttpClient,
        private auth: AuthService,
        private toast: ToastService,
        private cdr: ChangeDetectorRef,
        private route: ActivatedRoute,
        private userDirectory: UserDirectoryService,
        public lang: LanguageService,
        private translate: TranslateService,
    ) {
        // Re-render del calendario cuando el directorio resuelve nuevos
        // emails (la foto del dueño aparece en cuanto está disponible).
        this.userDirectory.directory$
            .pipe(takeUntil(this.destroy$))
            .subscribe(() => this.cdr.markForCheck());
    }

    /** URL del avatar del User cuyo email coincide con la tarea. null → cae
     *  al avatar de iniciales con tono asignado. */
    taskAvatarUrl(t: any): string | null {
        const email = (t?.owner_email || '').trim().toLowerCase();
        if (!email) return null;
        const u = this.userDirectory.peek(email);
        if (!u || !u.avatar_url) return null;
        const raw = u.avatar_url;
        if (raw.startsWith('http://') || raw.startsWith('https://')) return raw;
        return `${environment.apiUrl}${raw}`;
    }

    ngOnInit(): void {
        try {
            const guardado = localStorage.getItem(CalendarComponent.EXTERNOS_KEY);
            if (guardado !== null) this.showExternalOwners = guardado === '1';
        } catch { /* modo privado */ }
        this.loadNotes();
        this.loadAccounts();
        this.loadEvents();
        this.loadTasks();

        // Si llegamos vía /admin/calendar?new=task (desde Tareas → "Programar
        // en el calendario"), abrimos el modal de nueva tarea automáticamente.
        this.route.queryParamMap.pipe(takeUntil(this.destroy$)).subscribe(qp => {
            if (qp.get('new') === 'task') {
                setTimeout(() => this.openNewTaskForSelectedDay(), 200);
            }
        });
    }

    ngOnDestroy(): void {
        this.destroy$.next();
        this.destroy$.complete();
        if (this.oauthPoll) { clearInterval(this.oauthPoll); this.oauthPoll = null; }
        this.oauthPopup = null;
        if (this.focusClearTimer) { clearTimeout(this.focusClearTimer); this.focusClearTimer = null; }
    }

    // ============================================================
    // Backend operations (preserved)
    // ============================================================
    loadAccounts(): void {
        this.http.get<CalAccount[]>(`${environment.apiUrl}/api/calendar/accounts`,
            { headers: this.auth.getAuthHeaders() })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (data) => { this.accounts = data || []; this.cdr.detectChanges(); },
                error: () => this.toast.error(this.translate.instant('calendar.toast_accounts_error')),
            });
    }

    loadEvents(): void {
        this.isLoading = true;
        this.http.get<{events: CalEvent[]; linked_accounts: number}>(
            `${environment.apiUrl}/api/calendar/upcoming?days=60`,
            { headers: this.auth.getAuthHeaders() })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (res) => {
                    this.events = res.events || [];
                    this.isLoading = false;
                    this.cdr.detectChanges();
                },
                error: () => {
                    this.events = [];
                    this.isLoading = false;
                    this.cdr.detectChanges();
                },
            });
    }

    loadTasks(): void {
        this.isLoadingTasks = true;
        // Pull ALL action items for the tenant. The endpoint already ordered them
        // and includes due_date + bucket classification.
        this.http.get<{items: PendingTask[]; total: number}>(
            `${environment.apiUrl}/api/pendientes?limit=2000`,
            { headers: this.auth.getAuthHeaders() })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (res) => {
                    this.tasks = (res?.items || []).filter(t => isRealDueDate(t.due_date));
                    // Precargamos todos los emails de owners para que la foto
                    // aparezca de inmediato en los pills del calendario.
                    const emails = this.tasks.map(t => (t.owner_email || '').trim()).filter(Boolean);
                    this.userDirectory.preload(emails);
                    this.isLoadingTasks = false;
                    this.cdr.detectChanges();
                },
                error: () => {
                    this.tasks = [];
                    this.isLoadingTasks = false;
                    this.cdr.detectChanges();
                },
            });
    }

    private oauthPopup: Window | null = null;
    private oauthPoll: ReturnType<typeof setInterval> | null = null;

    connect(provider: 'google' | 'microsoft'): void {
        this.showSourcesMenu = false;
        const providerName = provider === 'google'
            ? this.translate.instant('calendar.provider_google')
            : this.translate.instant('calendar.provider_microsoft');
        this.toast.info(this.translate.instant('calendar.toast_opening_oauth', { provider: providerName }));
        this.http.get<{url: string; state: string}>(
            `${environment.apiUrl}/api/calendar/${provider}/auth_url`,
            { headers: this.auth.getAuthHeaders() })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (res) => this.openOauthPopup(res.url, provider),
                error: (err) => {
                    const envvar = provider === 'google' ? 'GOOGLE_OAUTH_CLIENT_ID' : 'MS_OAUTH_CLIENT_ID';
                    const detail = err?.error?.detail
                        || (err?.status === 503
                            ? this.translate.instant('calendar.toast_oauth_unconfigured', { provider: providerName, envvar })
                            : this.translate.instant('calendar.toast_oauth_flow_error', { provider: providerName }));
                    this.toast.error(detail);
                },
            });
    }

    private openOauthPopup(url: string, provider: 'google' | 'microsoft'): void {
        const w = 520, h = 720;
        const left = (window.screen.width - w) / 2;
        const top = (window.screen.height - h) / 2;
        const features = `width=${w},height=${h},left=${left},top=${top},toolbar=0,menubar=0,location=0`;
        const popup = window.open(url, `acten_oauth_${provider}`, features);
        if (!popup) {
            // Popup bloqueado → fallback: redirección directa.
            this.toast.warning(this.translate.instant('calendar.toast_popup_blocked'));
            window.location.href = url;
            return;
        }
        this.oauthPopup = popup;
        // Polling cada 1s: cuando se cierre, refrescamos cuentas + eventos.
        if (this.oauthPoll) clearInterval(this.oauthPoll);
        this.oauthPoll = setInterval(() => {
            if (!this.oauthPopup || this.oauthPopup.closed) {
                if (this.oauthPoll) { clearInterval(this.oauthPoll); this.oauthPoll = null; }
                this.oauthPopup = null;
                // Damos un beat para que el backend grabe la cuenta antes de leer.
                setTimeout(() => {
                    this.loadAccounts();
                    this.loadEvents();
                    const providerName = provider === 'google'
                        ? this.translate.instant('calendar.provider_google')
                        : this.translate.instant('calendar.provider_microsoft');
                    this.toast.success(this.translate.instant('calendar.toast_oauth_connected', { provider: providerName }));
                }, 600);
            }
        }, 1000);
    }

    disconnect(account: CalAccount): void {
        if (!confirm(this.translate.instant('calendar.confirm_disconnect', { email: account.account_email }))) return;
        this.http.delete(`${environment.apiUrl}/api/calendar/accounts/${account.id}`,
            { headers: this.auth.getAuthHeaders() })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: () => { this.toast.success(this.translate.instant('calendar.toast_account_disconnected')); this.loadAccounts(); this.loadEvents(); },
                error: () => this.toast.error(this.translate.instant('calendar.toast_disconnect_error')),
            });
    }

    syncNow(): void {
        if (this.isSyncing || !this.accounts.length) return;
        this.isSyncing = true;
        this.http.post<{events_added: number}>(
            `${environment.apiUrl}/api/calendar/sync`, {},
            { headers: this.auth.getAuthHeaders() })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (res) => {
                    this.isSyncing = false;
                    this.toast.success(this.translate.instant('calendar.toast_sync_success', { count: res.events_added }));
                    this.loadEvents();
                    this.loadTasks();
                },
                error: () => { this.isSyncing = false; this.toast.error(this.translate.instant('calendar.toast_sync_failed')); this.cdr.detectChanges(); },
            });
    }

    // ============================================================
    // Calendar grid generation
    // ============================================================
    get grid(): DayCell[] {
        const first = new Date(this.viewYear, this.viewMonth, 1);
        const startWeekday = first.getDay();
        const start = new Date(this.viewYear, this.viewMonth, 1 - startWeekday);
        const cells: DayCell[] = [];
        const filteredEvents = this.filteredEvents;
        const filteredTasks = this.filteredTasks;
        const today = this.startOfDay(this.today);
        const sel = this.startOfDay(this.selected);

        for (let i = 0; i < 42; i++) {
            const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
            const iso = this.toISO(d);
            const dayEvents = filteredEvents.filter(ev => this.sameDay(new Date(ev.start_at), d));
            const dayTasks = filteredTasks.filter(t => this.taskDateMatches(t, d));
            cells.push({
                date: d,
                iso,
                day: d.getDate(),
                inMonth: d.getMonth() === this.viewMonth,
                isToday: this.sameDay(d, today),
                isSelected: this.sameDay(d, sel),
                weekday: d.getDay(),
                events: dayEvents,
                tasks: dayTasks,
            });
        }
        return cells;
    }

    get weekRows(): DayCell[][] {
        const all = this.grid;
        const rows: DayCell[][] = [];
        for (let i = 0; i < all.length; i += 7) rows.push(all.slice(i, i + 7));
        // Drop trailing all-out-of-month row when 6th row is empty
        if (rows.length === 6 && rows[5].every(c => !c.inMonth)) rows.pop();
        return rows;
    }

    get currentWeek(): DayCell[] {
        const sel = this.startOfDay(this.selected);
        const startOfWeek = new Date(sel);
        startOfWeek.setDate(sel.getDate() - sel.getDay());
        const cells: DayCell[] = [];
        const filteredEvents = this.filteredEvents;
        const filteredTasks = this.filteredTasks;
        const today = this.startOfDay(this.today);
        for (let i = 0; i < 7; i++) {
            const d = new Date(startOfWeek.getFullYear(), startOfWeek.getMonth(), startOfWeek.getDate() + i);
            const dayEvents = filteredEvents.filter(ev => this.sameDay(new Date(ev.start_at), d));
            const dayTasks = filteredTasks.filter(t => this.taskDateMatches(t, d));
            cells.push({
                date: d,
                iso: this.toISO(d),
                day: d.getDate(),
                inMonth: d.getMonth() === this.viewMonth,
                isToday: this.sameDay(d, today),
                isSelected: this.sameDay(d, sel),
                weekday: d.getDay(),
                events: dayEvents,
                tasks: dayTasks,
            });
        }
        return cells;
    }

    get agendaGroups(): { label: string; date: Date; events: CalEvent[]; tasks: PendingTask[] }[] {
        const buckets = new Map<string, { label: string; date: Date; events: CalEvent[]; tasks: PendingTask[] }>();
        const ensure = (d: Date) => {
            const key = this.toISO(d);
            if (!buckets.has(key)) {
                buckets.set(key, { label: this.longDateLabel(d), date: d, events: [], tasks: [] });
            }
            return buckets.get(key)!;
        };
        for (const ev of this.filteredEvents) {
            const d = this.startOfDay(new Date(ev.start_at));
            ensure(d).events.push(ev);
        }
        for (const t of this.filteredTasks) {
            const d = this.parseTaskDue(t);
            if (!d) continue;
            ensure(this.startOfDay(d)).tasks.push(t);
        }
        return Array.from(buckets.values()).sort((a, b) => a.date.getTime() - b.date.getTime());
    }

    // ============================================================
    // Filters / derived data
    // ============================================================
    get filteredEvents(): CalEvent[] {
        if (!this.showMeetings) return [];
        const q = this.filterSearch.trim().toLowerCase();
        return this.events.filter(ev => {
            if (this.filterStatus === 'linked' && !ev.session_id) return false;
            if (this.filterStatus === 'pending' && ev.session_id) return false;
            if (q && !(ev.title || '').toLowerCase().includes(q)) return false;
            return true;
        });
    }

    /** Cuántas tareas desaparecerían al apagar el interruptor. Un filtro
     *  que no dice qué esconde no se usa. */
    get externalOwnersCount(): number {
        return this.tasks.filter(t => t.owner_in_project === false).length;
    }

    toggleExternalOwners(): void {
        this.showExternalOwners = !this.showExternalOwners;
        try {
            localStorage.setItem(
                CalendarComponent.EXTERNOS_KEY,
                this.showExternalOwners ? '1' : '0',
            );
        } catch { /* modo privado: dura la sesión */ }
        this.cdr.detectChanges();
    }

    get filteredTasks(): PendingTask[] {
        if (!this.showTasks) return [];
        const q = this.filterSearch.trim().toLowerCase();
        return this.tasks.filter(t => {
            if (!this.showCompletedTasks && (t.status === 'done' || t.status === 'cancelled')) return false;
            if (!this.showExternalOwners && t.owner_in_project === false) return false;
            if (q) {
                const hay = `${t.title || ''} ${t.owner_name || ''} ${t.project_name || ''}`.toLowerCase();
                if (!hay.includes(q)) return false;
            }
            return true;
        });
    }

    get selectedDayEvents(): CalEvent[] {
        return this.filteredEvents.filter(ev => this.sameDay(new Date(ev.start_at), this.selected));
    }

    get selectedDayTasks(): PendingTask[] {
        return this.filteredTasks.filter(t => this.taskDateMatches(t, this.selected));
    }

    get totalSelectedDayCount(): number {
        return this.selectedDayEvents.length + this.selectedDayTasks.length;
    }

    get deadlines(): DeadlineItem[] {
        const today = this.startOfDay(this.today);
        const horizon = new Date(today);
        horizon.setDate(horizon.getDate() + 14);

        const items: DeadlineItem[] = [];

        // Real action items first — these are the actual "tareas con fechas".
        for (const t of this.tasks) {
            if (t.status === 'done' || t.status === 'cancelled') continue;
            const d = this.parseTaskDue(t);
            if (!d) continue;
            const dueDay = this.startOfDay(d);
            if (dueDay > horizon) continue;
            const isOverdue = dueDay < today;
            const isToday = this.sameDay(dueDay, today);
            items.push({
                id: `task-${t.id}`,
                title: t.title || this.translate.instant('calendar.task_no_title'),
                when: isToday
                    ? this.translate.instant('calendar.deadline_due_today')
                    : isOverdue
                        ? this.translate.instant('calendar.deadline_overdue_at', { date: this.shortDateLabel(d) })
                        : this.shortDateLabel(d),
                urgent: isToday || isOverdue,
            });
        }

        // Reuniones sin acta también aparecen como pendientes secundarios.
        for (const ev of this.filteredEvents) {
            if (ev.session_id) continue;
            const d = new Date(ev.start_at);
            if (isNaN(d.getTime())) continue;
            if (d < today || d > horizon) continue;
            const sameToday = this.sameDay(d, today);
            items.push({
                id: `ev-${ev.id}`,
                title: this.translate.instant('calendar.deadline_acta_pending', { title: ev.title }),
                when: sameToday ? this.translate.instant('calendar.deadline_due_today') : this.shortDateLabel(d),
                urgent: sameToday,
            });
        }

        // Orden: urgentes primero, luego por fecha del campo `when` (orden natural en es-CO sirve).
        items.sort((a, b) => Number(b.urgent) - Number(a.urgent));
        return items.slice(0, 8);
    }

    get monthYearLabel(): string {
        return `${this.MONTH_LABELS[this.viewMonth]} ${this.viewYear}`;
    }

    get selectedDayLabel(): string {
        return this.longDateLabel(this.selected);
    }

    get selectedDayCountLabel(): string {
        const m = this.selectedDayEvents.length;
        const t = this.selectedDayTasks.length;
        if (!m && !t) return this.translate.instant('calendar.selected_day_none');
        const parts: string[] = [];
        if (m) parts.push(m === 1
            ? this.translate.instant('calendar.meetings_one')
            : this.translate.instant('calendar.meetings_other', { count: m }));
        if (t) parts.push(t === 1
            ? this.translate.instant('calendar.tasks_one')
            : this.translate.instant('calendar.tasks_other', { count: t }));
        return parts.join(' · ');
    }

    // ============================================================
    // Task helpers
    // ============================================================
    parseTaskDue(t: PendingTask): Date | null {
        // Sólo `YYYY-MM-DD` (con o sin sufijo horario). `new Date(s)` a secas
        // aceptaba cosas como «Dec 5 2026» o «2026» y las colocaba en el
        // calendario en días inventados.
        return parseLocalDate(toIsoDateOrNull(t?.due_date));
    }

    taskDateMatches(t: PendingTask, d: Date): boolean {
        const due = this.parseTaskDue(t);
        if (!due) return false;
        return this.sameDay(due, d);
    }

    taskKind(t: PendingTask): 'overdue' | 'today' | 'soon' | 'done' | 'cancelled' | 'blocked' | 'planned' {
        if (t.status === 'done') return 'done';
        if (t.status === 'cancelled') return 'cancelled';
        if (t.status === 'blocked') return 'blocked';
        const due = this.parseTaskDue(t);
        if (!due) return 'planned';
        const today = this.startOfDay(this.today);
        const day = this.startOfDay(due);
        if (day < today) return 'overdue';
        if (this.sameDay(day, today)) return 'today';
        const diffDays = Math.round((day.getTime() - today.getTime()) / 86400000);
        if (diffDays <= 7) return 'soon';
        return 'planned';
    }

    taskKindLabel(t: PendingTask): string {
        switch (this.taskKind(t)) {
            case 'overdue': return this.translate.instant('calendar.task_overdue');
            case 'today': return this.translate.instant('calendar.task_due_today');
            case 'soon': return this.translate.instant('calendar.task_soon');
            case 'done': return this.translate.instant('calendar.task_done');
            case 'cancelled': return this.translate.instant('calendar.task_cancelled');
            case 'blocked': return this.translate.instant('calendar.task_blocked');
            default: return this.translate.instant('calendar.task_planned');
        }
    }

    openTask(t: PendingTask, evt: Event): void {
        evt?.stopPropagation?.();
        if (t.session_id) {
            // Lleva al acta donde se originó la tarea.
            window.location.href = `/admin/curation/${t.session_id}`;
        } else {
            window.location.href = `/admin/pendientes`;
        }
    }

    /**
     * Click sobre una pill de tarea dentro de la grilla del mes/semana.
     * No navega — selecciona el día de la tarea y pone foco en su card del panel.
     */
    focusTask(t: PendingTask, evt: Event): void {
        evt?.stopPropagation?.();
        if (!t) return;
        const due = this.parseTaskDue(t);
        if (due) {
            this.selected = this.startOfDay(due);
            this.viewYear = due.getFullYear();
            this.viewMonth = due.getMonth();
        }
        this.showDetailPanel = true;
        this.focusedTaskId = t.id;
        // Scroll dentro del panel hacia la tarea seleccionada después del render.
        setTimeout(() => {
            const el = document.getElementById(`cal-task-${t.id}`);
            if (el && typeof el.scrollIntoView === 'function') {
                el.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
        }, 60);
        if (this.focusClearTimer) clearTimeout(this.focusClearTimer);
        this.focusClearTimer = setTimeout(() => {
            this.focusedTaskId = null;
            this.cdr.detectChanges();
        }, 2200);
    }

    /** Cuántos pills de tareas se renderizan en un día, dado un máximo total de 4. */
    visibleTasks(cell: DayCell): PendingTask[] {
        const MAX_PER_CELL = 4;
        const eventsShown = Math.min(3, cell.events.length);
        const slots = Math.max(0, MAX_PER_CELL - eventsShown);
        return cell.tasks.slice(0, slots);
    }

    /** Cuántos items quedaron fuera (sumando tareas + eventos no mostrados). */
    hiddenCount(cell: DayCell): number {
        const eventsShown = Math.min(3, cell.events.length);
        const tasksShown = this.visibleTasks(cell).length;
        const totalShown = eventsShown + tasksShown;
        return (cell.events.length + cell.tasks.length) - totalShown;
    }

    /** Etiqueta corta para mostrar dentro del pill del día: prioriza dueño. */
    taskOwnerLabel(t: PendingTask): string {
        const fallback = this.translate.instant('calendar.task_default');
        if (!t) return fallback;
        if (t.owner_name && t.owner_name.trim()) return t.owner_name.trim();
        if (t.owner_email && t.owner_email.includes('@')) {
            return t.owner_email.split('@')[0];
        }
        if (t.owner_email && t.owner_email.trim()) return t.owner_email.trim();
        return fallback;
    }

    eventColor(ev: CalEvent): string {
        // Hash por título para distribuir mejor los colores (los IDs consecutivos
        // tendían a caer en sólo 2 cubetas con `id % 6`).
        const key = (ev?.title || '') + ':' + (ev?.id ?? 0);
        let h = 0;
        for (let i = 0; i < key.length; i++) {
            h = (h * 31 + key.charCodeAt(i)) | 0;
        }
        return EVENT_PALETTE[Math.abs(h) % EVENT_PALETTE.length];
    }

    taskColor(t: PendingTask): string {
        // Mapeo determinístico: por estado primero, luego hash del título.
        const k = this.taskKind(t);
        if (k === 'overdue' || k === 'today') return 'rose';
        if (k === 'soon') return 'amber';
        if (k === 'done') return 'emerald';
        if (k === 'blocked') return 'violet';
        const key = (t?.title || '') + ':' + (t?.id ?? 0);
        let h = 0;
        for (let i = 0; i < key.length; i++) {
            h = (h * 31 + key.charCodeAt(i)) | 0;
        }
        return EVENT_PALETTE[Math.abs(h) % EVENT_PALETTE.length];
    }

    ownerInitials(t: PendingTask): string {
        const src = t?.owner_name || t?.owner_email || '';
        return this.eventInitials(src) || '?';
    }

    ownerColor(t: PendingTask): string {
        const key = (t?.owner_email || t?.owner_name || 'X');
        let h = 0;
        for (let i = 0; i < key.length; i++) {
            h = (h * 31 + key.charCodeAt(i)) | 0;
        }
        return EVENT_PALETTE[Math.abs(h) % EVENT_PALETTE.length];
    }

    eventTime(ev: CalEvent): string {
        if (!ev?.start_at) return '';
        const d = new Date(ev.start_at);
        if (isNaN(d.getTime())) return '';
        return d.toLocaleTimeString('es-CO', { hour: '2-digit', minute: '2-digit', hour12: false });
    }

    eventTimeRange(ev: CalEvent): string {
        const a = ev.start_at ? new Date(ev.start_at) : null;
        const b = ev.end_at ? new Date(ev.end_at) : null;
        if (!a) return '';
        const left = a.toLocaleTimeString('es-CO', { hour: '2-digit', minute: '2-digit', hour12: false });
        if (!b) return left;
        const right = b.toLocaleTimeString('es-CO', { hour: '2-digit', minute: '2-digit', hour12: false });
        return `${left} – ${right}`;
    }

    eventLocation(ev: CalEvent): string {
        if (ev.meeting_url) {
            try {
                const host = new URL(ev.meeting_url).hostname.replace('www.', '');
                if (host.includes('zoom')) return this.translate.instant('calendar.event_zoom');
                if (host.includes('meet.google')) return this.translate.instant('calendar.event_google_meet');
                if (host.includes('teams')) return this.translate.instant('calendar.event_teams');
                return host;
            } catch { return this.translate.instant('calendar.event_virtual'); }
        }
        return this.translate.instant('calendar.event_no_link');
    }

    eventStatusLabel(ev: CalEvent): string {
        if (ev.session_id) return this.translate.instant('calendar.event_has_minutes');
        const start = new Date(ev.start_at);
        if (!isNaN(start.getTime()) && start < this.today) return this.translate.instant('calendar.event_missing_minutes');
        return this.translate.instant('calendar.event_scheduled');
    }

    eventStatusKind(ev: CalEvent): 'linked' | 'missing' | 'scheduled' | 'live' {
        if (this.isEventLive(ev)) return 'live';
        if (ev.session_id) return 'linked';
        const start = new Date(ev.start_at);
        if (!isNaN(start.getTime()) && start < this.today) return 'missing';
        return 'scheduled';
    }

    isEventLive(ev: CalEvent): boolean {
        const now = new Date();
        const start = ev.start_at ? new Date(ev.start_at) : null;
        const end = ev.end_at ? new Date(ev.end_at) : null;
        if (!start || isNaN(start.getTime())) return false;
        if (!end || isNaN(end.getTime())) return false;
        return start <= now && now <= end;
    }

    badgeLabel(ev: CalEvent): string {
        if (this.isEventLive(ev)) return this.translate.instant('calendar.event_in_progress');
        if (ev.session_id) return this.translate.instant('calendar.event_has_minutes');
        const start = new Date(ev.start_at);
        if (!isNaN(start.getTime()) && start < this.today) return this.translate.instant('calendar.event_missing_minutes');
        return this.translate.instant('calendar.event_upcoming');
    }

    eventInitials(name: string): string {
        if (!name) return '?';
        const at = name.indexOf('@');
        const base = at > 0 ? name.slice(0, at) : name;
        const parts = base.replace(/[._-]+/g, ' ').trim().split(/\s+/);
        const first = parts[0]?.[0] || '';
        const second = parts[1]?.[0] || '';
        return (first + second).toUpperCase() || base[0].toUpperCase();
    }

    // ============================================================
    // Navigation handlers
    // ============================================================
    prevMonth(): void {
        if (this.viewMode === 'week') {
            const d = new Date(this.selected);
            d.setDate(d.getDate() - 7);
            this.selected = d;
            this.viewYear = d.getFullYear();
            this.viewMonth = d.getMonth();
            return;
        }
        if (this.viewMonth === 0) { this.viewMonth = 11; this.viewYear--; }
        else this.viewMonth--;
    }

    nextMonth(): void {
        if (this.viewMode === 'week') {
            const d = new Date(this.selected);
            d.setDate(d.getDate() + 7);
            this.selected = d;
            this.viewYear = d.getFullYear();
            this.viewMonth = d.getMonth();
            return;
        }
        if (this.viewMonth === 11) { this.viewMonth = 0; this.viewYear++; }
        else this.viewMonth++;
    }

    goToday(): void {
        this.today = new Date();
        this.viewYear = this.today.getFullYear();
        this.viewMonth = this.today.getMonth();
        this.selected = this.startOfDay(this.today);
        this.showDetailPanel = true;
    }

    selectDay(cell: DayCell): void {
        this.selected = this.startOfDay(cell.date);
        if (!cell.inMonth) {
            this.viewYear = cell.date.getFullYear();
            this.viewMonth = cell.date.getMonth();
        }
        this.showDetailPanel = true;
    }

    setViewMode(mode: ViewMode): void {
        this.viewMode = mode;
    }

    toggleFilters(): void { this.showFilters = !this.showFilters; }
    closeDetailPanel(): void { this.showDetailPanel = false; }
    openDetailPanel(): void { this.showDetailPanel = true; }
    toggleSourcesMenu(): void { this.showSourcesMenu = !this.showSourcesMenu; }
    closeSourcesMenu(): void { this.showSourcesMenu = false; }

    @HostListener('document:keydown.escape')
    onEscape(): void {
        if (this.showSourcesMenu) this.showSourcesMenu = false;
        else if (this.showFilters) this.showFilters = false;
    }

    openMeetingLink(ev: CalEvent, evt: Event): void {
        evt.stopPropagation();
        if (ev.meeting_url) window.open(ev.meeting_url, '_blank', 'noopener');
    }

    newMeeting(): void {
        // The backend has no "create event" endpoint; events flow from sync.
        // Surface a discoverable hint that helps the user with the right path.
        this.toast.info(this.translate.instant('calendar.toast_external_calendars'));
    }

    // ============================================================
    // Notes (client-side localStorage)
    // ============================================================
    private loadNotes(): void {
        try {
            const raw = localStorage.getItem(NOTES_STORAGE_KEY);
            const parsed = raw ? JSON.parse(raw) : [];
            this.notes = Array.isArray(parsed) ? parsed : [];
        } catch { this.notes = []; }
    }

    private persistNotes(): void {
        try { localStorage.setItem(NOTES_STORAGE_KEY, JSON.stringify(this.notes)); }
        catch { /* ignore */ }
    }

    openNoteEditor(): void {
        this.showNoteEditor = true;
    }

    cancelNoteEditor(): void {
        this.showNoteEditor = false;
        this.newNoteText = '';
    }

    addNote(): void {
        const text = (this.newNoteText || '').trim();
        if (!text) return;
        const next: NoteItem = {
            id: `n-${Date.now()}`,
            text,
            created_at: new Date().toISOString(),
        };
        this.notes = [next, ...this.notes];
        this.newNoteText = '';
        this.showNoteEditor = false;
        this.persistNotes();
    }

    removeNote(id: string): void {
        this.notes = this.notes.filter(n => n.id !== id);
        this.persistNotes();
    }

    noteDate(n: NoteItem): string {
        const d = n?.created_at ? new Date(n.created_at) : null;
        if (!d || isNaN(d.getTime())) return '—';
        return d.toLocaleDateString('es-CO', { day: '2-digit', month: 'short', year: 'numeric' });
    }

    // ============================================================
    // Date utilities
    // ============================================================
    private startOfDay(d: Date): Date {
        return new Date(d.getFullYear(), d.getMonth(), d.getDate());
    }

    private sameDay(a: Date, b: Date): boolean {
        return a.getFullYear() === b.getFullYear()
            && a.getMonth() === b.getMonth()
            && a.getDate() === b.getDate();
    }

    private toISO(d: Date): string {
        const y = d.getFullYear();
        const m = String(d.getMonth() + 1).padStart(2, '0');
        const dd = String(d.getDate()).padStart(2, '0');
        return `${y}-${m}-${dd}`;
    }

    longDateLabel(d: Date): string {
        // Formato "Miércoles, 27 de mayo de 2026" / "Dimecres, 27 de maig de
        // 2026" / "Wednesday, May 27 2026" según idioma activo. ES/CA usan
        // mes en minúsculas con conector "de"; EN preserva capitalización.
        const lang = (this.lang?.currentLang() || 'es');
        const wd = (DAY_LABELS_FULL_BY_LANG[lang] || DAY_LABELS_FULL_BY_LANG['es'])[d.getDay()];
        const monthsArr = (MONTH_LABELS_BY_LANG[lang] || MONTH_LABELS_BY_LANG['es']);
        if (lang === 'en') {
            return `${wd}, ${monthsArr[d.getMonth()]} ${d.getDate()} ${d.getFullYear()}`;
        }
        const mn = monthsArr[d.getMonth()].toLowerCase();
        const de = (DATE_CONNECTORS[lang] || DATE_CONNECTORS['es']).de;
        return `${wd}, ${d.getDate()} ${de} ${mn} ${de} ${d.getFullYear()}`;
    }

    shortDateLabel(d: Date): string {
        return d.toLocaleDateString('es-CO', { day: '2-digit', month: 'short' });
    }

    trackById(_i: number, item: { id: number | string }): number | string { return item?.id; }
    trackByIso(_i: number, item: DayCell): string { return item.iso; }
    trackByEv(_i: number, ev: CalEvent): number { return ev.id; }

    // ============================================================
    // Modal "Nueva tarea desde calendario"
    // ============================================================

    openNewTaskForSelectedDay(): void {
        const d = this.selected || new Date();
        this.newTask = {
            session_id: null,
            title: '',
            owner_name: '',
            owner_email: '',
            due_date: this.toISO(d),
            due_time: '',
            priority: 'media',
            description: '',
        };
        this.showNewTaskModal = true;
        if (!this.newTaskSessions.length) this.loadSessionsForTask();
        this.cdr.detectChanges();
    }

    closeNewTaskModal(): void {
        this.showNewTaskModal = false;
        this.cdr.detectChanges();
    }

    private loadSessionsForTask(): void {
        const headers = this.auth.getAuthHeaders();
        this.http.get<any>(`${environment.apiUrl}/api/sessions/?page=1&limit=100`, { headers })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (data) => {
                    const items = (data?.items ?? data) || [];
                    this.newTaskSessions = items.map((s: any) => ({
                        id: s.id, title: s.title || `Sesión #${s.id}`,
                    }));
                    this.cdr.detectChanges();
                },
                error: () => { /* opcional */ }
            });
    }

    submitNewTask(): void {
        if (!this.newTask.session_id) {
            this.toast.warning(this.translate.instant('calendar.toast_pick_meeting'));
            return;
        }
        if (!this.newTask.title.trim()) {
            this.toast.warning(this.translate.instant('calendar.toast_task_needs_title'));
            return;
        }
        this.isCreatingTask = true;
        const headers = this.auth.getAuthHeaders();
        const form = new FormData();
        form.append('title', this.newTask.title.trim());
        form.append('owner_name', this.newTask.owner_name.trim());
        form.append('owner_email', this.newTask.owner_email.trim());
        form.append('due_date', this.newTask.due_date);
        form.append('due_time', this.newTask.due_time);
        form.append('priority', this.newTask.priority || 'media');
        form.append('description', this.newTask.description.trim());

        this.http.post<any>(
            `${environment.apiUrl}/api/sessions/${this.newTask.session_id}/action_items`,
            form, { headers }
        ).pipe(takeUntil(this.destroy$)).subscribe({
            next: () => {
                this.isCreatingTask = false;
                this.toast.success(this.translate.instant('calendar.toast_task_created'));
                this.showNewTaskModal = false;
                this.loadTasks();
                this.cdr.detectChanges();
            },
            error: (err) => {
                this.isCreatingTask = false;
                const msg = err?.error?.detail || this.translate.instant('calendar.toast_task_create_error');
                this.toast.error(msg);
                this.cdr.detectChanges();
            },
        });
    }
}
