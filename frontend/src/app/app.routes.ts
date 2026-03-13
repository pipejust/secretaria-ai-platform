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
    { path: 'login', component: LoginComponent },
    { path: 'forgot-password', loadComponent: () => import('./components/forgot-password/forgot-password').then(m => m.ForgotPassword) },
    { path: 'reset-password', loadComponent: () => import('./components/reset-password/reset-password').then(m => m.ResetPassword) },
    {
        path: 'admin',
        component: AdminLayoutComponent,
        canActivate: [authGuard],
        children: [
            { path: 'dashboard', component: DashboardComponent },
            { path: 'projects', component: ProjectsComponent },
            { path: 'templates', component: TemplatesComponent },
            { path: 'users', component: UsersComponent },
            { path: 'roles', component: RolesComponent },
            { path: 'settings', component: SettingsComponent },
            { path: 'profile', component: ProfileComponent },
            { path: 'curation/:id', loadComponent: () => import('./components/curation-panel/curation-panel.component').then(m => m.CurationPanelComponent) },
            { path: '', redirectTo: 'dashboard', pathMatch: 'full' }
        ]
    },
    { path: '', redirectTo: '/login', pathMatch: 'full' },
    { path: '**', redirectTo: '/login' }
];
