import { Component, EventEmitter, Input, Output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { SchemaField } from '../../models';

@Component({
  selector: 'app-results-table',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="results-table">
      <div class="table-header">
        <div class="header-left">
          <h3 class="table-title">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
              <line x1="3" y1="9" x2="21" y2="9"/>
              <line x1="9" y1="21" x2="9" y2="9"/>
            </svg>
            {{ title }}
          </h3>
          @if (total > 0) {
            <span class="results-count">
              Showing {{ results.length }} of {{ total }} results
              @if (queryTime) {
                <span class="query-time">({{ queryTime }}ms)</span>
              }
            </span>
          }
        </div>
        <div class="header-actions">
          @if (results.length > 0) {
            <button class="btn btn-secondary" (click)="exportCsv()">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                <polyline points="7 10 12 15 17 10"/>
                <line x1="12" y1="15" x2="12" y2="3"/>
              </svg>
              Export CSV
            </button>
          }
        </div>
      </div>
      
      <!-- Filter Chips -->
      @if (activeFilters.length > 0) {
        <div class="active-filters">
          @for (filter of activeFilters; track $index) {
            <span class="chip">{{ filter }}</span>
          }
        </div>
      }
      
      @if (isLoading) {
        <div class="loading-state">
          <div class="spinner large"></div>
          <p>Executing query...</p>
        </div>
      } @else if (error) {
        <div class="error-state">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <circle cx="12" cy="12" r="10"/>
            <line x1="12" y1="8" x2="12" y2="12"/>
            <line x1="12" y1="16" x2="12.01" y2="16"/>
          </svg>
          <p>{{ error }}</p>
          <button class="btn btn-secondary" (click)="retry.emit()">Try Again</button>
        </div>
      } @else if (results.length === 0) {
        <div class="empty-state">
          <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1">
            <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
            <line x1="3" y1="9" x2="21" y2="9"/>
            <line x1="9" y1="21" x2="9" y2="9"/>
          </svg>
          <p>No results yet</p>
          <span class="hint">Configure your filters and columns, then generate a query</span>
        </div>
      } @else {
        <div class="table-container">
          <table class="data-table">
            <thead>
              <tr>
                <th class="row-num">#</th>
                @for (col of columns; track col.name) {
                  <th 
                    [class.sortable]="col.sortable"
                    [class.sorted]="sortField === col.name"
                    (click)="col.sortable && onSort(col.name)"
                  >
                    <span class="th-content">
                      {{ col.name }}
                      @if (sortField === col.name) {
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                          @if (sortOrder === 'asc') {
                            <path d="m18 15-6-6-6 6"/>
                          } @else {
                            <path d="m6 9 6 6 6-6"/>
                          }
                        </svg>
                      }
                    </span>
                  </th>
                }
              </tr>
            </thead>
            <tbody>
              @for (row of results; track row['_id'] || $index; let i = $index) {
                <tr class="animate-fade-in" [style.animation-delay]="(i * 20) + 'ms'">
                  <td class="row-num">{{ i + 1 }}</td>
                  @for (col of columns; track col.name) {
                    <td [class]="'type-' + col.type">
                      {{ formatValue(row[col.name], col.type) }}
                    </td>
                  }
                </tr>
              }
            </tbody>
          </table>
        </div>
      }
      
      <!-- Generated Query Preview -->
      @if (generatedQuery) {
        <div class="query-preview">
          <div class="query-header" (click)="showQuery = !showQuery">
            <span class="query-label">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>
              </svg>
              Generated Elasticsearch Query
            </span>
            <svg 
              class="toggle-icon"
              [class.expanded]="showQuery"
              width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
            >
              <polyline points="6 9 12 15 18 9"/>
            </svg>
          </div>
          @if (showQuery) {
            <div class="query-display">
              @if (indexId) {
                <div class="query-index-info">
                  <strong>Index:</strong> {{ indexId }}
                </div>
              }
              <pre class="query-code font-mono">{{ getQueryWithIndex() | json }}</pre>
            </div>
          }
        </div>
      }
    </div>
  `,
  styles: [`
    .results-table {
      display: flex;
      flex-direction: column;
      gap: var(--spacing-md);
    }
    
    .table-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: var(--spacing-md);
    }
    
    .header-left {
      display: flex;
      align-items: center;
      gap: var(--spacing-md);
    }
    
    .table-title {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      font-size: 1rem;
      font-weight: 600;
      color: var(--text-primary);
      
      svg {
        color: var(--accent-success);
      }
    }
    
    .results-count {
      font-size: 0.8125rem;
      color: var(--text-tertiary);
    }
    
    .query-time {
      color: var(--accent-primary);
      margin-left: 4px;
    }
    
    .header-actions {
      display: flex;
      gap: var(--spacing-sm);
    }
    
    .active-filters {
      display: flex;
      flex-wrap: wrap;
      gap: var(--spacing-xs);
    }
    
    .loading-state,
    .error-state,
    .empty-state {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: var(--spacing-2xl) var(--spacing-xl);
      text-align: center;
      color: var(--text-tertiary);
      
      svg {
        margin-bottom: var(--spacing-md);
        opacity: 0.4;
      }
      
      p {
        font-size: 0.9375rem;
        color: var(--text-secondary);
        margin-bottom: var(--spacing-sm);
      }
      
      .hint {
        font-size: 0.8125rem;
      }
    }
    
    .spinner.large {
      width: 40px;
      height: 40px;
      border-width: 3px;
      margin-bottom: var(--spacing-md);
    }
    
    .error-state {
      svg {
        color: var(--accent-danger);
        opacity: 0.7;
      }
    }
    
    .table-container {
      overflow-x: auto;
      border: 1px solid var(--border-primary);
      border-radius: var(--radius-lg);
    }
    
    .data-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.8125rem;
      
      th, td {
        padding: var(--spacing-sm) var(--spacing-md);
        text-align: left;
        border-bottom: 1px solid var(--border-primary);
        white-space: nowrap;
      }
      
      th {
        background: var(--bg-tertiary);
        color: var(--text-secondary);
        font-weight: 600;
        font-size: 0.75rem;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        position: sticky;
        top: 0;
        
        &.sortable {
          cursor: pointer;
          
          &:hover {
            color: var(--text-primary);
            background: var(--bg-card-hover);
          }
        }
        
        &.sorted {
          color: var(--accent-primary);
        }
      }
      
      .th-content {
        display: flex;
        align-items: center;
        gap: 4px;
      }
      
      td {
        background: var(--bg-secondary);
        color: var(--text-primary);
        max-width: 300px;
        overflow: hidden;
        text-overflow: ellipsis;
        
        &.type-date {
          color: var(--accent-warning);
        }
        
        &.type-keyword {
          font-family: 'JetBrains Mono', monospace;
          font-size: 0.75rem;
        }
      }
      
      .row-num {
        width: 50px;
        text-align: center;
        color: var(--text-tertiary);
        font-size: 0.75rem;
      }
      
      tbody tr {
        transition: background var(--transition-fast);
        
        &:hover td {
          background: var(--bg-tertiary);
        }
        
        &:last-child td {
          border-bottom: none;
        }
      }
    }
    
    .query-preview {
      background: var(--bg-secondary);
      border: 1px solid var(--border-primary);
      border-radius: var(--radius-md);
      overflow: hidden;
    }
    
    .query-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: var(--spacing-sm) var(--spacing-md);
      background: var(--bg-tertiary);
      cursor: pointer;
      transition: background var(--transition-fast);
      
      &:hover {
        background: var(--bg-card-hover);
      }
    }
    
    .query-label {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      font-size: 0.8125rem;
      color: var(--text-secondary);
      
      svg {
        color: var(--accent-tertiary);
      }
    }
    
    .toggle-icon {
      color: var(--text-tertiary);
      transition: transform var(--transition-fast);
      
      &.expanded {
        transform: rotate(180deg);
      }
    }
    
    .query-display {
      margin-top: var(--spacing-sm);
    }

    .query-index-info {
      padding: var(--spacing-xs) var(--spacing-sm);
      background: rgba(8, 145, 178, 0.1);
      border: 1px solid rgba(8, 145, 178, 0.2);
      border-radius: var(--radius-sm);
      font-size: 0.75rem;
      color: var(--accent-primary);
      margin-bottom: var(--spacing-xs);
      font-family: var(--font-mono, monospace);
    }

    .query-code {
      padding: var(--spacing-md);
      margin: 0;
      font-size: 0.75rem;
      color: var(--text-secondary);
      overflow-x: auto;
      max-height: 200px;
    }
  `]
})
export class ResultsTableComponent {
  @Input() title: string = 'Query Results';
  @Input() columns: SchemaField[] = [];
  @Input() results: Record<string, any>[] = [];
  @Input() total: number = 0;
  @Input() queryTime: number = 0;
  @Input() isLoading: boolean = false;
  @Input() error: string | null = null;
  @Input() activeFilters: string[] = [];
  @Input() generatedQuery: Record<string, any> | null = null;
  @Input() indexId?: string; // Index ID being queried
  @Input() sortField: string | null = null;
  @Input() sortOrder: 'asc' | 'desc' = 'desc';
  
  @Output() sortChange = new EventEmitter<{ field: string; order: 'asc' | 'desc' }>();
  @Output() retry = new EventEmitter<void>();

  showQuery = false;

  getQueryWithIndex(): any {
    const query = this.generatedQuery || {};
    if (this.indexId) {
      return {
        index_id: this.indexId,
        query: query
      };
    }
    return query;
  }

  onSort(field: string) {
    const newOrder = this.sortField === field && this.sortOrder === 'desc' ? 'asc' : 'desc';
    this.sortChange.emit({ field, order: newOrder });
  }

  formatValue(value: any, type: string): string {
    if (value === null || value === undefined) return '—';
    
    if (type === 'date' && value) {
      try {
        return new Date(value).toLocaleString();
      } catch {
        return String(value);
      }
    }
    
    if (typeof value === 'object') {
      return JSON.stringify(value);
    }
    
    return String(value);
  }

  exportCsv() {
    if (this.results.length === 0) return;
    
    const headers = this.columns.map(c => c.name);
    const rows = this.results.map(row => 
      this.columns.map(col => {
        const val = row[col.name];
        if (val === null || val === undefined) return '';
        if (typeof val === 'string' && val.includes(',')) return `"${val}"`;
        return String(val);
      }).join(',')
    );
    
    const csv = [headers.join(','), ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    
    const a = document.createElement('a');
    a.href = url;
    a.download = `${this.title.replace(/[^a-z0-9]/gi, '_')}_${Date.now()}.csv`;
    a.click();
    
    URL.revokeObjectURL(url);
  }
}

