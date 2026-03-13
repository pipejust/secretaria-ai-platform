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
    processed_decisions: "",
    processed_risks: "",
    processed_agreements: "",
    action_items: [],
    status: "processing"
  };

  sessionId: number | null = null;
  isLoading = true;
  isDispatchingEmails = false;
  isDispatchingPlatforms = false;
  saveStatusMessage = '';

  constructor(
    private http: HttpClient,
    private route: ActivatedRoute,
    private router: Router,
    private authService: AuthService,
    private cdr: ChangeDetectorRef
  ) {}

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
            this.meetingData = {
              id: data.session.id,
              title: data.session.title,
              date: data.session.date,
              status: data.session.status,
              raw_summary: data.session.raw_summary || '',
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

  approveAct() {
    // Generate document functionality (placeholder / outside epic scope or call specific endpoint)
    this.showSaveMessage('Funcionalidad de generar Word en desarrollo...');
  }
}
