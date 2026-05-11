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
  /** Data URL del logo "completo" (wordmark + monograma). Opcional. */
  logo_data_url: string;
  /** Data URL del imagologo cuadrado. Opcional. */
  icon_data_url: string;
}

/** Límite duro client-side antes de mandar al backend. El backend acepta
 *  hasta 4 MB de string base64 (~3 MB binario). Lo cortamos antes para
 *  evitar pedirle al admin que reintente. */
const MAX_BRAND_FILE_BYTES = 2 * 1024 * 1024;

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
      logo_data_url: '', icon_data_url: '',
    };
  }

  /** Lee un File del input y lo convierte a data URL base64 — formato que
   *  espera el backend en `logo_data_url`/`icon_data_url`. Resuelve a '' si
   *  el archivo es demasiado grande o no es imagen, dejando un mensaje de
   *  error en `errorMsg` para que el usuario sepa qué pasó. */
  private async _fileToDataUrl(file: File): Promise<string> {
    if (!file.type.startsWith('image/')) {
      this.errorMsg = `"${file.name}" no es una imagen válida.`;
      return '';
    }
    if (file.size > MAX_BRAND_FILE_BYTES) {
      this.errorMsg = `"${file.name}" pesa ${(file.size / 1024 / 1024).toFixed(1)} MB. Máximo permitido: ${MAX_BRAND_FILE_BYTES / 1024 / 1024} MB.`;
      return '';
    }
    return await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ''));
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
  }

  async onLogoSelected(ev: Event): Promise<void> {
    this.errorMsg = '';
    const input = ev.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    const url = await this._fileToDataUrl(file);
    if (url) this.form.logo_data_url = url;
    input.value = '';
  }

  async onIconSelected(ev: Event): Promise<void> {
    this.errorMsg = '';
    const input = ev.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    const url = await this._fileToDataUrl(file);
    if (url) this.form.icon_data_url = url;
    input.value = '';
  }

  clearLogo(): void { this.form.logo_data_url = ''; }
  clearIcon(): void { this.form.icon_data_url = ''; }

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
          // Solo mando los logos si el super-admin los subió. El backend
          // valida que el data URL sea válido antes de persistirlo.
          logo_data_url: this.form.logo_data_url || undefined,
          icon_data_url: this.form.icon_data_url || undefined,
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
