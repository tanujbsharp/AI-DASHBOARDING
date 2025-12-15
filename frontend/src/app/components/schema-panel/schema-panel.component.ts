import { Component, EventEmitter, Input, Output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { SchemaField } from '../../models';

@Component({
  selector: 'app-schema-panel',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="schema-panel">
      <div class="panel-header">
        <h3 class="panel-title">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="3" y="3" width="7" height="7"/>
            <rect x="14" y="3" width="7" height="7"/>
            <rect x="3" y="14" width="7" height="7"/>
            <rect x="14" y="14" width="7" height="7"/>
          </svg>
          Available Fields
        </h3>
        <span class="field-count">{{ fields.length }} fields</span>
      </div>
      
      <div class="search-box">
        <svg class="search-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="11" cy="11" r="8"/>
          <path d="m21 21-4.35-4.35"/>
        </svg>
        <input 
          type="text" 
          class="input search-input"
          placeholder="Search fields..."
          [(ngModel)]="searchTerm"
        >
      </div>
      
      <div class="fields-list">
        @for (field of filteredFields; track field.name; let i = $index) {
          <div 
            class="field-item animate-slide-in stagger-{{ (i % 10) + 1 }}"
            [class.selected]="isSelected(field)"
            (click)="toggleField(field)"
            [attr.data-tooltip]="getFieldTooltip(field)"
          >
            <div class="field-main">
              <span class="field-name font-mono">{{ field.name }}</span>
              <div class="field-badges">
                <span class="type-badge" [class]="'type-' + getTypeClass(field.type)">
                  {{ field.type }}
                </span>
              </div>
            </div>
            <div class="field-meta">
              @if (field.sortable) {
                <span class="meta-icon" data-tooltip="Sortable">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="m3 16 4 4 4-4"/><path d="M7 20V4"/><path d="m21 8-4-4-4 4"/><path d="M17 4v16"/>
                  </svg>
                </span>
              }
              @if (field.filterable) {
                <span class="meta-icon" data-tooltip="Filterable">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/>
                  </svg>
                </span>
              }
            </div>
            <div class="select-indicator">
              @if (isSelected(field)) {
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <polyline points="20 6 9 17 4 12"/>
                </svg>
              } @else {
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M12 5v14M5 12h14"/>
                </svg>
              }
            </div>
          </div>
        }
        
        @if (filteredFields.length === 0) {
          <div class="empty-state">
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <circle cx="11" cy="11" r="8"/>
              <path d="m21 21-4.35-4.35"/>
            </svg>
            <p>No fields match your search</p>
          </div>
        }
      </div>
    </div>
  `,
  styles: [`
    .schema-panel {
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
        color: var(--accent-primary);
      }
    }
    
    .field-count {
      font-size: 0.75rem;
      color: var(--text-tertiary);
      background: var(--bg-tertiary);
      padding: var(--spacing-xs) var(--spacing-sm);
      border-radius: var(--radius-sm);
    }
    
    .search-box {
      position: relative;
      
      .search-icon {
        position: absolute;
        left: 12px;
        top: 50%;
        transform: translateY(-50%);
        color: var(--text-tertiary);
      }
      
      .search-input {
        padding-left: 40px;
      }
    }
    
    .fields-list {
      flex: 1;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: var(--spacing-xs);
    }
    
    .field-item {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      padding: var(--spacing-sm) var(--spacing-md);
      background: var(--bg-secondary);
      border: 1px solid var(--border-primary);
      border-radius: var(--radius-md);
      cursor: pointer;
      transition: all var(--transition-fast);
      
      &:hover {
        background: var(--bg-tertiary);
        border-color: var(--border-hover);
      }
      
      &.selected {
        background: rgba(8, 145, 178, 0.08);
        border-color: var(--accent-primary);
        
        .select-indicator {
          color: var(--accent-primary);
        }
      }
    }
    
    .field-main {
      flex: 1;
      min-width: 0;
    }
    
    .field-name {
      display: block;
      font-size: 0.8125rem;
      color: var(--text-primary);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    
    .field-badges {
      margin-top: 4px;
    }
    
    .type-badge {
      font-size: 0.625rem;
      padding: 2px 6px;
      border-radius: 4px;
      font-weight: 500;
      text-transform: uppercase;
      
      &.type-text { background: rgba(124, 58, 237, 0.1); color: #7c3aed; }
      &.type-keyword { background: rgba(8, 145, 178, 0.1); color: var(--accent-primary); }
      &.type-number { background: rgba(5, 150, 105, 0.1); color: var(--accent-success); }
      &.type-date { background: rgba(217, 119, 6, 0.1); color: var(--accent-warning); }
      &.type-boolean { background: rgba(220, 38, 38, 0.1); color: var(--accent-danger); }
      &.type-object { background: rgba(71, 85, 105, 0.1); color: var(--text-secondary); }
    }
    
    .field-meta {
      display: flex;
      gap: 4px;
    }
    
    .meta-icon {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 24px;
      height: 24px;
      border-radius: var(--radius-sm);
      background: var(--bg-tertiary);
      color: var(--text-tertiary);
    }
    
    .select-indicator {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 24px;
      height: 24px;
      color: var(--text-tertiary);
      transition: all var(--transition-fast);
    }
    
    .empty-state {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: var(--spacing-2xl);
      color: var(--text-tertiary);
      
      svg {
        margin-bottom: var(--spacing-md);
        opacity: 0.5;
      }
      
      p {
        font-size: 0.875rem;
      }
    }
  `]
})
export class SchemaPanelComponent {
  @Input() fields: SchemaField[] = [];
  @Input() selectedFields: SchemaField[] = [];
  @Output() selectionChange = new EventEmitter<SchemaField[]>();

  searchTerm = '';

  get filteredFields(): SchemaField[] {
    if (!this.searchTerm.trim()) return this.fields;
    const term = this.searchTerm.toLowerCase();
    return this.fields.filter(f => 
      f.name.toLowerCase().includes(term) || 
      f.type.toLowerCase().includes(term)
    );
  }

  isSelected(field: SchemaField): boolean {
    return this.selectedFields.some(f => f.name === field.name);
  }

  toggleField(field: SchemaField) {
    if (this.isSelected(field)) {
      this.selectionChange.emit(
        this.selectedFields.filter(f => f.name !== field.name)
      );
    } else {
      this.selectionChange.emit([...this.selectedFields, field]);
    }
  }

  getTypeClass(type: string): string {
    if (['integer', 'long', 'float', 'double'].includes(type)) return 'number';
    if (type === 'text' || type === 'search_as_you_type') return 'text';
    if (type === 'keyword') return 'keyword';
    if (type === 'date') return 'date';
    if (type === 'boolean') return 'boolean';
    return 'object';
  }

  getFieldTooltip(field: SchemaField): string {
    const traits = [];
    if (field.sortable) traits.push('Sortable');
    if (field.filterable) traits.push('Filterable');
    if (field.searchable) traits.push('Searchable');
    if (field.has_keyword) traits.push('Has keyword subfield');
    return traits.length ? traits.join(' • ') : field.type;
  }
}

