import { Component, OnInit, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { AuthService } from '../../services/auth.service';
import { environment } from '../../../environments/environment';

@Component({
    selector: 'app-users',
    standalone: true,
    imports: [CommonModule, FormsModule],
    templateUrl: './users.component.html',
    styleUrls: ['./users.component.css']
})
export class UsersComponent implements OnInit {
    users: any[] = [];
    roles: any[] = [];
    isLoading = false;

    // Create User Form
    newUser = { email: '', password: '', full_name: '', role_id: '' };
    isCreating = false;

    errorMsg = '';
    successMsg = '';

    constructor(private http: HttpClient, private authService: AuthService, private cdr: ChangeDetectorRef) { }

    ngOnInit() {
        this.loadData();
    }

    loadData() {
        this.isLoading = true;
        const headers = this.authService.getAuthHeaders();

        // Load Users
        this.http.get<any[]>(`${environment.apiUrl}/users`, { headers }).subscribe({
            next: (data) => {
                this.users = data;
                this.isLoading = false;
                this.cdr.detectChanges();
            },
            error: () => {
                this.errorMsg = 'Error al cargar usuarios';
                this.isLoading = false;
                this.cdr.detectChanges();
            }
        });

        // Load Roles for the select dropdown
        this.http.get<any[]>(`${environment.apiUrl}/auth/roles`, { headers }).subscribe({
            next: (data) => {
                this.roles = data;
                this.cdr.detectChanges();
            },
            error: () => {
                this.cdr.detectChanges();
            }
        });
    }

    createUser() {
        if (!this.newUser.email || !this.newUser.password || !this.newUser.role_id) return;

        this.isCreating = true;
        this.errorMsg = '';
        this.successMsg = '';

        const payload = {
            ...this.newUser,
            role_id: parseInt(this.newUser.role_id, 10)
        };

        this.http.post<any>(`${environment.apiUrl}/auth/register/admin-only`, payload, { headers: this.authService.getAuthHeaders() })
            .subscribe({
                next: () => {
                    this.successMsg = 'Usuario registrado exitosamente.';
                    this.newUser = { email: '', password: '', full_name: '', role_id: '' };
                    this.loadData(); // Reload list
                    this.isCreating = false;
                },
                error: (err) => {
                    this.errorMsg = err.error?.detail || 'Error al registrar el usuario.';
                    this.isCreating = false;
                }
            });
    }

    toggleStatus(user: any) {
        const newStatus = !user.is_active;
        this.http.put(`${environment.apiUrl}/users/${user.id}/status?is_active=${newStatus}`, {}, { headers: this.authService.getAuthHeaders() })
            .subscribe({
                next: () => user.is_active = newStatus,
                error: (err) => this.errorMsg = err.error?.detail || 'Error al cambiar estado'
            });
    }
}
