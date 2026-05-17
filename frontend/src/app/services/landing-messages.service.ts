import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpHeaders, HttpParams } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';

/**
 * Bandeja de mensajes del formulario público del landing.
 *
 * El backend persiste cada envío de `/api/public/landing/contact` y expone
 * estos endpoints sólo al super-admin del tenant Acten:
 *
 *   GET    /api/landing-cms/messages?status=...&limit=100
 *   PATCH  /api/landing-cms/messages/{id}   body { status }
 *   DELETE /api/landing-cms/messages/{id}
 *
 * Todos requieren `Authorization: Bearer <token>`. La autorización dura se
 * resuelve en el backend; aquí sólo enviamos el token y propagamos el error
 * tal cual al componente para que pueda mostrarlo por toast.
 */

export type ContactMessageStatus = 'new' | 'read' | 'replied' | 'archived';
export type ContactEmailStatus = 'pending' | 'sent' | 'failed';

export interface ContactMessage {
  id: number;
  name: string;
  email: string;
  company: string | null;
  role: string | null;
  message: string;
  ip: string | null;
  status: ContactMessageStatus;
  email_status: ContactEmailStatus;
  email_error: string | null;
  read_at: string | null;
  replied_at: string | null;
  created_at: string;
}

export interface ContactMessagesPage {
  items: ContactMessage[];
  unread_count: number;
}

@Injectable({ providedIn: 'root' })
export class LandingMessagesService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = `${environment.apiUrl}/api/landing-cms/messages`;

  /** Lista mensajes opcionalmente filtrados por estado. */
  async loadMessages(
    token: string,
    status?: ContactMessageStatus | '',
    limit = 100,
  ): Promise<ContactMessagesPage> {
    const headers = new HttpHeaders({ Authorization: `Bearer ${token}` });
    let params = new HttpParams().set('limit', String(limit));
    if (status) params = params.set('status', status);
    return firstValueFrom(
      this.http.get<ContactMessagesPage>(this.baseUrl, { headers, params }),
    );
  }

  /** Actualiza el estado del mensaje. */
  async updateStatus(
    token: string,
    id: number,
    status: ContactMessageStatus,
  ): Promise<{ id: number; status: ContactMessageStatus }> {
    const headers = new HttpHeaders({ Authorization: `Bearer ${token}` });
    return firstValueFrom(
      this.http.patch<{ id: number; status: ContactMessageStatus }>(
        `${this.baseUrl}/${id}`,
        { status },
        { headers },
      ),
    );
  }

  /** Elimina permanentemente un mensaje. */
  async delete(token: string, id: number): Promise<{ deleted: number }> {
    const headers = new HttpHeaders({ Authorization: `Bearer ${token}` });
    return firstValueFrom(
      this.http.delete<{ deleted: number }>(`${this.baseUrl}/${id}`, { headers }),
    );
  }
}
