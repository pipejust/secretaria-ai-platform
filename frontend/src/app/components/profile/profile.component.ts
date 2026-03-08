import { Component, OnInit } from '@angular/core';
import { CommonModule, TitleCasePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AuthService } from '../../services/auth.service';

@Component({
  selector: 'app-profile',
  standalone: true,
  imports: [CommonModule, TitleCasePipe, FormsModule],
  templateUrl: './profile.component.html',
  styleUrls: ['./profile.component.css']
})
export class ProfileComponent implements OnInit {
  user: any = null;
  pwd = {
    current: '',
    new: '',
    confirm: ''
  };
  msg = '';
  isError = false;

  constructor(private authService: AuthService) { }

  ngOnInit(): void {
    this.authService.currentUser$.subscribe(u => {
      this.user = u;
    });
  }

  getInitials(name: string): string {
    if (!name) return 'U';
    const words = name.trim().split(' ');
    if (words.length >= 2) {
      return (words[0].charAt(0) + words[1].charAt(0)).toUpperCase();
    }
    return name.substring(0, 2).toUpperCase();
  }

  changePassword() {
    this.msg = '';
    this.isError = false;

    if (!this.pwd.current || !this.pwd.new || !this.pwd.confirm) {
      this.isError = true;
      this.msg = 'Por favor completa todos los campos';
      return;
    }

    if (this.pwd.new !== this.pwd.confirm) {
      this.isError = true;
      this.msg = 'Las nuevas contraseñas no coinciden';
      return;
    }

    this.authService.changePassword(this.pwd.current, this.pwd.new).subscribe({
      next: (res) => {
        this.msg = res.msg || 'Contraseña actualizada correctamente';
        this.isError = false;
        this.pwd = { current: '', new: '', confirm: '' };
      },
      error: (err) => {
        this.isError = true;
        this.msg = err.error?.detail || 'Error al actualizar contraseña';
      }
    });
  }
}
