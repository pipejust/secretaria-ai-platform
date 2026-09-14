import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';

/** GET /api/media-storage/config — la secret key nunca vuelve al navegador. */
export interface MediaStorageConfig {
  endpoint_url: string;
  region: string;
  bucket: string;
  access_key: string;
  has_secret_key: boolean;
  configured: boolean;
}

/** PUT /api/media-storage/config — `secret_key` vacía conserva la guardada. */
export interface MediaStorageConfigInput {
  endpoint_url: string;
  region: string;
  bucket: string;
  access_key: string;
  secret_key: string;
}

/** POST /api/media-storage/test — lista el bucket con las credenciales guardadas. */
export interface MediaStorageTestResult {
  ok: boolean;
  bucket: string;
  endpoint_url: string;
}

/** GET /api/sessions/{id}/video — URL firmada y temporal del vídeo de una sesión. */
export interface SessionVideo {
  url: string;
  expires_in: number;
}

/** Bucket S3 compatible (Hetzner Object Storage) del vídeo de reuniones;
 *  la configuración es de plataforma (solo super-admin). */
@Injectable({ providedIn: 'root' })
export class MediaStorageService {
  private http = inject(HttpClient);
  private base = environment.apiUrl + '/api/media-storage';

  getConfig(): Promise<MediaStorageConfig> {
    return firstValueFrom(this.http.get<MediaStorageConfig>(`${this.base}/config`));
  }

  saveConfig(body: MediaStorageConfigInput): Promise<MediaStorageConfig> {
    return firstValueFrom(this.http.put<MediaStorageConfig>(`${this.base}/config`, body));
  }

  test(): Promise<MediaStorageTestResult> {
    return firstValueFrom(this.http.post<MediaStorageTestResult>(`${this.base}/test`, {}));
  }

  sessionVideo(sessionId: number): Promise<SessionVideo> {
    return firstValueFrom(this.http.get<SessionVideo>(`${environment.apiUrl}/api/sessions/${sessionId}/video`));
  }
}
