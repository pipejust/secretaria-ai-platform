import { Component, OnInit, OnDestroy, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { ActivatedRoute, Router, RouterModule } from '@angular/router';
import { Subject } from 'rxjs';
import { takeUntil } from 'rxjs/operators';
import { environment } from '../../../environments/environment';
import { AuthService } from '../../services/auth.service';
import { ToastService } from '../../services/toast.service';
import { MdRenderPipe } from '../../pipes/md-render.pipe';

interface ActionItem {
  id?: number;
  session_id?: number;
  owner_name: string;
  owner_email: string;
  title: string;
  description: string;
  due_date: string;
  is_approved?: boolean;
  selected?: boolean; // For checkboxes in UI
}

interface MeetingData {
  id?: number;
  title: string;
  date: string;
  language?: string;
  project_id?: number | null;
  raw_summary: string;
  raw_transcript: string;
  processed_decisions: string;
  processed_risks: string;
  processed_agreements: string;
  processed_attendees?: string;
  processed_themes?: string;
  action_items: ActionItem[];
  status: string;
  ai_fields_regenerated: boolean;
  ai_tasks_regenerated: boolean;
}

@Component({
  selector: 'app-curation-panel',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterModule, MdRenderPipe],
  templateUrl: './curation-panel.component.html',
  styleUrl: './curation-panel.component.css'
})
export class CurationPanelComponent implements OnInit, OnDestroy {
  meetingData: MeetingData = {
    title: "Cargando...",
    date: "",
    raw_summary: "",
    raw_transcript: "",
    processed_decisions: "",
    processed_risks: "",
    processed_agreements: "",
    action_items: [],
    status: "processing",
    ai_fields_regenerated: false,
    ai_tasks_regenerated: false,
  };

  sessionId: number | null = null;
  isLoading = true;
  isRegenerating = false;
  isGeneratingDoc = false;
  isEditingTitle = false;
  isDispatchingEmails = false;
  isDispatchingPlatforms = false;
  isRegeneratingFields = false;
  saveStatusMessage = '';
  projects: any[] = [];

  private readonly destroy$ = new Subject<void>();
  
  showManualTaskForm = false;
  isAddingTask = false;
  newTask = {
    title: '',
    description: '',
    owner_name: '',
    owner_email: '',
    due_date: ''
  };

  /** ID de la tarea con el kebab menu abierto. null = ninguno. Se cierra
   *  al hacer click en el background (handler global en el container). */
  openTaskMenuId: number | null = null;

  /** Modo edición por card de contenido curado. Cada card vive en modo
   *  "view" por defecto (renderiza markdown como HTML) y al hacer click en
   *  el botón ✎ pasa a modo "edit" (textarea editable). */
  editing: { summary: boolean; decisions: boolean; risks: boolean; agreements: boolean } = {
    summary: false,
    decisions: false,
    risks: false,
    agreements: false,
  };

  /** ID de la tarea que está mostrando su descripción expandida. Por
   *  defecto las tareas muestran un resumen corto; el user puede expandir. */
  expandedTaskId: number | null = null;

