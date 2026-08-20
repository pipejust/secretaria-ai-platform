/**
 * Calendario.
 *
 * La pantalla pinta lo que le da `/api/v1/calendars`. Dos decisiones que
 * explican casi todo lo demás:
 *
 * 1. **Apagar y encender un calendario no vuelve a pedir la agenda.**
 *    Cada entrada llega con `calendar`, la clave de a cuál pertenece, y
 *    el filtro es local. Pedirle al servidor la agenda otra vez por cada
 *    clic haría que el calendario parpadeara.
 * 2. **Aquí no se decide quién puede editar qué.** Eso llega resuelto en
 *    `permission` y `read_only`. Repetir la regla en la pantalla es
 *    tener dos versiones de ella y que una se quede vieja.
 */
import { ChangeDetectionStrategy, Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';

import {
  Calendario, CalendarsService, CuentaConectada, EventoAgenda, Origen, TarjetaProveedor,
} from '../../services/calendars.service';
import { ToastService } from '../../services/toast.service';

type Vista = 'mes' | 'semana' | 'agenda';

interface Celda {
  fecha: Date;
  delMes: boolean;
  hoy: boolean;
  eventos: EventoAgenda[];
}

/** Los grupos de la barra lateral, en el orden en que se enseñan. */
const GRUPOS: { titulo: string; origenes: Origen[] }[] = [
  { titulo: 'Míos',           origenes: ['propio'] },
  { titulo: 'De la empresa',  origenes: ['equipo', 'proyecto'] },
  { titulo: 'Conectados',     origenes: ['google', 'microsoft', 'zoho'] },
  { titulo: 'Suscritos',      origenes: ['suscrito'] },
  { titulo: 'De Acten',       origenes: ['derivado'] },
];

const COLORES = [
  '#6366f1', '#0ea5e9', '#10b981', '#f59e0b', '#ef4444',
  '#ec4899', '#8b5cf6', '#14b8a6', '#64748b', '#78350f',
];

@Component({
  selector: 'app-calendar',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './calendar.component.html',
  styleUrls: ['./calendar.component.css'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CalendarComponent implements OnInit {
  private readonly api = inject(CalendarsService);
  private readonly toast = inject(ToastService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  readonly colores = COLORES;

  readonly calendarios = signal<Calendario[]>([]);
  readonly eventos = signal<EventoAgenda[]>([]);
  readonly cuentas = signal<CuentaConectada[]>([]);
  readonly cargando = signal(true);
  readonly sincronizando = signal(false);

  readonly vista = signal<Vista>('mes');
  readonly ancla = signal(new Date());

  // Menús y modales
  readonly menuAbierto = signal(false);
  readonly menuCalendario = signal<string | null>(null);
  readonly modal = signal<'evento' | 'suscribir' | 'nuevo' | null>(null);

  // Formularios
  formEvento = {
    id: null as number | null, calendar: '', title: '', description: '',
    location: '', fecha: '', hora: '09:00', horaFin: '10:00', all_day: false,
  };
  formSuscribir = { url: '', name: '' };
  formNuevo = { name: '', color: COLORES[0], origin: 'propio' as 'propio' | 'equipo' };
  guardando = signal(false);

  // ── Derivados de la lista ──────────────────────────────────────────
  readonly visibles = computed(() =>
    new Set(this.calendarios().filter(c => c.visible).map(c => c.key)));

  readonly grupos = computed(() => {
    const cal = this.calendarios();
    return GRUPOS
      .map(g => ({ titulo: g.titulo, items: cal.filter(c => g.origenes.includes(c.origin)) }))
      .filter(g => g.items.length > 0);
  });

  /** Dónde se puede crear: sin uno de estos, el botón «Nuevo evento» sobra. */
  readonly escribibles = computed(() =>
    this.calendarios().filter(c => !c.read_only && c.origin !== 'derivado'));

  readonly eventosVisibles = computed(() => {
    const on = this.visibles();
    return this.eventos().filter(e => on.has(e.calendar));
  });

  readonly colorPorClave = computed(() => {
    const m = new Map<string, string>();
    for (const c of this.calendarios()) { m.set(c.key, c.color); }
    return m;
  });

  readonly nombrePorClave = computed(() => {
    const m = new Map<string, string>();
    for (const c of this.calendarios()) { m.set(c.key, c.name); }
    return m;
  });

  /** Los calendarios que traen un fallo del proveedor: se enseña, no se calla. */
  readonly conProblema = computed(() =>
    this.calendarios().filter(c => c.sync_error || c.status === 'revoked'));

  // ── Rejilla ────────────────────────────────────────────────────────
  readonly celdas = computed<Celda[]>(() => {
    const base = this.ancla();
    const eventos = this.eventosVisibles();
    const hoy = new Date(); hoy.setHours(0, 0, 0, 0);

    let inicio: Date;
    let dias: number;
    if (this.vista() === 'semana') {
      inicio = new Date(base);
      inicio.setDate(base.getDate() - ((base.getDay() + 6) % 7));
      dias = 7;
    } else {
      const primero = new Date(base.getFullYear(), base.getMonth(), 1);
      inicio = new Date(primero);
      inicio.setDate(primero.getDate() - ((primero.getDay() + 6) % 7));
      dias = 42;
    }
    inicio.setHours(0, 0, 0, 0);

    const porDia = new Map<string, EventoAgenda[]>();
    for (const e of eventos) {
      const d = this.momento(e);
      if (!d) { continue; }
      const k = this.clave(d);
      (porDia.get(k) ?? porDia.set(k, []).get(k)!).push(e);
    }

    const salida: Celda[] = [];
    for (let i = 0; i < dias; i++) {
      const f = new Date(inicio);
      f.setDate(inicio.getDate() + i);
      salida.push({
        fecha: f,
        delMes: f.getMonth() === base.getMonth(),
        hoy: f.getTime() === hoy.getTime(),
        eventos: (porDia.get(this.clave(f)) ?? [])
          .sort((a, b) => (a.start_at || '').localeCompare(b.start_at || '')),
      });
    }
    return salida;
  });

  readonly agenda = computed(() => {
    const grupos = new Map<string, EventoAgenda[]>();
    for (const e of this.eventosVisibles()) {
      const d = this.momento(e);
      if (!d) { continue; }
      const k = this.clave(d);
      (grupos.get(k) ?? grupos.set(k, []).get(k)!).push(e);
    }
    return [...grupos.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([k, items]) => ({
        fecha: new Date(k + 'T00:00:00'),
        items: items.sort((a, b) => (a.start_at || '').localeCompare(b.start_at || '')),
      }));
  });

  readonly titulo = computed(() => {
    const d = this.ancla();
    if (this.vista() === 'semana') {
      const c = this.celdas();
      if (!c.length) { return ''; }
      const a = c[0].fecha, b = c[c.length - 1].fecha;
      return `${a.getDate()} – ${b.getDate()} de ${this.mes(b)} de ${b.getFullYear()}`;
    }
    return `${this.mes(d)} de ${d.getFullYear()}`;
  });

  // ── Ciclo de vida ──────────────────────────────────────────────────
  ngOnInit(): void {
    // El viaje de OAuth vuelve por aquí con un aviso en la URL.
    const q = this.route.snapshot.queryParamMap;
    const conectado = q.get('calendario_conectado');
    const error = q.get('calendario_error');
    if (conectado) {
      this.toast.success(`Cuenta de ${conectado} conectada. Ya se ven sus calendarios.`);
    } else if (error) {
      this.toast.error(`No se pudo conectar la cuenta: ${error}`);
    }
    if (conectado || error) {
      this.router.navigate([], { queryParams: {}, replaceUrl: true });
    }
    this.recargar();
  }

  recargar(): void {
    this.cargando.set(true);
    this.api.listar().subscribe({
      next: r => {
        this.calendarios.set(r.calendars);
        this.cargarEventos();
      },
      error: () => {
        this.cargando.set(false);
        this.toast.error('No se pudieron cargar los calendarios.');
      },
    });
    this.api.cuentas().subscribe({ next: r => this.cuentas.set(r.accounts) });
  }

  private cargarEventos(): void {
    const { desde, hasta } = this.ventana();
    this.api.eventos(desde, hasta).subscribe({
      next: r => { this.eventos.set(r.events); this.cargando.set(false); },
      error: () => { this.cargando.set(false); this.toast.error('No se pudo cargar la agenda.'); },
    });
  }

  /** Un mes de margen a cada lado: mover de mes no pide datos otra vez. */
  private ventana(): { desde: Date; hasta: Date } {
    const d = this.ancla();
    return {
      desde: new Date(d.getFullYear(), d.getMonth() - 1, 1),
      hasta: new Date(d.getFullYear(), d.getMonth() + 2, 0, 23, 59, 59),
    };
  }

  // ── Navegación ─────────────────────────────────────────────────────
  mover(paso: number): void {
    const d = new Date(this.ancla());
    if (this.vista() === 'semana') { d.setDate(d.getDate() + paso * 7); }
    else { d.setMonth(d.getMonth() + paso); }
    this.ancla.set(d);
    this.cargarEventos();
  }

  hoy(): void { this.ancla.set(new Date()); this.cargarEventos(); }
  cambiarVista(v: Vista): void { this.vista.set(v); }

  // ── La casilla y el color ──────────────────────────────────────────
  alternar(cal: Calendario): void {
    const visible = !cal.visible;
    // Se pinta ya y se guarda después: la casilla tiene que responder al
    // instante, y si el guardado falla se dice y se deshace.
    this.calendarios.update(l => l.map(c => c.key === cal.key ? { ...c, visible } : c));
    this.api.preferencia(cal.key, { visible }).subscribe({
      error: () => {
        this.calendarios.update(l => l.map(c => c.key === cal.key ? { ...c, visible: !visible } : c));
        this.toast.error('No se pudo guardar la preferencia.');
      },
    });
  }

  pintar(cal: Calendario, color: string): void {
    this.calendarios.update(l => l.map(c => c.key === cal.key ? { ...c, color } : c));
    this.menuCalendario.set(null);
    this.api.preferencia(cal.key, { color }).subscribe({
      error: () => this.toast.error('No se pudo guardar el color.'),
    });
  }

  // ── Conectar cuentas ───────────────────────────────────────────────
  conectar(provider: 'google' | 'microsoft' | 'zoho'): void {
    this.menuAbierto.set(false);
    this.api.urlDeConexion(provider, '/admin/calendar').subscribe({
      next: r => { window.location.href = r.url; },
      error: e => this.toast.error(
        e?.error?.detail ??
        `${provider} todavía no está dado de alta. Un administrador lo configura en Configuración → Calendarios.`),
    });
  }

  desconectar(cuenta: CuentaConectada): void {
    if (!confirm(`¿Desconectar ${cuenta.email || cuenta.etiqueta}? Se borran de Acten sus calendarios y sus eventos.`)) {
      return;
    }
    this.api.desconectar(cuenta.id).subscribe({
      next: r => {
        // Microsoft no tiene llamada para revocar: si no se dice, la
        // persona se queda creyendo que ya no tenemos acceso.
        this.toast.success(r.revocar_manual
          ? `Cuenta desconectada. El permiso se retira del todo en ${r.revocar_manual}`
          : 'Cuenta desconectada y permiso retirado.');
        this.recargar();
      },
      error: () => this.toast.error('No se pudo desconectar la cuenta.'),
    });
  }

  sincronizar(): void {
    this.sincronizando.set(true);
    this.api.sincronizar().subscribe({
      next: () => { this.sincronizando.set(false); this.recargar(); this.toast.success('Calendarios actualizados.'); },
      error: () => { this.sincronizando.set(false); this.toast.error('No se pudieron actualizar.'); },
    });
  }

  // ── Modales ────────────────────────────────────────────────────────
  abrirSuscribir(): void {
    this.menuAbierto.set(false);
    this.formSuscribir = { url: '', name: '' };
    this.modal.set('suscribir');
  }

  abrirNuevo(): void {
    this.menuAbierto.set(false);
    this.formNuevo = { name: '', color: COLORES[0], origin: 'propio' };
    this.modal.set('nuevo');
  }

  suscribir(): void {
    if (!this.formSuscribir.url.trim()) { return; }
    this.guardando.set(true);
    this.api.suscribir(this.formSuscribir.url.trim(), this.formSuscribir.name.trim()).subscribe({
      next: () => { this.guardando.set(false); this.modal.set(null); this.recargar();
                    this.toast.success('Calendario suscrito.'); },
      error: e => { this.guardando.set(false);
                    this.toast.error(e?.error?.detail ?? 'No se pudo leer esa dirección.'); },
    });
  }

  crearCalendario(): void {
    if (!this.formNuevo.name.trim()) { return; }
    this.guardando.set(true);
    this.api.crear({ name: this.formNuevo.name.trim(), color: this.formNuevo.color,
                     origin: this.formNuevo.origin }).subscribe({
      next: () => { this.guardando.set(false); this.modal.set(null); this.recargar(); },
      error: e => { this.guardando.set(false);
                    this.toast.error(e?.error?.detail ?? 'No se pudo crear.'); },
    });
  }

  borrarCalendario(cal: Calendario): void {
    this.menuCalendario.set(null);
    if (!confirm(`¿Borrar «${cal.name}»? Sus eventos propios pasan a tu calendario por defecto.`)) {
      return;
    }
    this.api.borrar(cal.key).subscribe({
      next: () => { this.recargar(); this.toast.success('Calendario borrado.'); },
      error: e => this.toast.error(e?.error?.detail ?? 'No se pudo borrar.'),
    });
  }

  sincronizarUno(cal: Calendario): void {
    this.menuCalendario.set(null);
    this.api.sincronizar(cal.key).subscribe({
      next: () => { this.cargarEventos(); this.toast.success(`«${cal.name}» actualizado.`); },
      error: e => this.toast.error(e?.error?.detail ?? 'No se pudo actualizar.'),
    });
  }

  // ── Eventos ────────────────────────────────────────────────────────
  nuevoEvento(dia?: Date): void {
    const destino = this.escribibles()[0];
    if (!destino) {
      this.toast.error('No tienes ningún calendario donde escribir.');
      return;
    }
    const f = dia ?? new Date();
    this.formEvento = {
      id: null, calendar: destino.key, title: '', description: '', location: '',
      fecha: this.clave(f), hora: '09:00', horaFin: '10:00', all_day: false,
    };
    this.modal.set('evento');
  }

  abrirEvento(e: EventoAgenda): void {
    if (e.kind === 'sesion' && e.session_id) {
      this.router.navigate(['/admin/curation', e.session_id]);
      return;
    }
    if (e.kind !== 'evento' || !e.editable) {
      // Lo de fuera y lo calculado no se edita aquí; abrir un formulario
      // que luego no guarda es peor que no abrirlo.
      if (e.url) { window.open(e.url, '_blank', 'noopener'); }
      return;
    }
    const d = this.aFecha(e.start_at);
    const fin = this.aFecha(e.end_at || '');
    this.formEvento = {
      id: Number(String(e.id).replace('evento-', '')),
      calendar: e.calendar, title: e.title, description: e.description ?? '',
      location: e.location ?? '', fecha: d ? this.clave(d) : '',
      hora: d ? this.hhmm(d) : '09:00', horaFin: fin ? this.hhmm(fin) : '',
      all_day: !!e.all_day,
    };
    this.modal.set('evento');
  }

  guardarEvento(): void {
    const f = this.formEvento;
    if (!f.title.trim() || !f.fecha) { return; }
    // La hora que se teclea es la del reloj de quien la teclea. Pegarle una
    // «Z» detrás la declaraba UTC: quien escribía las 9:00 en Bogotá veía
    // luego las 4:00, y el evento llegaba a Google a la hora equivocada.
    // Se construye el instante en local y se convierte.
    const inicio = f.all_day ? `${f.fecha}T00:00:00+00:00` : this.instante(f.fecha, f.hora);
    const fin = f.all_day
      ? `${f.fecha}T23:59:59+00:00`
      : (f.horaFin ? this.instante(f.fecha, f.horaFin) : '');
    const cuerpo = {
      calendar: f.calendar, title: f.title.trim(), description: f.description,
      location: f.location, start_at: inicio, end_at: fin, all_day: f.all_day,
    };
    this.guardando.set(true);
    const peticion = f.id
      ? this.api.editarEvento(f.id, cuerpo)
      : this.api.crearEvento(cuerpo);
    peticion.subscribe({
      next: r => {
        this.guardando.set(false);
        this.modal.set(null);
        this.cargarEventos();
        // Si el proveedor lo rechazó, el evento existe aquí pero no allá:
        // decirlo es la diferencia entre una hora ocupada y una que para
        // el resto está libre.
        const fallo = r?.event?.external_error;
        if (fallo) { this.toast.error(`Guardado en Acten, pero el proveedor lo rechazó: ${fallo}`); }
        else { this.toast.success('Evento guardado.'); }
      },
      error: e => { this.guardando.set(false);
                    this.toast.error(e?.error?.detail ?? 'No se pudo guardar el evento.'); },
    });
  }

  borrarEvento(): void {
    const id = this.formEvento.id;
    if (!id || !confirm('¿Borrar este evento? Si está en un calendario conectado, también se borra allá.')) {
      return;
    }
    this.api.borrarEvento(id).subscribe({
      next: () => { this.modal.set(null); this.cargarEventos(); this.toast.success('Evento borrado.'); },
      error: () => this.toast.error('No se pudo borrar.'),
    });
  }

  // ── Utilidades de pintado ──────────────────────────────────────────
  color(e: EventoAgenda): string { return this.colorPorClave().get(e.calendar) ?? '#94a3b8'; }
  nombreCalendario(clave: string): string { return this.nombrePorClave().get(clave) ?? ''; }

  hora(e: EventoAgenda): string {
    if (e.all_day) { return 'Todo el día'; }
    const d = this.aFecha(e.start_at);
    return d ? this.hhmm(d) : '';
  }

  diaCorto(d: Date): string {
    return d.toLocaleDateString('es-CO', { weekday: 'short', day: 'numeric', month: 'short' });
  }

  private mes(d: Date): string {
    const m = d.toLocaleDateString('es-CO', { month: 'long' });
    return m.charAt(0).toUpperCase() + m.slice(1);
  }

  /**
   * En qué día cae un evento, para colocarlo en la rejilla.
   *
   * Un evento de día entero llega como `2026-08-07T00:00:00+00:00`, y
   * `new Date(...)` lo convierte a la hora local: en Bogotá (UTC-5) eso
   * es el 6 a las 19:00, así que el 7 de agosto se pintaba el 6 y los
   * festivos salían todos un día antes. Para los de día entero la fecha
   * se construye en local a partir del `YYYY-MM-DD`, sin convertir nada.
   */
  private momento(e: EventoAgenda): Date | null {
    if (e.all_day && e.start_at.length >= 10) {
      const [a, m, d] = e.start_at.slice(0, 10).split('-').map(Number);
      return new Date(a, m - 1, d);
    }
    return this.aFecha(e.start_at);
  }

  private aFecha(v: string): Date | null {
    if (!v) { return null; }
    const d = new Date(v);
    return isNaN(d.getTime()) ? null : d;
  }

  private clave(d: Date): string {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }

  /** `2026-08-20` + `09:00` locales → el instante en ISO (UTC). */
  private instante(fecha: string, hora: string): string {
    const [a, m, d] = fecha.split('-').map(Number);
    const [hh, mm] = (hora || '00:00').split(':').map(Number);
    return new Date(a, m - 1, d, hh, mm, 0).toISOString();
  }

  private hhmm(d: Date): string {
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  }

  trackCal = (_: number, c: Calendario) => c.key;
  trackEv = (_: number, e: EventoAgenda) => e.id;
  trackCelda = (_: number, c: Celda) => c.fecha.getTime();
}
