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
    editingProject: any = null;
    isCreating = false;
    isUpdating = false;
    isDeleting = false;
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

    editProject(project: any) {
        this.editingProject = { ...project };
        this.newProject = { name: project.name, description: project.description };
        this.errorMsg = '';
        this.successMsg = '';
    }

    cancelEdit() {
        this.editingProject = null;
        this.newProject = { name: '', description: '' };
        this.errorMsg = '';
        this.successMsg = '';
    }

    updateProject() {
        if (!this.editingProject) return;
        this.isUpdating = true;
        this.errorMsg = '';
        this.successMsg = '';

        const payload = {
            id: this.editingProject.id,
            name: this.newProject.name,
            description: this.newProject.description,
            is_active: true
        };

        this.http.put<any>(`${environment.apiUrl}/projects/${this.editingProject.id}`, payload).subscribe({
            next: (data) => {
                const index = this.projects.findIndex(p => p.id === data.id);
                if (index !== -1) {
                    this.projects[index] = data;
                }
                this.cancelEdit();
                this.isUpdating = false;
                this.successMsg = 'Proyecto actualizado exitosamente';
                this.cdr.detectChanges();
            },
            error: (err) => {
                console.error(err);
                this.errorMsg = err.error?.detail || 'Error al actualizar el proyecto';
                this.isUpdating = false;
                this.cdr.detectChanges();
            }
        });
    }

    deleteProject(projectId: number) {
        if (!confirm('¿Estás seguro de que deseas eliminar este proyecto?')) return;

        this.isDeleting = true;
        this.errorMsg = '';
        this.successMsg = '';

        this.http.delete(`${environment.apiUrl}/projects/${projectId}`).subscribe({
            next: () => {
                this.projects = this.projects.filter(p => p.id !== projectId);
                this.isDeleting = false;
                this.successMsg = 'Proyecto eliminado exitosamente';
                if (this.editingProject?.id === projectId) {
                    this.cancelEdit();
                }
                this.cdr.detectChanges();
            },
            error: (err) => {
                console.error(err);
                this.errorMsg = err.error?.detail || 'Error al eliminar el proyecto';
                this.isDeleting = false;
                this.cdr.detectChanges();
            }
        });
    }
}
