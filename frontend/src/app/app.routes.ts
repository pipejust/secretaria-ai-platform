import { Routes } from '@angular/router';
import { LoginComponent } from './components/login/login.component';
import { AdminLayoutComponent } from './components/admin-layout/admin-layout.component';
import { DashboardComponent } from './components/dashboard/dashboard.component';
import { ProjectsComponent } from './components/projects/projects.component';
import { TemplatesComponent } from './components/templates/templates.component';
import { UsersComponent } from './components/users/users.component';
import { RolesComponent } from './components/roles/roles.component';
import { ProfileComponent } from './components/profile/profile.component';
import { SettingsComponent } from './components/settings/settings.component';
import { authGuard } from './guards/auth.guard';

export const routes: Routes = [
    { 
        path: 'login', 
        component: LoginComponent,
        title: 'Iniciar Sesión | Acten',
        data: { 
            description: 'Inicia sesión en Acten para gestionar tus asistentencias virtuales corporativas y administrar las actas de tus reuniones.',
            keywords: 'iniciar sesión, acten, asistentes virtuales, automatización actas',
            robots: 'index, follow'
        }
    },
    { 
        path: 'forgot-password', 
        loadComponent: () => import('./components/forgot-password/forgot-password').then(m => m.ForgotPassword),
        title: 'Recuperar Contraseña | Acten',
        data: { 
            description: 'Recupera el acceso a tu cuenta corporativa de Acten introduciendo tu correo electrónico.',
            robots: 'index, follow'
        }
    },
    { 
        path: 'reset-password', 
        loadComponent: () => import('./components/reset-password/reset-password').then(m => m.ResetPassword),
        title: 'Restablecer Contraseña | Acten',
        data: { robots: 'noindex, nofollow' }
    },
    {
        path: 'admin',
        component: AdminLayoutComponent,
        canActivate: [authGuard],
        children: [
            { 
                path: 'dashboard', 
                component: DashboardComponent,
                title: 'Panel de Control | Acten',
                data: { description: 'Resumen en tiempo real de métricas, actas generadas y tareas despachadas automáticamente por Inteligencia Artificial.', robots: 'noindex, nofollow' }
            },
            { 
                path: 'projects', 
                component: ProjectsComponent,
                title: 'Gestión de Proyectos | Acten',
                data: { description: 'Administra tus proyectos corporativos, mapeo de contactos y definición de rutas de integración hacia Trello, Jira, ClickUp o Azure.', robots: 'noindex, nofollow' }
            },
            { 
                path: 'templates', 
                component: TemplatesComponent,
                title: 'Plantillas Documentales | Acten',
                data: { description: 'Sincroniza y personaliza las plantillas para la generación automatizada de actas formales y reportes ejecutivos.', robots: 'noindex, nofollow' }
            },
            { 
                path: 'users', 
                component: UsersComponent,
                title: 'Usuarios | Acten',
                data: { description: 'Administra los accesos y credenciales del equipo a la plataforma de Inteligencia Artificial.', robots: 'noindex, nofollow' }
            },
            { 
                path: 'roles', 
                component: RolesComponent,
                title: 'Roles de Sistema | Acten',
                data: { robots: 'noindex, nofollow' }
            },
            {
                path: 'settings',
                component: SettingsComponent,
                title: 'Configuraciones Generales | Acten',
                data: { description: 'Configura las credenciales de API (Fireflies, Resend) y establece la conexión con plataformas de gestión de tareas externas.', robots: 'noindex, nofollow' }
            },
            {
                path: 'branding',
                loadComponent: () => import('./components/branding-settings/branding-settings.component').then(m => m.BrandingSettingsComponent),
                title: 'Marca / Branding | Acten',
                data: { description: 'Personaliza el nombre, logo, colores y datos de contacto de tu empresa.', robots: 'noindex, nofollow' },
            },
            {
                path: 'super/tenants',
                loadComponent: () => import('./components/super-tenants/super-tenants.component').then(m => m.SuperTenantsComponent),
                title: 'Empresas (Tenants) | Acten',
                data: { description: 'Gestión de empresas / tenants — sólo super-admins.', robots: 'noindex, nofollow' },
            },
            {
                path: 'profile',
                component: ProfileComponent,
                title: 'Mi Perfil | Acten',
                data: { robots: 'noindex, nofollow' }
            },
            {
                path: 'curation/:id',
                loadComponent: () => import('./components/curation-panel/curation-panel.component').then(m => m.CurationPanelComponent),
                title: 'Panel de Curaduría | Acten',
                data: { description: 'Modera, edita y despacha manualmente las tareas, compromisos y correos extraídos de la sesión virtual antes de ser enviados.', robots: 'noindex, nofollow' }
            },
            {
                path: 'pendientes',
                loadComponent: () => import('./components/pendientes/pendientes.component').then(m => m.PendientesComponent),
                title: 'Pendientes | Acten',
                data: { description: 'Trazabilidad transversal de tareas: vencidos, próximos, bloqueos y cumplimiento.', robots: 'noindex, nofollow' }
            },
            {
                path: 'reportes',
                loadComponent: () => import('./components/reportes/reportes.component').then(m => m.ReportesComponent),
                title: 'Reportes | Acten',
                data: { description: 'Reportes ejecutivos semanales y mensuales de actividad por proyecto.', robots: 'noindex, nofollow' }
            },
            {
                path: 'projects/:id',
                loadComponent: () => import('./components/project-detail/project-detail.component').then(m => m.ProjectDetailComponent),
                title: 'Detalle de Proyecto | Acten',
                data: { description: 'Resumen del proyecto: últimas decisiones, riesgos, tareas activas y reuniones recientes.', robots: 'noindex, nofollow' }
            },
            {
                path: 'ask',
                loadComponent: () => import('./components/ask/ask.component').then(m => m.AskComponent),
                title: 'Ask Acten | Acten',
                data: { description: 'Chat con RAG sobre tus actas anteriores.', robots: 'noindex, nofollow' }
            },
            {
                path: 'calendar',
                loadComponent: () => import('./components/calendar/calendar.component').then(m => m.CalendarComponent),
                title: 'Calendario | Acten',
                data: { description: 'Conecta tu Google o Microsoft Calendar.', robots: 'noindex, nofollow' }
            },
            {
                path: 'outputs/:id',
                loadComponent: () => import('./components/role-outputs/role-outputs.component').then(m => m.RoleOutputsComponent),
                title: 'Outputs | Acten',
                data: { description: 'Genera artefactos role-específicos a partir del acta.', robots: 'noindex, nofollow' }
            },
            { path: '', redirectTo: 'dashboard', pathMatch: 'full' }
        ]
    },
    { path: '', redirectTo: '/login', pathMatch: 'full' },
    { path: '**', redirectTo: '/login' }
];
