import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AuthService } from '../../services/auth.service';
import { BrandingService, Branding } from '../../services/branding.service';

/**
 * Pantalla `/admin/branding`.
 *
 * Permite a un admin personalizar la marca de su empresa: logo, colores,
 * datos de contacto. La plataforma sigue llamándose "Acten" (`platform_name`,
 * inmutable). Los cambios se persisten en `IntegrationSetting('branding')`.
 */
@Component({
  selector: 'app-branding-settings',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './branding-settings.component.html',
  styleUrls: ['./branding-settings.component.css'],
})
export class BrandingSettingsComponent implements OnInit {
  readonly branding = inject(BrandingService);
  private readonly auth = inject(AuthService);

  /** Modelo local para edición — copiado de la marca actual al entrar. */
  form: Branding = {
    platform_name: 'Acten',
    platform_tagline: '',
    company_name: '',
    company_tagline: '',
    company_email: '',
    company_address: '',
    company_website: '',
    company_phone: '',
    primary_color: '#4F46E5',
    secondary_color: '#06B6D4',
    accent_color: '#10B981',
    logo_data_url: '',
    icon_data_url: '',
    favicon_data_url: '',
  };

  isSaving = false;
  isUploading = false;
  successMsg = '';
  errorMsg = '';

  ngOnInit(): void {
    // Por si entran directo sin pasar por bootstrap, refresco desde DB.
    this.branding.loadFromServer().finally(() => {
      this.form = { ...this.branding.brand() };
    });
  }

  async save(): Promise<void> {
    this.isSaving = true;
    this.successMsg = '';
    this.errorMsg = '';
    try {
      // No mandamos las keys de plataforma — el backend las ignora igual.
      const patch = {
        company_name: this.form.company_name,
        company_tagline: this.form.company_tagline,
        company_email: this.form.company_email,
        company_address: this.form.company_address,
        company_website: this.form.company_website,
        company_phone: this.form.company_phone,
        primary_color: this.form.primary_color,
        secondary_color: this.form.secondary_color,
        accent_color: this.form.accent_color,
      };
      await this.branding.update(patch, this.auth.token);
      this.successMsg = 'Marca actualizada. Los cambios ya están aplicados.';
    } catch (err: any) {
      this.errorMsg = err?.error?.detail || 'No se pudo guardar la marca.';
    } finally {
      this.isSaving = false;
    }
  }

  async onLogoSelected(ev: Event): Promise<void> {
    const input = ev.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    this.isUploading = true;
    this.successMsg = '';
    this.errorMsg = '';
    try {
      await this.branding.uploadLogo(file, this.auth.token);
      this.form.logo_data_url = this.branding.brand().logo_data_url;
      this.successMsg = 'Logo subido correctamente.';
    } catch (err: any) {
      this.errorMsg = err?.error?.detail || 'No se pudo subir el logo.';
    } finally {
      this.isUploading = false;
      // Reseteamos el input para que el mismo archivo pueda re-seleccionarse.
      input.value = '';
    }
  }

  async removeLogo(): Promise<void> {
    if (!confirm('¿Quitar el logo? Volverá a mostrarse el nombre de la empresa.')) return;
    this.isUploading = true;
    try {
      await this.branding.deleteLogo(this.auth.token);
      this.form.logo_data_url = '';
      this.successMsg = 'Logo eliminado.';
    } catch (err: any) {
      this.errorMsg = err?.error?.detail || 'No se pudo eliminar el logo.';
    } finally {
      this.isUploading = false;
    }
  }
}
