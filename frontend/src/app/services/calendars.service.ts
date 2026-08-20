/**
 * El módulo de calendarios, del lado del navegador.
 *
 * Una regla que se nota en todo el archivo: **el permiso lo resuelve el
 * servidor y viaja con la lista**. Aquí no se repite la regla de quién
 * puede editar qué; se lee `permission` y `read_only` de cada calendario.
 * Una copia de esa lógica en la pantalla se queda vieja el día que la
 * regla cambie, y nadie se entera hasta que alguien edita lo que no debía.
 */
import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../environments/environment';

export type Permiso = 'ocupado' | 'ver' | 'editar' | 'gestionar';
export type Origen =
  | 'derivado' | 'propio' | 'equipo' | 'proyecto'
  | 'suscrito' | 'google' | 'microsoft' | 'zoho';

export interface Calendario {
  key: string;
  id?: number;
  name: string;
  description?: string;
  color: string;
  origin: Origen;
  permission: Permiso;
  read_only: boolean;
  visible: boolean;
  position: number;
  project_id?: number | null;
  is_default?: boolean;
  ics_url?: string;
  /** Dos cuentas suelen dar dos calendarios llamados igual; esto los distingue. */
  account_email?: string;
  account_id?: number | null;
  status?: string;
  sync_error?: string;
  last_synced_at?: string | null;
}

export interface EventoAgenda {
  id: string | number;
  /** La clave del calendario al que pertenece. Con esto se apaga y enciende. */
  calendar: string;
  title: string;
  description?: string;
  location?: string;
  start_at: string;
  end_at?: string;
  all_day?: boolean;
  kind: 'sesion' | 'tarea' | 'festivo' | 'evento' | 'externo';
  editable?: boolean;
  meeting_url?: string;
  url?: string;
  owner?: string;
  status?: string;
  priority?: string;
  session_id?: number | null;
  project_id?: number | null;
  external_uid?: string;
  external_error?: string;
}

export interface CuentaConectada {
  id: number;
  provider: 'google' | 'microsoft' | 'zoho';
  etiqueta: string;
  email: string;
  aviso: string;
  status: string;
  last_error: string;
  data_center: string;
  revocar_manual: string;
}

export interface TarjetaProveedor {
  provider: string;
  etiqueta: string;
  configurado: boolean;
  client_id: string;
  client_secret_pista: string;
  redirect_uri: string;
  redirect_sugerido: string;
  tenant: string;
  data_center: string;
  origen: string;
  usa_centros: boolean;
  scopes: string;
}

@Injectable({ providedIn: 'root' })
export class CalendarsService {
  private readonly http = inject(HttpClient);
  private readonly base = `${environment.apiUrl}/api/v1/calendars`;

  // ── La lista ────────────────────────────────────────────────────────
  listar(): Observable<{ calendars: Calendario[] }> {
    return this.http.get<{ calendars: Calendario[] }>(this.base);
  }

  crear(cuerpo: { name: string; color?: string; description?: string;
                  origin?: 'propio' | 'equipo' | 'proyecto'; project_id?: number | null;
  }): Observable<{ calendar: Calendario }> {
    return this.http.post<{ calendar: Calendario }>(this.base, cuerpo);
  }

  editar(clave: string, cambios: Partial<Pick<Calendario,
    'name' | 'description' | 'color' | 'is_default'>>): Observable<{ calendar: Calendario }> {
    return this.http.patch<{ calendar: Calendario }>(`${this.base}/${clave}`, cambios);
  }

  borrar(clave: string): Observable<unknown> {
    return this.http.delete(`${this.base}/${clave}`);
  }

  /** La casilla y el color son de quien mira, no del calendario. */
  preferencia(clave: string, cambio: { visible?: boolean; color?: string; position?: number }) {
    return this.http.put<{ key: string; visible: boolean; color: string }>(
      `${this.base}/${encodeURIComponent(clave)}/preferencia`, cambio);
  }

  // ── Compartir ───────────────────────────────────────────────────────
  compartidos(clave: string) {
    return this.http.get<{ shares: { user_id: number | null; nombre: string; permission: Permiso }[] }>(
      `${this.base}/${clave}/compartido`);
  }

  compartir(clave: string, user_id: number | null, permission: Permiso) {
    return this.http.post(`${this.base}/${clave}/compartido`, { user_id, permission });
  }

  dejarDeCompartir(clave: string, userId: number | null) {
    return this.http.delete(`${this.base}/${clave}/compartido/${userId ?? 0}`);
  }

  // ── Eventos ─────────────────────────────────────────────────────────
  eventos(desde: Date, hasta: Date, claves?: string[]) {
    const params: Record<string, string> = {
      desde: desde.toISOString(), hasta: hasta.toISOString(),
    };
    if (claves?.length) { params['calendarios'] = claves.join(','); }
    return this.http.get<{ events: EventoAgenda[] }>(`${this.base}/eventos`, { params });
  }

  crearEvento(cuerpo: {
    calendar?: string; title: string; description?: string; location?: string;
    start_at: string; end_at?: string; all_day?: boolean; meeting_url?: string;
    project_id?: number | null;
  }) {
    return this.http.post<{ event: any }>(`${this.base}/eventos`, cuerpo);
  }

  editarEvento(id: number, cambios: Record<string, unknown>) {
    return this.http.patch<{ event: any }>(`${this.base}/eventos/${id}`, cambios);
  }

  borrarEvento(id: number) {
    return this.http.delete(`${this.base}/eventos/${id}`);
  }

  // ── Cuentas de fuera ────────────────────────────────────────────────
  cuentas() {
    return this.http.get<{ accounts: CuentaConectada[] }>(`${this.base}/cuentas`);
  }

  /** Devuelve la dirección a la que mandar el navegador. */
  urlDeConexion(provider: string, volver: string) {
    return this.http.get<{ url: string }>(
      `${this.base}/${provider}/conectar`, { params: { volver } });
  }

  desconectar(accountId: number) {
    return this.http.delete<{ revocar_manual: string; eventos_borrados: number }>(
      `${this.base}/cuentas/${accountId}`);
  }

  suscribir(url: string, name = '', color = '#0ea5e9') {
    return this.http.post<{ calendar: Calendario }>(
      `${this.base}/suscribir`, { url, name, color });
  }

  sincronizar(clave?: string) {
    return clave
      ? this.http.post(`${this.base}/${clave}/sincronizar`, {})
      : this.http.post(`${this.base}/sincronizar`, {});
  }

  // ── Configuración (administrador) ───────────────────────────────────
  proveedores() {
    return this.http.get<{ proveedores: TarjetaProveedor[] }>(`${this.base}/config/proveedores`);
  }

  guardarProveedor(provider: string, datos: Partial<TarjetaProveedor> & { client_secret?: string }) {
    return this.http.put<TarjetaProveedor>(`${this.base}/config/proveedores/${provider}`, datos);
  }

  /** Dice si el proveedor reconoce la aplicación, sin conectar ninguna cuenta. */
  comprobarProveedor(provider: string) {
    return this.http.post<{ ok: boolean; detalle: string }>(
      `${this.base}/config/proveedores/${provider}/comprobar`, {});
  }
}
