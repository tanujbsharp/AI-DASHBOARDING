import { Component, OnInit, inject, EventEmitter, Output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService, FieldInfo, IndexSchema } from '../../services/api.service';

interface Filter {
  id: string;
  field: string;
  operator: string;
  value: string;
}

interface MegaFilter {
  id: string;
  label: string;
  description: string;
}

@Component({
  selector: 'app-prompt-playground',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="playground">
      <!-- Header -->
      <div class="playground-header">
        <div class="header-title">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
            <line x1="3" y1="9" x2="21" y2="9"/>
            <line x1="9" y1="21" x2="9" y2="9"/>
          </svg>
          <h2>Report Builder</h2>
        </div>
        <button class="close-btn" (click)="onClose.emit()">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        </button>
      </div>

      <div class="playground-content">
        <!-- Left Panel: Field Selector -->
        <div class="panel fields-panel">
          <div class="panel-header">
            <h3>Available Fields</h3>
            <span class="field-count">{{ availableFields.length }} fields</span>
          </div>
          <div class="search-box">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
            </svg>
            <input type="text" [(ngModel)]="fieldSearch" placeholder="Search fields...">
          </div>
          <div class="fields-list">
            @for (field of filteredAvailableFields; track field.name) {
              <div 
                class="field-item" 
                [class.selected]="isFieldSelected(field.name)"
                (click)="toggleField(field)"
                [title]="getFieldTooltip(field)"
              >
                <div class="field-info">
                  <span class="field-name">{{ formatFieldName(field.name) }}</span>
                  <span class="field-type">{{ field.type }}</span>
                </div>
                <div class="field-badges">
                  @if (field.filterable) {
                    <span class="badge filter">filter</span>
                  }
                  @if (field.sortable) {
                    <span class="badge sort">sort</span>
                  }
                </div>
                <div class="field-action">
                  @if (isFieldSelected(field.name)) {
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                      <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/>
                    </svg>
                  } @else {
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                      <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
                    </svg>
                  }
                </div>
              </div>
            }
          </div>
        </div>

        <!-- Middle Panel: Selected Columns & Filters -->
        <div class="panel config-panel">
          <!-- Selected Columns -->
          <div class="section">
            <div class="section-header">
              <h3>Selected Columns</h3>
              <span class="count">{{ selectedColumns.length }}</span>
            </div>
            <div class="selected-columns">
              @if (selectedColumns.length === 0) {
                <div class="empty-state">
                  <p>Click fields from the left panel to add columns</p>
                </div>
              } @else {
                @for (col of selectedColumns; track col; let i = $index) {
                  <div class="column-item">
                    <span class="column-number">{{ i + 1 }}</span>
                    <span class="column-name">{{ formatFieldName(col) }}</span>
                    <div class="column-actions">
                      <button class="icon-btn" (click)="moveColumn(i, -1)" [disabled]="i === 0" title="Move up">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                          <polyline points="18 15 12 9 6 15"/>
                        </svg>
                      </button>
                      <button class="icon-btn" (click)="moveColumn(i, 1)" [disabled]="i === selectedColumns.length - 1" title="Move down">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                          <polyline points="6 9 12 15 18 9"/>
                        </svg>
                      </button>
                      <button class="icon-btn remove" (click)="removeColumn(i)" title="Remove">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                          <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                        </svg>
                      </button>
                    </div>
                  </div>
                }
              }
            </div>
          </div>

          <!-- Mega Filter -->
          <div class="section">
            <div class="section-header">
              <h3>Report Context (Mega Filter)</h3>
            </div>
            <div class="mega-filters">
              @for (mf of megaFilters; track mf.id) {
                <button 
                  class="mega-filter-btn" 
                  [class.active]="selectedMegaFilter === mf.id"
                  (click)="selectMegaFilter(mf.id)"
                >
                  {{ mf.label }}
                </button>
              }
            </div>
            <div class="custom-mega-filter">
              <input 
                type="text" 
                [(ngModel)]="customMegaFilter" 
                placeholder="Or type custom context... e.g., 'trainings from last week'"
                (focus)="selectedMegaFilter = ''"
              >
            </div>
          </div>

          <!-- Additional Filters -->
          <div class="section">
            <div class="section-header">
              <h3>Additional Filters</h3>
              <button class="add-btn" (click)="addFilter()">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
                </svg>
                Add Filter
              </button>
            </div>
            <div class="filters-list">
              @for (filter of filters; track filter.id; let i = $index) {
                <div class="filter-row">
                  <select [(ngModel)]="filter.field" class="filter-field">
                    <option value="">Select field...</option>
                    @for (field of filterableFields; track field.name) {
                      <option [value]="field.name">{{ formatFieldName(field.name) }}</option>
                    }
                  </select>
                  <select [(ngModel)]="filter.operator" class="filter-operator">
                    <option value="=">equals</option>
                    <option value="!=">not equals</option>
                    <option value=">">greater than</option>
                    <option value="<">less than</option>
                    <option value="contains">contains</option>
                  </select>
                  <input 
                    type="text" 
                    [(ngModel)]="filter.value" 
                    placeholder="Value..."
                    class="filter-value"
                    [attr.list]="'values-' + i"
                  >
                  <datalist [id]="'values-' + i">
                    @for (val of getFieldSampleValues(filter.field); track val) {
                      <option [value]="val">{{ val }}</option>
                    }
                  </datalist>
                  <button class="icon-btn remove" (click)="removeFilter(i)">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                      <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                    </svg>
                  </button>
                </div>
              }
              @if (filters.length === 0) {
                <p class="no-filters">No additional filters. Click "Add Filter" to add one.</p>
              }
            </div>
          </div>

          <!-- Sort -->
          <div class="section">
            <div class="section-header">
              <h3>Sort By</h3>
            </div>
            <div class="sort-config">
              <select [(ngModel)]="sortField" class="sort-field">
                <option value="">Default sort</option>
                @for (field of sortableFields; track field.name) {
                  <option [value]="field.name">{{ formatFieldName(field.name) }}</option>
                }
              </select>
              <select [(ngModel)]="sortOrder" class="sort-order">
                <option value="desc">Descending</option>
                <option value="asc">Ascending</option>
              </select>
            </div>
          </div>
        </div>

        <!-- Right Panel: Preview & Execute -->
        <div class="panel preview-panel">
          <div class="section">
            <div class="section-header">
              <h3>Generated Prompt</h3>
            </div>
            <div class="prompt-preview">
              <pre>{{ generatedPrompt }}</pre>
            </div>
          </div>

          <div class="section">
            <div class="section-header">
              <h3>Report Title</h3>
            </div>
            <input 
              type="text" 
              [(ngModel)]="reportTitle" 
              placeholder="e.g., Training Completion Report - December 2025"
              class="title-input"
            >
          </div>

          <div class="action-buttons">
            <button class="btn secondary" (click)="resetAll()">
              Reset All
            </button>
            <button 
              class="btn primary" 
              [disabled]="selectedColumns.length === 0 || isLoading"
              (click)="generateReport()"
            >
              @if (isLoading) {
                <span class="spinner"></span>
                Generating...
              } @else {
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <polygon points="5 3 19 12 5 21 5 3"/>
                </svg>
                Generate Report
              }
            </button>
          </div>
        </div>
      </div>
    </div>
  `,
  styles: [`
    .playground {
      height: 100%;
      display: flex;
      flex-direction: column;
      background: var(--bg-secondary);
      border-radius: var(--radius-lg);
      border: 1px solid var(--border-primary);
      overflow: hidden;
    }

    .playground-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: var(--spacing-md) var(--spacing-lg);
      background: white;
      border-bottom: 1px solid var(--border-primary);
    }

    .header-title {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      
      svg { color: var(--accent-primary); }
      h2 { font-size: 1.125rem; font-weight: 600; color: var(--text-primary); margin: 0; }
    }

    .close-btn {
      width: 32px;
      height: 32px;
      border-radius: 50%;
      border: none;
      background: var(--bg-tertiary);
      color: var(--text-secondary);
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: all 0.15s;
      
      &:hover { background: var(--accent-danger); color: white; }
    }

    .playground-content {
      flex: 1;
      display: grid;
      grid-template-columns: 280px 1fr 320px;
      gap: 1px;
      background: var(--border-primary);
      overflow: hidden;
    }

    .panel {
      background: white;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }

    .panel-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: var(--spacing-md);
      border-bottom: 1px solid var(--border-primary);
      
      h3 { font-size: 0.875rem; font-weight: 600; color: var(--text-primary); margin: 0; }
      .field-count { font-size: 0.75rem; color: var(--text-tertiary); }
    }

    .search-box {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      padding: var(--spacing-sm) var(--spacing-md);
      border-bottom: 1px solid var(--border-primary);
      
      svg { color: var(--text-tertiary); flex-shrink: 0; }
      input {
        flex: 1;
        border: none;
        background: none;
        font-size: 0.875rem;
        color: var(--text-primary);
        &::placeholder { color: var(--text-tertiary); }
        &:focus { outline: none; }
      }
    }

    .fields-list {
      flex: 1;
      overflow-y: auto;
      padding: var(--spacing-sm);
    }

    .field-item {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      padding: var(--spacing-sm) var(--spacing-md);
      border-radius: var(--radius-md);
      cursor: pointer;
      transition: all 0.15s;
      
      &:hover { background: var(--bg-tertiary); }
      &.selected { 
        background: rgba(8, 145, 178, 0.1); 
        border: 1px solid rgba(8, 145, 178, 0.3);
      }
    }

    .field-info {
      flex: 1;
      min-width: 0;
      
      .field-name {
        display: block;
        font-size: 0.8125rem;
        font-weight: 500;
        color: var(--text-primary);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .field-type {
        font-size: 0.6875rem;
        color: var(--text-tertiary);
      }
    }

    .field-badges {
      display: flex;
      gap: 4px;
    }

    .badge {
      font-size: 0.625rem;
      padding: 2px 6px;
      border-radius: 4px;
      text-transform: uppercase;
      font-weight: 500;
      
      &.filter { background: rgba(217, 119, 6, 0.1); color: var(--accent-warning); }
      &.sort { background: rgba(5, 150, 105, 0.1); color: var(--accent-success); }
    }

    .field-action {
      color: var(--accent-primary);
    }

    .config-panel {
      overflow-y: auto;
    }

    .section {
      padding: var(--spacing-md);
      border-bottom: 1px solid var(--border-primary);
      
      &:last-child { border-bottom: none; }
    }

    .section-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: var(--spacing-sm);
      
      h3 { font-size: 0.8125rem; font-weight: 600; color: var(--text-primary); margin: 0; }
      .count {
        font-size: 0.75rem;
        background: var(--accent-primary);
        color: white;
        padding: 2px 8px;
        border-radius: 10px;
      }
    }

    .add-btn {
      display: flex;
      align-items: center;
      gap: 4px;
      padding: 4px 10px;
      background: var(--bg-tertiary);
      border: 1px solid var(--border-primary);
      border-radius: var(--radius-sm);
      font-size: 0.75rem;
      color: var(--text-secondary);
      cursor: pointer;
      transition: all 0.15s;
      
      &:hover { border-color: var(--accent-primary); color: var(--accent-primary); }
    }

    .selected-columns {
      display: flex;
      flex-direction: column;
      gap: var(--spacing-xs);
    }

    .empty-state {
      padding: var(--spacing-lg);
      text-align: center;
      color: var(--text-tertiary);
      font-size: 0.8125rem;
    }

    .column-item {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      padding: var(--spacing-sm);
      background: var(--bg-tertiary);
      border-radius: var(--radius-md);
      
      .column-number {
        width: 24px;
        height: 24px;
        border-radius: 50%;
        background: var(--accent-primary);
        color: white;
        font-size: 0.75rem;
        font-weight: 600;
        display: flex;
        align-items: center;
        justify-content: center;
      }
      
      .column-name {
        flex: 1;
        font-size: 0.8125rem;
        color: var(--text-primary);
      }
    }

    .column-actions {
      display: flex;
      gap: 4px;
    }

    .icon-btn {
      width: 24px;
      height: 24px;
      border-radius: 4px;
      border: none;
      background: white;
      color: var(--text-tertiary);
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: all 0.15s;
      
      &:hover:not(:disabled) { color: var(--text-primary); background: var(--bg-secondary); }
      &:disabled { opacity: 0.3; cursor: not-allowed; }
      &.remove:hover { color: var(--accent-danger); }
    }

    .mega-filters {
      display: flex;
      flex-wrap: wrap;
      gap: var(--spacing-xs);
      margin-bottom: var(--spacing-sm);
    }

    .mega-filter-btn {
      padding: 6px 12px;
      border-radius: 20px;
      border: 1px solid var(--border-primary);
      background: white;
      font-size: 0.75rem;
      color: var(--text-secondary);
      cursor: pointer;
      transition: all 0.15s;
      
      &:hover { border-color: var(--accent-primary); color: var(--accent-primary); }
      &.active { 
        background: var(--accent-primary); 
        border-color: var(--accent-primary);
        color: white; 
      }
    }

    .custom-mega-filter input {
      width: 100%;
      padding: var(--spacing-sm);
      border: 1px solid var(--border-primary);
      border-radius: var(--radius-md);
      font-size: 0.8125rem;
      
      &:focus { outline: none; border-color: var(--accent-primary); }
    }

    .filters-list {
      display: flex;
      flex-direction: column;
      gap: var(--spacing-sm);
    }

    .filter-row {
      display: flex;
      gap: var(--spacing-xs);
      align-items: center;
    }

    .filter-field, .filter-operator, .filter-value {
      padding: 6px 10px;
      border: 1px solid var(--border-primary);
      border-radius: var(--radius-sm);
      font-size: 0.8125rem;
      
      &:focus { outline: none; border-color: var(--accent-primary); }
    }

    .filter-field { flex: 2; }
    .filter-operator { flex: 1; }
    .filter-value { flex: 2; }

    .no-filters {
      font-size: 0.8125rem;
      color: var(--text-tertiary);
      text-align: center;
      padding: var(--spacing-sm);
    }

    .sort-config {
      display: flex;
      gap: var(--spacing-sm);
      
      select {
        flex: 1;
        padding: var(--spacing-sm);
        border: 1px solid var(--border-primary);
        border-radius: var(--radius-md);
        font-size: 0.8125rem;
        
        &:focus { outline: none; border-color: var(--accent-primary); }
      }
    }

    .preview-panel {
      background: var(--bg-tertiary);
      overflow-y: auto;
    }

    .prompt-preview {
      background: var(--bg-secondary);
      border: 1px solid var(--border-primary);
      border-radius: var(--radius-md);
      padding: var(--spacing-md);
      
      pre {
        margin: 0;
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.75rem;
        color: var(--text-secondary);
        white-space: pre-wrap;
        word-break: break-word;
      }
    }

    .title-input {
      width: 100%;
      padding: var(--spacing-sm);
      border: 1px solid var(--border-primary);
      border-radius: var(--radius-md);
      font-size: 0.8125rem;
      background: white;
      
      &:focus { outline: none; border-color: var(--accent-primary); }
    }

    .action-buttons {
      display: flex;
      gap: var(--spacing-sm);
      padding: var(--spacing-md);
      margin-top: auto;
    }

    .btn {
      flex: 1;
      padding: var(--spacing-md);
      border-radius: var(--radius-md);
      font-size: 0.875rem;
      font-weight: 600;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: var(--spacing-sm);
      transition: all 0.15s;
      
      &.secondary {
        background: white;
        border: 1px solid var(--border-primary);
        color: var(--text-secondary);
        
        &:hover { border-color: var(--accent-primary); color: var(--accent-primary); }
      }
      
      &.primary {
        background: linear-gradient(135deg, var(--accent-primary), var(--accent-secondary));
        border: none;
        color: white;
        
        &:hover:not(:disabled) { transform: translateY(-1px); box-shadow: 0 4px 12px rgba(8,145,178,0.3); }
        &:disabled { opacity: 0.6; cursor: not-allowed; }
      }
    }

    .spinner {
      width: 16px;
      height: 16px;
      border: 2px solid rgba(255,255,255,0.3);
      border-top-color: white;
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
    }

    @keyframes spin { to { transform: rotate(360deg); } }
  `]
})
export class PromptPlaygroundComponent implements OnInit {
  private api = inject(ApiService);

  @Output() onClose = new EventEmitter<void>();
  @Output() onReportGenerated = new EventEmitter<any>();

  // Schema
  schema: IndexSchema | null = null;
  availableFields: FieldInfo[] = [];
  fieldSearch = '';

  // Selected columns
  selectedColumns: string[] = [];

  // Filters
  megaFilters: MegaFilter[] = [
    { id: 'completed', label: 'Completed trainings', description: 'All completed trainings' },
    { id: 'last_month', label: 'Last month', description: 'Records from the last 30 days' },
    { id: 'last_week', label: 'Last week', description: 'Records from the last 7 days' },
    { id: 'this_year', label: 'This year', description: 'Records from the current year' },
    { id: 'in_progress', label: 'In progress', description: 'Trainings not yet completed' },
  ];
  selectedMegaFilter = '';
  customMegaFilter = '';
  filters: Filter[] = [];

  // Sort
  sortField = '';
  sortOrder: 'asc' | 'desc' = 'desc';

  // Report
  reportTitle = '';
  isLoading = false;

  ngOnInit() {
    this.loadSchema();
  }

  loadSchema() {
    this.api.getSchema('module_consumption_data').subscribe({
      next: (schema) => {
        this.schema = schema;
        this.availableFields = schema.fields;
      },
      error: (err) => console.error('Failed to load schema:', err)
    });
  }

  get filteredAvailableFields(): FieldInfo[] {
    if (!this.fieldSearch) return this.availableFields;
    const search = this.fieldSearch.toLowerCase();
    return this.availableFields.filter(f => 
      f.name.toLowerCase().includes(search) || 
      f.type.toLowerCase().includes(search)
    );
  }

  get filterableFields(): FieldInfo[] {
    return this.availableFields.filter(f => f.filterable);
  }

  get sortableFields(): FieldInfo[] {
    return this.availableFields.filter(f => f.sortable);
  }

  get generatedPrompt(): string {
    let parts: string[] = [];

    // Mega filter
    if (this.selectedMegaFilter) {
      const mf = this.megaFilters.find(m => m.id === this.selectedMegaFilter);
      if (mf) parts.push(`Show ${mf.label.toLowerCase()}`);
    } else if (this.customMegaFilter) {
      parts.push(`Show ${this.customMegaFilter}`);
    } else {
      parts.push('Create a table report');
    }

    // Additional filters
    if (this.filters.length > 0) {
      const validFilters = this.filters.filter(f => f.field && f.value);
      if (validFilters.length > 0) {
        const filterStrs = validFilters.map(f => 
          `${this.formatFieldName(f.field)} ${f.operator} "${f.value}"`
        );
        parts.push(`\nFilter by: ${filterStrs.join(', ')}`);
      }
    }

    // Columns
    if (this.selectedColumns.length > 0) {
      parts.push(`\nShow columns in this order:\n${this.selectedColumns.map((c, i) => 
        `  ${i + 1}. ${this.formatFieldName(c)}`
      ).join('\n')}`);
    }

    // Sort
    if (this.sortField) {
      parts.push(`\nSort by ${this.formatFieldName(this.sortField)} ${this.sortOrder}`);
    }

    return parts.join('') || 'Select fields and filters to build your report...';
  }

  formatFieldName(name: string): string {
    return name
      .replace(/_/g, ' ')
      .replace(/\./g, ' › ')
      .split(' ')
      .map(w => w.charAt(0).toUpperCase() + w.slice(1))
      .join(' ');
  }

  getFieldTooltip(field: FieldInfo): string {
    let tooltip = `${field.name} (${field.type})`;
    if (field.sample_values?.length) {
      tooltip += `\nExamples: ${field.sample_values.slice(0, 3).join(', ')}`;
    }
    return tooltip;
  }

  isFieldSelected(fieldName: string): boolean {
    return this.selectedColumns.includes(fieldName);
  }

  toggleField(field: FieldInfo) {
    const index = this.selectedColumns.indexOf(field.name);
    if (index >= 0) {
      this.selectedColumns.splice(index, 1);
    } else {
      this.selectedColumns.push(field.name);
    }
  }

  moveColumn(index: number, direction: number) {
    const newIndex = index + direction;
    if (newIndex >= 0 && newIndex < this.selectedColumns.length) {
      const temp = this.selectedColumns[index];
      this.selectedColumns[index] = this.selectedColumns[newIndex];
      this.selectedColumns[newIndex] = temp;
    }
  }

  removeColumn(index: number) {
    this.selectedColumns.splice(index, 1);
  }

  selectMegaFilter(id: string) {
    this.selectedMegaFilter = this.selectedMegaFilter === id ? '' : id;
    if (this.selectedMegaFilter) {
      this.customMegaFilter = '';
    }
  }

  addFilter() {
    this.filters.push({
      id: Date.now().toString(),
      field: '',
      operator: '=',
      value: ''
    });
  }

  removeFilter(index: number) {
    this.filters.splice(index, 1);
  }

  getFieldSampleValues(fieldName: string): string[] {
    const field = this.availableFields.find(f => f.name === fieldName);
    return field?.sample_values || [];
  }

  resetAll() {
    this.selectedColumns = [];
    this.selectedMegaFilter = '';
    this.customMegaFilter = '';
    this.filters = [];
    this.sortField = '';
    this.sortOrder = 'desc';
    this.reportTitle = '';
  }

  generateReport() {
    if (this.selectedColumns.length === 0) return;

    this.isLoading = true;
    
    // Build the prompt and send to chat
    const prompt = this.generatedPrompt;
    
    this.api.chat(prompt, []).subscribe({
      next: (response) => {
        this.isLoading = false;
        this.onReportGenerated.emit({
          title: this.reportTitle || 'Generated Report',
          prompt,
          response
        });
      },
      error: (err) => {
        this.isLoading = false;
        console.error('Failed to generate report:', err);
      }
    });
  }
}

