import { Component, OnInit, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
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

    constructor(private http: HttpClient, private authService: AuthService, private cdr: ChangeDetectorRef) { }

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

            this.http.post(`${environment.apiUrl}/sessions/upload`, formData).subscribe({
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

        this.http.get<any[]>(`${environment.apiUrl}/sessions/`).subscribe({
            next: (data) => {
                this.sessions = data;
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

    generateActa(sessionId: number) {
        // Aca se llamaría al motor de python que ensambla el Word
        alert(`Llamando al orquestador backend para ensamblar Acta y Tareas de la sesión #${sessionId}`);
    }

    viewCuration(sessionId: number) {
        // En un caso real navegariamos usando router.navigate(['/admin/curation', sessionId])
        alert(`Redirigiendo al panel de curación humano de la sesión #${sessionId}`);
    }
}
