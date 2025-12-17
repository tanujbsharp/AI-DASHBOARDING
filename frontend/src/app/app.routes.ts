import { Routes } from '@angular/router';
import { AppComponent } from './app.component';
import { DashboardsListComponent } from './components/dashboards-list/dashboards-list.component';
import { DashboardBuilderComponent } from './components/dashboard-builder/dashboard-builder.component';

export const routes: Routes = [
  { path: '', component: AppComponent },
  { path: 'dashboards', component: DashboardsListComponent },
  { path: 'dashboards/:id', component: DashboardBuilderComponent },
  { path: '**', redirectTo: '' }, // Default to root, AppComponent handles it
];

