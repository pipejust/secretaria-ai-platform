import { Component, OnInit, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { SettingsService } from '../../services/settings.service';
import { ToastService } from '../../services/toast.service';

@Component({
  selector: 'app-settings',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './settings.component.html',
  styleUrls: ['./settings.component.css']
})
export class SettingsComponent implements OnInit {
  smtpSettings = { provider: 'Resend', apiKey: '', senderEmail: '' };
  firefliesSettings: { apiKey: string; webhookUrl: string; webhook_token: string } =
    { apiKey: '', webhookUrl: '', webhook_token: '' };
  trelloSettings = { apiKey: '', apiToken: '', boardId: '', isActive: false };
  jiraSettings = { email: '', apiToken: '', domain: '', isActive: false };
  azureSettings = { organization: '', project: '', pat: '', isActive: false };
  clickupSettings = { apiToken: '', teamId: '', isActive: false };
  autoCurationSettings = { isEnabled: false, timeoutHours: 1 };

  isSaving = false;
  successMessage = '';
  errorMessage = '';

  constructor(
    private settingsService: SettingsService,
    private cdr: ChangeDetectorRef,
    private toast: ToastService,
  ) { }

  copyWebhookUrl(): void {
    const url = this.firefliesSettings.webhookUrl;
    if (!url) return;
    navigator.clipboard.writeText(url).then(
      () => this.toast.success('Webhook URL copiada al portapapeles.'),
      () => this.toast.error('No se pudo copiar la URL.'),
    );
  }

  ngOnInit(): void {
    this.settingsService.getSettings().subscribe({
      next: (data) => {
        if (data.smtp) this.smtpSettings = { ...this.smtpSettings, ...data.smtp };
        if (data.fireflies) this.firefliesSettings = { ...this.firefliesSettings, ...data.fireflies };
        if (data.trello) this.trelloSettings = { ...this.trelloSettings, ...data.trello };
        if (data.jira) this.jiraSettings = { ...this.jiraSettings, ...data.jira };
        if (data.azure) this.azureSettings = { ...this.azureSettings, ...data.azure };
        if (data.clickup) this.clickupSettings = { ...this.clickupSettings, ...data.clickup };
        if (data.autoCuration) this.autoCurationSettings = { ...this.autoCurationSettings, ...data.autoCuration };
        this.cdr.detectChanges();
      },
      error: (err) => console.error('Failed to load settings', err)
    });
  }

  saveSettings() {
    this.isSaving = true;
    this.successMessage = '';
    this.errorMessage = '';

    const payload = {
      smtp: this.smtpSettings,
      fireflies: this.firefliesSettings,
      trello: this.trelloSettings,
      jira: this.jiraSettings,
      azure: this.azureSettings,
      clickup: this.clickupSettings,
      autoCuration: this.autoCurationSettings
    };

    this.settingsService.saveSettings(payload).subscribe({
      next: () => {
        this.isSaving = false;
        this.successMessage = 'Ajustes de integración guardados exitosamente.';
        this.cdr.detectChanges();
        setTimeout(() => {
          this.successMessage = '';
          this.cdr.detectChanges();
        }, 4000);
      },
      error: (err) => {
        this.isSaving = false;
        this.errorMessage = 'Hubo un error guardando los ajustes.';
        console.error(err);
        this.cdr.detectChanges();
      }
    });
  }
}
