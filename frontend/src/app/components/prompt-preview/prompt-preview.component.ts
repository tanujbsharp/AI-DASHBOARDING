import { Component, EventEmitter, Input, Output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

@Component({
  selector: 'app-prompt-preview',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="prompt-preview">
      <div class="preview-header">
        <h3 class="preview-title">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z"/>
            <path d="M5 3v4"/><path d="M19 17v4"/><path d="M3 5h4"/><path d="M17 19h4"/>
          </svg>
          Generated Prompt
        </h3>
        <div class="actions">
          <button 
            class="btn btn-ghost btn-icon"
            [class.active]="isEditing"
            (click)="toggleEdit()"
            data-tooltip="Edit prompt"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/>
            </svg>
          </button>
          <button 
            class="btn btn-ghost btn-icon"
            (click)="copyToClipboard()"
            data-tooltip="Copy prompt"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
              <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
            </svg>
          </button>
        </div>
      </div>
      
      <div class="prompt-content">
        @if (isEditing) {
          <textarea 
            class="prompt-editor font-mono"
            [(ngModel)]="editablePrompt"
            rows="8"
            placeholder="Enter your prompt..."
          ></textarea>
          <div class="edit-actions">
            <button class="btn btn-ghost" (click)="cancelEdit()">Cancel</button>
            <button class="btn btn-primary" (click)="saveEdit()">Apply Changes</button>
          </div>
        } @else {
          <pre class="prompt-text font-mono">{{ prompt || 'Configure your report settings above to generate a prompt...' }}</pre>
        }
      </div>
      
      <div class="generate-section">
        <button 
          class="btn btn-primary generate-btn"
          [disabled]="!canGenerate || isLoading"
          (click)="onGenerate()"
        >
          @if (isLoading) {
            <span class="spinner"></span>
            Generating...
          } @else {
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
            </svg>
            Generate Query & Execute
          }
        </button>
      </div>
    </div>
  `,
  styles: [`
    .prompt-preview {
      display: flex;
      flex-direction: column;
      gap: var(--spacing-md);
    }
    
    .preview-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    
    .preview-title {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      font-size: 1rem;
      font-weight: 600;
      color: var(--text-primary);
      
      svg {
        color: var(--accent-warning);
      }
    }
    
    .actions {
      display: flex;
      gap: var(--spacing-xs);
    }
    
    .btn-icon {
      width: 36px;
      height: 36px;
      padding: 0;
      
      &.active {
        background: var(--bg-tertiary);
        color: var(--accent-primary);
      }
    }
    
    .prompt-content {
      background: var(--bg-secondary);
      border: 1px solid var(--border-primary);
      border-radius: var(--radius-md);
      overflow: hidden;
    }
    
    .prompt-text {
      padding: var(--spacing-md);
      margin: 0;
      font-size: 0.8125rem;
      line-height: 1.7;
      color: var(--text-secondary);
      white-space: pre-wrap;
      word-break: break-word;
      max-height: 200px;
      overflow-y: auto;
    }
    
    .prompt-editor {
      width: 100%;
      padding: var(--spacing-md);
      background: var(--bg-secondary);
      border: none;
      color: var(--text-primary);
      font-size: 0.8125rem;
      line-height: 1.7;
      resize: vertical;
      
      &:focus {
        outline: none;
      }
    }
    
    .edit-actions {
      display: flex;
      justify-content: flex-end;
      gap: var(--spacing-sm);
      padding: var(--spacing-sm) var(--spacing-md);
      background: var(--bg-tertiary);
      border-top: 1px solid var(--border-primary);
    }
    
    .generate-section {
      display: flex;
      justify-content: center;
      padding-top: var(--spacing-sm);
    }
    
    .generate-btn {
      padding: var(--spacing-md) var(--spacing-2xl);
      font-size: 1rem;
      
      .spinner {
        width: 16px;
        height: 16px;
        border-width: 2px;
      }
    }
  `]
})
export class PromptPreviewComponent {
  @Input() prompt: string = '';
  @Input() canGenerate: boolean = false;
  @Input() isLoading: boolean = false;
  
  @Output() promptChange = new EventEmitter<string>();
  @Output() generate = new EventEmitter<void>();

  isEditing = false;
  editablePrompt = '';

  toggleEdit() {
    if (!this.isEditing) {
      this.editablePrompt = this.prompt;
    }
    this.isEditing = !this.isEditing;
  }

  cancelEdit() {
    this.isEditing = false;
    this.editablePrompt = '';
  }

  saveEdit() {
    this.promptChange.emit(this.editablePrompt);
    this.isEditing = false;
  }

  copyToClipboard() {
    navigator.clipboard.writeText(this.prompt);
  }

  onGenerate() {
    this.generate.emit();
  }
}

