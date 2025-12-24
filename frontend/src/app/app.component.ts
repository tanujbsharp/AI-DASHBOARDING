import { Component, OnInit, inject, ViewChild, ElementRef, AfterViewChecked, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { ApiService, ChatResponse, ChatMessage as ApiChatMessage, VisualizationConfig, IndexSchema } from './services/api.service';
import { PromptPlaygroundComponent } from './components/prompt-playground/prompt-playground.component';
import { ChartDisplayComponent, ChartData, ChartTypeOption } from './components/chart-display/chart-display.component';
import { AddToDashboardModalComponent } from './components/add-to-dashboard-modal/add-to-dashboard-modal.component';
import { DashboardItem } from './models/dashboard.models';

interface DisplayMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: Date;
  isLoading?: boolean;
  response?: ChatResponse;
  showQuery?: boolean;
  tableSize?: number;
  isTableLoading?: boolean;
}

interface DashboardWidgetPayload {
  type: DashboardItem['type'];
  title: string;
  index_id?: string;
  query_payload: Record<string, any>;
  render_config: DashboardItem['render_config'];
  timeframe_key?: string;
  timezone?: string;
  date_field?: string;
  date_mode?: string;
}

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, PromptPlaygroundComponent, ChartDisplayComponent, AddToDashboardModalComponent],
  template: `
    <div class="app-container">
      <!-- Header -->
      <header class="app-header">
        <div class="logo">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z"/>
          </svg>
          <span class="logo-text">AI Data Assistant</span>
        </div>
        <div class="header-actions">
          <button class="builder-btn" routerLink="/dashboards">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
              <line x1="3" y1="9" x2="21" y2="9"/>
              <line x1="9" y1="21" x2="9" y2="9"/>
            </svg>
            My Dashboards
          </button>
          <button class="builder-btn" [class.active]="showPlayground" (click)="togglePlayground()">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
              <line x1="3" y1="9" x2="21" y2="9"/>
              <line x1="9" y1="21" x2="9" y2="9"/>
            </svg>
            Report Builder
          </button>
          <button class="builder-btn" [class.active]="showIndexViewer" (click)="toggleIndexViewer()">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
              <line x1="3" y1="9" x2="21" y2="9"/>
              <line x1="9" y1="3" x2="9" y2="21"/>
            </svg>
            Index Explorer
          </button>
          <div class="header-status" [class.connected]="isConnected" [class.disconnected]="!isConnected">
            <span class="status-dot"></span>
            {{ isConnected ? 'Connected' : 'Disconnected' }}
          </div>
        </div>
      </header>
      
      <!-- Prompt Playground Overlay -->
      @if (showPlayground) {
        <div class="playground-overlay">
          <app-prompt-playground 
            (onClose)="showPlayground = false"
            (onReportGenerated)="handleReportGenerated($event)"
          />
        </div>
      }

      @if (showIndexViewer) {
        <div class="index-viewer-overlay">
          <div class="index-viewer-panel">
            <div class="index-panel-header">
              <div>
                <h2>Index Explorer</h2>
                <p>Review every configured index with live sample rows.</p>
              </div>
              <button class="icon-button" (click)="toggleIndexViewer(false)">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <line x1="18" y1="6" x2="6" y2="18"/>
                  <line x1="6" y1="6" x2="18" y2="18"/>
                </svg>
              </button>
            </div>
            
            @if (isIndexLoading) {
              <div class="index-loading">
                <div class="spinner"></div>
                <p>Loading index schemas...</p>
              </div>
            } @else if (indexLoadError) {
              <div class="index-error">
                <p>{{ indexLoadError }}</p>
                <button class="btn btn-secondary" (click)="loadIndexSchemas()">Retry</button>
              </div>
            } @else {
              <div class="index-scroll">
                @if (!indexSchemas.length) {
                  <div class="index-empty">
                    <p>No index schemas available.</p>
                    <button class="btn btn-secondary" (click)="loadIndexSchemas()">Refresh</button>
                  </div>
                } @else {
                  @for (schema of indexSchemas; track schema.index_id) {
                    <section class="index-schema-card">
                      <div class="schema-header">
                        <div>
                          <h3>{{ schema.index_id }}</h3>
                          <p>{{ schema.index_name }}</p>
                        </div>
                        <div class="schema-stats">
                          <div>
                            <span>Documents</span>
                            <strong>{{ schema.document_count | number }}</strong>
                          </div>
                          <div>
                            <span>Fields</span>
                            <strong>{{ schema.field_count }}</strong>
                          </div>
                        </div>
                      </div>
                      
                      <div class="schema-table-wrapper">
                        <div class="schema-table-header">
                          <div>
                            <h4>Sample Data</h4>
                            <span *ngIf="schema.sample_documents?.length">{{ schema.sample_documents?.length }} rows</span>
                          </div>
                          <div class="schema-actions">
                            <a class="btn btn-secondary btn-download"
                               [href]="'/api/indexes/' + schema.index_id + '/export/?format=csv'"
                               target="_blank" rel="noopener">
                              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                                <polyline points="7 10 12 15 17 10"/>
                                <line x1="12" y1="15" x2="12" y2="3"/>
                              </svg>
                              Download CSV
                            </a>
                          </div>
                        </div>
                        <div class="schema-table">
                          <table>
                            <thead>
                              <tr>
                                @for (column of getIndexTableColumns(schema); track column) {
                                  <th>{{ column }}</th>
                                }
                              </tr>
                            </thead>
                            <tbody>
                              @for (doc of schema.sample_documents ?? []; track docIndex; let docIndex = $index) {
                                <tr>
                                  @for (column of getIndexTableColumns(schema); track column) {
                                    <td>{{ formatIndexValue(doc[column]) }}</td>
                                  }
                                </tr>
                              } @empty {
                                <tr>
                                  <td [attr.colspan]="getIndexTableColumns(schema).length || 1">
                                    No sample data available. Try refreshing the schema cache.
                                  </td>
                                </tr>
                              }
                            </tbody>
                          </table>
                        </div>
                      </div>
                    </section>
                  }
                }
              </div>
            }
          </div>
        </div>
      }

      <!-- Chat Area -->
      <main class="chat-area" #chatContainer>
        @if (messages.length === 0) {
          <div class="welcome">
            <div class="welcome-icon">
              <svg width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z"/>
              </svg>
            </div>
            <h1>What would you like to know?</h1>
            <p>Ask me anything about your data. I'll figure out where to look and give you the answers.</p>
            
            <div class="suggestions">
              <button class="suggestion" (click)="askQuestion('Show me all training completions from last month')">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/>
                  <polyline points="22 4 12 14.01 9 11.01"/>
                </svg>
                Training completions from last month
              </button>
              <button class="suggestion" (click)="askQuestion('Give me a report on users in Chennai')">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>
                  <circle cx="9" cy="7" r="4"/>
                  <path d="M23 21v-2a4 4 0 0 0-3-3.87"/>
                  <path d="M16 3.13a4 4 0 0 1 0 7.75"/>
                </svg>
                Report on users in Chennai
              </button>
              <button class="suggestion" (click)="askQuestion('What are the modules with most completions')">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <line x1="18" y1="20" x2="18" y2="10"/>
                  <line x1="12" y1="20" x2="12" y2="4"/>
                  <line x1="6" y1="20" x2="6" y2="14"/>
                </svg>
                What are the modules with most completions
              </button>
              <button class="suggestion" (click)="askQuestion('I need a table with user information')">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                  <line x1="3" y1="9" x2="21" y2="9"/>
                  <line x1="9" y1="21" x2="9" y2="9"/>
                </svg>
                Create a user information table
              </button>
            </div>
          </div>
        }

        @for (msg of messages; track msg.id) {
          <div class="message" [class]="msg.role">
            <div class="message-avatar">
              @if (msg.role === 'user') {
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/>
                  <circle cx="12" cy="7" r="4"/>
                </svg>
              } @else {
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21"/>
                </svg>
              }
            </div>
            
            <div class="message-body">
              @if (msg.isLoading) {
                <div class="loading">
                  <div class="dots"><span></span><span></span><span></span></div>
                  <span>Thinking...</span>
                </div>
              } @else {
                <div class="message-text" [innerHTML]="formatMessage(msg.content)"></div>
                
                @if (msg.response?.type === 'error') {
                  <div class="error-notice">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                      <circle cx="12" cy="12" r="10"/>
                      <line x1="12" y1="8" x2="12" y2="12"/>
                      <line x1="12" y1="16" x2="12.01" y2="16"/>
                    </svg>
                    {{ msg.response?.error }}
                  </div>
                }
                
                @if (msg.response?.type === 'query' && msg.response?.query_result) {
                  <div class="results-card">
                    @if (canAddToDashboard(msg)) {
                      <button class="action-btn icon-only add-to-dashboard-fab" (click)="openAddToDashboard(msg)" title="Add to dashboard">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                          <line x1="12" y1="5" x2="12" y2="19"/>
                          <line x1="5" y1="12" x2="19" y2="12"/>
                        </svg>
                      </button>
                    }
                    @if (!(msg.response?.query_result?.results?.length) && !msg.response?.query_result?.aggregations) {
                      <div class="no-results">
                        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                          <circle cx="11" cy="11" r="8"/>
                          <path d="m21 21-4.35-4.35"/>
                        </svg>
                        <p>No matching records found</p>
                        <span>Try adjusting your search criteria</span>
                      </div>
                      <!-- Always show query button for 0 results, even if query is missing -->
                      <button class="action-btn" (click)="toggleQuery(msg)" style="margin-top: 12px;">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                          <polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>
                        </svg>
                        {{ msg.showQuery ? 'Hide' : 'Show' }} Query
                      </button>
                      @if (shouldShowQuery(msg)) {
                        <pre class="query-code">{{ getQueryJson(msg) }}</pre>
                      }
                    } @else if (msg.response?.query_result?.aggregations && (msg.response?.response_type === 'bar_chart' || msg.response?.response_type === 'pie_chart' || msg.response?.response_type === 'line_chart' || !(msg.response?.query_result?.results?.length))) {
                      <!-- Aggregation results with optional chart -->
                      <div class="aggregation-results">
                        @if (getChartData(msg.response?.visualization, msg.response?.query_result?.aggregations, msg.response?.title); as chartData) {
                          <!-- Chart visualization (auto-generated when the visualization payload is missing) -->
                          <app-chart-display [chartData]="chartData" />
                        }
                        
                        <div class="agg-header">
                          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M3 3v18h18"/>
                            <path d="M18.7 8l-5.1 5.2-2.8-2.7L7 14.3"/>
                          </svg>
                          <span>Based on {{ msg.response?.query_result?.total ?? 0 }} matching records</span>
                        </div>
                        <div class="agg-cards">
                          @for (agg of getAggregations(msg.response?.query_result?.aggregations); track agg.name) {
                            <div class="agg-card" [class.has-items]="agg.items?.length" [title]="agg.description || ''">
                              @if (agg.items?.length) {
                                <div class="agg-header-mini">
                                  <span class="agg-count">{{ agg.value }}</span>
                                  <span class="agg-label">{{ agg.name }}</span>
                                </div>
                                <div class="agg-items">
                                  @for (item of agg.items; track item) {
                                    <span class="agg-item">{{ item }}</span>
                                  }
                                </div>
                              } @else {
                                <div class="agg-value">{{ agg.value }}</div>
                                <div class="agg-label">{{ agg.name }}</div>
                                @if (agg.description) {
                                  <div class="agg-description">{{ agg.description }}</div>
                                }
                              }
                            </div>
                          }
                        </div>
                        <button class="action-btn" (click)="toggleQuery(msg)" style="margin-top: 12px;">
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>
                          </svg>
                          {{ msg.showQuery ? 'Hide' : 'Show' }} Query
                        </button>
                        @if (canAddToDashboard(msg)) {
                          <button class="action-btn icon-only add-to-dashboard-inline" (click)="openAddToDashboard(msg)" title="Add to dashboard">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                              <line x1="12" y1="5" x2="12" y2="19"/>
                              <line x1="5" y1="12" x2="19" y2="12"/>
                            </svg>
                          </button>
                        }
                        @if (shouldShowQuery(msg)) {
                          <pre class="query-code">{{ getQueryJson(msg) }}</pre>
                        }
                      </div>
                    } @else {
                    <div class="results-header">
                      <div class="results-info">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                          <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
                        </svg>
                        <span class="results-count">{{ msg.response?.query_result?.total ?? 0 }} results</span>
                        <span class="results-time">{{ msg.response?.query_result?.took ?? 0 }}ms</span>
                      </div>
                      <div class="results-actions">
                        <div class="rows-control" [class.loading]="msg.isTableLoading">
                          <label>Rows</label>
                          <select
                            [value]="getCurrentRowSelection(msg)"
                            (change)="onTableSizeChange(msg, $any($event.target).value)"
                            [disabled]="!!msg.isTableLoading"
                            aria-label="Rows per table"
                          >
                            <option value="6">6</option>
                            <option value="20">20</option>
                            <option value="50">50</option>
                            <option value="100">100</option>
                            <option value="-1">All</option>
                          </select>
                        </div>
                        <button class="action-btn" (click)="toggleQuery(msg)">
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>
                          </svg>
                          {{ msg.showQuery ? 'Hide' : 'Show' }} Query
                        </button>
                        <button class="action-btn primary" (click)="exportFromMessage(msg)">
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                            <polyline points="7 10 12 15 17 10"/>
                            <line x1="12" y1="15" x2="12" y2="3"/>
                          </svg>
                          Export
                        </button>
                      </div>
                    </div>
                    
                    @if (shouldShowQuery(msg)) {
                      <pre class="query-code">{{ getQueryJson(msg) }}</pre>
                    }
                    
                    <!-- Results Table -->
                    <div class="results-table-wrapper">
                      <table class="results-table">
                        <thead>
                          <tr>
                            <th>#</th>
                            @for (col of getTableColumns(msg.response?.query_result?.results?.[0], msg.response?.fields_to_show); track col) {
                              <th>{{ formatFieldName(col) }}</th>
                            }
                          </tr>
                        </thead>
                        <tbody>
                          @for (row of getVisibleRows(msg); track $index; let i = $index) {
                            <tr>
                              <td class="row-num">{{ i + 1 }}</td>
                              @for (col of getTableColumns(row, msg.response?.fields_to_show); track col) {
                                <td>{{ formatFieldValue(row[col]) }}</td>
                              }
                            </tr>
                          }
                        </tbody>
                      </table>
                    </div>

                    @if (getTotalResults(msg) > getVisibleRowCount(msg)) {
                      <div class="more-results">
                        Showing {{ getVisibleRowCount(msg) }} of {{ getTotalResults(msg) }} — increase Rows or click Export to download all
                      </div>
                    }
                    }
                  </div>
                }
              }
              
              <div class="message-time">{{ msg.timestamp | date:'shortTime' }}</div>
            </div>
          </div>
        }
      </main>

      <!-- Input Area -->
      <footer class="input-area">
        <div class="input-wrapper">
          <input
            type="text"
            [(ngModel)]="inputText"
            (keyup.enter)="sendMessage()"
            [disabled]="isProcessing"
            placeholder="Ask me about your data..."
            class="chat-input"
          >
          <button 
            class="send-btn" 
            (click)="sendMessage()"
            [disabled]="!inputText.trim() || isProcessing"
          >
            @if (isProcessing) {
              <div class="spinner"></div>
            } @else {
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="22" y1="2" x2="11" y2="13"/>
                <polygon points="22 2 15 22 11 13 2 9 22 2"/>
              </svg>
            }
          </button>
        </div>
        <p class="input-hint">I'll find the right data source and format the results for you</p>
      </footer>

      @if (showAddToDashboardModal && pendingWidgetData) {
        <app-add-to-dashboard-modal
          [widgetData]="pendingWidgetData"
          (saved)="handleWidgetSaved($event)"
          (closed)="closeAddToDashboardModal()"
        />
      }

      @if (dashboardToast) {
        <div class="dashboard-toast">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
          </svg>
          <span>{{ dashboardToast.message }}</span>
        </div>
      }
    </div>
  `,
  styles: [`
    .app-container {
      height: 100vh;
      display: flex;
      flex-direction: column;
      background: var(--bg-primary);
    }
    
    .app-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: var(--spacing-md) var(--spacing-xl);
      background: white;
      border-bottom: 1px solid var(--border-primary);
      box-shadow: var(--shadow-sm);
      z-index: 100;
      position: relative;
    }
    
    .logo {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      svg { color: var(--accent-primary); }
    }
    
    .logo-text {
      font-size: 1.25rem;
      font-weight: 700;
      color: var(--text-primary);
    }
    
    .header-actions {
      display: flex;
      align-items: center;
      gap: var(--spacing-md);
    }
    
    .builder-btn {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      padding: 8px 16px;
      background: var(--bg-tertiary);
      border: 1px solid var(--border-primary);
      border-radius: var(--radius-lg);
      font-size: 0.875rem;
      font-weight: 500;
      color: var(--text-secondary);
      cursor: pointer;
      transition: all 0.15s;
      
      svg { color: var(--accent-primary); }
      
      &:hover {
        border-color: var(--accent-primary);
        color: var(--accent-primary);
        background: rgba(8, 145, 178, 0.05);
      }
      
      &.active {
        background: var(--accent-primary);
        border-color: var(--accent-primary);
        color: white;
        svg { color: white; }
      }
    }
    
    .header-status {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 6px 12px;
      border-radius: 20px;
      font-size: 0.75rem;
      font-weight: 500;
      
      &.connected {
        background: rgba(5, 150, 105, 0.1);
        color: var(--accent-success);
        .status-dot { background: var(--accent-success); }
      }
      
      &.disconnected {
        background: rgba(220, 38, 38, 0.1);
        color: var(--accent-danger);
        .status-dot { background: var(--accent-danger); }
      }
    }
    
    .status-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
    }
    
    .playground-overlay {
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: rgba(0, 0, 0, 0.5);
      z-index: 1000;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: var(--spacing-xl);
      animation: fadeIn 0.2s ease;
      
      app-prompt-playground {
        width: 100%;
        max-width: 1400px;
        height: 90vh;
        animation: slideUp 0.3s ease;
      }
    }

    .index-viewer-overlay {
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: rgba(15, 23, 42, 0.65);
      z-index: 1000;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: var(--spacing-xl);
      animation: fadeIn 0.2s ease;
    }

    .index-viewer-panel {
      width: 100%;
      max-width: 1200px;
      max-height: 90vh;
      background: var(--bg-secondary);
      border-radius: var(--radius-xl);
      box-shadow: var(--shadow-lg);
      padding: var(--spacing-xl);
      display: flex;
      flex-direction: column;
      gap: var(--spacing-lg);
      animation: slideUp 0.3s ease;
    }

    .index-panel-header {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: var(--spacing-md);
      
      h2 {
        margin-bottom: 4px;
      }
      
      p {
        color: var(--text-secondary);
      }
      
      .icon-button {
        border: none;
        background: var(--bg-tertiary);
        border-radius: 50%;
        width: 36px;
        height: 36px;
        display: flex;
        align-items: center;
        justify-content: center;
        cursor: pointer;
        transition: background var(--transition-fast);
        
        &:hover {
          background: var(--bg-primary);
        }
      }
    }

    .index-scroll {
      overflow-y: auto;
      padding-right: 8px;
      display: flex;
      flex-direction: column;
      gap: var(--spacing-lg);
    }

    .index-loading,
    .index-error,
    .index-empty {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: var(--spacing-md);
      padding: var(--spacing-xl);
      border: 1px dashed var(--border-primary);
      border-radius: var(--radius-lg);
      color: var(--text-secondary);
      
      .spinner {
        width: 32px;
        height: 32px;
        border-radius: 50%;
        border: 3px solid var(--border-primary);
        border-top-color: var(--accent-primary);
        animation: spin 0.8s linear infinite;
      }
    }

    @keyframes spin {
      to {
        transform: rotate(360deg);
      }
    }

    .index-schema-card {
      border: 1px solid var(--border-primary);
      border-radius: var(--radius-lg);
      padding: var(--spacing-lg);
      background: var(--bg-card);
      box-shadow: var(--shadow-sm);
      display: flex;
      flex-direction: column;
      gap: var(--spacing-md);
    }

    .schema-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: var(--spacing-lg);
      
      h3 {
        margin-bottom: 4px;
      }
      
      p {
        color: var(--text-secondary);
        font-size: 0.9rem;
      }
      
      .schema-stats {
        display: flex;
        gap: var(--spacing-lg);
        
        div {
          text-align: right;
          span {
            display: block;
            font-size: 0.75rem;
            color: var(--text-secondary);
          }
          strong {
            font-size: 1.1rem;
          }
        }
      }
    }

    .schema-table-wrapper {
      border: 1px solid var(--border-primary);
      border-radius: var(--radius-md);
      overflow: hidden;
      background: white;
    }

    .schema-table-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: var(--spacing-sm) var(--spacing-md);
      border-bottom: 1px solid var(--border-primary);
      background: var(--bg-tertiary);
      h4 {
        margin: 0;
      }
      span {
        color: var(--text-secondary);
        font-size: 0.85rem;
      }
      
      .schema-actions {
        display: flex;
        align-items: center;
        gap: var(--spacing-sm);
        
        .btn-download {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          padding: 6px 12px;
          font-size: 0.8rem;
          text-decoration: none;
          
          svg {
            stroke: currentColor;
          }
        }
      }
    }

    .schema-table {
      overflow-x: auto;
      
      table {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.85rem;
        
        th, td {
          padding: 10px 12px;
          border-bottom: 1px solid var(--border-primary);
          text-align: left;
          white-space: nowrap;
        }
        
        th {
          font-weight: 600;
          color: var(--text-secondary);
          background: rgba(8, 145, 178, 0.05);
        }
        
        tr:last-child td {
          border-bottom: none;
        }
      }
    }
    
    @keyframes fadeIn {
      from { opacity: 0; }
      to { opacity: 1; }
    }
    
    @keyframes slideUp {
      from { opacity: 0; transform: translateY(20px); }
      to { opacity: 1; transform: translateY(0); }
    }
    
    .chat-area {
      flex: 1;
      overflow-y: auto;
      padding: var(--spacing-xl);
      display: flex;
      flex-direction: column;
      gap: var(--spacing-lg);
    }
    
    .welcome {
      flex: 1;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      text-align: center;
      max-width: 600px;
      margin: 0 auto;
      
      .welcome-icon {
        width: 100px;
        height: 100px;
        border-radius: 50%;
        background: linear-gradient(135deg, rgba(8, 145, 178, 0.1), rgba(124, 58, 237, 0.1));
        display: flex;
        align-items: center;
        justify-content: center;
        margin-bottom: var(--spacing-lg);
        svg { color: var(--accent-primary); }
      }
      
      h1 {
        font-size: 1.75rem;
        font-weight: 700;
        color: var(--text-primary);
        margin-bottom: var(--spacing-sm);
      }
      
      p {
        color: var(--text-secondary);
        margin-bottom: var(--spacing-xl);
        font-size: 1.0625rem;
      }
    }
    
    .suggestions {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: var(--spacing-sm);
      width: 100%;
    }
    
    .suggestion {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      padding: var(--spacing-md);
      background: white;
      border: 1px solid var(--border-primary);
      border-radius: var(--radius-lg);
      color: var(--text-secondary);
      font-size: 0.875rem;
      text-align: left;
      cursor: pointer;
      transition: all var(--transition-fast);
      
      svg { color: var(--accent-primary); flex-shrink: 0; }
      
      &:hover {
        border-color: var(--accent-primary);
        background: var(--bg-tertiary);
        color: var(--text-primary);
        transform: translateY(-2px);
        box-shadow: var(--shadow-md);
      }
    }
    
    .message {
      display: flex;
      gap: var(--spacing-md);
      max-width: 95%;
      width: 100%;
      
      &.user {
        max-width: 700px;
        align-self: flex-end;
        flex-direction: row-reverse;
        
        .message-avatar { background: var(--accent-primary); color: white; }
        .message-body { background: rgba(8, 145, 178, 0.08); border-color: rgba(8, 145, 178, 0.2); }
      }
      
      &.assistant {
        .message-avatar { 
          background: linear-gradient(135deg, var(--accent-primary), var(--accent-tertiary)); 
          color: white; 
        }
      }
    }
    
    .message-avatar {
      width: 40px;
      height: 40px;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }
    
    .message-body {
      flex: 1;
      min-width: 0;
      background: white;
      border: 1px solid var(--border-primary);
      border-radius: var(--radius-lg);
      padding: var(--spacing-md);
      box-shadow: var(--shadow-sm);
      min-width: 200px;
      max-width: 100%;
    }
    
    .message-text {
      color: var(--text-primary);
      line-height: 1.6;
      
      :deep(strong) { font-weight: 600; }
      :deep(p) { margin: 0 0 0.5em 0; &:last-child { margin: 0; } }
    }
    
    .loading {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      color: var(--text-tertiary);
      
      .dots {
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
    }
    
    @keyframes bounce {
      0%, 80%, 100% { transform: scale(0); }
      40% { transform: scale(1); }
    }
    
    .error-notice {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      margin-top: var(--spacing-sm);
      padding: var(--spacing-sm) var(--spacing-md);
      background: rgba(220, 38, 38, 0.08);
      border: 1px solid rgba(220, 38, 38, 0.2);
      border-radius: var(--radius-md);
      color: var(--accent-danger);
      font-size: 0.875rem;
    }
    
    .results-card {
      margin-top: var(--spacing-md);
      background: var(--bg-tertiary);
      border: 1px solid var(--border-primary);
      border-radius: var(--radius-lg);
      overflow: hidden;
      position: relative;
    }
    
    .results-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: var(--spacing-sm) var(--spacing-md);
      background: white;
      border-bottom: 1px solid var(--border-primary);
    }
    
    .results-info {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      svg { color: var(--accent-success); }
    }
    
    .results-count {
      font-weight: 600;
      color: var(--text-primary);
    }
    
    .results-time {
      font-size: 0.75rem;
      color: var(--text-tertiary);
      background: var(--bg-tertiary);
      padding: 2px 8px;
      border-radius: 4px;
    }
    
    .results-actions {
      display: flex;
      align-items: center;
      gap: var(--spacing-xs);
    }

    .rows-control {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 6px 10px;
      background: var(--bg-tertiary);
      border: 1px solid var(--border-primary);
      border-radius: var(--radius-md);
      color: var(--text-secondary);
      font-size: 0.75rem;

      label {
        opacity: 0.85;
      }

      select {
        border: none;
        background: transparent;
        color: var(--text-primary);
        font-size: 0.75rem;
        cursor: pointer;

        &:focus { outline: none; }
      }

      &.loading {
        opacity: 0.6;
      }
    }
    
    .action-btn {
      display: flex;
      align-items: center;
      gap: 4px;
      padding: 6px 12px;
      background: var(--bg-tertiary);
      border: 1px solid var(--border-primary);
      border-radius: var(--radius-md);
      color: var(--text-secondary);
      font-size: 0.75rem;
      font-weight: 500;
      cursor: pointer;
      transition: all var(--transition-fast);
      
      &:hover { border-color: var(--accent-primary); color: var(--accent-primary); }
      &.primary {
        background: var(--accent-primary);
        border-color: var(--accent-primary);
        color: white;
        &:hover { background: var(--accent-secondary); }
      }
    }
    
    .query-code {
      margin: 0;
      padding: var(--spacing-md);
      background: var(--bg-secondary);
      border-bottom: 1px solid var(--border-primary);
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.75rem;
      color: var(--text-secondary);
      overflow-x: auto;
      max-height: 150px;
    }
    
    .data-cards {
      padding: var(--spacing-md);
      display: flex;
      flex-direction: column;
      gap: var(--spacing-sm);
      max-height: 400px;
      overflow-y: auto;
    }
    
    .data-card {
      display: flex;
      gap: var(--spacing-md);
      padding: var(--spacing-md);
      background: white;
      border: 1px solid var(--border-primary);
      border-radius: var(--radius-md);
      transition: all var(--transition-fast);
      
      &:hover {
        border-color: var(--accent-primary);
        box-shadow: var(--shadow-sm);
      }
    }
    
    .card-number {
      width: 28px;
      height: 28px;
      border-radius: 50%;
      background: var(--bg-tertiary);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 0.75rem;
      font-weight: 600;
      color: var(--text-tertiary);
      flex-shrink: 0;
    }
    
    .card-content {
      flex: 1;
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: var(--spacing-sm) var(--spacing-lg);
    }
    
    .field-row {
      display: flex;
      flex-direction: column;
      gap: 2px;
    }
    
    .field-label {
      font-size: 0.6875rem;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--text-tertiary);
    }
    
    .field-value {
      font-size: 0.875rem;
      color: var(--text-primary);
      word-break: break-word;
    }
    
    .more-results {
      padding: var(--spacing-sm) var(--spacing-md);
      text-align: center;
      font-size: 0.8125rem;
      color: var(--text-tertiary);
      background: white;
      border-top: 1px solid var(--border-primary);
    }
    
    .results-table-wrapper {
      overflow-x: auto;
      max-height: 400px;
      overflow-y: auto;
      padding-top: var(--spacing-md);
    }
    
    .results-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.8125rem;
      background: white;
      
      th, td {
        padding: 10px 12px;
        text-align: left;
        border-bottom: 1px solid var(--border-primary);
        white-space: nowrap;
        max-width: 200px;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      
      th {
        background: var(--bg-tertiary);
        font-weight: 600;
        color: var(--text-secondary);
        font-size: 0.75rem;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        position: sticky;
        top: 0;
        z-index: 1;
      }
      
      td {
        color: var(--text-primary);
      }
      
      .row-num {
        width: 40px;
        color: var(--text-tertiary);
        text-align: center;
        font-weight: 500;
      }
      
      tbody tr:hover td {
        background: var(--bg-tertiary);
      }
      
      tbody tr:last-child td {
        border-bottom: none;
      }
    }
    
    .no-results {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: var(--spacing-xl);
      text-align: center;
      
      svg { color: var(--text-tertiary); margin-bottom: var(--spacing-md); }
      p { font-weight: 600; color: var(--text-secondary); margin-bottom: var(--spacing-xs); }
      span { font-size: 0.875rem; color: var(--text-tertiary); }
    }
    
    .aggregation-results {
      padding: var(--spacing-lg);
      background: white;
    }

    .add-to-dashboard-fab {
      position: absolute;
      top: var(--spacing-md);
      right: var(--spacing-md);
      z-index: 3;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-radius: 999px;
      border: 1px solid rgba(8, 145, 178, 0.2);
      color: var(--accent-primary);
      padding: 6px;
      width: 34px;
      height: 34px;
      background: white;
      box-shadow: 0 4px 14px rgba(8, 145, 178, 0.2);

      svg {
        pointer-events: none;
      }

      &:hover {
        background: var(--accent-primary);
        color: white;
        border-color: var(--accent-primary);
      }
    }
    
    .agg-header {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      margin-bottom: var(--spacing-lg);
      color: var(--text-primary);
      font-weight: 600;
      
      svg { color: var(--accent-success); }
    }
    
    .agg-cards {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: var(--spacing-md);
    }
    
    .agg-card {
      background: linear-gradient(135deg, rgba(8, 145, 178, 0.08), rgba(124, 58, 237, 0.05));
      border: 1px solid rgba(8, 145, 178, 0.2);
      border-radius: var(--radius-lg);
      padding: var(--spacing-lg);
      text-align: center;
    }
    
    .agg-value {
      font-size: 2rem;
      font-weight: 700;
      color: var(--accent-primary);
      line-height: 1.2;
    }
    
    .agg-label {
      font-size: 0.875rem;
      color: var(--text-secondary);
      margin-top: var(--spacing-xs);
    }
    
    .agg-description {
      font-size: 0.75rem;
      color: var(--text-tertiary);
      margin-top: 4px;
      font-style: italic;
    }
    
    .agg-header-mini {
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      margin-bottom: var(--spacing-sm);
      padding-bottom: var(--spacing-sm);
      border-bottom: 1px solid var(--border-primary);
      
      .agg-count {
        font-size: 1.5rem;
        font-weight: 700;
        color: var(--accent-primary);
      }
      
      .agg-label {
        font-size: 0.875rem;
        color: var(--text-secondary);
        margin-top: 0;
        text-transform: capitalize;
      }
    }
    
    .agg-card.has-items {
      text-align: left;
      padding: var(--spacing-md);
    }
    
    .agg-card.has-items .agg-label {
      font-weight: 600;
      color: var(--text-primary);
      margin-bottom: var(--spacing-sm);
      margin-top: 0;
    }
    
    .agg-items {
      display: flex;
      flex-wrap: wrap;
      gap: var(--spacing-xs);
    }
    
    .agg-item {
      background: white;
      border: 1px solid var(--border-primary);
      border-radius: var(--radius-sm);
      padding: 4px 8px;
      font-size: 0.8125rem;
      color: var(--text-primary);
    }
    
    .message-time {
      font-size: 0.6875rem;
      color: var(--text-tertiary);
      margin-top: var(--spacing-sm);
    }
    
    .input-area {
      padding: var(--spacing-md) var(--spacing-xl) var(--spacing-lg);
      background: white;
      border-top: 1px solid var(--border-primary);
    }
    
    .input-wrapper {
      display: flex;
      gap: var(--spacing-sm);
      max-width: 800px;
      margin: 0 auto;
    }
    
    .chat-input {
      flex: 1;
      padding: var(--spacing-md) var(--spacing-lg);
      background: var(--bg-tertiary);
      border: 1px solid var(--border-primary);
      border-radius: 24px;
      font-size: 1rem;
      color: var(--text-primary);
      transition: all var(--transition-fast);
      
      &::placeholder { color: var(--text-tertiary); }
      &:focus {
        outline: none;
        border-color: var(--accent-primary);
        box-shadow: 0 0 0 3px rgba(8, 145, 178, 0.1);
      }
      &:disabled { opacity: 0.6; }
    }
    
    .send-btn {
      width: 48px;
      height: 48px;
      border-radius: 50%;
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
        box-shadow: 0 4px 15px rgba(8, 145, 178, 0.4);
      }
      &:disabled { opacity: 0.5; cursor: not-allowed; }
      
      .spinner {
        width: 20px;
        height: 20px;
        border: 2px solid rgba(255,255,255,0.3);
        border-top-color: white;
        border-radius: 50%;
        animation: spin 0.8s linear infinite;
      }
    }
    
    @keyframes spin { to { transform: rotate(360deg); } }
    
    .input-hint {
      text-align: center;
      font-size: 0.75rem;
      color: var(--text-tertiary);
      margin-top: var(--spacing-sm);
    }

    .dashboard-toast {
      position: fixed;
      right: 24px;
      bottom: 24px;
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 12px 18px;
      border-radius: var(--radius-lg);
      background: rgba(15, 23, 42, 0.95);
      color: white;
      box-shadow: 0 15px 35px rgba(15, 23, 42, 0.45);
      font-weight: 500;
      z-index: 3000;
      animation: fadeInUp 0.3s ease;
    }

    @keyframes fadeInUp {
      from {
        opacity: 0;
        transform: translateY(8px);
      }
      to {
        opacity: 1;
        transform: translateY(0);
      }
    }
  `]
})
export class AppComponent implements OnInit, AfterViewChecked, OnDestroy {
  private api = inject(ApiService);
  @ViewChild('chatContainer') private chatContainer!: ElementRef;

