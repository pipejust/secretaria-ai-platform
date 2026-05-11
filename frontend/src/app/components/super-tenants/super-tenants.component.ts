import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../environments/environment';
import { AuthService } from '../../services/auth.service';

interface TenantOut {
  id: number;
  slug: string;
  name: string;
  domain: string | null;
  is_active: boolean;
  user_count: number;
  created_at: string;
}

interface CreateForm {
  slug: string;
  name: string;
  domain: string;
  admin_email: string;
  admin_password: string;
  admin_full_name: string;
}

/**
 * Pantalla `/admin/super/tenants`.
 *
 * Sólo accesible para super-admins de plataforma. Crea, lista, edita y
 * desactiva EMPRESAS (tenants). Cada empresa creada queda aislada — sus
 * usuarios sólo ven sus propios datos.
 */
@Component({
  selector: 'app-super-tenants',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './super-tenants.component.html',
  styleUrls: ['./super-tenants.component.css'],
})
export class SuperTenantsComponent implements OnInit {
  private http = inject(HttpClient);
  private auth = inject(AuthService);
  private apiUrl = `${environment.apiUrl}/api/super/tenants`;

  tenants: TenantOut[] = [];
  loading = false;
  errorMsg = '';
  successMsg = '';

  showCreate = false;
  isCreating = false;
  form: CreateForm = this._emptyForm();

  ngOnInit(): void {
    this.refresh();
  }

  private _headers(): HttpHeaders {
    return new HttpHeaders({ Authorization: `Bearer ${this.auth.token || ''}` });
  }

  private _emptyForm(): CreateForm {
    return {
      slug: '', name: '', domain: '',
      admin_email: '', admin_password: '', admin_full_name: '',
    };
  }

  async refresh(): Promise<void> {
    this.loading = true;
    this.errorMsg = '';
    try {
      const data = await firstValueFrom(
        this.http.get<TenantOut[]>(`${this.apiUrl}/`, { headers: this._headers() }),
      );
      this.tenants = data;
    } catch (err: any) {
      this.errorMsg = err?.error?.detail || 'No se pudo cargar la lista de empresas.';
    } finally {
      this.loading = false;
    }
  }

  async createTenant(): Promise<void> {
    this.isCreating = true;
    this.errorMsg = '';
    this.successMsg = '';
    try {
      const out = await firstValueFrom(
        this.http.post<TenantOut>(`${this.apiUrl}/`, {
          slug: this.form.slug.trim().toLowerCase(),
          name: this.form.name.trim(),
          domain: this.form.domain.trim() || null,
          admin_email: this.form.admin_email.trim().toLowerCase(),
          admin_password: this.form.admin_password,
          admin_full_name: this.form.admin_full_name.trim(),
        }, { headers: this._headers() }),
      );
      this.tenants = [...this.tenants, out];
      this.successMsg = `Empresa '${out.name}' creada. Comparte estas credenciales con el admin del cliente.`;
      this.showCreate = false;
      this.form = this._emptyForm();
    } catch (err: any) {
      this.errorMsg = err?.error?.detail || 'No se pudo crear la empresa.';
    } finally {
      this.isCreating = false;
    }
  }

  async toggleActive(t: TenantOut): Promise<void> {
    if (t.slug === 'acten') {
      alert('No se puede desactivar el tenant principal (acten).');
      return;
    }
    const next = !t.is_active;
    try {
      await firstValueFrom(
        this.http.put<TenantOut>(`${this.apiUrl}/${t.slug}`, { is_active: next }, { headers: this._headers() }),
      );
      t.is_active = next;
    } catch (err: any) {
      this.errorMsg = err?.error?.detail || 'No se pudo cambiar el estado.';
    }
  }

  async setDomain(t: TenantOut): Promise<void> {
    const dom = prompt(`Dominio personalizado para "${t.name}" (deja vacío para borrar):`, t.domain || '');
    if (dom === null) return;
    try {
      const out = await firstValueFrom(
        this.http.put<TenantOut>(`${this.apiUrl}/${t.slug}`, { domain: dom.trim() }, { headers: this._headers() }),
      );
      t.domain = out.domain;
      this.successMsg = `Dominio actualizado para ${t.name}.`;
    } catch (err: any) {
      this.errorMsg = err?.error?.detail || 'No se pudo actualizar el dominio.';
    }
  }
}
