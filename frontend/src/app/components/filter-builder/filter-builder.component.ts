import { Component, EventEmitter, Input, Output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AdditionalFilter, FilterOperator, MegaFilter, SchemaField } from '../../models';

@Component({
  selector: 'app-filter-builder',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="filter-builder">
      <!-- Mega Filter Section -->
      <div class="section mega-filter-section">
        <div class="section-header">
          <h4 class="section-title">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="10"/>
              <polyline points="12 6 12 12 16 14"/>
            </svg>
            Time Period
          </h4>
        </div>
        <div class="mega-filter-options">
          @for (filter of megaFilters; track filter.id) {
            <button 
              class="mega-option"
              [class.active]="selectedMegaFilter === filter.id"
              (click)="selectMegaFilter(filter.id)"
            >
              {{ filter.label }}
            </button>
          }
        </div>
      </div>
      
      <!-- Additional Filters Section -->
      <div class="section additional-filters-section">
        <div class="section-header">
          <h4 class="section-title">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/>
            </svg>
            Additional Filters
          </h4>
          <button class="btn btn-ghost btn-sm" (click)="addFilter()">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
            </svg>
            Add Filter
          </button>
        </div>
        
        @if (additionalFilters.length === 0) {
          <div class="no-filters">
            <span>No additional filters applied</span>
          </div>
        } @else {
          <div class="filters-list">
            @for (filter of additionalFilters; track filter.id; let i = $index) {
              <div class="filter-row animate-slide-in">
                <select 
                  class="select field-select"
                  [(ngModel)]="filter.field"
                  (ngModelChange)="onFilterChange()"
                >
                  <option value="" disabled>Select field</option>
                  @for (field of filterableFields; track field.name) {
                    <option [value]="field.name">{{ field.name }}</option>
                  }
                </select>
                
                <select 
                  class="select operator-select"
                  [(ngModel)]="filter.operator"
                  (ngModelChange)="onFilterChange()"
                >
                  @for (op of operators; track op.value) {
                    <option [value]="op.value">{{ op.label }}</option>
                  }
                </select>
                
                @if (filter.operator === 'between') {
                  <input 
                    type="text" 
                    class="input value-input small"
                    placeholder="Min"
                    [(ngModel)]="filter.min_value"
                    (ngModelChange)="onFilterChange()"
                  >
                  <span class="between-separator">and</span>
                  <input 
                    type="text" 
                    class="input value-input small"
                    placeholder="Max"
                    [(ngModel)]="filter.max_value"
                    (ngModelChange)="onFilterChange()"
                  >
                } @else if (filter.operator === 'in') {
                  <input 
                    type="text" 
                    class="input value-input"
                    placeholder="Values (comma-separated)"
                    [ngModel]="filter.values?.join(', ')"
                    (ngModelChange)="updateInValues(filter, $event)"
                  >
                } @else {
                  <input 
                    type="text" 
                    class="input value-input"
                    placeholder="Value"
                    [(ngModel)]="filter.value"
                    (ngModelChange)="onFilterChange()"
                  >
                }
                
                <button class="remove-filter-btn" (click)="removeFilter(filter.id)">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                  </svg>
                </button>
              </div>
            }
          </div>
        }
      </div>
      
      <!-- Active Filters Chips -->
      @if (additionalFilters.length > 0) {
        <div class="active-filters">
          <span class="chips-label">Active:</span>
          <div class="filter-chips">
            @for (filter of additionalFilters; track filter.id) {
              @if (filter.field && filter.value) {
                <span class="chip chip-accent">
                  {{ filter.field }} {{ getOperatorSymbol(filter.operator) }} {{ filter.value }}
                  <button class="chip-remove" (click)="removeFilter(filter.id)">×</button>
                </span>
              }
            }
          </div>
        </div>
      }
    </div>
  `,
  styles: [`
    .filter-builder {
      display: flex;
      flex-direction: column;
      gap: var(--spacing-lg);
    }
    
    .section {
      background: var(--bg-secondary);
      border: 1px solid var(--border-primary);
      border-radius: var(--radius-md);
      padding: var(--spacing-md);
    }
    
    .section-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: var(--spacing-md);
    }
    
    .section-title {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      font-size: 0.875rem;
      font-weight: 600;
      color: var(--text-secondary);
      
      svg {
        color: var(--accent-tertiary);
      }
    }
    
    .btn-sm {
      padding: var(--spacing-xs) var(--spacing-sm);
      font-size: 0.75rem;
    }
    
    .mega-filter-options {
      display: flex;
      flex-wrap: wrap;
      gap: var(--spacing-sm);
    }
    
    .mega-option {
      padding: var(--spacing-sm) var(--spacing-md);
      background: var(--bg-tertiary);
      border: 1px solid var(--border-primary);
      border-radius: var(--radius-md);
      color: var(--text-secondary);
      font-size: 0.8125rem;
      font-weight: 500;
      cursor: pointer;
      transition: all var(--transition-fast);
      
      &:hover {
        border-color: var(--border-hover);
        color: var(--text-primary);
      }
      
      &.active {
        background: rgba(8, 145, 178, 0.1);
        border-color: var(--accent-primary);
        color: var(--accent-primary);
      }
    }
    
    .no-filters {
      padding: var(--spacing-md);
      text-align: center;
      color: var(--text-tertiary);
      font-size: 0.8125rem;
      border: 1px dashed var(--border-primary);
      border-radius: var(--radius-sm);
    }
    
    .filters-list {
      display: flex;
      flex-direction: column;
      gap: var(--spacing-sm);
    }
    
    .filter-row {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      padding: var(--spacing-sm);
      background: var(--bg-tertiary);
      border-radius: var(--radius-md);
    }
    
    .field-select {
      flex: 1;
      min-width: 150px;
    }
    
    .operator-select {
      width: 130px;
    }
    
    .value-input {
      flex: 1;
      min-width: 100px;
      
      &.small {
        width: 80px;
        flex: 0;
      }
    }
    
    .between-separator {
      color: var(--text-tertiary);
      font-size: 0.8125rem;
    }
    
    .remove-filter-btn {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 32px;
      height: 32px;
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
    
    .active-filters {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      flex-wrap: wrap;
    }
    
    .chips-label {
      font-size: 0.75rem;
      color: var(--text-tertiary);
    }
    
    .filter-chips {
      display: flex;
      flex-wrap: wrap;
      gap: var(--spacing-xs);
    }
    
    .chip-remove {
      background: none;
      border: none;
      color: inherit;
      cursor: pointer;
      font-size: 1rem;
      line-height: 1;
      margin-left: 4px;
      opacity: 0.7;
      
      &:hover {
        opacity: 1;
      }
    }
  `]
})
export class FilterBuilderComponent {
  @Input() megaFilters: MegaFilter[] = [];
  @Input() fields: SchemaField[] = [];
  @Input() selectedMegaFilter: string | null = null;
  @Input() additionalFilters: AdditionalFilter[] = [];
  
  @Output() megaFilterChange = new EventEmitter<string | null>();
  @Output() filtersChange = new EventEmitter<AdditionalFilter[]>();

  operators = [
    { value: 'equals', label: 'Equals' },
    { value: 'not_equals', label: 'Not Equals' },
    { value: 'contains', label: 'Contains' },
    { value: 'greater_than', label: 'Greater Than' },
    { value: 'less_than', label: 'Less Than' },
    { value: 'between', label: 'Between' },
    { value: 'in', label: 'In List' },
  ];

  private filterIdCounter = 0;

  get filterableFields(): SchemaField[] {
    return this.fields.filter(f => f.filterable);
  }

  selectMegaFilter(filterId: string) {
    if (this.selectedMegaFilter === filterId) {
      this.megaFilterChange.emit(null);
    } else {
      this.megaFilterChange.emit(filterId);
    }
  }

  addFilter() {
    const newFilter: AdditionalFilter = {
      id: `filter-${++this.filterIdCounter}`,
      field: '',
      operator: 'equals',
      value: '',
    };
    this.filtersChange.emit([...this.additionalFilters, newFilter]);
  }

  removeFilter(id: string) {
    this.filtersChange.emit(this.additionalFilters.filter(f => f.id !== id));
  }

  onFilterChange() {
    this.filtersChange.emit([...this.additionalFilters]);
  }

  updateInValues(filter: AdditionalFilter, value: string) {
    filter.values = value.split(',').map(v => v.trim()).filter(v => v);
    this.onFilterChange();
  }

  getOperatorSymbol(operator: FilterOperator): string {
    const symbols: Record<FilterOperator, string> = {
      equals: '=',
      not_equals: '≠',
      contains: '~',
      greater_than: '>',
      less_than: '<',
      between: '↔',
      in: '∈',
    };
    return symbols[operator] || '=';
  }
}

