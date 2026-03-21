import { Component, OnInit, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { ActivatedRoute, Router } from '@angular/router';
import { environment } from '../../../environments/environment';
import { AuthService } from '../../services/auth.service';

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
}

@Component({
  selector: 'app-curation-panel',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './curation-panel.component.html',
  styleUrl: './curation-panel.component.css'
})
export class CurationPanelComponent implements OnInit {
  meetingData: MeetingData = {
    title: "Cargando...",
    date: "",
    raw_summary: "",
    raw_transcript: "",
    processed_decisions: "",
    processed_risks: "",
    processed_agreements: "",
    action_items: [],
    status: "processing"
  };

  sessionId: number | null = null;
  isLoading = true;
  isRegenerating = false;
  isGeneratingDoc = false;
  isEditingTitle = false;
  isDispatchingEmails = false;
  isDispatchingPlatforms = false;
  isFetchingSummary = false;
  isRegeneratingFields = false;
  saveStatusMessage = '';
  projects: any[] = [];
  
  showManualTaskForm = false;
  isAddingTask = false;
  newTask = {
    title: '',
    description: '',
    owner_name: '',
    owner_email: '',
    due_date: ''
  };

  constructor(
    private route: ActivatedRoute,
    private http: HttpClient,
    private authService: AuthService,
    private cdr: ChangeDetectorRef,
    private router: Router
  ) {}

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
    this.route.paramMap.subscribe(params => {
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
    this.http.get<any[]>(`${environment.apiUrl}/api/projects`, { headers }).subscribe({
      next: (data) => this.projects = data,
      error: (err) => console.error('Error loading projects', err)
    });
  }

  loadSessionDetails() {
    this.isLoading = true;
    
    // Pass the explicit getAuthHeaders just in case the Interceptor doesn't catch standalone requests.
    const headers = this.authService.getAuthHeaders();
    
    this.http.get<any>(`${environment.apiUrl}/api/sessions/${this.sessionId}`, { headers }).subscribe({
      next: (data) => {
        try {
            console.log("Curation data received:", data);
            
            // Safe date parsing to avoid InvalidPipeArgument
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
              // Ensure action_items is always an array
              action_items: (data.action_items || []).map((item: any) => ({
                 ...item,
                 selected: false // Initialize checkbox state
              }))
            };
            this.isLoading = false;
            this.cdr.detectChanges();
        } catch (e) {
            console.error('Data parsing error', e);
            this.isLoading = false;
            this.cdr.detectChanges();
        }
      },
      error: (err) => {
        console.error('Error loading session details', err);
        this.isLoading = false;
        this.cdr.detectChanges();
      }
    });
  }

  updateTaskField(task: ActionItem) {
    if (!task.id) return;
    const body = new FormData();
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
        console.error('Error adding manual task', err);
        this.showSaveMessage('Error al agregar la tarea manual', true);
        this.cdr.detectChanges();
      }
    });
  }

  showSaveMessage(msg: string, isError = false) {
    this.saveStatusMessage = msg;
    setTimeout(() => this.saveStatusMessage = '', 3000);
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
    if (!this.meetingData.raw_transcript) {
      this.showSaveMessage('No hay transcripción para regenerar tareas.', true);
      this.cdr.detectChanges();
      return;
    }
    this.isRegenerating = true;
    this.showSaveMessage('Regenerando tareas con LLaMA... Esto puede tardar unos segundos.');
    this.cdr.detectChanges();
    
    const headers = this.authService.getAuthHeaders();
    const payload = { raw_transcript: this.meetingData.raw_transcript };
    
    this.http.post(`${environment.apiUrl}/api/sessions/${this.sessionId}/regenerate_tasks`, payload, { headers }).subscribe({
      next: (res: any) => {
        this.isRegenerating = false;
        this.showSaveMessage('Tareas regeneradas correctamente.');
        if (res.action_items) {
          // Aseguramos que tengan el selected map
          this.meetingData.action_items = res.action_items.map((item: any) => ({
                 ...item,
                 selected: false
              }));
        }
        this.cdr.detectChanges();
      },
      error: (err) => {
        this.isRegenerating = false;
        console.error('Error regenerating tasks', err);
        this.showSaveMessage('Error al regenerar las tareas.', true);
        this.cdr.detectChanges();
      }
    });
  }

  regenerateFields() {
    if (!this.meetingData.raw_transcript) {
      this.showSaveMessage('No hay transcripción para sugerir campos.', true);
      this.cdr.detectChanges();
      return;
    }
    this.isRegeneratingFields = true;
    this.showSaveMessage('Regenerando campos con Inteligencia Artificial... Esto tarda un momento.');
    this.cdr.detectChanges();
    
    const headers = this.authService.getAuthHeaders();
    const payload = { raw_transcript: this.meetingData.raw_transcript };
    
    this.http.post(`${environment.apiUrl}/api/sessions/${this.sessionId}/regenerate_fields`, payload, { headers }).subscribe({
      next: (res: any) => {
        this.isRegeneratingFields = false;
        this.showSaveMessage('Campos regenerados correctamente.');
        if (res.fields) {
          this.meetingData.processed_decisions = res.fields.processed_decisions;
          this.meetingData.processed_risks = res.fields.processed_risks;
          this.meetingData.processed_agreements = res.fields.processed_agreements;
        }
        this.cdr.detectChanges();
      },
      error: (err) => {
        this.isRegeneratingFields = false;
        console.error('Error regenerating fields', err);
        this.showSaveMessage('Error al sugerir los campos con IA.', true);
        this.cdr.detectChanges();
      }
    });
  }

  fetchSummaryFromAPI() {
    this.isFetchingSummary = true;
    this.showSaveMessage('Obteniendo resumen ejecutivo original...');
    this.cdr.detectChanges();
    
    const headers = this.authService.getAuthHeaders();
    this.http.post(`${environment.apiUrl}/api/sessions/${this.sessionId}/fetch_summary`, {}, { headers }).subscribe({
      next: (res: any) => {
        this.isFetchingSummary = false;
        if (res.summary) {
          this.meetingData.raw_summary = res.summary;
          this.showSaveMessage('Resumen ejecutivo recuperado exitosamente.');
        } else {
          this.showSaveMessage('No se detectó resumen ejecutivo disponible.', true);
        }
        this.cdr.detectChanges();
      },
      error: (err) => {
        this.isFetchingSummary = false;
        console.error('Error fetching executive summary', err);
        const detailMessage = err.error && err.error.detail ? err.error.detail : 'Error al obtener resumen ejecutivo desde el API.';
        this.showSaveMessage(detailMessage, true);
        this.cdr.detectChanges();
      }
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
        console.error('Error saving manual edits', err);
        this.showSaveMessage('Error al guardar los cambios de la sesión', true);
        this.cdr.detectChanges();
      }
    });
  }

  goBack() {
    this.router.navigate(['/admin/dashboard']);
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
        console.error(`Error downloading ${format}`, err);
        this.showSaveMessage(`Error descargando el Documento ${format.toUpperCase()}`, true);
        this.cdr.detectChanges();
      }
    });
  }
}
