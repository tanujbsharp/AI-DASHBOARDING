import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router, RouterModule } from '@angular/router';
import { DashboardService } from '../../services/dashboard.service';
import { Dashboard } from '../../models/dashboard.models';

@Component({
  selector: 'app-dashboards-list',
  standalone: true,
  imports: [CommonModule, RouterModule],
  template: `
    <div class="dashboards-list">
      <div class="list-header">
        <div class="header-left">
          <button class="back-btn" (click)="goBack()">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="15 18 9 12 15 6"/>
            </svg>
            Back
          </button>
          <h1>My Dashboards</h1>
        </div>
      </div>
      
      <div class="list-content">
        @if (dashboards.length === 0) {
          <div class="empty-state">
            <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1">
              <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
              <line x1="3" y1="9" x2="21" y2="9"/>
              <line x1="9" y1="21" x2="9" y2="9"/>
            </svg>
            <p>No dashboards yet</p>
            <span class="hint">Add widgets from the chat interface using the + icon to create your first dashboard</span>
          </div>
        } @else {
          <div class="dashboards-grid">
            @for (dashboard of dashboards; track dashboard.id) {
              <div class="dashboard-card" (click)="openDashboard(dashboard.id)">
                <div class="card-header">
                  <h3>{{ dashboard.name }}</h3>
                  <button 
                    class="delete-btn" 
                    (click)="deleteDashboard($event, dashboard)"
                    title="Delete dashboard"
                  >
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                      <polyline points="3 6 5 6 21 6"/>
                      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                    </svg>
                  </button>
                </div>
                <div class="card-meta">
                  <span>Updated {{ formatDate(dashboard.updated_at) }}</span>
                </div>
              </div>
            }
          </div>
        }
      </div>
    </div>
  `,
  styles: [`
    .dashboards-list {
      height: 100vh;
      display: flex;
      flex-direction: column;
      background: var(--bg-primary, #f9fafb);
    }
    
    .list-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: var(--spacing-lg, 20px) var(--spacing-xl, 24px);
      background: white;
      border-bottom: 1px solid var(--border-primary, #e5e7eb);
      box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
    }
    
    .header-left {
      display: flex;
      align-items: center;
      gap: var(--spacing-md, 16px);
      
      h1 {
        margin: 0;
        font-size: 1.5rem;
        font-weight: 600;
        color: var(--text-primary, #1f2937);
      }
    }
    
    .back-btn {
      display: flex;
      align-items: center;
      gap: var(--spacing-xs, 8px);
      padding: var(--spacing-xs, 8px) var(--spacing-sm, 12px);
      background: var(--bg-tertiary, #f9fafb);
      border: 1px solid var(--border-primary, #e5e7eb);
      border-radius: var(--radius-md, 8px);
      color: var(--text-secondary, #6b7280);
      font-size: 0.875rem;
      cursor: pointer;
      transition: all 0.2s;
      
      &:hover {
        background: var(--bg-card-hover, #f3f4f6);
        color: var(--text-primary, #1f2937);
      }
    }
    
    .list-content {
      flex: 1;
      padding: var(--spacing-xl, 24px);
      overflow-y: auto;
    }
    
    .empty-state {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: var(--spacing-2xl, 48px);
      text-align: center;
      height: 100%;
      
      svg {
        color: var(--text-tertiary, #9ca3af);
        margin-bottom: var(--spacing-md, 16px);
      }
      
      p {
        font-size: 1.125rem;
        font-weight: 600;
        color: var(--text-secondary, #6b7280);
        margin-bottom: var(--spacing-xs, 8px);
      }
      
      .hint {
        font-size: 0.875rem;
        color: var(--text-tertiary, #9ca3af);
      }
    }
    
    .dashboards-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
      gap: var(--spacing-lg, 20px);
    }
    
    .dashboard-card {
      background: white;
      border: 1px solid var(--border-primary, #e5e7eb);
      border-radius: var(--radius-lg, 12px);
      padding: var(--spacing-lg, 20px);
      cursor: pointer;
      transition: all 0.2s;
      box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
      
      &:hover {
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
        transform: translateY(-2px);
      }
    }
    
    .card-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: var(--spacing-sm, 12px);
      
      h3 {
        margin: 0;
        font-size: 1.125rem;
        font-weight: 600;
        color: var(--text-primary, #1f2937);
      }
    }
    
    .delete-btn {
      width: 28px;
      height: 28px;
      display: flex;
      align-items: center;
      justify-content: center;
      background: transparent;
      border: none;
      border-radius: var(--radius-sm, 4px);
      color: var(--text-secondary, #6b7280);
      cursor: pointer;
      transition: all 0.2s;
      
      &:hover {
        background: #fee2e2;
        color: #dc2626;
      }
    }
    
    .card-meta {
      font-size: 0.8125rem;
      color: var(--text-tertiary, #9ca3af);
    }
  `]
})
export class DashboardsListComponent implements OnInit {
  private router = inject(Router);
  private dashboardService = inject(DashboardService);
  
  dashboards: Dashboard[] = [];
  
  ngOnInit() {
    this.loadDashboards();
  }
  
  loadDashboards() {
    this.dashboards = this.dashboardService.getAllDashboards();
  }
  
  openDashboard(id: string) {
    this.router.navigate(['/dashboards', id]);
  }
  
  deleteDashboard(event: Event, dashboard: Dashboard) {
    event.stopPropagation();
    if (confirm(`Delete dashboard "${dashboard.name}"? This cannot be undone.`)) {
      this.dashboardService.deleteDashboard(dashboard.id);
      this.loadDashboards();
    }
  }
  
  goBack() {
    this.router.navigate(['/']);
  }
  
  formatDate(dateString: string): string {
    const date = new Date(dateString);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);
    
    if (diffMins < 1) return 'just now';
    if (diffMins < 60) return `${diffMins} minute${diffMins > 1 ? 's' : ''} ago`;
    if (diffHours < 24) return `${diffHours} hour${diffHours > 1 ? 's' : ''} ago`;
    if (diffDays < 7) return `${diffDays} day${diffDays > 1 ? 's' : ''} ago`;
    
    return date.toLocaleDateString();
  }
}

