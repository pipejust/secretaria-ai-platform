import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { AuthService } from '../../services/auth.service';

@Component({
    selector: 'app-login',
    standalone: true,
    imports: [CommonModule, FormsModule],
    templateUrl: './login.component.html',
    styleUrls: ['./login.component.css']
})
export class LoginComponent {
    email = '';
    password = '';
    showPassword = false;
    isLoading = false;
    errorMessage = '';

    constructor(private authService: AuthService, private router: Router) { }

    togglePassword() {
        this.showPassword = !this.showPassword;
    }

    onSubmit() {
        if (!this.email || !this.password) return;

        this.isLoading = true;
        this.errorMessage = '';

        this.authService.login(this.email, this.password).subscribe({
            next: (res) => {
                this.isLoading = false;
                // Navegar al layout admin al iniciar sesión exitosamente
                this.router.navigate(['/admin/dashboard']);
            },
            error: (err) => {
                this.isLoading = false;
                if (err.status === 401) {
                    this.errorMessage = 'Correo o contraseña incorrectos';
                } else {
                    this.errorMessage = 'Error conectando al servidor. Inténtalo más tarde.';
                }
            }
        });
    }
}