  messages: DisplayMessage[] = [];
  inputText = '';
  isProcessing = false;
  isConnected = false;
  showPlayground = false;
  showIndexViewer = false;
  isIndexLoading = false;
  indexLoadError?: string;
  indexSchemas: IndexSchema[] = [];
  private indexColumnCache = new Map<string, string[]>();
  showAddToDashboardModal = false;
  pendingWidgetData: DashboardWidgetPayload | null = null;
  dashboardToast?: { message: string };

  private messageId = 0;
  private shouldScroll = false;
  private toastTimeoutId: ReturnType<typeof setTimeout> | null = null;

  ngOnInit() {
    this.checkConnection();
  }

  ngAfterViewChecked() {
    if (this.shouldScroll) {
      this.scrollToBottom();
      this.shouldScroll = false;
    }
  }

  ngOnDestroy() {
    if (this.toastTimeoutId) {
      clearTimeout(this.toastTimeoutId);
      this.toastTimeoutId = null;
    }
  }

  checkConnection() {
    this.api.healthCheck().subscribe({
      next: () => this.isConnected = true,
      error: () => this.isConnected = false
    });
  }

  togglePlayground() {
    this.showPlayground = !this.showPlayground;
  }

  toggleIndexViewer(force?: boolean) {
    this.showIndexViewer = force !== undefined ? force : !this.showIndexViewer;
    if (this.showIndexViewer && !this.indexSchemas.length && !this.isIndexLoading) {
      this.loadIndexSchemas();
    }
  }

