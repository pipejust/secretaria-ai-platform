import { Component, OnInit, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { DragDropModule, CdkDragDrop, moveItemInArray, transferArrayItem } from '@angular/cdk/drag-drop';
import { AuthService } from '../../services/auth.service';
import { environment } from '../../../environments/environment';

/** Tab activa del panel lateral de detalle de plantilla. */
type DetailTab = 'preview' | 'details' | 'history';

/** Categoría de plantilla (placeholder visual hasta que el backend
 *  exponga un campo `category`). Por ahora derivamos del proyecto o
 *  caemos a "all" como categoría agregada. */
interface TemplateCategory {
    id: string;
    label: string;
    icon: 'all' | 'meeting' | 'project' | 'reports' | 'finance' | 'comms' | 'strategy';
    /** Predicate function que devuelve true si la plantilla pertenece. */
    match?: (t: any) => boolean;
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
    editingTemplateId: number | null = null;
    searchText = '';
    showUploadModal = false;

    /** Plantilla seleccionada para el panel lateral derecho. Auto-set
     *  al cargar la primera vez si hay plantillas. */
    selectedTemplate: any = null;

    /** Categoría activa para filtrar la lista central. */
    activeCategory: string = 'all';

    /** Tab activa del panel de detalle (Vista previa / Detalles / Historial). */
    detailTab: DetailTab = 'preview';

    /** Categorías del sidebar — placeholders visuales con counters
     *  reales calculados sobre `templates`. El predicado `match` decide
     *  cuál pertenece a cada bucket. Cuando el backend exponga un campo
     *  `category` real, reemplazar los predicados por igualdad estricta. */
    readonly categories: TemplateCategory[] = [
        { id: 'all',      label: 'Todas las plantillas', icon: 'all' },
        { id: 'meeting',  label: 'Reunión',              icon: 'meeting',  match: (t) => /reuni|sesi|acta|minut/i.test(t?.name || '') },
        { id: 'project',  label: 'Gestión de proyectos', icon: 'project',  match: (t) => /proyecto|plan|riesg|matriz|acci[oó]n/i.test(t?.name || '') },
        { id: 'reports',  label: 'Reportes',             icon: 'reports',  match: (t) => /reporte|status|resumen|ejecutiv/i.test(t?.name || '') },
        { id: 'finance',  label: 'Finanzas',             icon: 'finance',  match: (t) => /finan|presup|budget|cost/i.test(t?.name || '') },
        { id: 'comms',    label: 'Comunicación',         icon: 'comms',    match: (t) => /correo|email|notific|comunic|mensaj/i.test(t?.name || '') },
        { id: 'strategy', label: 'Estrategia',           icon: 'strategy', match: (t) => /estrat|kickoff|roadmap/i.test(t?.name || '') },
    ];

    /** Cuántas plantillas hay en cada categoría — recalculado al cargar
     *  o cuando `templates` cambia. */
    countByCategory(catId: string): number {
        if (catId === 'all') return (this.templates || []).length;
        const cat = this.categories.find((c) => c.id === catId);
        if (!cat || !cat.match) return 0;
        return (this.templates || []).filter(cat.match).length;
    }

    /** Plantillas visibles en la lista central según búsqueda + categoría. */
    get filteredTemplates() {
        let result = this.templates || [];

        // Filtro por categoría
        if (this.activeCategory !== 'all') {
            const cat = this.categories.find((c) => c.id === this.activeCategory);
            if (cat?.match) result = result.filter(cat.match);
        }

        // Búsqueda libre
        const search = (this.searchText || '').toLowerCase().trim();
        if (search) {
            result = result.filter((t) =>
                (t?.name && t.name.toLowerCase().includes(search)) ||
                (t?.id && t.id.toString().includes(search)) ||
                (t?.project?.name && t.project.name.toLowerCase().includes(search))
            );
        }
        return result;
    }

    // ============================================================
    // CONFIGURADOR (drag & drop) — sin cambios funcionales
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

    /** Descripciones cortas para cada bloque del configurador. Se muestran
     *  en la columna "Bloques disponibles" del modal. */
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
    ) { }

    ngOnInit() { this.loadData(); }

    loadData() {
        this.isLoading = true;
        const headers = this.authService.getAuthHeaders();

        this.http.get<any[]>(`${environment.apiUrl}/templates`, { headers }).subscribe({
            next: (data) => {
                this.templates = data || [];
                // Auto-selección de la primera plantilla para el panel lateral.
                if (!this.selectedTemplate && this.templates.length) {
                    this.selectedTemplate = this.templates[0];
                }
                this.isLoading = false;
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

    selectTemplate(t: any) {
        this.selectedTemplate = t;
        this.detailTab = 'preview';
    }

    closeDetailPanel() { this.selectedTemplate = null; }

    setDetailTab(tab: DetailTab) { this.detailTab = tab; }

    /** Categoría inferida para una plantilla — útil para mostrar el badge
     *  de tipo en cada fila. */
    typeOf(t: any): { id: string; label: string; icon: TemplateCategory['icon'] } {
        for (const cat of this.categories) {
            if (cat.id === 'all') continue;
            if (cat.match && cat.match(t)) return { id: cat.id, label: cat.label, icon: cat.icon };
        }
        return { id: 'meeting', label: 'Reunión', icon: 'meeting' };
    }

    /** Estado visual de una plantilla. Hasta que el backend exponga
     *  un campo `status` real, lo derivamos del mapping_config: si la
     *  plantilla ya tiene bloques configurados → Activa; si no → Borrador. */
    statusOf(t: any): { key: 'active' | 'draft' | 'archived'; label: string } {
        const mc = t?.mapping_config;
        try {
            const parsed = mc ? JSON.parse(String(mc)) : null;
            if (Array.isArray(parsed) && parsed.length > 0) {
                return { key: 'active', label: 'Activa' };
            }
        } catch {}
        return { key: 'draft', label: 'Borrador' };
    }

    /** Versión derivada: si no existe en backend, generamos "v1.x"
     *  basado en el id (placeholder visual). */
    versionOf(t: any): string {
        if (t?.version) return `v${t.version}`;
        const minor = t?.id ? (t.id % 9) + 1 : 1;
        return `v1.${minor}`;
    }

    /** Fecha de actualización de la plantilla. Si no viene del backend,
     *  intentamos created_at; sino mostramos guión. */
    updatedOf(t: any): string {
        const raw = t?.updated_at || t?.created_at;
        if (!raw) return '—';
        const d = new Date(raw);
        if (isNaN(d.getTime())) return String(raw);
        const months = ['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'];
        return `${d.getDate()} ${months[d.getMonth()]} ${d.getFullYear()}`;
    }

    /** Tiempo relativo legible para el panel lateral ("hace 3 días"). */
    updatedAgo(t: any): string {
        const raw = t?.updated_at || t?.created_at;
        if (!raw) return '—';
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

    /** Descripción visual cuando el backend no tiene description. Usa el
     *  nombre como fallback con un copy genérico. */
    descriptionOf(t: any): string {
        if (t?.description) return t.description;
        const type = this.typeOf(t).label;
        return `Plantilla de ${type.toLowerCase()} con estructura preconfigurada.`;
    }

    /** Casos de uso del panel — placeholders visuales por tipo. */
    useCasesOf(t: any): string[] {
        const type = this.typeOf(t).id;
        switch (type) {
            case 'meeting':  return ['Reuniones de equipo', 'Sincronizaciones internas', 'Reuniones con cliente', 'Comités directivos'];
            case 'project':  return ['Kickoffs', 'Seguimiento de tareas', 'Matriz de riesgos', 'Status semanal'];
            case 'reports':  return ['Reporte ejecutivo', 'Status del cliente', 'KPIs mensuales'];
            case 'finance':  return ['Revisión presupuestal', 'Análisis financiero'];
            case 'comms':    return ['Updates internos', 'Notificaciones a equipo', 'Correos de seguimiento'];
            case 'strategy': return ['Planeación trimestral', 'Roadmap product'];
            default:         return ['Reuniones', 'Reportes', 'Seguimiento'];
        }
    }

    // ============================================================
    // SUBIDA / EDICIÓN / ELIMINACIÓN — sin cambios funcionales
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
        this.errorMsg = '';
        this.successMsg = '';
        this.showUploadModal = true;
    }

    editTemplate(template: any) {
        this.editingTemplateId = template.id;
        this.templateName = template.name;
        this.selectedProjectId = template.project_id;
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
                this.successMsg = this.editingTemplateId
                    ? 'Plantilla actualizada exitosamente'
                    : 'Documento subido con éxito. Ahora configura las etiquetas del Word.';
                this.lastUploadedTemplateId = this.editingTemplateId || res.template_id;
                this.loadData();
                this.isUploading = false;

                if (!this.editingTemplateId) {
                    this.openConfigurator({ id: res.template_id, mapping_config: null });
                } else {
                    setTimeout(() => { this.showUploadModal = false; }, 1500);
                }
            },
            error: (err) => {
                this.errorMsg = err.error?.detail || 'Error al guardar la plantilla.';
                this.isUploading = false;
            }
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

    /** Quita un bloque de la estructura activa y lo devuelve a "disponibles". */
    removeFromStructure(token: any) {
        const idx = this.activeTokens.findIndex((t) => t.id === token.id);
        if (idx >= 0) {
            const [removed] = this.activeTokens.splice(idx, 1);
            this.availableTokens.push(removed);
        }
    }

    copyTag(tag: string) {
        navigator.clipboard.writeText(tag);
    }

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
        } catch (e) {
            loadedMapping = [];
        }

        try {
            if (template.style_config) {
                const parsedStyles = JSON.parse(template.style_config);
                if (parsedStyles && typeof parsedStyles === 'object') {
                    this.styleConfig = { ...this.styleConfig, ...parsedStyles };
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
        if (!this.lastUploadedTemplateId) {
            this.showConfigurator = false;
            return;
        }

        this.isSavingMapping = true;
        this.saveMappingSuccessMsg = '';

        const mappingPayload = {
            mapping_config: JSON.stringify(this.activeTokens.map(t => t.id)),
            style_config: JSON.stringify(this.styleConfig)
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
}
