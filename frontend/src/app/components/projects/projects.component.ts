import { Component, OnInit, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { AuthService } from '../../services/auth.service';

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
        // For this prototype, if there's no project endpoint yet we just simulate it 
        // Usually it goes to some 'http://localhost:8080/projects' endpoint

        // We haven't created the project GET router yet on backend. We'll set empty list for UI mockup.
        setTimeout(() => {
            /*
            this.projects = [
                { id: 1, name: 'Comité Organizacional Mensual', description: 'Reunión recurrente' },
                { id: 2, name: 'Entrevistas Técnicas', description: 'Captura de tareas en Hiring' }
            ];
            */
            this.projects = [];
            this.isLoading = false;
            this.cdr.detectChanges();
        }, 1200);
    }

    createProject() {
        // Mockup for now
        this.isCreating = true;
        setTimeout(() => {
            this.projects.push({ id: Date.now(), ...this.newProject });
            this.newProject = { name: '', description: '' };
            this.isCreating = false;
            this.successMsg = 'Proyecto creado exitosamente';
        }, 600);
    }
}
