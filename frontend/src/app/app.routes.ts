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
        title: 'Iniciar Sesión | Secretaria AI',
        data: { 
            description: 'Inicia sesión en Secretaria AI para gestionar tus asistentencias virtuales corporativas y administrar las actas de tus reuniones.',
            keywords: 'iniciar sesión, secretaria ai, asistentes virtuales, automatización actas',
            robots: 'index, follow'
        }
    },
    { 
        path: 'forgot-password', 
        loadComponent: () => import('./components/forgot-password/forgot-password').then(m => m.ForgotPassword),
        title: 'Recuperar Contraseña | Secretaria AI',
        data: { 
            description: 'Recupera el acceso a tu cuenta corporativa de Secretaria AI introduciendo tu correo electrónico.',
            robots: 'index, follow'
        }
    },
    { 
        path: 'reset-password', 
        loadComponent: () => import('./components/reset-password/reset-password').then(m => m.ResetPassword),
        title: 'Restablecer Contraseña | Secretaria AI',
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
                title: 'Panel de Control | Secretaria AI',
                data: { description: 'Resumen en tiempo real de métricas, actas generadas y tareas despachadas automáticamente por Inteligencia Artificial.', robots: 'noindex, nofollow' }
            },
            { 
                path: 'projects', 
                component: ProjectsComponent,
                title: 'Gestión de Proyectos | Secretaria AI',
                data: { description: 'Administra tus proyectos corporativos, mapeo de contactos y definición de rutas de integración hacia Trello, Jira, ClickUp o Azure.', robots: 'noindex, nofollow' }
            },
            { 
                path: 'templates', 
                component: TemplatesComponent,
                title: 'Plantillas Documentales | Secretaria AI',
                data: { description: 'Sincroniza y personaliza las plantillas para la generación automatizada de actas formales y reportes ejecutivos.', robots: 'noindex, nofollow' }
            },
            { 
                path: 'users', 
                component: UsersComponent,
                title: 'Usuarios | Secretaria AI',
                data: { description: 'Administra los accesos y credenciales del equipo a la plataforma de Inteligencia Artificial.', robots: 'noindex, nofollow' }
            },
            { 
                path: 'roles', 
                component: RolesComponent,
                title: 'Roles de Sistema | Secretaria AI',
                data: { robots: 'noindex, nofollow' }
            },
            { 
                path: 'settings', 
                component: SettingsComponent,
                title: 'Configuraciones Generales | Secretaria AI',
                data: { description: 'Configura las credenciales de API (Fireflies, Resend) y establece la conexión con plataformas de gestión de tareas externas.', robots: 'noindex, nofollow' }
            },
            { 
                path: 'profile', 
                component: ProfileComponent,
                title: 'Mi Perfil | Secretaria AI',
                data: { robots: 'noindex, nofollow' }
            },
            { 
                path: 'curation/:id', 
                loadComponent: () => import('./components/curation-panel/curation-panel.component').then(m => m.CurationPanelComponent),
                title: 'Panel de Curaduría | Secretaria AI',
                data: { description: 'Modera, edita y despacha manualmente las tareas, compromisos y correos extraídos de la sesión virtual antes de ser enviados.', robots: 'noindex, nofollow' }
            },
            { path: '', redirectTo: 'dashboard', pathMatch: 'full' }
        ]
    },
    { path: '', redirectTo: '/login', pathMatch: 'full' },
    { path: '**', redirectTo: '/login' }
];
