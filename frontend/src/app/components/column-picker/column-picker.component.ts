import { Component, EventEmitter, Input, Output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { CdkDragDrop, DragDropModule, moveItemInArray } from '@angular/cdk/drag-drop';
import { SchemaField } from '../../models';

@Component({
  selector: 'app-column-picker',
  standalone: true,
  imports: [CommonModule, DragDropModule],
  template: `
    <div class="column-picker">
      <div class="panel-header">
        <h3 class="panel-title">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
            <line x1="9" y1="3" x2="9" y2="21"/>
          </svg>
          Selected Columns
        </h3>
        <span class="column-count">{{ columns.length }} selected</span>
      </div>
      
      @if (columns.length === 0) {
        <div class="empty-state">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>
            <polyline points="3.27 6.96 12 12.01 20.73 6.96"/>
            <line x1="12" y1="22.08" x2="12" y2="12"/>
          </svg>
          <p>Select fields from the schema panel</p>
          <span class="hint">Click on fields to add them as columns</span>
        </div>
      } @else {
        <div 
          cdkDropList
          class="columns-list"
          (cdkDropListDropped)="onDrop($event)"
        >
          @for (column of columns; track column.name; let i = $index) {
            <div class="column-item animate-slide-in" cdkDrag>
              <div class="drag-handle" cdkDragHandle>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <circle cx="9" cy="5" r="1"/><circle cx="9" cy="12" r="1"/><circle cx="9" cy="19" r="1"/>
                  <circle cx="15" cy="5" r="1"/><circle cx="15" cy="12" r="1"/><circle cx="15" cy="19" r="1"/>
                </svg>
              </div>
              <span class="column-index">{{ i + 1 }}</span>
              <span class="column-name font-mono">{{ column.name }}</span>
              <span class="column-type">{{ column.type }}</span>
              <button class="remove-btn" (click)="removeColumn(column)">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                </svg>
              </button>
              
              <div class="drag-placeholder" *cdkDragPlaceholder></div>
            </div>
          }
        </div>
        
        <div class="actions">
          <button class="btn btn-ghost" (click)="clearAll()">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="3 6 5 6 21 6"/>
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
            </svg>
            Clear All
          </button>
        </div>
      }
    </div>
  `,
  styles: [`
    .column-picker {
      display: flex;
      flex-direction: column;
      height: 100%;
      gap: var(--spacing-md);
    }
    
    .panel-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    
    .panel-title {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      font-size: 1rem;
      font-weight: 600;
      color: var(--text-primary);
      
      svg {
        color: var(--accent-secondary);
      }
    }
    
    .column-count {
      font-size: 0.75rem;
      color: var(--accent-primary);
      background: rgba(8, 145, 178, 0.1);
      padding: var(--spacing-xs) var(--spacing-sm);
      border-radius: var(--radius-sm);
    }
    
    .empty-state {
      flex: 1;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      text-align: center;
      color: var(--text-tertiary);
      padding: var(--spacing-2xl);
      border: 2px dashed var(--border-primary);
      border-radius: var(--radius-lg);
      
      svg {
        margin-bottom: var(--spacing-md);
        opacity: 0.4;
      }
      
      p {
        font-size: 0.9375rem;
        color: var(--text-secondary);
        margin-bottom: var(--spacing-xs);
      }
      
      .hint {
        font-size: 0.8125rem;
      }
    }
    
    .columns-list {
      flex: 1;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: var(--spacing-xs);
    }
    
    .column-item {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      padding: var(--spacing-sm) var(--spacing-md);
      background: var(--bg-secondary);
      border: 1px solid var(--border-primary);
      border-radius: var(--radius-md);
      transition: all var(--transition-fast);
      
      &:hover {
        border-color: var(--border-hover);
        background: var(--bg-tertiary);
      }
      
      &.cdk-drag-preview {
        box-shadow: var(--shadow-lg);
        border-color: var(--accent-primary);
      }
    }
    
    .drag-handle {
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: grab;
      color: var(--text-tertiary);
      padding: 4px;
      
      &:active {
        cursor: grabbing;
      }
      
      &:hover {
        color: var(--text-secondary);
      }
    }
    
    .column-index {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 24px;
      height: 24px;
      background: var(--bg-tertiary);
      border-radius: var(--radius-sm);
      font-size: 0.75rem;
      font-weight: 600;
      color: var(--accent-primary);
    }
    
    .column-name {
      flex: 1;
      font-size: 0.8125rem;
      color: var(--text-primary);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    
    .column-type {
      font-size: 0.6875rem;
      padding: 2px 8px;
      background: var(--bg-tertiary);
      border-radius: 4px;
      color: var(--text-tertiary);
      text-transform: uppercase;
    }
    
    .remove-btn {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 28px;
      height: 28px;
      background: transparent;
      border: none;
      border-radius: var(--radius-sm);
      color: var(--text-tertiary);
      cursor: pointer;
      transition: all var(--transition-fast);
      
      &:hover {
        background: rgba(255, 107, 107, 0.15);
        color: var(--accent-danger);
      }
    }
    
    .drag-placeholder {
      background: var(--bg-tertiary);
      border: 2px dashed var(--accent-primary);
      border-radius: var(--radius-md);
      min-height: 44px;
      transition: transform 250ms cubic-bezier(0, 0, 0.2, 1);
    }
    
    .cdk-drag-animating {
      transition: transform 250ms cubic-bezier(0, 0, 0.2, 1);
    }
    
    .actions {
      display: flex;
      justify-content: flex-end;
      padding-top: var(--spacing-sm);
      border-top: 1px solid var(--border-primary);
    }
  `]
})
export class ColumnPickerComponent {
  @Input() columns: SchemaField[] = [];
  @Output() columnsChange = new EventEmitter<SchemaField[]>();

  onDrop(event: CdkDragDrop<SchemaField[]>) {
    const newColumns = [...this.columns];
    moveItemInArray(newColumns, event.previousIndex, event.currentIndex);
    this.columnsChange.emit(newColumns);
  }

  removeColumn(column: SchemaField) {
    this.columnsChange.emit(this.columns.filter(c => c.name !== column.name));
  }

  clearAll() {
    this.columnsChange.emit([]);
  }
}

