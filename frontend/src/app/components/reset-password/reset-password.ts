import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { ActivatedRoute, Router, RouterModule } from '@angular/router';
import { environment } from '../../../environments/environment';

@Component({
  selector: 'app-reset-password',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterModule],
  templateUrl: './reset-password.html',
  styleUrl: './reset-password.css'
})
export class ResetPassword implements OnInit {
  token: string = '';
  newPassword: string = '';
  confirmPassword: string = '';
  
  isLoading: boolean = false;
  successMessage: string = '';
  errorMessage: string = '';

  constructor(
    private http: HttpClient,
    private route: ActivatedRoute,
    private router: Router
  ) {}

  ngOnInit() {
    this.route.queryParams.subscribe(params => {
      this.token = params['token'] || '';
      if (!this.token) {
        this.errorMessage = 'Token de recuperación no válido o extraviado. Por favor, solicita uno nuevo.';
      }
    });
  }

  onSubmit() {
    if (!this.token || !this.newPassword || !this.confirmPassword) return;
    
    if (this.newPassword !== this.confirmPassword) {
      this.errorMessage = 'Las contraseñas no coinciden.';
      return;
    }

    this.isLoading = true;
    this.successMessage = '';
    this.errorMessage = '';

    const payload = {
      token: this.token,
      new_password: this.newPassword
    };

    this.http.post<{msg: string}>(`${environment.apiUrl}/auth/reset-password`, payload)
      .subscribe({
        next: (response) => {
          this.isLoading = false;
          this.successMessage = response.msg;
        },
        error: (err) => {
          this.isLoading = false;
          this.errorMessage = err.error?.detail || "Ha ocurrido un error al intentar cambiar la contraseña.";
          console.error('Reset password error:', err);
        }
      });
  }
}
