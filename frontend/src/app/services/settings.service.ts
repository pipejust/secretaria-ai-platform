import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { AuthService } from './auth.service';

@Injectable({
    providedIn: 'root'
})
export class SettingsService {
    private apiUrl = 'http://localhost:14789/api/settings';

    constructor(private http: HttpClient, private authService: AuthService) { }

    getSettings(): Observable<any> {
        return this.http.get<any>(this.apiUrl, { headers: this.authService.getAuthHeaders() });
    }

    saveSettings(payload: any): Observable<any> {
        return this.http.post<any>(this.apiUrl, payload, { headers: this.authService.getAuthHeaders() });
    }
}
