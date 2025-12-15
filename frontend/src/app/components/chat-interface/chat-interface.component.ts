import { Component, EventEmitter, Input, Output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  query?: Record<string, any>;
  results?: Record<string, any>[];
  resultCount?: number;
  queryTime?: number;
  error?: string;
  isLoading?: boolean;
  showQuery?: boolean;
  // New fields for response types
  responseType?: string;
  visualization?: {
    type: string;
    title?: string;
  };
  aggregations?: Record<string, any>;
  metrics?: Record<string, { label: string; description: string }>;
  timePeriod?: { description: string; is_lifetime: boolean };
}

interface KPIData {
  label: string;
  value: number;
  description?: string;
}

@Component({
  selector: 'app-chat-interface',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="chat-interface">
      <!-- Messages Area -->
      <div class="messages-area" #messagesContainer>
        @if (messages.length === 0) {
          <div class="welcome-message">
            <div class="welcome-icon">
              <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z"/>
              </svg>
            </div>
            <h2>AI Reports Assistant</h2>
            <p>Ask me anything about your training data and I'll provide accurate insights</p>
            <div class="example-queries">
              <span class="examples-label">Try asking:</span>
              <button class="example-btn" (click)="useExample('How many modules were published?')">
                "How many modules were published?"
              </button>
              <button class="example-btn" (click)="useExample('Show me completions by city')">
                "Show me completions by city"
              </button>
              <button class="example-btn" (click)="useExample('Top 10 modules by completion rate')">
                "Top 10 modules by completion rate"
              </button>
              <button class="example-btn" (click)="useExample('Modules delivered vs completed in November')">
                "Modules delivered vs completed in November"
              </button>
            </div>
          </div>
        }
        
        @for (message of messages; track message.id) {
          <div class="message" [class]="message.role">
            <div class="message-avatar">
              @if (message.role === 'user') {
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/>
                  <circle cx="12" cy="7" r="4"/>
                </svg>
              } @else {
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z"/>
                </svg>
              }
            </div>
            <div class="message-content">
              <div class="message-text">{{ message.content }}</div>
              
              @if (message.isLoading) {
                <div class="loading-indicator">
                  <div class="typing-dots">
                    <span></span><span></span><span></span>
                  </div>
                  <span class="loading-text">Analyzing your request...</span>
                </div>
              }
              
              @if (message.error) {
                <div class="error-box">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <circle cx="12" cy="12" r="10"/>
                    <line x1="12" y1="8" x2="12" y2="12"/>
                    <line x1="12" y1="16" x2="12.01" y2="16"/>
                  </svg>
                  {{ message.error }}
                </div>
              }
              
              <!-- KPI Widget Display -->
              @if (message.responseType === 'kpi_widget' && message.aggregations) {
                <div class="kpi-widget-container">
                  @for (kpi of extractKPIs(message); track kpi.label) {
                    <div class="kpi-widget">
                      <div class="kpi-label">{{ kpi.label }}</div>
                      <div class="kpi-value">{{ formatNumber(kpi.value) }}</div>
                      @if (kpi.description) {
                        <div class="kpi-description">{{ kpi.description }}</div>
                      }
                    </div>
                  }
                </div>
              }
              
              <!-- Multi KPI Display -->
              @if (message.responseType === 'multi_kpi' && message.aggregations) {
                <div class="multi-kpi-container">
                  @for (kpi of extractKPIs(message); track kpi.label) {
                    <div class="kpi-card">
                      <div class="kpi-card-label">{{ kpi.label }}</div>
                      <div class="kpi-card-value">{{ formatNumber(kpi.value) }}</div>
                    </div>
                  }
                </div>
              }
              
              <!-- Comparison Display -->
              @if (message.responseType === 'comparison' && message.aggregations) {
                <div class="comparison-container">
                  @for (kpi of extractKPIs(message); track kpi.label; let i = $index) {
                    <div class="comparison-item">
                      <div class="comparison-label">{{ kpi.label }}</div>
                      <div class="comparison-value">{{ formatNumber(kpi.value) }}</div>
                    </div>
                    @if (i === 0 && extractKPIs(message).length > 1) {
                      <div class="comparison-vs">VS</div>
                    }
                  }
                  @if (extractKPIs(message).length >= 2) {
                    <div class="comparison-rate">
                      {{ calculateRate(extractKPIs(message)[0].value, extractKPIs(message)[1].value) }}
                    </div>
                  }
                </div>
              }
              
              <!-- Bar Chart Aggregation Display -->
              @if ((message.responseType === 'bar_chart' || message.responseType === 'pie_chart') && message.aggregations) {
                <div class="chart-data-container">
                  @for (bucket of extractBuckets(message.aggregations); track bucket.key) {
                    <div class="bar-item">
                      <div class="bar-label">{{ bucket.key }}</div>
                      <div class="bar-track">
                        <div class="bar-fill" [style.width.%]="getBarWidth(bucket.count, message.aggregations)"></div>
                      </div>
                      <div class="bar-value">{{ formatNumber(bucket.count) }}</div>
                    </div>
                  }
                </div>
              }
              
              @if (message.query) {
                <div class="query-section">
                  <button class="toggle-query" (click)="message['showQuery'] = !message['showQuery']">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                      <polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>
                    </svg>
                    {{ message['showQuery'] ? 'Hide' : 'Show' }} Generated Query
                    <svg 
                      width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                      [style.transform]="message['showQuery'] ? 'rotate(180deg)' : 'rotate(0)'"
                    >
                      <polyline points="6 9 12 15 18 9"/>
                    </svg>
                  </button>
                  @if (message['showQuery']) {
                    <pre class="query-code font-mono">{{ message.query | json }}</pre>
                  }
                </div>
              }
              
              <!-- Table results for table response type -->
              @if (message.responseType === 'table' && message.results && message.results.length > 0) {
                <div class="results-section">
                  <div class="results-header">
                    <span class="results-count">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                        <line x1="3" y1="9" x2="21" y2="9"/>
                        <line x1="9" y1="21" x2="9" y2="9"/>
                      </svg>
                      {{ message.resultCount }} results
                    </span>
                    @if (message.queryTime) {
                      <span class="query-time">{{ message.queryTime }}ms</span>
                    }
                    <button class="export-btn" (click)="exportResults(message.results)">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                        <polyline points="7 10 12 15 17 10"/>
                        <line x1="12" y1="15" x2="12" y2="3"/>
                      </svg>
                      Export
                    </button>
                  </div>
                  <div class="results-table-container">
                    <table class="results-table">
                      <thead>
                        <tr>
                          <th>#</th>
                          @for (key of getResultKeys(message.results[0]); track key) {
                            <th>{{ key }}</th>
                          }
                        </tr>
                      </thead>
                      <tbody>
                        @for (row of message.results.slice(0, 20); track $index; let i = $index) {
                          <tr>
                            <td class="row-num">{{ i + 1 }}</td>
                            @for (key of getResultKeys(row); track key) {
                              <td>{{ formatValue(row[key]) }}</td>
                            }
                          </tr>
                        }
                      </tbody>
                    </table>
                    @if (message.results.length > 20) {
                      <div class="more-results">
                        + {{ message.results.length - 20 }} more results (export to see all)
                      </div>
                    }
                  </div>
                </div>
              }
              
              <!-- Legacy table display for non-typed responses -->
              @if (!message.responseType && message.results && message.results.length > 0) {
                <div class="results-section">
                  <div class="results-header">
                    <span class="results-count">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                        <line x1="3" y1="9" x2="21" y2="9"/>
                        <line x1="9" y1="21" x2="9" y2="9"/>
                      </svg>
                      {{ message.resultCount }} results
                    </span>
                    @if (message.queryTime) {
                      <span class="query-time">{{ message.queryTime }}ms</span>
                    }
                    <button class="export-btn" (click)="exportResults(message.results)">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                        <polyline points="7 10 12 15 17 10"/>
                        <line x1="12" y1="15" x2="12" y2="3"/>
                      </svg>
                      Export
                    </button>
                  </div>
                  <div class="results-table-container">
                    <table class="results-table">
                      <thead>
                        <tr>
                          <th>#</th>
                          @for (key of getResultKeys(message.results[0]); track key) {
                            <th>{{ key }}</th>
                          }
                        </tr>
                      </thead>
                      <tbody>
                        @for (row of message.results.slice(0, 20); track $index; let i = $index) {
                          <tr>
                            <td class="row-num">{{ i + 1 }}</td>
                            @for (key of getResultKeys(row); track key) {
                              <td>{{ formatValue(row[key]) }}</td>
                            }
                          </tr>
                        }
                      </tbody>
                    </table>
                    @if (message.results.length > 20) {
                      <div class="more-results">
                        + {{ message.results.length - 20 }} more results (export to see all)
                      </div>
                    }
                  </div>
                </div>
              }
              
              <div class="message-time">
                {{ message.timestamp | date:'shortTime' }}
                @if (message.timePeriod) {
                  <span class="time-period-badge">{{ message.timePeriod.description }}</span>
                }
              </div>
            </div>
          </div>
        }
      </div>
      
      <!-- Input Area -->
      <div class="input-area">
        <div class="input-container">
          <input
            type="text"
            class="chat-input"
            [(ngModel)]="inputText"
            (keyup.enter)="sendMessage()"
            [disabled]="isProcessing"
            placeholder="Ask about your training data..."
          >
          <button 
            class="send-btn"
            (click)="sendMessage()"
            [disabled]="!inputText.trim() || isProcessing"
          >
            @if (isProcessing) {
              <span class="spinner"></span>
            } @else {
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="22" y1="2" x2="11" y2="13"/>
                <polygon points="22 2 15 22 11 13 2 9 22 2"/>
              </svg>
            }
          </button>
        </div>
        <div class="input-hint">
          Press Enter to send • Data sourced directly from your index
        </div>
      </div>
    </div>
  `,
  styles: [`
    .chat-interface {
      display: flex;
      flex-direction: column;
      height: 100%;
      background: var(--bg-secondary);
      border-radius: var(--radius-lg);
      border: 1px solid var(--border-primary);
      overflow: hidden;
    }
    
    .messages-area {
      flex: 1;
      overflow-y: auto;
      padding: var(--spacing-lg);
      display: flex;
      flex-direction: column;
      gap: var(--spacing-lg);
    }
    
    .welcome-message {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      text-align: center;
      padding: var(--spacing-2xl);
      flex: 1;
      
      .welcome-icon {
        width: 80px;
        height: 80px;
        border-radius: 50%;
        background: linear-gradient(135deg, rgba(8, 145, 178, 0.1), rgba(124, 58, 237, 0.1));
        display: flex;
        align-items: center;
        justify-content: center;
        margin-bottom: var(--spacing-lg);
        
        svg {
          color: var(--accent-primary);
        }
      }
      
      h2 {
        font-size: 1.5rem;
        font-weight: 700;
        color: var(--text-primary);
        margin-bottom: var(--spacing-sm);
      }
      
      p {
        color: var(--text-secondary);
        margin-bottom: var(--spacing-xl);
      }
    }
    
    .example-queries {
      display: flex;
      flex-direction: column;
      gap: var(--spacing-sm);
      align-items: center;
    }
    
    .examples-label {
      font-size: 0.875rem;
      color: var(--text-tertiary);
      margin-bottom: var(--spacing-xs);
    }
    
    .example-btn {
      padding: var(--spacing-sm) var(--spacing-md);
      background: var(--bg-tertiary);
      border: 1px solid var(--border-primary);
      border-radius: var(--radius-md);
      color: var(--text-secondary);
      font-size: 0.875rem;
      cursor: pointer;
      transition: all var(--transition-fast);
      
      &:hover {
        background: var(--bg-card-hover);
        border-color: var(--accent-primary);
        color: var(--accent-primary);
      }
    }
    
    .message {
      display: flex;
      gap: var(--spacing-md);
      
      &.user {
        .message-avatar {
          background: var(--accent-primary);
          color: white;
        }
        
        .message-content {
          background: rgba(8, 145, 178, 0.08);
          border-color: rgba(8, 145, 178, 0.2);
        }
      }
      
      &.assistant {
        .message-avatar {
          background: linear-gradient(135deg, var(--accent-primary), var(--accent-tertiary));
          color: white;
        }
      }
    }
    
    .message-avatar {
      width: 36px;
      height: 36px;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }
    
    .message-content {
      flex: 1;
      background: var(--bg-tertiary);
      border: 1px solid var(--border-primary);
      border-radius: var(--radius-lg);
      padding: var(--spacing-md);
      min-width: 0;
    }
    
    .message-text {
      color: var(--text-primary);
      line-height: 1.6;
      margin-bottom: var(--spacing-sm);
    }
    
    /* KPI Widget Styles */
    .kpi-widget-container {
      margin: var(--spacing-md) 0;
    }
    
    .kpi-widget {
      background: linear-gradient(135deg, #5cbdce, #67c5d4);
      border-radius: var(--radius-lg);
      padding: var(--spacing-xl) var(--spacing-2xl);
      text-align: center;
      box-shadow: 0 4px 20px rgba(92, 189, 206, 0.3);
      border: 3px solid #f97316;
      border-radius: 16px;
    }
    
    .kpi-label {
      font-size: 1.125rem;
      font-weight: 600;
      color: #1a1a1a;
      margin-bottom: var(--spacing-md);
    }
    
    .kpi-value {
      font-size: 4rem;
      font-weight: 700;
      color: #1a1a1a;
      line-height: 1;
    }
    
    .kpi-description {
      font-size: 0.875rem;
      color: #333;
      margin-top: var(--spacing-sm);
      opacity: 0.8;
    }
    
    /* Multi KPI Styles */
    .multi-kpi-container {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: var(--spacing-md);
      margin: var(--spacing-md) 0;
    }
    
    .kpi-card {
      background: linear-gradient(135deg, #f0f9ff, #e0f2fe);
      border: 1px solid #bae6fd;
      border-radius: var(--radius-md);
      padding: var(--spacing-lg);
      text-align: center;
    }
    
    .kpi-card-label {
      font-size: 0.75rem;
      font-weight: 600;
      color: var(--text-secondary);
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin-bottom: var(--spacing-sm);
    }
    
    .kpi-card-value {
      font-size: 1.75rem;
      font-weight: 700;
      color: #0891b2;
    }
    
    /* Comparison Styles */
    .comparison-container {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: var(--spacing-lg);
      margin: var(--spacing-md) 0;
      padding: var(--spacing-lg);
      background: linear-gradient(135deg, #f8fafc, #f1f5f9);
      border-radius: var(--radius-lg);
      flex-wrap: wrap;
    }
    
    .comparison-item {
      text-align: center;
      padding: var(--spacing-md);
    }
    
    .comparison-label {
      font-size: 0.875rem;
      color: var(--text-secondary);
      margin-bottom: var(--spacing-sm);
    }
    
    .comparison-value {
      font-size: 2.5rem;
      font-weight: 700;
      color: #0891b2;
    }
    
    .comparison-vs {
      font-size: 1.25rem;
      font-weight: 700;
      color: var(--text-tertiary);
      padding: 0 var(--spacing-md);
    }
    
    .comparison-rate {
      width: 100%;
      text-align: center;
      padding-top: var(--spacing-md);
      font-size: 1rem;
      font-weight: 600;
      color: #059669;
      border-top: 1px solid var(--border-primary);
      margin-top: var(--spacing-sm);
    }
    
    /* Bar Chart Data Styles */
    .chart-data-container {
      margin: var(--spacing-md) 0;
      display: flex;
      flex-direction: column;
      gap: var(--spacing-sm);
    }
    
    .bar-item {
      display: flex;
      align-items: center;
      gap: var(--spacing-md);
    }
    
    .bar-label {
      min-width: 150px;
      max-width: 150px;
      font-size: 0.8125rem;
      color: var(--text-primary);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    
    .bar-track {
      flex: 1;
      height: 24px;
      background: var(--bg-secondary);
      border-radius: var(--radius-sm);
      overflow: hidden;
    }
    
    .bar-fill {
      height: 100%;
      background: linear-gradient(90deg, #0891b2, #06b6d4);
      border-radius: var(--radius-sm);
      transition: width 0.5s ease-out;
    }
    
    .bar-value {
      min-width: 60px;
      text-align: right;
      font-size: 0.875rem;
      font-weight: 600;
      color: var(--text-primary);
    }
    
    .time-period-badge {
      display: inline-block;
      margin-left: var(--spacing-sm);
      padding: 2px 8px;
      background: rgba(8, 145, 178, 0.1);
      border-radius: var(--radius-sm);
      font-size: 0.625rem;
      color: var(--accent-primary);
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    
    .loading-indicator {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      color: var(--text-tertiary);
      font-size: 0.875rem;
    }
    
    .typing-dots {
      display: flex;
      gap: 4px;
      
      span {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: var(--accent-primary);
        animation: bounce 1.4s infinite ease-in-out both;
        
        &:nth-child(1) { animation-delay: -0.32s; }
        &:nth-child(2) { animation-delay: -0.16s; }
      }
    }
    
    @keyframes bounce {
      0%, 80%, 100% { transform: scale(0); }
      40% { transform: scale(1); }
    }
    
    .error-box {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      padding: var(--spacing-sm) var(--spacing-md);
      background: rgba(220, 38, 38, 0.1);
      border: 1px solid rgba(220, 38, 38, 0.2);
      border-radius: var(--radius-md);
      color: var(--accent-danger);
      font-size: 0.875rem;
      margin-top: var(--spacing-sm);
    }
    
    .query-section {
      margin-top: var(--spacing-md);
    }
    
    .toggle-query {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      padding: var(--spacing-xs) var(--spacing-sm);
      background: transparent;
      border: none;
      color: var(--text-tertiary);
      font-size: 0.8125rem;
      cursor: pointer;
      transition: color var(--transition-fast);
      
      &:hover {
        color: var(--accent-primary);
      }
      
      svg:last-child {
        transition: transform var(--transition-fast);
      }
    }
    
    .query-code {
      margin-top: var(--spacing-sm);
      padding: var(--spacing-md);
      background: var(--bg-secondary);
      border: 1px solid var(--border-primary);
      border-radius: var(--radius-md);
      font-size: 0.75rem;
      color: var(--text-secondary);
      overflow-x: auto;
      max-height: 200px;
    }
    
    .results-section {
      margin-top: var(--spacing-md);
    }
    
    .results-header {
      display: flex;
      align-items: center;
      gap: var(--spacing-md);
      margin-bottom: var(--spacing-sm);
    }
    
    .results-count {
      display: flex;
      align-items: center;
      gap: var(--spacing-xs);
      font-size: 0.875rem;
      font-weight: 600;
      color: var(--text-primary);
      
      svg {
        color: var(--accent-success);
      }
    }
    
    .query-time {
      font-size: 0.75rem;
      color: var(--text-tertiary);
      background: var(--bg-secondary);
      padding: 2px 8px;
      border-radius: var(--radius-sm);
    }
    
    .export-btn {
      display: flex;
      align-items: center;
      gap: var(--spacing-xs);
      padding: var(--spacing-xs) var(--spacing-sm);
      background: var(--bg-secondary);
      border: 1px solid var(--border-primary);
      border-radius: var(--radius-sm);
      color: var(--text-secondary);
      font-size: 0.75rem;
      cursor: pointer;
      transition: all var(--transition-fast);
      margin-left: auto;
      
      &:hover {
        border-color: var(--accent-primary);
        color: var(--accent-primary);
      }
    }
    
    .results-table-container {
      overflow-x: auto;
      border: 1px solid var(--border-primary);
      border-radius: var(--radius-md);
    }
    
    .results-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.8125rem;
      
      th, td {
        padding: var(--spacing-sm);
        text-align: left;
        border-bottom: 1px solid var(--border-primary);
        white-space: nowrap;
        max-width: 200px;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      
      th {
        background: var(--bg-secondary);
        font-weight: 600;
        color: var(--text-secondary);
        font-size: 0.75rem;
        text-transform: uppercase;
        position: sticky;
        top: 0;
      }
      
      td {
        color: var(--text-primary);
      }
      
      .row-num {
        width: 40px;
        color: var(--text-tertiary);
        text-align: center;
      }
      
      tbody tr:hover td {
        background: var(--bg-tertiary);
      }
      
      tbody tr:last-child td {
        border-bottom: none;
      }
    }
    
    .more-results {
      padding: var(--spacing-sm);
      text-align: center;
      color: var(--text-tertiary);
      font-size: 0.8125rem;
      background: var(--bg-secondary);
      border-top: 1px solid var(--border-primary);
    }
    
    .message-time {
      font-size: 0.6875rem;
      color: var(--text-tertiary);
      margin-top: var(--spacing-sm);
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
    }
    
    .input-area {
      padding: var(--spacing-md) var(--spacing-lg);
      background: var(--bg-tertiary);
      border-top: 1px solid var(--border-primary);
    }
    
    .input-container {
      display: flex;
      gap: var(--spacing-sm);
    }
    
    .chat-input {
      flex: 1;
      padding: var(--spacing-md) var(--spacing-lg);
      background: var(--bg-secondary);
      border: 1px solid var(--border-primary);
      border-radius: var(--radius-lg);
      color: var(--text-primary);
      font-size: 1rem;
      transition: all var(--transition-fast);
      
      &::placeholder {
        color: var(--text-tertiary);
      }
      
      &:focus {
        outline: none;
        border-color: var(--accent-primary);
        box-shadow: 0 0 0 3px rgba(8, 145, 178, 0.1);
      }
      
      &:disabled {
        opacity: 0.6;
      }
    }
    
    .send-btn {
      width: 48px;
      height: 48px;
      border-radius: var(--radius-lg);
      background: linear-gradient(135deg, var(--accent-primary), var(--accent-secondary));
      border: none;
      color: white;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: all var(--transition-fast);
      
      &:hover:not(:disabled) {
        transform: scale(1.05);
        box-shadow: 0 4px 12px rgba(8, 145, 178, 0.3);
      }
      
      &:disabled {
        opacity: 0.5;
        cursor: not-allowed;
      }
      
      .spinner {
        width: 20px;
        height: 20px;
        border-color: rgba(255,255,255,0.3);
        border-top-color: white;
      }
    }
    
    .input-hint {
      text-align: center;
      font-size: 0.75rem;
      color: var(--text-tertiary);
      margin-top: var(--spacing-sm);
    }
  `]
})
export class ChatInterfaceComponent {
  @Input() isProcessing = false;
  @Input() messages: ChatMessage[] = [];
  
  @Output() sendQuery = new EventEmitter<string>();

  inputText = '';

  sendMessage() {
    if (!this.inputText.trim() || this.isProcessing) return;
    this.sendQuery.emit(this.inputText.trim());
    this.inputText = '';
  }

  useExample(example: string) {
    this.inputText = example;
    this.sendMessage();
  }

  extractKPIs(message: ChatMessage): KPIData[] {
    const kpis: KPIData[] = [];
    const aggregations = message.aggregations || {};
    const metrics = message.metrics || {};
    
    for (const [key, value] of Object.entries(aggregations)) {
      if (!value || typeof value !== 'object') continue;
      
      const metricInfo = metrics[key] || {};
      
      // Handle cardinality/value aggregations
      if ('value' in value) {
        kpis.push({
          label: metricInfo.label || this.humanize(key),
          value: value.value,
          description: metricInfo.description
        });
      }
      // Handle filter aggregations with nested values
      else if ('doc_count' in value) {
        // Look for nested value aggregations
        for (const [nestedKey, nestedValue] of Object.entries(value)) {
          if (nestedKey === 'doc_count' || nestedKey === 'meta') continue;
          if (nestedValue && typeof nestedValue === 'object' && 'value' in nestedValue) {
            const nestedMetricInfo = metrics[`${key}_${nestedKey}`] || metrics[key] || {};
            kpis.push({
              label: nestedMetricInfo.label || this.humanize(`${key} ${nestedKey}`),
              value: (nestedValue as any).value,
              description: nestedMetricInfo.description
            });
          }
        }
        // If no nested values, use doc_count
        if (kpis.length === 0 || !Object.keys(value).some(k => k !== 'doc_count' && k !== 'meta')) {
          kpis.push({
            label: metricInfo.label || this.humanize(key),
            value: value.doc_count,
            description: metricInfo.description
          });
        }
      }
    }
    
    return kpis;
  }

  extractBuckets(aggregations: Record<string, any>): { key: string; count: number }[] {
    const buckets: { key: string; count: number }[] = [];
    
    for (const value of Object.values(aggregations)) {
      if (value && typeof value === 'object' && 'buckets' in value && Array.isArray(value.buckets)) {
        for (const bucket of value.buckets.slice(0, 15)) { // Limit to 15 for display
          // Extract count value - prefer nested cardinality aggregations over doc_count
          let count = 0;
          // Check for _count field (added by backend for nested cardinality)
          if (bucket._count !== undefined) {
            count = bucket._count;
          } else {
            // Check for nested cardinality aggregations (unique_users, unique_modules, etc.)
            let foundNested = false;
            for (const [nestedKey, nestedValue] of Object.entries(bucket)) {
              if (nestedKey === 'key' || nestedKey === 'doc_count' || nestedKey.startsWith('_')) continue;
              if (nestedValue && typeof nestedValue === 'object' && 'value' in nestedValue) {
                count = nestedValue.value;
                foundNested = true;
                break;
              }
            }
            // Fall back to doc_count if no nested aggregation found
            if (!foundNested) {
              count = bucket.doc_count || 0;
            }
          }
          
          buckets.push({
            key: bucket.key || 'Unknown',
            count: count
          });
        }
        break; // Only process first terms aggregation
      }
    }
    
    return buckets;
  }

  getBarWidth(count: number, aggregations: Record<string, any>): number {
    const buckets = this.extractBuckets(aggregations);
    const maxCount = Math.max(...buckets.map(b => b.count), 1);
    return (count / maxCount) * 100;
  }

  calculateRate(value1: number, value2: number): string {
    if (value1 === 0) return '0% rate';
    const rate = (value2 / value1) * 100;
    return `${rate.toFixed(1)}% completion rate`;
  }

  humanize(str: string): string {
    return str
      .replace(/_/g, ' ')
      .replace(/([A-Z])/g, ' $1')
      .trim()
      .split(' ')
      .map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
      .join(' ');
  }

  formatNumber(value: number | string): string {
    if (typeof value === 'string') return value;
    if (value >= 1000000) {
      return (value / 1000000).toFixed(1) + 'M';
    }
    if (value >= 1000) {
      return (value / 1000).toFixed(1) + 'K';
    }
    return value.toLocaleString();
  }

  getResultKeys(row: Record<string, any>): string[] {
    if (!row) return [];
    return Object.keys(row).filter(k => !k.startsWith('_'));
  }

  formatValue(value: any): string {
    if (value === null || value === undefined) return '—';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
  }

  exportResults(results: Record<string, any>[]) {
    if (!results?.length) return;
    
    const keys = this.getResultKeys(results[0]);
    const rows = results.map(row => 
      keys.map(key => {
        const val = row[key];
        if (val === null || val === undefined) return '';
        if (typeof val === 'string' && val.includes(',')) return `"${val}"`;
        return String(val);
      }).join(',')
    );
    
    const csv = [keys.join(','), ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    
    const a = document.createElement('a');
    a.href = url;
    a.download = `query_results_${Date.now()}.csv`;
    a.click();
    
    URL.revokeObjectURL(url);
  }
}