  loadIndexSchemas() {
    this.isIndexLoading = true;
    this.indexLoadError = undefined;
    this.indexColumnCache.clear();
    
    this.api.getAllSchemas().subscribe({
      next: (response) => {
        const schemasRecord = response.schemas || {};
        this.indexSchemas = Object.values(schemasRecord).map(schema => ({
          ...schema,
          sample_documents: schema.sample_documents ?? []
        }));
        this.isIndexLoading = false;
      },
      error: (err) => {
        this.indexLoadError = err?.message || 'Failed to load index schemas.';
        this.isIndexLoading = false;
      }
    });
  }

  handleReportGenerated(event: { title: string; prompt: string; response: ChatResponse }) {
    this.showPlayground = false;
    
    // Add the prompt as a user message
    const userMessage: DisplayMessage = {
      id: `msg-${++this.messageId}`,
      role: 'user',
      content: `📊 Report: ${event.title}\n\n${event.prompt}`,
      timestamp: new Date()
    };
    this.messages.push(userMessage);
    
    // Add the response as assistant message
    const assistantMessage: DisplayMessage = {
      id: `msg-${++this.messageId}`,
      role: 'assistant',
      content: event.response.message,
      timestamp: new Date(),
      response: event.response,
      tableSize: this.getDefaultTableSize(event.response),
      isTableLoading: false
    };
    this.messages.push(assistantMessage);
    
    this.shouldScroll = true;
  }

