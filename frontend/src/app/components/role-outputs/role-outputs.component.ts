import { Component, OnInit, OnDestroy, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterModule } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { Subject } from 'rxjs';
import { takeUntil } from 'rxjs/operators';
import { AuthService } from '../../services/auth.service';
import { ToastService } from '../../services/toast.service';
import { environment } from '../../../environments/environment';
import { TranslateModule, TranslateService } from '@ngx-translate/core';

interface Template { id: number; name: string; role_type: string; output_format: string; }
interface SessionOutput { id: number; title: string; body: string; output_format: string; template_id: number; created_at: string; }

@Component({
    selector: 'app-role-outputs',
    standalone: true,
    imports: [CommonModule, FormsModule, RouterModule, TranslateModule],
    templateUrl: './role-outputs.component.html',
    styleUrls: ['./role-outputs.component.css'],
})
export class RoleOutputsComponent implements OnInit, OnDestroy {
    sessionId: number | null = null;
    templates: Template[] = [];
    outputs: SessionOutput[] = [];
    selectedTemplateId: number | null = null;
    provider: 'openai' | 'groq' = 'openai';
    isGenerating = false;
    private readonly destroy$ = new Subject<void>();

    constructor(
        private route: ActivatedRoute,
        private http: HttpClient,
        private auth: AuthService,
        private toast: ToastService,
        private cdr: ChangeDetectorRef,
        private translate: TranslateService,
    ) {}

    ngOnInit(): void {
        this.route.paramMap.pipe(takeUntil(this.destroy$)).subscribe(p => {
            const id = p.get('id');
            if (id) {
                this.sessionId = +id;
                this.loadTemplates();
                this.loadOutputs();
            }
        });
    }

    ngOnDestroy(): void { this.destroy$.next(); this.destroy$.complete(); }

    loadTemplates(): void {
        this.http.get<Template[]>(`${environment.apiUrl}/api/output_templates`,
            { headers: this.auth.getAuthHeaders() })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (data) => { this.templates = data; if (data.length) this.selectedTemplateId = data[0].id; this.cdr.detectChanges(); },
                error: () => this.toast.error(this.translate.instant('role_outputs.toast_templates_load_error')),
            });
    }

    loadOutputs(): void {
        if (!this.sessionId) return;
        this.http.get<SessionOutput[]>(
            `${environment.apiUrl}/api/sessions/${this.sessionId}/outputs`,
            { headers: this.auth.getAuthHeaders() })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (data) => { this.outputs = data || []; this.cdr.detectChanges(); },
                error: () => { this.outputs = []; this.cdr.detectChanges(); },
            });
    }

    generate(): void {
        if (!this.sessionId || !this.selectedTemplateId) return;
        this.isGenerating = true;
        this.http.post<SessionOutput>(
            `${environment.apiUrl}/api/sessions/${this.sessionId}/outputs?template_id=${this.selectedTemplateId}&provider=${this.provider}`,
            {},
            { headers: this.auth.getAuthHeaders() })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (out) => {
                    this.isGenerating = false;
                    this.outputs = [out, ...this.outputs];
                    this.toast.success(this.translate.instant('role_outputs.toast_generated', { title: out.title }));
                    this.cdr.detectChanges();
                },
                error: (err) => {
                    this.isGenerating = false;
                    const detail = err?.error?.detail || this.translate.instant('role_outputs.toast_generate_error');
                    this.toast.error(detail);
                    this.cdr.detectChanges();
                },
            });
    }

    deleteOutput(out: SessionOutput): void {
        if (!confirm(this.translate.instant('role_outputs.confirm_delete', { title: out.title }))) return;
        this.http.delete(`${environment.apiUrl}/api/outputs/${out.id}`,
            { headers: this.auth.getAuthHeaders() })
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: () => { this.outputs = this.outputs.filter(o => o.id !== out.id); this.toast.success(this.translate.instant('role_outputs.toast_deleted')); this.cdr.detectChanges(); },
                error: () => this.toast.error(this.translate.instant('role_outputs.toast_delete_error')),
            });
    }

    download(out: SessionOutput): void {
        const blob = new Blob([out.body], { type: 'text/markdown;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${out.title.replace(/[^a-z0-9]/gi, '_')}.md`;
        document.body.appendChild(a); a.click(); document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    trackById(_i: number, item: { id: number }): number { return item.id; }
}
