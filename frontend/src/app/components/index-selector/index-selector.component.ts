import { Component, EventEmitter, Input, Output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { OpenSearchIndex } from '../../models';

@Component({
  selector: 'app-index-selector',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="index-selector">
      <label class="label">
        <svg class="icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M22 12h-4l-3 9L9 3l-3 9H2"/>
        </svg>
        Select Data Source
      </label>
      <select 
        class="select" 
        [ngModel]="selectedIndex?.id"
        (ngModelChange)="onSelectChange($event)"
      >
        <option value="" disabled>Choose an index...</option>
        @for (index of indexes; track index.id) {
          <option [value]="index.id">{{ index.display_name }}</option>
        }
      </select>
      @if (selectedIndex) {
        <div class="selected-info animate-fade-in">
          <span class="chip chip-accent">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="20 6 9 17 4 12"/>
            </svg>
            {{ selectedIndex.name }}
          </span>
        </div>
      }
    </div>
  `,
  styles: [`
    .index-selector {
      display: flex;
      flex-direction: column;
      gap: var(--spacing-sm);
    }
    
    .label {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      font-size: 0.875rem;
      font-weight: 600;
      color: var(--text-secondary);
      
      .icon {
        color: var(--accent-primary);
      }
    }
    
    .select {
      font-size: 1rem;
      padding: var(--spacing-md);
    }
    
    .selected-info {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
    }
  `]
})
export class IndexSelectorComponent {
  @Input() indexes: OpenSearchIndex[] = [];
  @Input() selectedIndex: OpenSearchIndex | null = null;
  @Output() indexChange = new EventEmitter<OpenSearchIndex>();

  onSelectChange(indexId: string) {
    const index = this.indexes.find(i => i.id === indexId);
    if (index) {
      this.indexChange.emit(index);
    }
  }
}