  askQuestion(question: string) {
    this.inputText = question;
    this.sendMessage();
  }

  sendMessage() {
    if (!this.inputText.trim() || this.isProcessing) return;

    const userMessage: DisplayMessage = {
      id: `msg-${++this.messageId}`,
      role: 'user',
      content: this.inputText.trim(),
      timestamp: new Date()
    };
    this.messages.push(userMessage);

    const assistantMessage: DisplayMessage = {
      id: `msg-${++this.messageId}`,
      role: 'assistant',
      content: '',
      timestamp: new Date(),
      isLoading: true
    };
    this.messages.push(assistantMessage);

    const query = this.inputText.trim();
    this.inputText = '';
    this.isProcessing = true;
    this.shouldScroll = true;

    // Build history for context
    const history: ApiChatMessage[] = this.messages
      .filter(m => !m.isLoading && m.content)
      .slice(-6)
      .map(m => ({ role: m.role, content: m.content }));

    this.api.chat(query, history).subscribe({
      next: (response) => {
        assistantMessage.isLoading = false;
        assistantMessage.content = response.message;
        assistantMessage.response = response;
        assistantMessage.tableSize = this.getDefaultTableSize(response);
        assistantMessage.isTableLoading = false;
        this.isProcessing = false;
        this.shouldScroll = true;
      },
      error: (err) => {
        assistantMessage.isLoading = false;
        assistantMessage.content = 'Sorry, I encountered an error. Please try again.';
        assistantMessage.response = { 
          type: 'error', 
          message: 'Error',
          error: err.message || 'Connection failed' 
        };
        assistantMessage.tableSize = 20;
        assistantMessage.isTableLoading = false;
        this.isProcessing = false;
        this.shouldScroll = true;
      }
    });
  }