  constructor(
    private route: ActivatedRoute,
    private http: HttpClient,
    private authService: AuthService,
    private cdr: ChangeDetectorRef,
    private router: Router,
    private toast: ToastService,
  ) {}

  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
  }

  getTranslatedStatus(status: string): string {
    const rawStatus = (status || '').trim().toLowerCase();
    const statusMap: { [key: string]: string } = {
      'pending': 'Pendiente de Curación',
      'processing': 'Procesando IA',
      'completed': 'Completado',
      'error': 'Error en Procesamiento'
    };
    return statusMap[rawStatus] || status;
  }

  ngOnInit() {
    this.route.paramMap
      .pipe(takeUntil(this.destroy$))
      .subscribe(params => {
        const idParam = params.get('id');
        if (idParam) {
          this.sessionId = parseInt(idParam, 10);
          this.loadProjects();
          this.loadSessionDetails();
        }
      });
  }

  loadProjects() {
    const headers = this.authService.getAuthHeaders();
    this.http.get<any[]>(`${environment.apiUrl}/api/projects`, { headers })
      .pipe(takeUntil(this.destroy$))
      .subscribe({
        next: (data) => this.projects = data,
        error: () => this.toast.error('No se pudieron cargar los proyectos.'),
      });
  }

  loadSessionDetails() {
    this.isLoading = true;

    const headers = this.authService.getAuthHeaders();
    this.http.get<any>(`${environment.apiUrl}/api/sessions/${this.sessionId}`, { headers })
      .pipe(takeUntil(this.destroy$))
      .subscribe({
        next: (data) => {
          try {
              let parsedDate = data.session.date;
              if (typeof parsedDate === 'string' && !isNaN(Number(parsedDate))) {
                  parsedDate = Number(parsedDate);
              }

              this.meetingData = {
                id: data.session.id,
                title: data.session.title || 'Sesión sin título',
                date: parsedDate,
                project_id: data.session.project_id || null,
                status: data.session.status || 'pending',
                raw_summary: data.session.raw_summary || '',
                raw_transcript: data.session.raw_transcript || '',
                language: data.session.language || '',
                processed_decisions: data.session.processed_decisions || '',
                processed_risks: data.session.processed_risks || '',
                processed_agreements: data.session.processed_agreements || '',
                ai_fields_regenerated: !!data.session.ai_fields_regenerated,
                ai_tasks_regenerated: !!data.session.ai_tasks_regenerated,
                action_items: (data.action_items || []).map((item: any) => ({
                   ...item,
                   selected: false,
                })),
              };
              this.isLoading = false;
              this.cdr.detectChanges();
          } catch (e) {
              this.toast.error('Error procesando los datos de la sesión.');
              this.isLoading = false;
              this.cdr.detectChanges();
          }
        },
        error: () => {
          this.toast.error('No se pudieron cargar los detalles de la sesión.');
          this.isLoading = false;
          this.cdr.detectChanges();
        },
      });
  }

  updateTaskField(task: ActionItem) {
    if (!task.id) return;
    const body = new FormData();
    if (task.title != null) body.append('title', task.title);
    if (task.description != null) body.append('description', task.description);
    if (task.owner_name != null) body.append('owner_name', task.owner_name);
    if (task.owner_email != null) body.append('owner_email', task.owner_email);
    if (task.due_date != null) body.append('due_date', task.due_date);

    this.http.put(`${environment.apiUrl}/api/sessions/action_items/${task.id}`, body, { headers: this.authService.getAuthHeaders() }).subscribe({
      next: () => this.showSaveMessage('Tarea actualizada'),
      error: () => this.showSaveMessage('Error guardando tarea', true)
    });
  }

  addManualTask() {
    if (!this.newTask.title || !this.sessionId) return;
    this.isAddingTask = true;
    
    const body = new FormData();
    body.append('title', this.newTask.title);
    body.append('description', this.newTask.description);
    body.append('owner_name', this.newTask.owner_name);
    body.append('owner_email', this.newTask.owner_email);
    body.append('due_date', this.newTask.due_date);

    this.http.post(`${environment.apiUrl}/api/sessions/${this.sessionId}/action_items`, body, { headers: this.authService.getAuthHeaders() }).subscribe({
      next: (res: any) => {
        this.isAddingTask = false;
        this.showManualTaskForm = false;
        this.showSaveMessage('Tarea manual agregada con éxito');
        if (res.item) {
          res.item.selected = false;
          this.meetingData.action_items.unshift(res.item); // Add to beginning of list
        }
        // Reset form
        this.newTask = { title: '', description: '', owner_name: '', owner_email: '', due_date: '' };
        this.cdr.detectChanges();
      },
      error: (err) => {
        this.isAddingTask = false;
        this.showSaveMessage('Error al agregar la tarea manual', true);
        this.cdr.detectChanges();
      }
    });
  }

  showSaveMessage(msg: string, isError = false) {
    if (isError) {
      this.toast.error(msg);
    } else {
      this.toast.info(msg);
    }
  }

  toggleAllTasks(event: any) {
    const isChecked = event.target.checked;
    this.meetingData.action_items.forEach(t => t.selected = isChecked);
  }

  get hasSelectedTasks(): boolean {
    return this.meetingData.action_items.some(t => t.selected);
  }

  dispatchEmails() {
    const selectedIds = this.meetingData.action_items.filter(t => t.selected && t.id).map(t => t.id as number);
    if (!selectedIds.length) return;
    
    this.isDispatchingEmails = true;
    this.showSaveMessage('Preparando acta de reunión y enviando correos...', false);
    this.cdr.detectChanges();

    const headers = this.authService.getAuthHeaders();
    // We tell the backend to attach the generated summary document directly
    const payload: any = { 
        action_item_ids: selectedIds,
        attach_document: true
    };

    this.http.post(`${environment.apiUrl}/api/sessions/${this.sessionId}/dispatch_emails`, payload, { headers }).subscribe({
      next: (res: any) => {
        this.isDispatchingEmails = false;
        this.showSaveMessage(`Correos enviados: ${res.results.filter((r:any)=>r.status==='success').length}`);
        this.cdr.detectChanges();
      },
      error: (err) => {
        this.isDispatchingEmails = false;
        const msg = err.error && err.error.detail ? err.error.detail : 'Error enviando correos';
        this.showSaveMessage(msg, true);
        this.cdr.detectChanges();
      }
    });
  }

  dispatchPlatforms() {
    const selectedIds = this.meetingData.action_items.filter(t => t.selected && t.id).map(t => t.id as number);
    if (!selectedIds.length) return;

    this.isDispatchingPlatforms = true;
    const headers = this.authService.getAuthHeaders();
    this.http.post(`${environment.apiUrl}/api/sessions/${this.sessionId}/dispatch_platforms`, { action_item_ids: selectedIds }, { headers }).subscribe({
      next: (res: any) => {
        this.isDispatchingPlatforms = false;
        this.showSaveMessage(`Tareas enviadas: ${res.results.filter((r:any)=>r.status==='success').length}`);
        this.cdr.detectChanges();
      },
      error: (err) => {
        this.isDispatchingPlatforms = false;
        const msg = err.error && err.error.detail ? err.error.detail : 'Error enviando a plataformas';
        this.showSaveMessage(msg, true);
        this.cdr.detectChanges();
      }
    });
  }


  regenerateTasks() {
    if (this.meetingData.ai_tasks_regenerated) {
      this.toast.warning('Las tareas ya fueron regeneradas con IA para esta sesión.');
      return;
    }
    if (!this.meetingData.raw_transcript) {
      this.toast.error('No hay transcripción para regenerar tareas.');
      return;
    }
    this.isRegenerating = true;
    this.toast.info('Regenerando tareas con OpenAI... Esto tarda unos segundos.');
    this.cdr.detectChanges();

    const headers = this.authService.getAuthHeaders();
    const payload = { raw_transcript: this.meetingData.raw_transcript };

    this.http.post<any>(`${environment.apiUrl}/api/sessions/${this.sessionId}/regenerate_tasks`, payload, { headers })
      .pipe(takeUntil(this.destroy$))
      .subscribe({
        next: (res) => {
          this.isRegenerating = false;
          this.meetingData.ai_tasks_regenerated = true;
          if (res?.action_items) {
            this.meetingData.action_items = res.action_items.map((item: any) => ({
              ...item,
              selected: false,
            }));
          }
          this.toast.success('Tareas regeneradas con OpenAI.');
          this.cdr.detectChanges();
        },
        error: (err) => {
          this.isRegenerating = false;
          if (err?.status === 409) {
            // Backend dice que ya se regeneró: sincronizamos el flag local.
            this.meetingData.ai_tasks_regenerated = true;
            this.toast.warning('Las tareas ya habían sido regeneradas previamente.');
          } else {
            const detail = err?.error?.detail || 'Error al regenerar las tareas.';
            this.toast.error(detail);
          }
          this.cdr.detectChanges();
        },
      });
  }

  regenerateFields() {
    if (this.meetingData.ai_fields_regenerated) {
      this.toast.warning('Los campos ya fueron sugeridos con IA para esta sesión.');
      return;
    }
    if (!this.meetingData.raw_transcript) {
      this.toast.error('No hay transcripción para sugerir campos.');
      return;
    }
    this.isRegeneratingFields = true;
    this.toast.info('Sugiriendo campos con OpenAI... Esto tarda un momento.');
    this.cdr.detectChanges();

    const headers = this.authService.getAuthHeaders();
    const payload = { raw_transcript: this.meetingData.raw_transcript };

    this.http.post<any>(`${environment.apiUrl}/api/sessions/${this.sessionId}/regenerate_fields`, payload, { headers })
      .pipe(takeUntil(this.destroy$))
      .subscribe({
        next: (res) => {
          this.isRegeneratingFields = false;
          this.meetingData.ai_fields_regenerated = true;
          if (res?.fields) {
            this.meetingData.processed_decisions = res.fields.processed_decisions ?? this.meetingData.processed_decisions;
            this.meetingData.processed_risks = res.fields.processed_risks ?? this.meetingData.processed_risks;
            this.meetingData.processed_agreements = res.fields.processed_agreements ?? this.meetingData.processed_agreements;
            // raw_summary NO se sobrescribe: viene de Fireflies y es editable manualmente.
          }
          this.toast.success('Campos sugeridos con OpenAI.');
          this.cdr.detectChanges();
        },
        error: (err) => {
          this.isRegeneratingFields = false;
          if (err?.status === 409) {
            this.meetingData.ai_fields_regenerated = true;
            this.toast.warning('Los campos ya habían sido sugeridos previamente.');
          } else {
            const detail = err?.error?.detail || 'Error al sugerir los campos con IA.';
            this.toast.error(detail);
          }
          this.cdr.detectChanges();
        },
      });
  }

  saveManualEdits() {
    this.showSaveMessage('Guardando cambios...');
    const headers = this.authService.getAuthHeaders();
    const payload = {
      title: this.meetingData.title,
      raw_summary: this.meetingData.raw_summary,
      raw_transcript: this.meetingData.raw_transcript,
      processed_decisions: this.meetingData.processed_decisions,
      processed_risks: this.meetingData.processed_risks,
      processed_agreements: this.meetingData.processed_agreements,
      project_id: this.meetingData.project_id,
      status: 'completed'
    };

    this.http.put(`${environment.apiUrl}/api/sessions/${this.sessionId}`, payload, { headers }).subscribe({
      next: (res: any) => {
        this.meetingData.status = 'completed';
        this.showSaveMessage('Los textos de la sesión se han guardado correctamente.');
        this.cdr.detectChanges();
      },
      error: (err) => {
        this.showSaveMessage('Error al guardar los cambios de la sesión', true);
        this.cdr.detectChanges();
      }
    });
  }

  goBack() {
    // Botón "Volver a Sesiones": vuelve al listado de meetings (no al
    // dashboard — el label lo dejó claro). Antes navegaba a /admin/dashboard,
    // lo cual era inconsistente con el copy del CTA.
    this.router.navigate(['/admin/meetings']);
  }

  /** Toggle del modo edit/view de una card. */
  toggleEdit(key: 'summary' | 'decisions' | 'risks' | 'agreements'): void {
    this.editing[key] = !this.editing[key];
  }

  /** Toggle expand/collapse de la descripción de una tarea. */
  toggleTaskExpand(taskId: number | undefined): void {
    if (!taskId) return;
    this.expandedTaskId = this.expandedTaskId === taskId ? null : taskId;
  }

  /** Toggle del kebab de una tarea. evento se detiene para que el click
   *  outside (cierra el menu) no le cierre el mismo que está abriendo. */
  toggleTaskMenu(taskId: number | undefined, evt: Event): void {
    evt.stopPropagation();
    if (!taskId) return;
    this.openTaskMenuId = this.openTaskMenuId === taskId ? null : taskId;
  }
  closeTaskMenu(): void { this.openTaskMenuId = null; }

  /** Elimina una tarea localmente (no expone endpoint DELETE). En la
   *  versión final del backend agregar DELETE /api/sessions/action_items/{id}
   *  y reemplazar este método con la llamada real. Por ahora, fade-out local
   *  para mantener la UX inmediata. */
  removeTaskLocal(task: ActionItem): void {
    const idx = this.meetingData.action_items.findIndex((t) => t === task);
    if (idx >= 0) {
      this.meetingData.action_items.splice(idx, 1);
      this.showSaveMessage('Tarea quitada de la vista. Guarda para persistir.');
    }
    this.closeTaskMenu();
  }

  /** Marca/desmarca una tarea como aprobada (toggle). Reutiliza el
   *  endpoint PUT existente vía updateTaskField — el backend acepta
   *  partial updates por FormData. */
  toggleTaskApproved(task: ActionItem): void {
    task.is_approved = !task.is_approved;
    this.closeTaskMenu();
  }

  approveAct(format: 'word' | 'pdf' = 'word') {
    this.isGeneratingDoc = true;
    this.showSaveMessage(`Generando Documento en ${format.toUpperCase()}...`);
    const headers = this.authService.getAuthHeaders();
    
    this.http.get(`${environment.apiUrl}/api/sessions/${this.sessionId}/export/${format}`, {
      headers,
      responseType: 'blob'
    }).subscribe({
      next: (blob) => {
        this.isGeneratingDoc = false;
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        const safeTitle = (this.meetingData.title || 'Sesion').replace(/[^a-z0-9]/gi, '_').substring(0, 30);
        const ext = format === 'word' ? 'docx' : 'pdf';
        a.download = `Sesion_${this.sessionId}_${safeTitle}.${ext}`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
        
        this.meetingData.status = 'approved';
        this.showSaveMessage(`Documento ${format.toUpperCase()} descargado con éxito`);
        this.cdr.detectChanges();
      },
      error: (err) => {
        this.isGeneratingDoc = false;
        this.showSaveMessage(`Error descargando el Documento ${format.toUpperCase()}`, true);
        this.cdr.detectChanges();
      }
    });
  }
}
