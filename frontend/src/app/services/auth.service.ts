import { Injectable } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { BehaviorSubject, Observable, tap } from 'rxjs';
import { environment } from '../../../environments/environment';

@Injectable({
    providedIn: 'root'
})
export class AuthService {
    // Apuntamos al endpoint configurado en enviroment
    private apiUrl = `${environment.apiUrl}/auth`;
    private currentUserSubject = new BehaviorSubject<any>(null);
    public currentUser$ = this.currentUserSubject.asObservable();

    constructor(private http: HttpClient) {
        this.loadUserFromStorage();
    }

    get token(): string | null {
        return localStorage.getItem('access_token');
    }

    get currentUserValue(): any {
        return this.currentUserSubject.value;
    }

    login(email: string, password: string): Observable<any> {
        const body = {
            username: email,
            password: password
        };

        const headers = new HttpHeaders({
            'Content-Type': 'application/json'
        });

        return this.http.post<any>(`${this.apiUrl}/login`, body, { headers })
            .pipe(
                tap(response => {
                    if (response && response.access_token) {
                        localStorage.setItem('access_token', response.access_token);
                        // Inyectar el payload JWT básico o el user base hasta que cargue el resto, o forzar la carga
                        this.loadUserProfile();
                    }
                })
            );
    }

    changePassword(current_password: string, new_password: string): Observable<any> {
        return this.http.put(`${this.apiUrl}/password`, {
            current_password,
            new_password
        }, { headers: this.getAuthHeaders() });
    }

    logout() {
        localStorage.removeItem('access_token');
        this.currentUserSubject.next(null);
        // Force fully reload en caso extremo o solo routing limpio
        setTimeout(() => {
            // Forzar en frontend refresh para evitar bugs en menús colapsados state
            window.location.href = '/login';
        }, 100);
    }

    loadUserProfile() {
        if (!this.token) return;

        this.http.get<any>(`${this.apiUrl}/me`).subscribe({
            next: (user) => {
                this.currentUserSubject.next(user);
            },
            error: () => this.logout() // token was invalid
        });
    }

    private loadUserFromStorage() {
        if (this.token) {
            this.loadUserProfile();
        }
    }

    // Use to get headers in other services
    getAuthHeaders(): HttpHeaders {
        return new HttpHeaders({
            'Authorization': `Bearer ${this.token || ''}`
        });
    }
}