  formatMessage(content: string): string {
    return content
      .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
      .replace(/\n/g, '<br>');
  }

  formatFieldName(key: string): string {
    return key
      .replace(/_/g, ' ')
      .replace(/\./g, ' › ')
      .replace(/\b\w/g, c => c.toUpperCase());
  }

  formatFieldValue(value: any): string {
    if (value === null || value === undefined) return '—';
    if (typeof value === 'string') {
      const iso = this.tryFormatIsoDate(value);
      if (iso) return iso;
      const epoch = this.tryFormatEpoch(value);
      if (epoch) return epoch;
      return value;
    }
    const epochFromNumber = this.tryFormatEpoch(value);
    if (epochFromNumber) return epochFromNumber;
    if (typeof value === 'boolean') return value ? 'Yes' : 'No';
    if (typeof value === 'object') return JSON.stringify(value);
    if (typeof value === 'number') return String(value);
    return String(value);
  }

  getDisplayFields(row: Record<string, any>, preferredFields?: string[]): { key: string; value: any }[] {
    const skip = ['_id', '_score'];
    let keys = Object.keys(row).filter(k => !skip.includes(k));
    
    if (preferredFields?.length) {
      const preferred = preferredFields.filter(f => keys.includes(f));
      const others = keys.filter(k => !preferredFields.includes(k));
      keys = [...preferred, ...others];
    }
    
    return keys.slice(0, 6).map(key => ({ key, value: row[key] }));
  }

