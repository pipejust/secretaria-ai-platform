import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { environment } from '../../../environments/environment';

@Component({
  selector: 'app-forgot-password',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './forgot-password.html',
  styleUrl: './forgot-password.css'
})
export class ForgotPassword {
  email: string = '';
  isLoading: boolean = false;
  successMessage: string = '';
  errorMessage: string = '';

  constructor(private http: HttpClient) {}

  onSubmit() {
    if (!this.email) return;

    this.isLoading = true;
    this.successMessage = '';
    this.errorMessage = '';

    this.http.post<{msg: string}>(`${environment.apiUrl}/auth/forgot-password`, { email: this.email })
      .subscribe({
        next: (response) => {
          this.isLoading = false;
          this.successMessage = response.msg;
          this.email = '';
        },
        error: (err) => {
          this.isLoading = false;
          // Security best practice: generic error message
          this.errorMessage = "Se ha producido un error intentando enviar el correo. Por favor, inténtelo de nuevo más tarde.";
          console.error('Forgot password error:', err);
        }
      });
  }
}
