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
    timestamp: number;
}

@Component({
    selector: 'app-ask',
    standalone: true,
    imports: [CommonModule, FormsModule, RouterModule],
    templateUrl: './ask.component.html',
    styleUrls: ['./ask.component.css'],
})
export class AskComponent implements OnInit, OnDestroy {
    question = '';
    history: ChatTurn[] = [];
    isAsking = false;
    projects: Array<{ id: number; name: string }> = [];
    projectId: number | null = null;
    private readonly destroy$ = new Subject<void>();

    constructor(
        private http: HttpClient,
        private authService: AuthService,
        private toast: ToastService,
        private cdr: ChangeDetectorRef,
    ) {}

    ngOnInit(): void {
        this.loadProjects();
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
                    const msg = err?.error?.detail || 'Error consultando a Notiva.';
                    this.toast.error(msg);
                    this.cdr.detectChanges();
                },
            });
    }

    trackByTurn(_i: number, t: ChatTurn): number {
        return t.timestamp;
    }

    trackByCitation(_i: number, c: Citation): string {
        return `${c.session_id}-${c.kind}`;
    }
}