  getTableColumns(row: Record<string, any> | undefined, preferredFields?: string[]): string[] {
    if (!row) return [];
    const skip = ['_id', '_score'];
    let keys = Object.keys(row).filter(k => !skip.includes(k));
    
    // CRITICAL: Always prioritize first_name, last_name, email_addr at the start
    const priorityFields = ['first_name', 'last_name', 'email_addr'];
    const orderedKeys: string[] = [];
    const seenFields = new Set<string>();
    
    // First, add priority fields if they exist
    for (const priorityField of priorityFields) {
      if (keys.includes(priorityField) && !seenFields.has(priorityField)) {
        orderedKeys.push(priorityField);
        seenFields.add(priorityField);
      }
    }
    
    if (preferredFields?.length) {
      // Then add preferred fields (excluding priority fields already added)
      for (const field of preferredFields) {
        if (keys.includes(field) && !seenFields.has(field)) {
          orderedKeys.push(field);
          seenFields.add(field);
        }
      }
    } else {
      // If no preferred fields, add remaining keys in their original order
      for (const key of keys) {
        if (!seenFields.has(key)) {
          orderedKeys.push(key);
          seenFields.add(key);
        }
      }
    }
    
    // Add any remaining keys that weren't in priority or preferred
    for (const key of keys) {
      if (!seenFields.has(key)) {
        orderedKeys.push(key);
      }
    }
    
    return orderedKeys.slice(0, 8); // Limit to 8 columns for readability
  }

  private tryFormatIsoDate(value: string): string | null {
    const trimmed = value?.trim();
    if (!trimmed || !/^\d{4}-\d{2}-\d{2}/.test(trimmed)) return null;
    try {
      return new Date(trimmed).toLocaleDateString('en-US', {
        year: 'numeric', month: 'short', day: 'numeric'
      });
    } catch {
      return null;
    }
  }

  private tryFormatEpoch(value: unknown): string | null {
    if (typeof value === 'number' && this.isEpochNumber(value)) {
      return this.formatEpochValue(value);
    }
    if (typeof value === 'string') {
      const trimmed = value.trim();
      if (this.isEpochString(trimmed)) {
        const num = Number(trimmed);
        if (Number.isFinite(num)) {
          return this.formatEpochValue(num);
        }
      }
    }
    return null;
  }

  private isEpochNumber(value: number): boolean {
    return Number.isFinite(value) && value >= 1e9 && value <= 1e13;
  }

  private isEpochString(value: string): boolean {
    return /^\d{10}$/.test(value) || /^\d{13}$/.test(value);
  }

