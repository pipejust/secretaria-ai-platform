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
  raw_summary: string;
  raw_transcript: string;
  processed_decisions: string;
  processed_risks: string;
  processed_agreements: string;
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
  isDispatchingEmails = false;
  isDispatchingPlatforms = false;
  isRegeneratingFields = false;
  saveStatusMessage = '';

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
        this.loadSessionDetails();
      }
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
              status: data.session.status || 'pending',
              raw_summary: data.session.raw_summary || '',
              raw_transcript: data.session.raw_transcript || '',
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

  updateEmail(task: ActionItem) {
    if (!task.id) return;
    const body = new FormData();
    body.append('owner_email', task.owner_email);

    this.http.put(`${environment.apiUrl}/api/sessions/action_items/${task.id}`, body, { headers: this.authService.getAuthHeaders() }).subscribe({
      next: () => this.showSaveMessage('Email actualizado'),
      error: () => this.showSaveMessage('Error guardando email', true)
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
    const headers = this.authService.getAuthHeaders();
    this.http.post(`${environment.apiUrl}/api/sessions/${this.sessionId}/dispatch_emails`, { action_item_ids: selectedIds }, { headers }).subscribe({
      next: (res: any) => {
        this.isDispatchingEmails = false;
        this.showSaveMessage(`Correos enviados: ${res.results.filter((r:any)=>r.status==='success').length}`);
      },
      error: (err) => {
        this.isDispatchingEmails = false;
        this.showSaveMessage('Error enviando correos', true);
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
      },
      error: (err) => {
        this.isDispatchingPlatforms = false;
        this.showSaveMessage('Error enviando a plataformas', true);
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
    
    this.http.post(`${environment.apiUrl}/api/sessions/${this.sessionId}/regenerate_tasks`, {}, { headers }).subscribe({
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
    
    this.http.post(`${environment.apiUrl}/api/sessions/${this.sessionId}/regenerate_fields`, {}, { headers }).subscribe({
      next: (res: any) => {
        this.isRegeneratingFields = false;
        this.showSaveMessage('Campos regenerados correctamente.');
        if (res.fields) {
          this.meetingData.raw_summary = res.fields.raw_summary;
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

  saveManualEdits() {
    this.showSaveMessage('Guardando cambios...');
    const headers = this.authService.getAuthHeaders();
    const payload = {
      raw_summary: this.meetingData.raw_summary,
      raw_transcript: this.meetingData.raw_transcript,
      processed_decisions: this.meetingData.processed_decisions,
      processed_risks: this.meetingData.processed_risks,
      processed_agreements: this.meetingData.processed_agreements,
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

  approveAct() {
    this.showSaveMessage('Generando Documento en Word...');
    const headers = this.authService.getAuthHeaders();
    
    this.http.get(`${environment.apiUrl}/api/sessions/${this.sessionId}/export/word`, {
      headers,
      responseType: 'blob'
    }).subscribe({
      next: (blob) => {
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        const safeTitle = (this.meetingData.title || 'Sesion').replace(/[^a-z0-9]/gi, '_').substring(0, 30);
        a.download = `Sesion_${this.sessionId}_${safeTitle}.docx`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
        
        this.meetingData.status = 'approved';
        this.showSaveMessage('Documento descargado con éxito');
        this.cdr.detectChanges();
      },
      error: (err) => {
        console.error('Error downloading word', err);
        this.showSaveMessage('Error descargando el Documento', true);
      }
    });
  }
}
