import { CommonModule } from '@angular/common';
import { ChangeDetectorRef, Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { MediaStorageConfigInput, MediaStorageService } from '../../services/media-storage.service';

/** Forma mínima de un HttpErrorResponse para sacar el `detail` del backend. */
interface ErrorLike { error?: { detail?: unknown } | null; message?: string; }

/** Panel de super-admin: bucket S3 compatible donde se guarda el vídeo de
 *  las reuniones del bot propio. Las llaves viajan una vez y se guardan
 *  cifradas; la secret nunca vuelve (vacía = conservar). */
@Component({
  selector: 'app-media-storage-panel',
  standalone: true,
  imports: [CommonModule, FormsModule, TranslateModule],
  templateUrl: './media-storage-panel.component.html',
  styleUrls: ['./super-tenants.component.css'],
})
export class MediaStoragePanelComponent implements OnInit {
  private api = inject(MediaStorageService);
  private translate = inject(TranslateService);
  private cd = inject(ChangeDetectorRef);

  form: MediaStorageConfigInput = { endpoint_url: '', region: '', bucket: '', access_key: '', secret_key: '' };
  configured = false;
  hasSecret = false;
  busy = false;
  error = '';
  notice = '';

  async ngOnInit(): Promise<void> { await this.load(); }

  get canSave(): boolean {
    const f = this.form;
    return !!(f.endpoint_url.trim() && f.bucket.trim() && f.access_key.trim()) && (this.hasSecret || !!f.secret_key.trim());
  }

  async load(): Promise<void> {
    try {
      const cfg = await this.api.getConfig();
      this.form = { endpoint_url: cfg.endpoint_url, region: cfg.region, bucket: cfg.bucket, access_key: cfg.access_key, secret_key: '' };
      this.configured = cfg.configured;
      this.hasSecret = cfg.has_secret_key;
    } catch (e) {
      this.error = this.detail(e) ?? this.translate.instant('tenants.storage_load_error');
    }
    this.cd.markForCheck();
  }

  async save(): Promise<void> {
    await this.run(async () => {
      const cfg = await this.api.saveConfig({ ...this.form, secret_key: this.form.secret_key.trim() });
      this.form.secret_key = '';
      this.configured = cfg.configured;
      this.hasSecret = cfg.has_secret_key;
      this.notice = this.translate.instant('tenants.storage_saved');
    });
  }

  async test(): Promise<void> {
    await this.run(async () => {
      const result = await this.api.test();
      this.notice = this.translate.instant('tenants.storage_test_ok', { bucket: result.bucket });
    });
  }

  private async run(work: () => Promise<void>): Promise<void> {
    this.busy = true; this.error = ''; this.notice = '';
    try { await work(); }
    catch (e) { this.error = this.detail(e) ?? this.translate.instant('tenants.storage_error'); }
    finally { this.busy = false; this.cd.markForCheck(); }
  }

  private detail(e: unknown): string | null {
    if (typeof e !== 'object' || e === null) return null;
    const detail = (e as ErrorLike).error?.detail;
    if (typeof detail === 'string') return detail;
    if (typeof detail === 'object' && detail !== null && 'message' in detail) {
      const message = (detail as { message?: unknown }).message;
      return typeof message === 'string' ? message : null;
    }
    return null;
  }
}
