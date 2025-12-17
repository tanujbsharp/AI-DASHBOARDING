import { bootstrapApplication } from '@angular/platform-browser';
import { appConfig } from './app/app.config';
import { AppShellComponent } from './app/app-shell.component';

bootstrapApplication(AppShellComponent, appConfig)
  .catch((err) => console.error(err));