  private formatEpochValue(value: number): string {
    const ms = value < 1e12 ? value * 1000 : value;
    const date = new Date(ms);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric'
    });
  }

  getIndexTableColumns(schema: IndexSchema): string[] {
    if (!schema) return [];
    if (this.indexColumnCache.has(schema.index_id)) {
      return this.indexColumnCache.get(schema.index_id)!;
    }
    const skip = ['_id', '_score'];
    const sample = schema.sample_documents?.[0];
    let columns: string[];
    
    if (sample) {
      columns = Object.keys(sample)
        .filter(key => !skip.includes(key))
        .slice(0, 8);
    } else {
      columns = schema.fields.slice(0, 6).map(field => field.name);
    }
    
    if (!columns.length) {
      columns = ['module_name', 'email_addr', 'created_on'].filter(col =>
        schema.fields.some(field => field.name === col)
      );
    }
    
    this.indexColumnCache.set(schema.index_id, columns);
    return columns;
  }

  formatIndexValue(value: any): string {
    if (value === null || value === undefined) return '—';
    if (Array.isArray(value)) {
      const preview = value.slice(0, 3).join(', ');
      return value.length > 3 ? `${preview}…` : preview;
    }
    if (typeof value === 'object') {
      try {
        return JSON.stringify(value);
      } catch {
        return '[object]';
      }
    }
    if (typeof value === 'number') {
      return this.formatNumber(value);
    }
    if (typeof value === 'boolean') {
      return value ? 'Yes' : 'No';
    }
    return String(value);
  }

  toggleQuery(msg: DisplayMessage) {
    msg.showQuery = !msg.showQuery;
  }

  shouldShowQuery(msg: DisplayMessage): boolean {
    // Show query if button is toggled, even if query is empty (will show empty object)
    return !!msg.showQuery;
  }

  getQueryJson(msg: DisplayMessage): string {
    return JSON.stringify(msg.response?.query ?? {}, null, 2);
  }

  getAggregations(aggregations: Record<string, any> | undefined): { name: string; value: string | number; items?: string[]; description?: string }[] {
    if (!aggregations) return [];
    
    return Object.entries(aggregations).map(([name, data]) => {
      let value: string | number;
      let items: string[] | undefined;
      let label: string;
      let description: string | undefined;
      
      if (typeof data === 'object') {
        // Use the label from backend if available
        label = data._label || this.formatAggLabel(name);
        description = data._description;
        
        if ('value' in data) {
          value = typeof data.value === 'number' ? this.formatNumber(data.value) : data.value;
        } else if ('count' in data && typeof data.count === 'object' && 'value' in data.count) {
          // Nested aggregation (e.g., filter > cardinality)
          value = this.formatNumber(data.count.value);
        } else if ('unique_completed' in data && typeof data.unique_completed === 'object') {
          // Another nested pattern
          value = this.formatNumber(data.unique_completed.value);
        } else if ('buckets' in data && Array.isArray(data.buckets)) {
          // Show actual bucket values - filter out empty/blank keys
          const validBuckets = data.buckets.filter((b: any) => b.key !== undefined && String(b.key).trim() !== '');
          items = validBuckets.map((b: any) => `${b.key} (${b.doc_count})`);
          const bucketTotal = validBuckets.reduce(
            (sum: number, bucket: any) => sum + (typeof bucket.doc_count === 'number' ? bucket.doc_count : 0),
            0
          );
          value = this.formatNumber(bucketTotal);
        } else if ('doc_count' in data) {
          // Filter aggregation result
          value = this.formatNumber(data.doc_count);
        } else if (this.isStatsAggregation(data)) {
          value = this.formatStatsSummary(data);
        } else {
          value = this.formatObjectSummary(data);
        }
      } else {
        label = this.formatAggLabel(name);
        value = String(data);
      }
      
      return { 
        name: label, 
        value,
        items,
        description
      };
    });
  }

  private isStatsAggregation(data: Record<string, any>): boolean {
    if (!data) return false;
    const statsKeys = ['min', 'max', 'avg', 'sum', 'count'];
    return statsKeys.some(key => typeof data[key] === 'number');
  }

  private formatStatsSummary(data: Record<string, any>): string {
    const parts: string[] = [];

    const avg = data['avg'];
    if (typeof avg === 'number') {
      parts.push(`Avg ${this.formatNumber(avg, 1)}`);
    }

    const min = data['min'];
    if (typeof min === 'number') {
      parts.push(`Min ${this.formatNumber(min, 1)}`);
    }

    const max = data['max'];
    if (typeof max === 'number') {
      parts.push(`Max ${this.formatNumber(max, 1)}`);
    }

    const count = data['count'];
    if (typeof count === 'number') {
      parts.push(`${this.formatNumber(count)} records`);
    }

    const sum = data['sum'];
    if (!parts.length && typeof sum === 'number') {
      parts.push(`Sum ${this.formatNumber(sum, 1)}`);
    }

    return parts.join(' · ') || this.formatObjectSummary(data);
  }

  private formatObjectSummary(value: Record<string, any>): string {
    try {
      return Object.entries(value)
        .filter(([k]) => !k.startsWith('_'))
        .map(([k, v]) => `${this.formatAggLabel(k)}: ${typeof v === 'number' ? this.formatNumber(v) : v}`)
        .join(' · ');
    } catch {
      return '[data]';
    }
  }

  private formatNumber(value: number, decimals = 0): string {
    if (typeof value !== 'number' || isNaN(value)) return '0';
    return new Intl.NumberFormat('en-US', {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals
    }).format(value);
  }

  private formatAggLabel(name: string): string {
    // Fallback label formatting if backend doesn't provide one
    const labelMap: Record<string, string> = {
      // User metrics
      'unique_users': 'Unique Users',
      'unique_emails': 'Unique Users',
      'total_users': 'Total Users',
      'users_completed': 'Users Completed',
      
      // Module metrics
      'unique_modules': 'Unique Modules',
      'unique_modules_published': 'Modules Published',
      'unique_modules_consumed': 'Modules Consumed',
      'total_modules': 'Total Modules',
      'modules_completed': 'Modules Completed',
      'completed_modules': 'Completed Modules',
      
      // Common aggregation names
      'main_metric': 'Main Metric',
      'metric_1': 'Metric 1',
      'metric_2': 'Metric 2',
      
      // Breakdowns
      'by_module': 'By Module',
      'by_city': 'By City',
      'by_country': 'By Country',
      'modules_breakdown': 'Module Breakdown',
      'completions_by_module': 'Completions by Module',
      'completions_by_city': 'Completions by City',
      
      // Filter aggregations
      'completed': 'Completed',
      'completed_only': 'Completed',
      'not_completed': 'Not Completed',
    };
    
    const lower = name.toLowerCase();
    if (labelMap[lower]) return labelMap[lower];
    
    // Generate from name - make it more readable
    return name
      .replace(/_/g, ' ')
      .replace(/\b\w/g, c => c.toUpperCase())
      .replace('Unique ', '')  // Don't double up on "Unique"
      .trim();
  }

  getChartData(
    visualization: VisualizationConfig | undefined,
    aggregations: Record<string, any> | undefined,
    fallbackTitle?: string
  ): ChartData | null {
    if (!aggregations) return null;

    // Skip explicitly non-chart visualization hints
    if (visualization && ['kpi_widget', 'multi_kpi', 'table'].includes(visualization.type)) {
      return null;
    }

    const chartTypes = ['bar', 'pie', 'doughnut', 'line', 'horizontalBar', 'polarArea'];
    const vizType = visualization?.type;

    let labels: string[] = [];
    let data: number[] = [];
    let datasetLabel = 'Count';
    let inferredFallbackType: ChartTypeOption = 'bar';

    // First, check if this can be represented as a comparison chart
    const comparisonData: { label: string; value: number }[] = [];
    
    for (const [name, aggData] of Object.entries(aggregations)) {
      if (typeof aggData === 'object') {
        if ('value' in aggData && typeof aggData.value === 'number') {
          comparisonData.push({
            label: aggData._label || this.formatAggLabel(name),
            value: Math.round(aggData.value)
          });
        } else if ('count' in aggData && typeof aggData.count === 'object' && 'value' in aggData.count) {
          comparisonData.push({
            label: aggData._label || this.formatAggLabel(name),
            value: Math.round(aggData.count.value)
          });
        } else if ('unique_completed' in aggData && typeof aggData.unique_completed === 'object') {
          comparisonData.push({
            label: aggData._label || this.formatAggLabel(name),
            value: Math.round(aggData.unique_completed.value)
          });
        } else if ('doc_count' in aggData && !('buckets' in aggData)) {
          comparisonData.push({
            label: aggData._label || this.formatAggLabel(name),
            value: aggData.doc_count
          });
        }
      }
    }

    if (comparisonData.length >= 2) {
      labels = comparisonData.map(d => d.label);
      data = comparisonData.map(d => d.value);
      datasetLabel = 'Count';

      const comparisonTitle = visualization?.title || fallbackTitle || 'Comparison';
      const comparisonType: ChartTypeOption =
        vizType === 'comparison'
          ? 'bar'
          : (vizType && chartTypes.includes(vizType) ? (vizType as ChartTypeOption) : 'bar');

      return {
        type: comparisonType,
        title: comparisonTitle,
        labels,
        datasets: [{
          label: datasetLabel,
          data
        }]
      };
    }

    // Otherwise, look for bucket aggregations (breakdown by category)
    let foundBuckets = false;
    for (const [name, aggData] of Object.entries(aggregations)) {
      if (typeof aggData === 'object' && 'buckets' in aggData && Array.isArray(aggData.buckets)) {
        const validBuckets = aggData.buckets.filter((b: any) => b.key && String(b.key).trim() !== '');
        if (!validBuckets.length) continue;

        const isDateHistogram = (
          'key_as_string' in validBuckets[0] ||
          (typeof validBuckets[0]?.key === 'number' && validBuckets[0].key > 1e8) ||
          (typeof validBuckets[0]?.key === 'string' && (validBuckets[0].key.includes('-') || validBuckets[0].key.length === 10))
        );

        inferredFallbackType = isDateHistogram ? 'line' : 'bar';
        labels = validBuckets.map((b: any) => this.formatChartLabel(b.key));
        data = validBuckets.map((b: any) => {
          if (isDateHistogram) {
            return b.doc_count || 0;
          }
          if (b._count !== undefined) {
            return b._count;
          }
          for (const [nestedKey, nestedValue] of Object.entries(b)) {
            if (nestedKey === 'key' || nestedKey === 'doc_count' || nestedKey.startsWith('_')) continue;
            if (nestedValue && typeof nestedValue === 'object' && 'value' in nestedValue) {
              return (nestedValue as { value: number }).value;
            }
          }
          return b.doc_count || 0;
        });

        datasetLabel = aggData._label || this.formatAggLabel(name);
        foundBuckets = true;
        break;
      }
    }

    if (!foundBuckets || labels.length === 0) return null;

    const resolvedType: ChartTypeOption =
      vizType === 'comparison'
        ? 'bar'
        : vizType && chartTypes.includes(vizType)
          ? vizType as ChartTypeOption
          : inferredFallbackType;

    const title = visualization?.title || fallbackTitle || 'Chart';

    return {
      type: resolvedType,
      title,
      labels,
      datasets: [{
        label: datasetLabel,
        data
      }]
    };
  }
  private formatChartLabel(key: any): string {
    // Check if it's a Unix timestamp in milliseconds (13 digits, reasonable date range)
    if (typeof key === 'number' && key > 1000000000000 && key < 2000000000000) {
      return this.formatTimestampAsMonth(key);
    }
    // Check if it's a string that looks like a timestamp
    if (typeof key === 'string' && /^\d{13}$/.test(key)) {
      return this.formatTimestampAsMonth(parseInt(key, 10));
    }
    return this.truncateLabel(String(key), 25);
  }

  private formatTimestampAsMonth(timestamp: number): string {
    const date = new Date(timestamp);
    return date.toLocaleDateString('en-US', { month: 'short', year: 'numeric' });
  }

  private truncateLabel(label: string, maxLength: number): string {
    if (label.length <= maxLength) return label;
    return label.substring(0, maxLength - 3) + '...';
  }

  private getDefaultTableSize(response?: ChatResponse): number {
    const available = response?.query_result?.results?.length ?? 0;
    if (available > 0) {
      return Math.min(available, 20);
    }
    return 20;
  }

  getCurrentRowSelection(msg: DisplayMessage): number {
    if (typeof msg.tableSize === 'number') {
      return msg.tableSize;
    }
    return this.getDefaultTableSize(msg.response);
  }

  exportFromMessage(msg: DisplayMessage) {
    const response = msg.response;
    const queryResult = response?.query_result;
    const results = queryResult?.results ?? [];
    const fields = response?.fields_to_show;
    const indexId = response?.index_id;
    const query = response?.query;
    const total = queryResult?.total ?? results.length;

    const downloadBlob = (blob: Blob, fileName: string) => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = fileName;
      a.click();
      URL.revokeObjectURL(url);
    };

    const fallbackToClientExport = () => {
      if (indexId && query && total > results.length) {
        const size = Math.min(total, 10000);
        if (size <= results.length) {
          this.exportResults(results, fields);
          return;
        }
        const fullQuery = { ...query, from: 0 };
        this.api.executeQuery(indexId, fullQuery, size).subscribe({
          next: (res) => this.exportResults(res?.results ?? results, fields),
          error: () => this.exportResults(results, fields)
        });
        return;
      }
      this.exportResults(results, fields);
    };

    // Preferred: server-side streaming export (works beyond max_result_window and avoids client memory issues)
    if (indexId && query) {
      const filename = `${(response?.title || 'query_results').replace(/[^a-z0-9]/gi, '_')}_${Date.now()}.csv`;
      this.api.exportQueryCsv(indexId, query, fields, filename).subscribe({
        next: (blob) => {
          if (!blob || blob.size === 0) {
            fallbackToClientExport();
            return;
          }
          downloadBlob(blob, filename);
        },
        error: () => fallbackToClientExport()
      });
      return;
    }

    this.exportResults(results, fields);
  }

  getTotalResults(msg: DisplayMessage): number {
    const res = msg.response?.query_result;
    if (!res) return 0;
    const total = res.total;
    if (typeof total === 'number' && total > 0) {
      return total;
    }
    const fallback = res.count ?? res.results?.length ?? 0;
    return typeof fallback === 'number' ? fallback : 0;
  }

  getVisibleRowCount(msg: DisplayMessage): number {
    const results = msg.response?.query_result?.results ?? [];
    const desired = msg.tableSize ?? 20;
    const limit = desired === -1 ? results.length : desired;
    return Math.min(limit, results.length);
  }

  getVisibleRows(msg: DisplayMessage): Record<string, any>[] {
    const results = msg.response?.query_result?.results ?? [];
    return results.slice(0, this.getVisibleRowCount(msg));
  }

  onTableSizeChange(msg: DisplayMessage, raw: any) {
    const desired = typeof raw === 'number' ? raw : parseInt(String(raw), 10);
    msg.tableSize = Number.isFinite(desired) ? desired : 20;

    const response = msg.response;
    const indexId = response?.index_id;
    const query = response?.query;
    const queryResult = response?.query_result;
    const current = queryResult?.results?.length ?? 0;
    const total = this.getTotalResults(msg);

    if (!indexId || !query || !queryResult) return;

    const availableTotal = total > 0 ? total : current;
    const targetSize = (msg.tableSize === -1)
      ? Math.min(availableTotal, 10000)
      : Math.min(Math.max(msg.tableSize, 0), 10000);

    // If we already have enough rows loaded, no need to re-run.
    if (targetSize <= current) return;

    msg.isTableLoading = true;
    const rerunQuery = { ...query, from: 0 };
    this.api.executeQuery(indexId, rerunQuery, targetSize).subscribe({
      next: (res) => {
        msg.isTableLoading = false;
        if (!msg.response) return;
        // Create new object references to trigger Angular change detection
        const newQueryResult = {
          ...queryResult,
          results: res?.results ?? queryResult.results,
          count: res?.count ?? queryResult.count,
          total: res?.total ?? queryResult.total,
          took: res?.took ?? queryResult.took
        };
        msg.response = {
          ...msg.response,
          query_result: newQueryResult
        };
      },
      error: () => {
        msg.isTableLoading = false;
      }
    });
  }

  exportResults(results: Record<string, any>[], fields?: string[]) {
    if (!results?.length) return;
    
    const keys = fields?.length ? fields : Object.keys(results[0]).filter(k => !k.startsWith('_'));
    const rows = results.map(row => 
      keys.map(k => {
        const v = row[k];
        if (v === null || v === undefined) return '';
        if (typeof v === 'string' && v.includes(',')) return `"${v}"`;
        return String(v);
      }).join(',')
    );
    
    const csv = [keys.join(','), ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `data_export_${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  private scrollToBottom() {
    try {
      this.chatContainer.nativeElement.scrollTop = this.chatContainer.nativeElement.scrollHeight;
    } catch {}
  }

  canAddToDashboard(msg: DisplayMessage): boolean {
    const response = msg.response;
    if (!response || response.type !== 'query') return false;
    if (!response.query) return false;
    if (response.response_type === 'text_response') return false;
    const hasData = !!(response.query_result?.results?.length || response.query_result?.aggregations);
    return hasData;
  }

  openAddToDashboard(msg: DisplayMessage) {
    const response = msg.response;
    if (!response?.query) return;

    this.pendingWidgetData = {
      type: this.resolveDashboardType(response),
      title: this.buildWidgetTitle(msg),
      index_id: response.index_id,
      query_payload: response.query,
      render_config: this.buildRenderConfig(response),
      // Include timeframe metadata if available
      timeframe_key: response.timeframe_key,
      timezone: response.timezone,
      date_field: response.date_field,
      date_mode: response.date_mode,
    };
    this.showAddToDashboardModal = true;
  }

  closeAddToDashboardModal() {
    this.showAddToDashboardModal = false;
    this.pendingWidgetData = null;
  }

  handleWidgetSaved(item: DashboardItem) {
    this.closeAddToDashboardModal();
    this.showToast(`Added "${item.title}" to My Dashboards`);
  }

  private buildWidgetTitle(msg: DisplayMessage): string {
    return (
      msg.response?.title?.trim() ||
      msg.response?.visualization?.title?.trim() ||
      msg.content?.trim() ||
      'Dashboard Widget'
    );
  }

  private buildRenderConfig(response: ChatResponse): DashboardItem['render_config'] {
    const config: DashboardItem['render_config'] = {
      response_type: response.response_type,
    };

    if (response.fields_to_show?.length) {
      config.fields_to_show = response.fields_to_show;
    }

    if (response.visualization) {
      config.visualization = { ...response.visualization };
    }

    if (response.metrics) {
      config.metrics = response.metrics;
    }

    return config;
  }

  private resolveDashboardType(response: ChatResponse): DashboardItem['type'] {
    switch (response.response_type) {
      case 'bar_chart':
        return 'bar_chart';
      case 'line_chart':
        return 'line_chart';
      case 'pie_chart':
        return 'pie_chart';
      case 'kpi_widget':
        return 'kpi_widget';
      case 'multi_kpi':
        return 'multi_kpi';
      case 'comparison':
        return 'comparison';
      case 'table':
        return 'table';
    }

    const vizType = response.visualization?.type;
    if (vizType) {
      if (vizType === 'pie' || vizType === 'doughnut') return 'pie_chart';
      if (vizType === 'line') return 'line_chart';
      return 'bar_chart';
    }

    if (response.query_result?.results?.length) {
      return 'table';
    }

    if (response.query_result?.aggregations) {
      return 'bar_chart';
    }

    return 'table';
  }

  private showToast(message: string) {
    this.dashboardToast = { message };
    if (this.toastTimeoutId) {
      clearTimeout(this.toastTimeoutId);
    }
    this.toastTimeoutId = setTimeout(() => {
      this.dashboardToast = undefined;
      this.toastTimeoutId = null;
    }, 3200);
  }
}
