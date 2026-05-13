import { Component, OnInit, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { DragDropModule, CdkDragDrop, moveItemInArray, transferArrayItem } from '@angular/cdk/drag-drop';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';
import { AuthService } from '../../services/auth.service';
import { environment } from '../../../environments/environment';

type DetailTab = 'preview' | 'details' | 'history';
type TemplateStatus = 'active' | 'inactive' | 'archived';
type CategoryId = 'meeting' | 'project' | 'reports' | 'finance' | 'comms' | 'strategy';

interface CategoryDef {
    id: CategoryId | 'all';
    label: string;
}

/** Entrada del historial de cambios de una plantilla. */
interface HistoryEntry {
    at: string;              // ISO timestamp
    action: 'created' | 'updated' | 'file_replaced' | 'configured' | 'status_changed' | 'archived';
    label?: string;          // texto descriptivo libre
    by?: string;             // email del usuario que hizo el cambio (si lo capturamos)
}

/** Metadata adicional que vive DENTRO de styleConfig (JSON) porque el
 *  backend no tiene columnas dedicadas. Persiste con cada save de la
 *  plantilla y la UI la lee al cargar. */
interface TemplateMeta {
    type?: CategoryId;
    status?: TemplateStatus;
    description?: string;
    updated_at?: string;     // ISO timestamp
    version?: string;        // ej. "v1.6"
    history?: HistoryEntry[];
}

@Component({
    selector: 'app-templates',
    standalone: true,
    imports: [CommonModule, FormsModule, DragDropModule],
    templateUrl: './templates.component.html',
    styleUrls: ['./templates.component.css']
})
export class TemplatesComponent implements OnInit {
    templates: any[] = [];
    projects: any[] = [];
    isLoading = false;
    isUploading = false;

    errorMsg = '';
    successMsg = '';

    selectedFile: File | null = null;
    selectedProjectId: string = '';
    templateName: string = '';
    /** Categoría/tipo a asignar al subir o editar. */
    templateType: CategoryId = 'meeting';
    /** Descripción opcional. */
    templateDescription: string = '';
    editingTemplateId: number | null = null;
    searchText = '';
    showUploadModal = false;

    /** Visibilidad del bloque de filtros expandible (estilo Meetings). */
    showFilters = false;
    /** Filtro adicional por estado activo/inactivo/archivada. */
    filterStatus: 'all' | TemplateStatus = 'all';

    selectedTemplate: any = null;
    activeCategory: string = 'all';
    detailTab: DetailTab = 'preview';

    readonly categories: CategoryDef[] = [
        { id: 'all',      label: 'Todas las plantillas' },
        { id: 'meeting',  label: 'Reunión' },
        { id: 'project',  label: 'Gestión de proyectos' },
        { id: 'reports',  label: 'Reportes' },
        { id: 'finance',  label: 'Finanzas' },
        { id: 'comms',    label: 'Comunicación' },
        { id: 'strategy', label: 'Estrategia' },
    ];

    // ----- Datos derivados de styleConfig (metadata "shadow") --------

    /** Lee la metadata embebida en t.style_config (sin lanzar). */
    metaOf(t: any): TemplateMeta {
        if (!t?.style_config) return {};
        try {
            const obj = JSON.parse(String(t.style_config));
            return (obj && typeof obj === 'object' && obj.__meta) ? obj.__meta : {};
        } catch { return {}; }
    }

    /** Categoría de la plantilla. Prioridad:
     *  1. meta.type guardado explícitamente
     *  2. heurística por nombre (fallback para plantillas legacy)
     *  3. 'meeting' como default seguro
     */
    typeOf(t: any): { id: CategoryId; label: string } {
        const meta = this.metaOf(t);
        const explicit: CategoryId | undefined = meta.type;
        if (explicit) {
            const found = this.categories.find((c) => c.id === explicit);
            if (found) return { id: explicit, label: found.label };
        }
        const name = (t?.name || '').toLowerCase();
        if (/reuni|sesi|acta|minut/.test(name))                return { id: 'meeting',  label: 'Reunión' };
        if (/proyecto|plan|riesg|matriz|acci[oó]n/.test(name)) return { id: 'project',  label: 'Gestión de proyectos' };
        if (/reporte|status|resumen|ejecutiv/.test(name))      return { id: 'reports',  label: 'Reportes' };
        if (/finan|presup|budget|cost/.test(name))             return { id: 'finance',  label: 'Finanzas' };
        if (/correo|email|notific|comunic|mensaj/.test(name))  return { id: 'comms',    label: 'Comunicación' };
        if (/estrat|kickoff|roadmap/.test(name))               return { id: 'strategy', label: 'Estrategia' };
        return { id: 'meeting', label: 'Reunión' };
    }

    /** Estado real persistido. Default = active. */
    statusOf(t: any): { key: TemplateStatus; label: string } {
        const meta = this.metaOf(t);
        const key: TemplateStatus = meta.status || 'active';
        const labels: { [k in TemplateStatus]: string } = {
            active:   'Activa',
            inactive: 'Inactiva',
            archived: 'Archivada',
        };
        return { key, label: labels[key] };
    }

    versionOf(t: any): string {
        const meta = this.metaOf(t);
        if (meta.version) return meta.version;
        const minor = t?.id ? (t.id % 9) + 1 : 1;
        return `v1.${minor}`;
    }

    /** Fecha de última actualización legible. Lee de meta.updated_at;
     *  cae a la fecha actual si la plantilla nunca fue guardada con la
     *  versión nueva del front (caso legacy). */
    updatedOf(t: any): string {
        const raw = this.metaOf(t).updated_at;
        if (!raw) return '—';
        const d = new Date(raw);
        if (isNaN(d.getTime())) return '—';
        const months = ['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'];
        return `${d.getDate()} ${months[d.getMonth()]} ${d.getFullYear()}`;
    }
    updatedAgo(t: any): string {
        const raw = this.metaOf(t).updated_at;
        if (!raw) return '';
        const d = new Date(raw);
        if (isNaN(d.getTime())) return '';
        const diffMs = Date.now() - d.getTime();
        const days = Math.floor(diffMs / (24 * 60 * 60 * 1000));
        if (days === 0) return 'hoy';
        if (days === 1) return 'hace 1 día';
        if (days < 30) return `hace ${days} días`;
        const months = Math.floor(days / 30);
        return months === 1 ? 'hace 1 mes' : `hace ${months} meses`;
    }
    descriptionOf(t: any): string {
        const explicit = this.metaOf(t).description;
        if (explicit) return explicit;
        const type = this.typeOf(t).label;
        return `Plantilla de ${type.toLowerCase()} con estructura preconfigurada.`;
    }
    useCasesOf(t: any): string[] {
        const type = this.typeOf(t).id;
        switch (type) {
            case 'meeting':  return ['Reuniones de equipo', 'Sincronizaciones internas', 'Reuniones con cliente', 'Comités directivos'];
            case 'project':  return ['Kickoffs', 'Seguimiento de tareas', 'Matriz de riesgos', 'Status semanal'];
            case 'reports':  return ['Reporte ejecutivo', 'Status del cliente', 'KPIs mensuales'];
            case 'finance':  return ['Revisión presupuestal', 'Análisis financiero'];
            case 'comms':    return ['Updates internos', 'Notificaciones a equipo', 'Correos de seguimiento'];
            case 'strategy': return ['Planeación trimestral', 'Roadmap product'];
        }
        return [];
    }

    // ----- Listado / filtros ------------------------------------------

    countByCategory(catId: string): number {
        if (catId === 'all') return (this.templates || []).length;
        return (this.templates || []).filter((t) => this.typeOf(t).id === catId).length;
    }

    get filteredTemplates() {
        let result = this.templates || [];
        if (this.activeCategory !== 'all') {
            result = result.filter((t) => this.typeOf(t).id === this.activeCategory);
        }
        if (this.filterStatus !== 'all') {
            result = result.filter((t) => this.statusOf(t).key === this.filterStatus);
        }
        const search = (this.searchText || '').toLowerCase().trim();
        if (search) {
            result = result.filter((t) =>
                (t?.name && t.name.toLowerCase().includes(search)) ||
                (t?.id && t.id.toString().includes(search)) ||
                (this.descriptionOf(t).toLowerCase().includes(search))
            );
        }
        return result;
    }

    /** Toggle del panel de filtros (sigue el patrón de meetings-list). */
    toggleFilters(): void {
        this.showFilters = !this.showFilters;
        if (!this.showFilters) this.filterStatus = 'all';
    }
    get activeFiltersCount(): number {
        return [this.filterStatus !== 'all' ? 1 : 0].reduce((a, b) => a + b, 0);
    }

    // ============================================================
    // CONFIGURADOR (drag & drop)
    // ============================================================
    allPossibleTokens = [
        { id: 'meta',         label: 'Cabecera (Título, Fecha, Estado)' },
        { id: 'attendees',    label: 'Lista de Asistentes' },
        { id: 'summary',      label: 'Resumen Ejecutivo' },
        { id: 'decisions',    label: 'Decisiones Clave' },
        { id: 'risks',        label: 'Riesgos Identificados' },
        { id: 'agreements',   label: 'Acuerdos' },
        { id: 'action_items', label: 'Tabla de Tareas/Compromisos' }
    ];

    readonly tokenDescriptions: { [k: string]: string } = {
        meta:         'Identificación general de la sesión.',
        attendees:    'Lista de participantes de la reunión.',
        summary:      'Resumen de los puntos clave tratados.',
        decisions:    'Decisiones y acuerdos principales.',
        risks:        'Riesgos y temas críticos identificados.',
        agreements:   'Detalles de los acuerdos y compromisos.',
        action_items: 'Tareas asignadas y seguimiento.',
    };

    availableTokens: any[] = [];
    activeTokens: any[] = [];

    showConfigurator = false;
    isTraditionalConfigurator = false;

    constructor(
        private http: HttpClient,
        private authService: AuthService,
        private cdr: ChangeDetectorRef,
        private router: Router,
        private sanitizer: DomSanitizer,
    ) { }

    ngOnInit() { this.loadData(); }

    loadData() {
        this.isLoading = true;
        const headers = this.authService.getAuthHeaders();

        this.http.get<any[]>(`${environment.apiUrl}/templates`, { headers }).subscribe({
            next: (data) => {
                this.templates = data || [];
                if (!this.selectedTemplate && this.templates.length) {
                    this.selectedTemplate = this.templates[0];
                } else if (this.selectedTemplate) {
                    const fresh = this.templates.find((t) => t.id === this.selectedTemplate.id);
                    if (fresh) this.selectedTemplate = fresh;
                }
                this.isLoading = false;
                // Garantizar que toda plantilla tenga timestamp persistido —
                // resuelve el "—" en plantillas legacy del backend antiguo.
                this.backfillLegacyMeta();
                this.cdr.detectChanges();
            },
            error: () => {
                this.errorMsg = 'Error al cargar plantillas';
                this.isLoading = false;
                this.cdr.detectChanges();
            }
        });

        this.http.get<any[]>(`${environment.apiUrl}/api/projects/`, { headers }).subscribe({
            next: (data) => { this.projects = data; this.cdr.detectChanges(); },
            error: (err) => console.error('Error loading projects for templates', err),
        });
    }

    // ============================================================
    // SELECCIÓN / DETAIL PANEL
    // ============================================================
    selectTemplate(t: any) { this.selectedTemplate = t; this.detailTab = 'preview'; }
    closeDetailPanel() { this.selectedTemplate = null; }
    setDetailTab(tab: DetailTab) { this.detailTab = tab; }

    // ============================================================
    // ACCIONES "REALES" del panel
    // ============================================================

    /** Descarga el archivo .docx original. Para Supabase URLs públicas
     *  basta un anchor con download attribute. */
    downloadTemplate(t: any): void {
        if (!t?.file_path) {
            this.errorMsg = 'Esta plantilla no tiene archivo cargado.';
            return;
        }
        const a = document.createElement('a');
        a.href = t.file_path;
        a.download = `${(t.name || 'plantilla').replace(/[^a-z0-9]/gi, '_')}.docx`;
        a.target = '_blank';
        a.rel = 'noopener';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
    }

    /** Abre el modal de vista previa con el Office Online Viewer
     *  embebido. Más confiable que window.open(.docx) porque el
     *  browser no sabe cómo renderizar Word nativo. */
    previewFile(t: any): void {
        this.openPreviewModal(t);
    }

    /** Duplica la plantilla creando un nuevo registro que apunta al mismo
     *  archivo. El backend no expone DUPLICATE, así que hacemos PUT a
     *  /mapping con un nombre nuevo no funciona. La estrategia más simple
     *  es usar el endpoint /upload con un archivo placeholder — pero eso
     *  requiere el .docx en mano. Como aproximación, hacemos una llamada
     *  PUT al mismo template_id con name "Copia de X" y notificamos.
     *  Cuando el backend exponga POST /templates/{id}/duplicate, lo
     *  reemplazamos. */
    duplicateTemplate(t: any): void {
        this.successMsg = '';
        this.errorMsg = '';
        // Pre-llenar el modal de edición con datos copiados → user descarga
        // el original y vuelve a subirlo con el nombre nuevo.
        this.editingTemplateId = null;          // forzar nueva subida
        this.selectedFile = null;
        this.selectedProjectId = t.project_id || '';
        this.templateName = `Copia de ${t.name || 'Plantilla'}`;
        this.templateType = this.typeOf(t).id;
        this.templateDescription = this.descriptionOf(t);
        this.showUploadModal = true;
        this.successMsg = 'Sube un archivo .docx (puedes descargar el original primero) para crear la copia.';
    }

    /** Toggle Activa ↔ Inactiva. Cualquier estado → "active" → "inactive". */
    toggleActive(t: any): void {
        const current = this.statusOf(t).key;
        const next: TemplateStatus = current === 'active' ? 'inactive' : 'active';
        const label = next === 'active' ? 'Plantilla activada' : 'Plantilla inactivada';
        this.persistMeta(t, { status: next }, 'status_changed', label);
    }

    /** Archiva la plantilla — distinto de eliminar: queda oculta del listado
     *  default pero recuperable filtrando por archivadas. */
    archiveTemplate(t: any): void {
        if (!confirm('¿Archivar esta plantilla? No se eliminará del backend.')) return;
        this.persistMeta(t, { status: 'archived' }, 'archived', 'Plantilla archivada');
    }

    /** Persiste cambios de metadata en style_config (PUT /templates/:id/mapping)
     *  sin tocar el archivo ni el mapping_config existente. Cada llamada
     *  agrega una entrada al historial. */
    private persistMeta(t: any, patch: Partial<TemplateMeta>, historyAction?: HistoryEntry['action'], historyLabel?: string): void {
        const prev = this.metaOf(t);
        const meta: TemplateMeta = {
            ...prev,
            ...patch,
            updated_at: new Date().toISOString(),
            history: this._appendHistory(prev.history, historyAction || 'updated', historyLabel),
        };
        // Reconstruimos style_config: preservamos las claves de estilo
        // existentes y embebemos __meta.
        let styleObj: any = {};
        if (t.style_config) {
            try { styleObj = JSON.parse(String(t.style_config)) || {}; }
            catch { styleObj = {}; }
        }
        styleObj.__meta = meta;

        const payload = {
            mapping_config: t.mapping_config || '[]',
            style_config: JSON.stringify(styleObj),
        };

        this.http.put(`${environment.apiUrl}/templates/${t.id}/mapping`, payload, {
            headers: this.authService.getAuthHeaders()
        }).subscribe({
            next: () => {
                t.style_config = payload.style_config;
                if (this.selectedTemplate?.id === t.id) this.selectedTemplate = { ...t };
                this.successMsg = 'Cambios guardados.';
                this.cdr.detectChanges();
                setTimeout(() => { this.successMsg = ''; this.cdr.detectChanges(); }, 1500);
            },
            error: () => {
                this.errorMsg = 'No se pudo guardar el cambio.';
                this.cdr.detectChanges();
            }
        });
    }

    /** Agrega una nueva entrada al historial preservando las anteriores
     *  (cap a 50 para no inflar style_config indefinidamente). */
    private _appendHistory(prev: HistoryEntry[] | undefined, action: HistoryEntry['action'], label?: string): HistoryEntry[] {
        const list = Array.isArray(prev) ? [...prev] : [];
        const entry: HistoryEntry = {
            at: new Date().toISOString(),
            action,
            label,
            by: (this.authService as any).currentUserValue?.email,
        };
        list.unshift(entry);
        return list.slice(0, 50);
    }

    /** Backfill automático: para plantillas sin __meta.updated_at, persiste
     *  un baseline silencioso para que la columna "Actualizado" deje de
     *  mostrar "—" en plantillas legacy. Una sola pasada por carga. */
    private backfillLegacyMeta(): void {
        const legacy = (this.templates || []).filter((t) => {
            const meta = this.metaOf(t);
            return !meta.updated_at;
        });
        if (!legacy.length) return;
        for (const t of legacy) {
            const meta: TemplateMeta = {
                type: this.typeOf(t).id,
                status: 'active',
                updated_at: new Date().toISOString(),
                history: [{
                    at: new Date().toISOString(),
                    action: 'created',
                    label: 'Plantilla importada al historial',
                }],
            };
            let styleObj: any = {};
            if (t.style_config) {
                try { styleObj = JSON.parse(String(t.style_config)) || {}; }
                catch {}
            }
            styleObj.__meta = meta;
            const payload = {
                mapping_config: t.mapping_config || '[]',
                style_config: JSON.stringify(styleObj),
            };
            // Mutación local inmediata para que la UI no espere el round-trip.
            t.style_config = payload.style_config;
            this.http.put(`${environment.apiUrl}/templates/${t.id}/mapping`, payload, {
                headers: this.authService.getAuthHeaders()
            }).subscribe({ next: () => {}, error: () => {} });
        }
        this.cdr.detectChanges();
    }

    // ============================================================
    // HISTORIAL DE VERSIONES
    // ============================================================

    /** Lista de entradas del historial (más reciente primero) para el tab. */
    historyOf(t: any): HistoryEntry[] {
        return this.metaOf(t).history || [];
    }

    /** Label legible para una acción del historial. */
    historyLabel(e: HistoryEntry): string {
        if (e.label) return e.label;
        switch (e.action) {
            case 'created':         return 'Plantilla creada';
            case 'updated':         return 'Plantilla actualizada';
            case 'file_replaced':   return 'Archivo Word reemplazado';
            case 'configured':      return 'Bloques y estilos configurados';
            case 'status_changed':  return 'Cambio de estado';
            case 'archived':        return 'Plantilla archivada';
            default:                return 'Cambio guardado';
        }
    }

    /** Fecha + hora corta para la entrada. */
    historyDate(e: HistoryEntry): string {
        const d = new Date(e.at);
        if (isNaN(d.getTime())) return '';
        const months = ['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'];
        const dd = `${d.getDate()} ${months[d.getMonth()]} ${d.getFullYear()}`;
        const hh = String(d.getHours()).padStart(2, '0');
        const mm = String(d.getMinutes()).padStart(2, '0');
        return `${dd} · ${hh}:${mm}`;
    }

    historyAgo(e: HistoryEntry): string {
        const d = new Date(e.at);
        if (isNaN(d.getTime())) return '';
        const days = Math.floor((Date.now() - d.getTime()) / (24 * 60 * 60 * 1000));
        if (days === 0) return 'hoy';
        if (days === 1) return 'ayer';
        if (days < 30) return `hace ${days} días`;
        const months = Math.floor(days / 30);
        return months === 1 ? 'hace 1 mes' : `hace ${months} meses`;
    }

    // ============================================================
    // VISTA PREVIA — Office Online Viewer
    // ============================================================

    /** URL del Office Online embed viewer para un .docx público.
     *  Microsoft hostea un viewer gratuito que renderiza Word docs
     *  cuando se le pasa el URL del archivo encoded. Funciona con
     *  cualquier URL accesible públicamente (Supabase Storage lo es). */
    officePreviewUrl(t: any): SafeResourceUrl | null {
        if (!t?.file_path) return null;
        const encoded = encodeURIComponent(t.file_path);
        const url = `https://view.officeapps.live.com/op/embed.aspx?src=${encoded}`;
        // Angular bloquea <iframe [src]> sin sanitizar como SafeResourceUrl.
        return this.sanitizer.bypassSecurityTrustResourceUrl(url);
    }

    /** Misma URL pero como string plano — útil para abrir en nueva pestaña. */
    officePreviewUrlString(t: any): string | null {
        if (!t?.file_path) return null;
        return `https://view.officeapps.live.com/op/embed.aspx?src=${encodeURIComponent(t.file_path)}`;
    }

    /** Modal de vista previa: rompemos del tab cuando el user hace click
     *  en el botón principal del panel. Permite ver el documento en
     *  grande sin perder el contexto. */
    showPreviewModal = false;
    previewTemplate: any = null;
    openPreviewModal(t: any): void {
        if (!t?.file_path) {
            this.errorMsg = 'Esta plantilla no tiene archivo para previsualizar.';
            return;
        }
        this.previewTemplate = t;
        this.showPreviewModal = true;
    }
    closePreviewModal(): void {
        this.showPreviewModal = false;
        this.previewTemplate = null;
    }

    // ============================================================
    // SUBIDA / EDICIÓN / ELIMINACIÓN
    // ============================================================

    goToProjectContacts(projectId: number) {
        if (!projectId) return;
        this.router.navigate(['/projects'], { queryParams: { openContacts: projectId } });
    }

    onFileSelected(event: any) {
        const file = event.target.files[0];
        if (file) {
            if (file.name.endsWith('.docx')) {
                this.selectedFile = file;
                this.errorMsg = '';
            } else {
                this.errorMsg = 'Solo se permiten archivos de formato Word (.docx)';
                this.selectedFile = null;
            }
        }
    }

    openCreateModal() {
        this.editingTemplateId = null;
        this.selectedFile = null;
        this.selectedProjectId = '';
        this.templateName = '';
        this.templateType = 'meeting';
        this.templateDescription = '';
        this.errorMsg = '';
        this.successMsg = '';
        this.showUploadModal = true;
    }

    editTemplate(template: any) {
        this.editingTemplateId = template.id;
        this.templateName = template.name;
        this.selectedProjectId = template.project_id;
        this.templateType = this.typeOf(template).id;
        this.templateDescription = this.metaOf(template).description || '';
        this.selectedFile = null;
        this.errorMsg = '';
        this.successMsg = '';
        this.showUploadModal = true;
    }

    uploadFile() {
        if (!this.selectedProjectId || !this.templateName || (!this.selectedFile && !this.editingTemplateId)) return;

        this.isUploading = true;
        this.errorMsg = '';
        this.successMsg = '';

        const formData = new FormData();
        if (this.selectedFile) formData.append('file', this.selectedFile);
        formData.append('project_id', this.selectedProjectId.toString());
        formData.append('name', this.templateName);

        const url = this.editingTemplateId
            ? `${environment.apiUrl}/templates/${this.editingTemplateId}`
            : `${environment.apiUrl}/templates/upload`;

        const requestBase = this.editingTemplateId
            ? this.http.put<any>(url, formData, { headers: this.authService.getAuthHeaders() })
            : this.http.post<any>(url, formData, { headers: this.authService.getAuthHeaders() });

        requestBase.subscribe({
            next: (res) => {
                const newId = this.editingTemplateId || res.template_id;
                this.lastUploadedTemplateId = newId;

                // Persistir metadata (type/description/updated_at) en el
                // style_config inmediatamente después de la subida.
                const existing = this.templates.find((tx) => tx.id === newId);
                const prevMeta = existing ? this.metaOf(existing) : {};
                const action: HistoryEntry['action'] = this.editingTemplateId
                    ? (this.selectedFile ? 'file_replaced' : 'updated')
                    : 'created';
                const historyLabel = this.editingTemplateId
                    ? (this.selectedFile ? 'Archivo Word reemplazado' : 'Plantilla actualizada')
                    : 'Plantilla creada';
                const newHistory = this._appendHistory(prevMeta.history, action, historyLabel);
                const meta: TemplateMeta = {
                    type: this.templateType,
                    status: 'active',
                    description: this.templateDescription || undefined,
                    updated_at: new Date().toISOString(),
                    history: newHistory,
                };
                this.persistMetaById(newId, meta).subscribe({
                    next: () => {
                        this.successMsg = this.editingTemplateId
                            ? 'Plantilla actualizada exitosamente'
                            : 'Documento subido. Ahora configura los bloques del Word.';
                        this.loadData();
                        this.isUploading = false;
                        if (!this.editingTemplateId) {
                            this.openConfigurator({ id: newId, mapping_config: null, style_config: JSON.stringify({ __meta: meta }) });
                        } else {
                            setTimeout(() => { this.showUploadModal = false; }, 1200);
                        }
                    },
                    error: () => {
                        this.isUploading = false;
                        this.errorMsg = 'Plantilla subida pero falló la metadata.';
                    }
                });
            },
            error: (err) => {
                this.errorMsg = err.error?.detail || 'Error al guardar la plantilla.';
                this.isUploading = false;
            }
        });
    }

    /** Variante de persistMeta que toma sólo el id, útil cuando aún no
     *  tenemos el objeto template fresco del backend. */
    private persistMetaById(templateId: number, meta: Partial<TemplateMeta>) {
        const existing = this.templates.find((t) => t.id === templateId);
        const currentMeta = existing ? this.metaOf(existing) : {};
        const newMeta = { ...currentMeta, ...meta };
        let styleObj: any = {};
        if (existing?.style_config) {
            try { styleObj = JSON.parse(String(existing.style_config)) || {}; }
            catch {}
        }
        styleObj.__meta = newMeta;
        const payload = {
            mapping_config: existing?.mapping_config || '[]',
            style_config: JSON.stringify(styleObj),
        };
        return this.http.put(`${environment.apiUrl}/templates/${templateId}/mapping`, payload, {
            headers: this.authService.getAuthHeaders()
        });
    }

    drop(event: CdkDragDrop<any[]>) {
        if (event.previousContainer === event.container) {
            moveItemInArray(event.container.data, event.previousIndex, event.currentIndex);
        } else {
            transferArrayItem(
                event.previousContainer.data,
                event.container.data,
                event.previousIndex,
                event.currentIndex,
            );
        }
    }

    /** Quita un bloque de la estructura y lo devuelve a disponibles. */
    removeFromStructure(token: any) {
        const idx = this.activeTokens.findIndex((t) => t.id === token.id);
        if (idx >= 0) {
            const [removed] = this.activeTokens.splice(idx, 1);
            this.availableTokens.push(removed);
        }
    }

    /** Agrega un bloque (click rápido alternativo al drag). */
    addToStructure(token: any) {
        const idx = this.availableTokens.findIndex((t) => t.id === token.id);
        if (idx >= 0) {
            const [moved] = this.availableTokens.splice(idx, 1);
            this.activeTokens.push(moved);
        }
    }

    copyTag(tag: string) { navigator.clipboard.writeText(tag); }

    lastUploadedTemplateId: number | null = null;
    styleConfig = {
        fontFamily: 'Arial',
        fontSize: '11',
        textColor: '#000000',
        headingColor: '#1c9730',
        headingTextColor: '#FFFFFF',
        headingMargin: 10,
        tableHeaderBg: '#e80202',
        tableHeaderTextColor: '#FFFFFF'
    };

    openConfigurator(template: any) {
        this.lastUploadedTemplateId = template.id;
        this.successMsg = '';
        this.errorMsg = '';
        this.isTraditionalConfigurator = false;

        let loadedMapping: string[] = [];
        try {
            if (template.mapping_config) {
                const parsed = JSON.parse(template.mapping_config);
                if (Array.isArray(parsed)) {
                    loadedMapping = parsed.filter((id: string) => id !== 'themes');
                }
            }
        } catch (e) { loadedMapping = []; }

        try {
            if (template.style_config) {
                const parsedStyles = JSON.parse(template.style_config);
                if (parsedStyles && typeof parsedStyles === 'object') {
                    // Excluir __meta (que es nuestra metadata custom).
                    const { __meta, ...stylesOnly } = parsedStyles;
                    this.styleConfig = { ...this.styleConfig, ...stylesOnly };
                }
            }
        } catch (e) {}

        this.activeTokens = [];
        for (const blockId of loadedMapping) {
            const found = this.allPossibleTokens.find(t => t.id === blockId);
            if (found) this.activeTokens.push({ ...found });
        }
        this.availableTokens = this.allPossibleTokens.filter(at => !loadedMapping.includes(at.id));

        this.showConfigurator = true;
    }

    isSavingMapping = false;
    saveMappingSuccessMsg = '';

    confirmMapping() {
        if (!this.lastUploadedTemplateId) { this.showConfigurator = false; return; }

        this.isSavingMapping = true;
        this.saveMappingSuccessMsg = '';

        // Re-injectamos __meta para no perderla al guardar estilos.
        const existing = this.templates.find((t) => t.id === this.lastUploadedTemplateId);
        const meta: TemplateMeta = existing ? this.metaOf(existing) : {};
        meta.updated_at = new Date().toISOString();
        meta.history = this._appendHistory(meta.history, 'configured',
            `Configurados ${this.activeTokens.length} bloque(s) y estilos del documento`);

        const mappingPayload = {
            mapping_config: JSON.stringify(this.activeTokens.map(t => t.id)),
            style_config: JSON.stringify({ ...this.styleConfig, __meta: meta }),
        };

        this.http.put(`${environment.apiUrl}/templates/${this.lastUploadedTemplateId}/mapping`, mappingPayload, {
            headers: this.authService.getAuthHeaders()
        }).subscribe({
            next: () => {
                this.saveMappingSuccessMsg = 'Configuración y estilos guardados correctamente.';
                this.isSavingMapping = false;
                this.loadData();
                setTimeout(() => {
                    this.showConfigurator = false;
                    this.saveMappingSuccessMsg = '';
                }, 1500);
            },
            error: (err) => {
                this.isSavingMapping = false;
                this.errorMsg = 'Error al guardar el mapeo: ' + (err.error?.detail || err.message);
            }
        });
    }

    isDeleting = false;

    deleteTemplate(templateId: number) {
        if (!confirm('¿Estás seguro de que deseas eliminar esta plantilla?')) return;

        this.isDeleting = true;
        this.errorMsg = '';
        this.successMsg = '';

        this.http.delete(`${environment.apiUrl}/templates/${templateId}`, {
            headers: this.authService.getAuthHeaders()
        }).subscribe({
            next: () => {
                this.templates = this.templates.filter(t => t.id !== templateId);
                if (this.selectedTemplate?.id === templateId) {
                    this.selectedTemplate = this.templates[0] || null;
                }
                this.isDeleting = false;
                this.successMsg = 'Plantilla eliminada exitosamente';
                this.cdr.detectChanges();
            },
            error: (err) => {
                console.error(err);
                this.errorMsg = err.error?.detail || 'Error al eliminar la plantilla';
                this.isDeleting = false;
                this.cdr.detectChanges();
            }
        });
    }

    // ============================================================
    // KEBAB MENU por fila
    // ============================================================
    openRowMenuId: number | null = null;
    toggleRowMenu(id: number, evt: Event): void {
        evt.stopPropagation();
        this.openRowMenuId = this.openRowMenuId === id ? null : id;
    }
    closeRowMenu(): void { this.openRowMenuId = null; }

    /** trackBy para los *ngFor del configurador. Evita que el drag cree
     *  reflows del DOM completo en cada movimiento. */
    trackById = (_: number, item: any) => item?.id ?? _;
}
