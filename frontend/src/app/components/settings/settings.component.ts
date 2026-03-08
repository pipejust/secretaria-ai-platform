import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { SettingsService } from '../../services/settings.service';

@Component({
  selector: 'app-settings',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './settings.component.html',
  styleUrls: ['./settings.component.css']
})
export class SettingsComponent implements OnInit {
  smtpSettings = { provider: 'Resend', apiKey: '', senderEmail: '' };
  firefliesSettings = { apiKey: '', webhookUrl: '' };
  trelloSettings = { apiKey: '', apiToken: '', boardId: '' };
  jiraSettings = { email: '', apiToken: '', domain: '' };
  azureSettings = { organization: '', project: '', pat: '' };
  clickupSettings = { apiToken: '', teamId: '' };

  isSaving = false;
  successMessage = '';
  errorMessage = '';

  constructor(private settingsService: SettingsService) { }

  ngOnInit(): void {
    this.settingsService.getSettings().subscribe({
      next: (data) => {
        if (data.smtp) this.smtpSettings = data.smtp;
        if (data.fireflies) this.firefliesSettings = data.fireflies;
        if (data.trello) this.trelloSettings = data.trello;
        if (data.jira) this.jiraSettings = data.jira;
        if (data.azure) this.azureSettings = data.azure;
        if (data.clickup) this.clickupSettings = data.clickup;
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
      clickup: this.clickupSettings
    };

    this.settingsService.saveSettings(payload).subscribe({
      next: () => {
        this.isSaving = false;
        this.successMessage = 'Ajustes de integración guardados exitosamente.';
        setTimeout(() => this.successMessage = '', 4000);
      },
      error: (err) => {
        this.isSaving = false;
        this.errorMessage = 'Hubo un error guardando los ajustes.';
        console.error(err);
      }
    });
  }
}
