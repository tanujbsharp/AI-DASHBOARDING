import { Component, EventEmitter, Input, Output, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { DashboardService } from '../../services/dashboard.service';
import { Dashboard, DashboardItem } from '../../models/dashboard.models';

@Component({
  selector: 'app-add-to-dashboard-modal',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="modal-overlay" (click)="onClose()">
      <div class="modal-content" (click)="$event.stopPropagation()">
        <div class="modal-header">
          <h2>Add to Dashboard</h2>
          <button class="close-btn" (click)="onClose()">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="18" y1="6" x2="6" y2="18"/>
              <line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </div>
        
        <div class="modal-body">
          <div class="form-group">
            <label>Widget Title</label>
            <input 
              type="text" 
              [(ngModel)]="widgetTitle" 
              class="form-input"
              placeholder="Enter widget title"
            />
          </div>
          
          <div class="form-group">
            <label>Select Dashboard</label>
            <select [(ngModel)]="selectedDashboardId" class="form-select">
              <option [value]="null">Create new dashboard...</option>
              @for (dashboard of dashboards; track dashboard.id) {
                <option [value]="dashboard.id">{{ dashboard.name }}</option>
              }
            </select>
          </div>
          
          @if (selectedDashboardId === null) {
            <div class="form-group">
              <label>New Dashboard Name</label>
              <input 
                type="text" 
                [(ngModel)]="newDashboardName" 
                class="form-input"
                placeholder="Enter dashboard name"
              />
            </div>
          }
        </div>
        
        <div class="modal-footer">
          <button class="btn btn-secondary" (click)="onClose()">Cancel</button>
          <button 
            class="btn btn-primary" 
            (click)="onSave()"
            [disabled]="!canSave()"
          >
            Save
          </button>
        </div>
      </div>
    </div>
  `,
  styles: [`
    .modal-overlay {
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: rgba(0, 0, 0, 0.5);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 2000;
      animation: fadeIn 0.2s ease;
    }
    
    @keyframes fadeIn {
      from { opacity: 0; }
      to { opacity: 1; }
    }
    
    .modal-content {
      background: var(--bg-secondary, white);
      border-radius: var(--radius-lg, 12px);
      box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
      width: 90%;
      max-width: 500px;
      max-height: 90vh;
      display: flex;
      flex-direction: column;
      animation: slideUp 0.3s ease;
    }
    
    @keyframes slideUp {
      from { 
        opacity: 0;
        transform: translateY(20px);
      }
      to { 
        opacity: 1;
        transform: translateY(0);
      }
    }
    
    .modal-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: var(--spacing-lg, 20px);
      border-bottom: 1px solid var(--border-primary, #e5e7eb);
      
      h2 {
        margin: 0;
        font-size: 1.25rem;
        font-weight: 600;
        color: var(--text-primary, #1f2937);
      }
    }
    
    .close-btn {
      background: transparent;
      border: none;
      cursor: pointer;
      padding: 4px;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--text-secondary, #6b7280);
      transition: color 0.2s;
      
      &:hover {
        color: var(--text-primary, #1f2937);
      }
    }
    
    .modal-body {
      padding: var(--spacing-lg, 20px);
      flex: 1;
      overflow-y: auto;
    }
    
    .form-group {
      margin-bottom: var(--spacing-md, 16px);
      
      label {
        display: block;
        margin-bottom: var(--spacing-xs, 8px);
        font-size: 0.875rem;
        font-weight: 500;
        color: var(--text-secondary, #6b7280);
      }
    }
    
    .form-input,
    .form-select {
      width: 100%;
      padding: var(--spacing-sm, 12px);
      border: 1px solid var(--border-primary, #e5e7eb);
      border-radius: var(--radius-md, 8px);
      font-size: 0.9375rem;
      color: var(--text-primary, #1f2937);
      background: var(--bg-tertiary, #f9fafb);
      transition: all 0.2s;
      
      &:focus {
        outline: none;
        border-color: var(--accent-primary, #0891b2);
        box-shadow: 0 0 0 3px rgba(8, 145, 178, 0.1);
      }
    }
    
    .modal-footer {
      display: flex;
      gap: var(--spacing-sm, 12px);
      padding: var(--spacing-lg, 20px);
      border-top: 1px solid var(--border-primary, #e5e7eb);
      justify-content: flex-end;
    }
    
    .btn {
      padding: var(--spacing-sm, 12px) var(--spacing-md, 16px);
      border-radius: var(--radius-md, 8px);
      font-size: 0.875rem;
      font-weight: 500;
      cursor: pointer;
      transition: all 0.2s;
      border: 1px solid transparent;
      
      &.btn-secondary {
        background: var(--bg-tertiary, #f9fafb);
        color: var(--text-secondary, #6b7280);
        border-color: var(--border-primary, #e5e7eb);
        
        &:hover {
          background: var(--bg-card-hover, #f3f4f6);
        }
      }
      
      &.btn-primary {
        background: var(--accent-primary, #0891b2);
        color: white;
        border-color: var(--accent-primary, #0891b2);
        
        &:hover:not(:disabled) {
          background: var(--accent-secondary, #06b6d4);
        }
        
        &:disabled {
          opacity: 0.5;
          cursor: not-allowed;
        }
      }
    }
  `]
})
export class AddToDashboardModalComponent {
  private dashboardService = inject(DashboardService);
  
  @Input() widgetData!: {
    type: DashboardItem['type'];
    title: string;
    index_id?: string;
    query_payload: Record<string, any>;
    render_config: DashboardItem['render_config'];
  };
  
  @Output() saved = new EventEmitter<DashboardItem>();
  @Output() closed = new EventEmitter<void>();
  
  dashboards: Dashboard[] = [];
  selectedDashboardId: string | null = null;
  newDashboardName = '';
  widgetTitle = '';
  
  ngOnInit() {
    this.dashboards = this.dashboardService.getAllDashboards();
    this.widgetTitle = this.widgetData.title || 'Untitled Widget';
  }
  
  canSave(): boolean {
    if (!this.widgetTitle.trim()) return false;
    if (this.selectedDashboardId === null) {
      return !!this.newDashboardName.trim();
    }
    return true;
  }
  
  onSave() {
    if (!this.canSave()) return;
    
    let dashboardId = this.selectedDashboardId;
    
    // Create new dashboard if needed
    if (dashboardId === null) {
      const newDashboard = this.dashboardService.createDashboard(this.newDashboardName.trim());
      dashboardId = newDashboard.id;
    }
    
    // Get existing items to determine layout position
    const existingItems = this.dashboardService.getDashboardItems(dashboardId);
    const maxY = existingItems.length > 0 
      ? Math.max(...existingItems.map(item => item.layout.y + item.layout.rows))
      : 0;
    
    // Create the item
    const item = this.dashboardService.addItem({
      dashboard_id: dashboardId,
      type: this.widgetData.type,
      title: this.widgetTitle.trim(),
      index_id: this.widgetData.index_id,
      query_payload: this.widgetData.query_payload,
      render_config: this.widgetData.render_config,
      layout: {
        x: 0,
        y: maxY,
        cols: this.getDefaultCols(this.widgetData.type),
        rows: this.getDefaultRows(this.widgetData.type),
      },
    });
    
    this.saved.emit(item);
    this.onClose();
  }
  
  onClose() {
    this.closed.emit();
  }
  
  private getDefaultCols(type: DashboardItem['type']): number {
    switch (type) {
      case 'kpi_widget':
      case 'multi_kpi':
      case 'comparison':
        return 6; // 6 cols = 300px (6 * 50px)
      case 'bar_chart':
      case 'pie_chart':
      case 'line_chart':
        return 12; // 12 cols = 600px
      case 'table':
        return 18; // 18 cols = 900px
      default:
        return 8; // 8 cols = 400px
    }
  }
  
  private getDefaultRows(type: DashboardItem['type']): number {
    switch (type) {
      case 'kpi_widget':
      case 'multi_kpi':
      case 'comparison':
        return 3; // 3 rows = 150px (3 * 50px)
      case 'bar_chart':
      case 'pie_chart':
      case 'line_chart':
        return 5; // 5 rows = 250px
      case 'table':
        return 6; // 6 rows = 300px
      default:
        return 4; // 4 rows = 200px
    }
  }
}

