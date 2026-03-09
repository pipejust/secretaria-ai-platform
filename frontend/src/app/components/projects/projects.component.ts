import { Component, OnInit, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { AuthService } from '../../services/auth.service';
import { environment } from '../../../environments/environment';

@Component({
    selector: 'app-projects',
    standalone: true,
    imports: [CommonModule, FormsModule],
    templateUrl: './projects.component.html',
    styleUrls: ['./projects.component.css']
})
export class ProjectsComponent implements OnInit {
    projects: any[] = [];
    isLoading = false;

    newProject = { name: '', description: '' };
    isCreating = false;
    errorMsg = '';
    successMsg = '';

    constructor(private http: HttpClient, private authService: AuthService, private cdr: ChangeDetectorRef) { }

    ngOnInit() {
        this.loadProjects();
    }

    loadProjects() {
        this.isLoading = true;
        this.http.get<any[]>(`${environment.apiUrl}/projects/`).subscribe({
            next: (data) => {
                this.projects = data;
                this.isLoading = false;
                this.cdr.detectChanges();
            },
            error: (err) => {
                console.error(err);
                this.errorMsg = 'Error al cargar los proyectos';
                this.isLoading = false;
                this.cdr.detectChanges();
            }
        });
    }

    createProject() {
        this.isCreating = true;
        this.errorMsg = '';
        this.successMsg = '';

        const payload = {
            name: this.newProject.name,
            description: this.newProject.description,
            is_active: true
        };

        this.http.post<any>(`${environment.apiUrl}/projects/`, payload).subscribe({
            next: (data) => {
                this.projects.push(data);
                this.newProject = { name: '', description: '' };
                this.isCreating = false;
                this.successMsg = 'Proyecto creado exitosamente';
                this.cdr.detectChanges();
            },
            error: (err) => {
                console.error(err);
                this.errorMsg = err.error?.detail || 'Error al crear el proyecto';
                this.isCreating = false;
                this.cdr.detectChanges();
            }
        });
    }
}
