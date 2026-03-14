import { Component, OnInit, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { AuthService } from '../../services/auth.service';
import { environment } from '../../../environments/environment';

@Component({
    selector: 'app-dashboard',
    standalone: true,
    imports: [CommonModule, FormsModule],
    templateUrl: './dashboard.component.html',
    styleUrls: ['./dashboard.component.css']
})
export class DashboardComponent implements OnInit {
    sessions: any[] = [];
    isLoading = false;

    constructor(
        private http: HttpClient, 
        private authService: AuthService, 
        private cdr: ChangeDetectorRef,
        private router: Router
    ) { }

    ngOnInit() {
        this.loadSessions();
    }

    onFileSelected(event: any) {
        const file: File = event.target.files[0];
        if (file) {
            this.isLoading = true;
            const formData = new FormData();
            formData.append('file', file);
            formData.append('title', file.name);

            this.http.post(`${environment.apiUrl}/api/sessions/upload`, formData).subscribe({
                next: (res: any) => {
                    alert('Archivo subido exitosamente.');
                    this.loadSessions();
                },
                error: (err) => {
                    alert('Error subiendo el archivo: ' + err.message);
                    this.isLoading = false;
                }
            });
        }
    }

    loadSessions() {
        this.isLoading = true;

        this.http.get<any[]>(`${environment.apiUrl}/api/sessions/`).subscribe({
            next: (data) => {
                // Ensure dates are parsed correctly
                this.sessions = data.map(s => {
                    let parsedDate = s.date;
                    if (typeof parsedDate === 'string' && !isNaN(Number(parsedDate))) {
                        parsedDate = Number(parsedDate);
                    }
                    return { ...s, date: parsedDate };
                });
                this.isLoading = false;
                this.cdr.detectChanges();
            },
            error: (err) => {
                console.error('Error fetching sessions:', err);
                this.sessions = [];
                this.isLoading = false;
                this.cdr.detectChanges();
            }
        });
    }

    searchText: string = '';
    statusFilter: string = '';
    sortColumn: string = 'date';
    sortDirection: 'asc' | 'desc' = 'desc';

    sortBy(column: string) {
        if (this.sortColumn === column) {
            this.sortDirection = this.sortDirection === 'asc' ? 'desc' : 'asc';
        } else {
            this.sortColumn = column;
            this.sortDirection = 'asc'; // Default to asc when clicking a new column
            if (column === 'date' || column === 'id') {
                this.sortDirection = 'desc'; // Exception: new IDs and dates default to descending
            }
        }
    }

    get filteredSessions() {
        let filtered = this.sessions || [];
        
        if (this.statusFilter) {
            filtered = filtered.filter(s => s.status === this.statusFilter);
        }
        
        if (this.searchText.trim()) {
            const search = this.searchText.toLowerCase();
            filtered = filtered.filter(s => 
                (s.title && s.title.toLowerCase().includes(search)) ||
                (s.id && s.id.toString().includes(search))
            );
        }
        
        // Sorting logic based on selected column: Clone array to trigger Angular Change Detection
        return [...filtered].sort((a, b) => {
            let valA = a[this.sortColumn];
            let valB = b[this.sortColumn];

            // Normalize values for sorting
            if (this.sortColumn === 'date') {
                if (typeof valA === 'string' && !isNaN(Number(valA))) valA = Number(valA);
                if (typeof valB === 'string' && !isNaN(Number(valB))) valB = Number(valB);
                valA = new Date(valA).getTime() || 0;
                valB = new Date(valB).getTime() || 0;
            } else if (typeof valA === 'string') {
                valA = valA.toLowerCase();
                valB = valB.toLowerCase();
            } else {
                valA = valA || 0;
                valB = valB || 0;
            }

            if (valA < valB) {
                return this.sortDirection === 'asc' ? -1 : 1;
            }
            if (valA > valB) {
                return this.sortDirection === 'asc' ? 1 : -1;
            }
            return 0;
        });
    }

    generateActa(session: any) {
        // Enforce the headers and get the blob stream directly from Dashboard
        const headers = this.authService.getAuthHeaders();
        this.http.get(`${environment.apiUrl}/api/sessions/${session.id}/export/word`, { headers, responseType: 'blob' }).subscribe({
            next: (blob: Blob) => {
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                const safeTitle = (session.title || 'Sesion').replace(/[^a-z0-9]/gi, '_').substring(0, 30);
                a.download = `Sesion_${session.id}_${safeTitle}.docx`;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                window.URL.revokeObjectURL(url);
            },
            error: (err) => {
                console.error('Error generando documento', err);
                alert('Error descargando el documento. Asegúrese de tener conexión.');
            }
        });
    }

    viewCuration(sessionId: number) {
        this.router.navigate(['/admin/curation', sessionId]);
    }
}
