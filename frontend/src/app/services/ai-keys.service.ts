/**
 * La llave del motor de IA, editable desde el administrador.
 *
 * La pantalla nunca recibe la llave en claro: solo una pista del tipo
 * `gsk_…4f2a` para saber que hay una puesta y cuál es, sin poder
 * copiarla desde ahí.
 */
import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';

import { environment } from '../../environments/environment';

export interface LlaveIa {
  proveedor: string;
  etiqueta: string;
  configurada: boolean;
  /** 'empresa' si la puso un administrador, 'entorno' si es la de respaldo. */
  origen: string;
  pista: string;
  hay_respaldo_de_entorno: boolean;
}

@Injectable({ providedIn: 'root' })
export class AiKeysService {
  private readonly http = inject(HttpClient);
  private readonly base = `${environment.apiUrl}/api/v1/ai`;

  listar() {
    return this.http.get<{ proveedores: LlaveIa[] }>(`${this.base}/proveedores`);
  }

  /** Una llave vacía borra la de la empresa y vuelve a la del entorno. */
  guardar(proveedor: string, api_key: string) {
    return this.http.put<LlaveIa>(`${this.base}/proveedores/${proveedor}`, { api_key });
  }

  comprobar(proveedor: string) {
    return this.http.post<{ ok: boolean; detalle: string }>(
      `${this.base}/proveedores/${proveedor}/comprobar`, {});
  }
}
