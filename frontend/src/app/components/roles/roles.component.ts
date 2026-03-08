import { Component, OnInit, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { AuthService } from '../../services/auth.service';

@Component({
    selector: 'app-roles',
    standalone: true,
    imports: [CommonModule, FormsModule],
    templateUrl: './roles.component.html',
    styleUrls: ['./roles.component.css']
})
export class RolesComponent implements OnInit {
    roles: any[] = [];
    isLoading = false;

    // Create Form
    newRole = { name: '', description: '' };
    isCreating = false;
    errorMsg = '';
    successMsg = '';

    constructor(private http: HttpClient, private authService: AuthService, private cdr: ChangeDetectorRef) { }

    ngOnInit() {
        this.loadRoles();
    }

    loadRoles() {
        this.isLoading = true;
        this.http.get<any[]>('http://localhost:8080/auth/roles', { headers: this.authService.getAuthHeaders() })
            .subscribe({
                next: (data) => {
                    this.roles = data;
                    this.isLoading = false;
                    this.cdr.detectChanges();
                },
                error: (err) => {
                    this.errorMsg = 'Error al cargar roles. Verifica permisos.';
                    this.isLoading = false;
                    this.cdr.detectChanges();
                }
            });
    }

    createRole() {
        if (!this.newRole.name) return;

        this.isCreating = true;
        this.errorMsg = '';
        this.successMsg = '';

        this.http.post<any>('http://localhost:8080/auth/roles', this.newRole, { headers: this.authService.getAuthHeaders() })
            .subscribe({
                next: (res) => {
                    this.successMsg = 'Rol creado exitosamente.';
                    this.roles.push(res);
                    this.newRole = { name: '', description: '' };
                    this.isCreating = false;
                },
                error: (err) => {
                    this.errorMsg = err.error?.detail || 'Error al crear el rol.';
                    this.isCreating = false;
                }
            });
    }
}
