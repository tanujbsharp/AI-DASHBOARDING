import { Component, OnInit, inject, ViewChild, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, Router, RouterModule } from '@angular/router';
import { GridsterModule, GridsterComponent } from 'angular-gridster2';
import { GridsterConfig, GridsterItem } from 'angular-gridster2';
import { DashboardService } from '../../services/dashboard.service';
import { ApiService } from '../../services/api.service';
import { Dashboard, DashboardItem } from '../../models/dashboard.models';
import { ChartDisplayComponent } from '../chart-display/chart-display.component';

interface WidgetData extends GridsterItem {
  item: DashboardItem;
  isLoading?: boolean;
  error?: string;
  queryResult?: any;
}

type Layout = { x: number; y: number; cols: number; rows: number };

@Component({
  selector: 'app-dashboard-builder',
  standalone: true,
  imports: [CommonModule, GridsterModule, RouterModule, ChartDisplayComponent],
  template: `
    <div class="dashboard-builder">
      <div class="dashboard-header">
        <div class="header-left">
          <button class="back-btn" (click)="goBack()">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="15 18 9 12 15 6"/>
            </svg>
            Back
          </button>
          <h1>{{ dashboard?.name || 'Dashboard' }}</h1>
        </div>
        <div class="header-actions">
          <button class="btn btn-secondary" (click)="refreshAll()" [disabled]="isRefreshing">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="23 4 23 10 17 10"/>
              <polyline points="1 20 1 14 7 14"/>
              <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
            </svg>
            Refresh All
          </button>
          <button class="btn btn-danger" (click)="deleteDashboard()">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="3 6 5 6 21 6"/>
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
            </svg>
            Delete Dashboard
          </button>
        </div>
      </div>

      @if (!dashboard) {
        <div class="empty-state">
          <p>Dashboard not found</p>
          <button class="btn btn-primary" (click)="goBack()">Go Back</button>
        </div>
      } @else if (widgets.length === 0) {
        <div class="empty-state">
          <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1">
            <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
            <line x1="3" y1="9" x2="21" y2="9"/>
            <line x1="9" y1="21" x2="9" y2="9"/>
          </svg>
          <p>No widgets yet</p>
          <span class="hint">Add widgets from the chat interface using the + icon</span>
        </div>
      } @else {
        <gridster [options]="gridsterOptions" class="dashboard-grid" #gridster>
          @for (widget of widgets; track widget.item.id) {
            <!-- IMPORTANT: no (change) binding here; Gridster callbacks are used -->
            <gridster-item [item]="widget">
              <div class="widget-container">
                <div
                  class="widget-header"
                  (mouseenter)="showHeaderTooltip($event, widget.item.title)"
                  (mouseleave)="hideHeaderTooltip()"
                >
                  <div class="widget-header-left">
                    <div class="drag-handle">
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <circle cx="9" cy="5" r="1"/>
                        <circle cx="9" cy="12" r="1"/>
                        <circle cx="9" cy="19" r="1"/>
                        <circle cx="15" cy="5" r="1"/>
                        <circle cx="15" cy="12" r="1"/>
                        <circle cx="15" cy="19" r="1"/>
                      </svg>
                    </div>
                    <div class="widget-title">{{ widget.item.title }}</div>
                  </div>
                  <div class="widget-actions">
                    <button class="icon-btn" (click)="refreshWidget(widget)" [disabled]="widget.isLoading" title="Refresh">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <polyline points="23 4 23 10 17 10"/>
                        <polyline points="1 20 1 14 7 14"/>
                        <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
                      </svg>
                    </button>
                    <button class="icon-btn" (click)="editTitle(widget)" title="Edit Title">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                        <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                      </svg>
                    </button>
                    <button class="icon-btn danger" (click)="deleteWidget(widget)" title="Delete">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <polyline points="3 6 5 6 21 6"/>
                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                      </svg>
                    </button>
                  </div>
                </div>

                <div class="widget-content">
                  @if (widget.isLoading) {
                    <div class="widget-loading">
                      <div class="spinner"></div>
                      <span>Loading...</span>
                    </div>
                  } @else if (widget.error) {
                    <div class="widget-error">
                      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <circle cx="12" cy="12" r="10"/>
                        <line x1="12" y1="8" x2="12" y2="12"/>
                        <line x1="12" y1="16" x2="12.01" y2="16"/>
                      </svg>
                      <p>{{ widget.error }}</p>
                    </div>
                  } @else {
                    @switch (widget.item.type) {
                      @case ('kpi_widget') {
                        <div class="kpi-widget">
                          @if (widget.queryResult?.aggregations) {
                            @for (kpi of extractKPIs(widget.queryResult.aggregations, widget.item.render_config.metrics); track kpi.label) {
                              <div class="kpi-item">
                                <div class="kpi-label">{{ kpi.label }}</div>
                                <div class="kpi-value">{{ formatNumber(kpi.value) }}</div>
                                @if (kpi.description) {
                                  <div class="kpi-description">{{ kpi.description }}</div>
                                }
                              </div>
                            }
                          } @else {
                            <div class="empty-widget">No aggregations found</div>
                          }
                        </div>
                      }
                      @case ('multi_kpi') {
                        <div class="multi-kpi">
                          @if (widget.queryResult?.aggregations) {
                            @for (kpi of extractKPIs(widget.queryResult.aggregations, widget.item.render_config.metrics); track kpi.label) {
                              <div class="kpi-card">
                                <div class="kpi-card-label">{{ kpi.label }}</div>
                                <div class="kpi-card-value">{{ formatNumber(kpi.value) }}</div>
                              </div>
                            }
                          }
                        </div>
                      }
                      @case ('comparison') {
                        <div class="comparison-widget">
                          @if (widget.queryResult?.aggregations) {
                            @for (kpi of extractKPIs(widget.queryResult.aggregations, widget.item.render_config.metrics); track kpi.label; let i = $index) {
                              <div class="comparison-item">
                                <div class="comparison-label">{{ kpi.label }}</div>
                                <div class="comparison-value">{{ formatNumber(kpi.value) }}</div>
                              </div>
                              @if (i === 0 && extractKPIs(widget.queryResult.aggregations, widget.item.render_config.metrics).length > 1) {
                                <div class="comparison-vs">VS</div>
                              }
                            }
                          }
                        </div>
                      }
                      @case ('bar_chart') {
                        @if (widget.queryResult?.aggregations && widget.item.render_config.visualization) {
                          <app-chart-display [chartData]="getChartData(widget.queryResult.aggregations, widget.item.render_config.visualization)" />
                        }
                      }
                      @case ('pie_chart') {
                        @if (widget.queryResult?.aggregations && widget.item.render_config.visualization) {
                          <app-chart-display [chartData]="getChartData(widget.queryResult.aggregations, widget.item.render_config.visualization)" />
                        }
                      }
                      @case ('line_chart') {
                        @if (widget.queryResult?.aggregations && widget.item.render_config.visualization) {
                          <app-chart-display [chartData]="getChartData(widget.queryResult.aggregations, widget.item.render_config.visualization)" />
                        }
                      }
                      @case ('table') {
                        <div class="table-widget">
                          @if (widget.queryResult?.results?.length) {
                            <table class="data-table">
                              <thead>
                                <tr>
                                  <th>#</th>
                                  @for (col of getTableColumns(widget.queryResult.results[0], widget.item.render_config.fields_to_show); track col) {
                                    <th>{{ formatFieldName(col) }}</th>
                                  }
                                </tr>
                              </thead>
                              <tbody>
                                @for (row of widget.queryResult.results.slice(0, 50); track $index; let i = $index) {
                                  <tr>
                                    <td class="row-num">{{ i + 1 }}</td>
                                    @for (col of getTableColumns(row, widget.item.render_config.fields_to_show); track col) {
                                      <td>{{ formatFieldValue(row[col]) }}</td>
                                    }
                                  </tr>
                                }
                              </tbody>
                            </table>
                            @if (widget.queryResult.results.length > 50) {
                              <div class="table-footer">
                                Showing 50 of {{ widget.queryResult.total || widget.queryResult.results.length }} results
                              </div>
                            }
                          } @else if (widget.queryResult?.aggregations) {
                            <div class="empty-widget">
                              <p>This query returns aggregations, not table data.</p>
                              <p style="font-size: 0.75rem; color: var(--text-tertiary); margin-top: 8px;">
                                Try changing the widget type to a chart or KPI widget.
                              </p>
                            </div>
                          } @else {
                            <div class="empty-widget">
                              <p>No data available</p>
                              @if (widget.queryResult) {
                                <p style="font-size: 0.75rem; color: var(--text-tertiary); margin-top: 8px;">
                                  Query executed successfully but returned no results.
                                </p>
                              }
                            </div>
                          }
                        </div>
                      }
                    }
                  }
                </div>
              </div>
            </gridster-item>
          }
        </gridster>
      }
    </div>

    <div
      class="widget-header-tooltip"
      *ngIf="headerTooltip.visible"
      [ngStyle]="{ top: headerTooltip.top + 'px', left: headerTooltip.left + 'px' }"
    >
      {{ headerTooltip.title }}
    </div>
  `,
  styles: [`
    .dashboard-builder { height: 100vh; display: flex; flex-direction: column; background: var(--bg-primary, #f9fafb); }

    .dashboard-header {
      display: flex; align-items: center; justify-content: space-between;
      padding: var(--spacing-lg, 20px) var(--spacing-xl, 24px);
      background: white; border-bottom: 1px solid var(--border-primary, #e5e7eb);
      box-shadow: 0 1px 3px rgba(0,0,0,0.1);
    }

    .header-left { display: flex; align-items: center; gap: var(--spacing-md, 16px); }
    .header-left h1 { margin: 0; font-size: 1.5rem; font-weight: 600; color: var(--text-primary, #1f2937); }

    .back-btn {
      display: flex; align-items: center; gap: var(--spacing-xs, 8px);
      padding: var(--spacing-xs, 8px) var(--spacing-sm, 12px);
      background: var(--bg-tertiary, #f9fafb);
      border: 1px solid var(--border-primary, #e5e7eb);
      border-radius: var(--radius-md, 8px);
      color: var(--text-secondary, #6b7280);
      font-size: 0.875rem; cursor: pointer; transition: all 0.2s;
    }
    .back-btn:hover { background: var(--bg-card-hover, #f3f4f6); color: var(--text-primary, #1f2937); }

    .header-actions { display: flex; gap: var(--spacing-sm, 12px); }

    .btn {
      display: flex; align-items: center; gap: var(--spacing-xs, 8px);
      padding: var(--spacing-sm, 12px) var(--spacing-md, 16px);
      border-radius: var(--radius-md, 8px);
      font-size: 0.875rem; font-weight: 500;
      cursor: pointer; transition: all 0.2s; border: 1px solid transparent;
    }
    .btn.btn-secondary {
      background: var(--bg-tertiary, #f9fafb);
      color: var(--text-secondary, #6b7280);
      border-color: var(--border-primary, #e5e7eb);
    }
    .btn.btn-secondary:hover:not(:disabled) { background: var(--bg-card-hover, #f3f4f6); }
    .btn.btn-secondary:disabled { opacity: 0.5; cursor: not-allowed; }
    .btn.btn-danger { background: #ef4444; color: white; border-color: #ef4444; }
    .btn.btn-danger:hover { background: #dc2626; }

    .empty-state {
      flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;
      padding: var(--spacing-2xl, 48px); text-align: center;
    }
    .empty-state svg { color: var(--text-tertiary, #9ca3af); margin-bottom: var(--spacing-md, 16px); }
    .empty-state p { font-size: 1.125rem; font-weight: 600; color: var(--text-secondary, #6b7280); margin-bottom: var(--spacing-xs, 8px); }
    .empty-state .hint { font-size: 0.875rem; color: var(--text-tertiary, #9ca3af); margin-bottom: var(--spacing-lg, 20px); }

    .dashboard-grid { flex: 1; padding: 16px; overflow-y: auto; background: var(--bg-primary, #f9fafb); }
    :host ::ng-deep gridster { background: transparent; }

    :host ::ng-deep gridster-item { border-radius: var(--radius-md, 8px); overflow: visible !important; }
    :host ::ng-deep .gridster-item-moving { z-index: 1000; }

    /* make handles easy to grab, esp vertical */
    :host ::ng-deep .gridster-item-resizable-handle {
      pointer-events: auto !important;
      z-index: 9999 !important;
      background: var(--accent-primary, #0891b2);
      opacity: 0.6;
      border-radius: 2px;
    }
    :host ::ng-deep gridster-item:hover .gridster-item-resizable-handle { opacity: 0.95; }

    :host ::ng-deep .gridster-item-resizable-handle.handle-s,
    :host ::ng-deep .gridster-item-resizable-handle.handle-n {
      height: 14px !important;
      width: 56px !important;
      left: 50% !important;
      margin-left: -28px !important;
      cursor: ns-resize !important;
    }
    :host ::ng-deep .gridster-item-resizable-handle.handle-s { bottom: -7px !important; }
    :host ::ng-deep .gridster-item-resizable-handle.handle-n { top: -7px !important; }

    :host ::ng-deep .gridster-item-resizable-handle.handle-e { width: 8px !important; right: -4px !important; cursor: ew-resize !important; }
    :host ::ng-deep .gridster-item-resizable-handle.handle-w { width: 8px !important; left: -4px !important; cursor: ew-resize !important; }

    :host ::ng-deep .gridster-item-resizable-handle.handle-se,
    :host ::ng-deep .gridster-item-resizable-handle.handle-sw,
    :host ::ng-deep .gridster-item-resizable-handle.handle-ne,
    :host ::ng-deep .gridster-item-resizable-handle.handle-nw {
      width: 16px !important; height: 16px !important;
    }
    :host ::ng-deep .gridster-item-resizable-handle.handle-se { right: -7px !important; bottom: -7px !important; cursor: nwse-resize !important; }
    :host ::ng-deep .gridster-item-resizable-handle.handle-sw { left: -7px !important; bottom: -7px !important; cursor: nesw-resize !important; }
    :host ::ng-deep .gridster-item-resizable-handle.handle-ne { right: -7px !important; top: -7px !important; cursor: nesw-resize !important; }
    :host ::ng-deep .gridster-item-resizable-handle.handle-nw { left: -7px !important; top: -7px !important; cursor: nwse-resize !important; }

    .widget-container {
      height: 100%;
      display: flex; flex-direction: column;
      background: white;
      border: 1px solid var(--border-primary, #e5e7eb);
      border-radius: var(--radius-md, 8px);
      box-shadow: 0 1px 2px rgba(0,0,0,0.08);
      overflow: visible;
    }

    .widget-header {
      display: flex; align-items: center; justify-content: space-between;
      padding: 8px 12px;
      background: var(--bg-tertiary, #f9fafb);
      border-bottom: 1px solid var(--border-primary, #e5e7eb);
      cursor: grab; user-select: none;
      position: relative;
    }
    .widget-header:active { cursor: grabbing; }

    .widget-header-tooltip {
      position: fixed;
      transform: translate(-50%, -100%);
      background: rgba(15, 23, 42, 0.96);
      color: #fff;
      font-size: 0.75rem;
      padding: 4px 8px;
      border-radius: 6px;
      box-shadow: 0 6px 18px rgba(0,0,0,0.2);
      pointer-events: none;
      z-index: 5000;
      white-space: nowrap;
    }

    .widget-header-tooltip::after {
      content: '';
      position: absolute;
      bottom: -6px;
      left: 50%;
      transform: translateX(-50%);
      border: 6px solid transparent;
      border-top-color: rgba(15, 23, 42, 0.96);
    }

    .widget-header-left { display: flex; align-items: center; gap: 8px; flex: 1; min-width: 0; }
    .drag-handle { display: flex; align-items: center; color: var(--text-tertiary, #9ca3af); cursor: grab; flex-shrink: 0; }
    .drag-handle:active { cursor: grabbing; }

    .widget-title { font-size: 0.8125rem; font-weight: 600; color: var(--text-primary, #1f2937); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

    .widget-actions { display: flex; gap: var(--spacing-xs, 4px); pointer-events: auto; }
    .icon-btn {
      width: 24px; height: 24px;
      display: flex; align-items: center; justify-content: center;
      background: transparent; border: none; border-radius: var(--radius-sm, 4px);
      color: var(--text-secondary, #6b7280);
      cursor: pointer; transition: all 0.2s; opacity: 0.7;
      pointer-events: auto;
    }
    .icon-btn:hover:not(:disabled) { background: var(--bg-card-hover, #f3f4f6); color: var(--text-primary, #1f2937); opacity: 1; }
    .icon-btn.danger:hover:not(:disabled) { background: #fee2e2; color: #dc2626; opacity: 1; }
    .icon-btn:disabled { opacity: 0.3; cursor: not-allowed; }
    .icon-btn svg { width: 12px; height: 12px; }

    .widget-content {
      flex: 1;
      padding: 12px;
      overflow: auto;
      pointer-events: auto;
      max-height: 100%;
      /* Allow embedded visualizations (charts) to fill the available height */
      display: flex;
      flex-direction: column;
      min-height: 0;
    }

    /* Ensure chart widgets can actually take height inside the flex container */
    :host ::ng-deep app-chart-display {
      flex: 1 1 auto;
      min-height: 0;
    }

    .widget-loading, .widget-error {
      display: flex; flex-direction: column; align-items: center; justify-content: center;
      padding: var(--spacing-xl, 32px); text-align: center;
    }
    .widget-loading .spinner {
      width: 32px; height: 32px;
      border: 3px solid var(--border-primary, #e5e7eb);
      border-top-color: var(--accent-primary, #0891b2);
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
      margin-bottom: var(--spacing-sm, 12px);
    }
    .widget-error svg { color: #ef4444; margin-bottom: var(--spacing-sm, 12px); }
    .widget-loading p, .widget-error p { color: var(--text-secondary, #6b7280); font-size: 0.875rem; }

    @keyframes spin { to { transform: rotate(360deg); } }

    .kpi-widget, .multi-kpi, .comparison-widget { display: flex; flex-direction: column; gap: var(--spacing-md, 16px); }
    .kpi-item { text-align: center; padding: 12px 16px; background: linear-gradient(135deg, #5cbdce, #67c5d4); border-radius: var(--radius-md, 8px); }
    .kpi-label { font-size: 0.75rem; font-weight: 600; color: #1a1a1a; margin-bottom: 8px; }
    .kpi-value { font-size: 1.75rem; font-weight: 700; color: #1a1a1a; }
    .kpi-description { font-size: 0.75rem; color: #333; margin-top: var(--spacing-xs, 8px); }

    .multi-kpi { display: grid; grid-template-columns: repeat(auto-fit, minmax(80px, 1fr)); gap: 8px; }
    .kpi-card { padding: 10px 12px; background: linear-gradient(135deg, #f0f9ff, #e0f2fe); border: 1px solid #bae6fd; border-radius: var(--radius-md, 8px); text-align: center; }
    .kpi-card-label { font-size: 0.6875rem; font-weight: 600; color: var(--text-secondary, #6b7280); text-transform: uppercase; margin-bottom: 6px; }
    .kpi-card-value { font-size: 1.25rem; font-weight: 700; color: #0891b2; }

    .comparison-widget { display: flex; align-items: center; justify-content: center; gap: 12px; flex-wrap: wrap; }
    .comparison-item { text-align: center; }
    .comparison-label { font-size: 0.75rem; color: var(--text-secondary, #6b7280); margin-bottom: 6px; }
    .comparison-value { font-size: 1.5rem; font-weight: 700; color: #0891b2; }
    .comparison-vs { font-size: 0.875rem; font-weight: 700; color: var(--text-tertiary, #9ca3af); }

    .table-widget { overflow-x: auto; }
    .data-table { width: 100%; border-collapse: collapse; font-size: 0.75rem; }
    .data-table th, .data-table td { padding: 6px 8px; text-align: left; border-bottom: 1px solid var(--border-primary, #e5e7eb); }
    .data-table th { background: var(--bg-tertiary, #f9fafb); font-weight: 600; color: var(--text-secondary, #6b7280); font-size: 0.6875rem; text-transform: uppercase; }
    .data-table .row-num { width: 30px; text-align: center; color: var(--text-tertiary, #9ca3af); font-size: 0.6875rem; }
    .data-table tbody tr:hover td { background: var(--bg-tertiary, #f9fafb); }

    .table-footer { padding: var(--spacing-sm, 12px); text-align: center; font-size: 0.75rem; color: var(--text-tertiary, #9ca3af); border-top: 1px solid var(--border-primary, #e5e7eb); }
    .empty-widget { padding: var(--spacing-xl, 32px); text-align: center; color: var(--text-tertiary, #9ca3af); }
  `]
})
export class DashboardBuilderComponent implements OnInit, OnDestroy {
  headerTooltip = {
    visible: false,
    title: '',
    top: 0,
    left: 0,
  };
  @ViewChild(GridsterComponent) gridster?: GridsterComponent;

  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private dashboardService = inject(DashboardService);
  private apiService = inject(ApiService);

  dashboard: Dashboard | null = null;
  widgets: WidgetData[] = [];
  isRefreshing = false;

  private isDragging = false;
  private isResizing = false;

  private saveDebounceTimer: any = null;
  private dirtyWidgetIds = new Set<string>();

  // === GRIDSTER OPTIONS (DO NOT MUTATE LATER) ===
  gridsterOptions: GridsterConfig = {
    // IMPORTANT: use verticalFixed so rows use fixedRowHeight (vertical resizing becomes predictable)
    // 'fit' makes cell height depend on container, which often breaks "vertical resize feels real"
    gridType: 'verticalFixed',

    compactType: 'none',
    margin: 8,
    outerMargin: true,

    useTransformPositioning: true,
    mobileBreakpoint: 640,

    minCols: 24,
    maxCols: 24,
    minRows: 1,
    maxRows: 2000,

    defaultItemCols: 6,
    defaultItemRows: 3,

    fixedRowHeight: 50, // used by verticalFixed
    minItemCols: 3,
    maxItemCols: 24,
    minItemRows: 2,
    maxItemRows: 40,

    scrollSensitivity: 10,
    scrollSpeed: 20,

    enableEmptyCellClick: false,
    enableEmptyCellContextMenu: false,
    enableEmptyCellDrop: false,
    enableEmptyCellDrag: false,

    draggable: {
      enabled: true,
      ignoreContent: false,
      dragHandleClass: 'widget-header',
      ignoreContentClass: 'widget-content',

      start: (item: GridsterItem, _comp: any, _event: any) => {
        this.isDragging = true;
        const w = item as WidgetData;
        if (w?.item?.id) this.dirtyWidgetIds.add(w.item.id);
      },
      stop: (item: GridsterItem, _comp: any, _event: any) => {
        this.isDragging = false;
        const w = item as WidgetData;
        if (w?.item?.id) this.dirtyWidgetIds.add(w.item.id);
        this.flushSaveNow(); // save immediately when drag stops
      }
    } as any,

    resizable: {
      enabled: true,
      handles: { s: true, e: true, n: true, w: true, se: true, sw: true, ne: true, nw: true },

      start: (item: GridsterItem, _comp: any, _event: any) => {
        this.isResizing = true;
        const w = item as WidgetData;
        if (w?.item?.id) this.dirtyWidgetIds.add(w.item.id);
      },
      stop: (item: GridsterItem, _comp: any, _event: any) => {
        this.isResizing = false;
        const w = item as WidgetData;
        if (w?.item?.id) this.dirtyWidgetIds.add(w.item.id);
        this.flushSaveNow(); // save immediately when resize stops
      }
    } as any,

    // IMPORTANT: allow pushing items during resize
    pushItems: true,
    pushResizeItems: true,
    disablePushOnResize: false,

    swap: false,
    displayGrid: 'none',
    disableWindowResize: false,
    disableWarnings: false,
    scrollToNewItems: false,

    // IMPORTANT: these are the REAL change events (NOT (change) on gridster-item)
    itemChangeCallback: (item: GridsterItem) => this.onGridItemChanged(item),
    itemResizeCallback: (item: GridsterItem) => this.onGridItemChanged(item),
  };

  ngOnInit() {
    const dashboardId = this.route.snapshot.paramMap.get('id');
    if (!dashboardId) {
      this.router.navigate(['/']);
      return;
    }

    this.dashboard = this.dashboardService.getDashboard(dashboardId);
    if (!this.dashboard) return;

    this.loadWidgets();

    // Ensure grid recalculates after widgets render
    setTimeout(() => this.gridsterOptions.api?.optionsChanged?.(), 0);
  }

  // === Persistence (guaranteed) ===
  private layoutStorageKey(dashboardId: string) {
    return `bsharp.dashboard.layout.${dashboardId}`;
  }

  private readLayoutMap(): Record<string, Layout> {
    if (!this.dashboard?.id) return {};
    try {
      const raw = localStorage.getItem(this.layoutStorageKey(this.dashboard.id));
      if (!raw) return {};
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === 'object' ? parsed : {};
    } catch {
      return {};
    }
  }

  private writeLayout(widgetId: string, layout: Layout) {
    if (!this.dashboard?.id) return;
    const map = this.readLayoutMap();
    map[widgetId] = layout;
    localStorage.setItem(this.layoutStorageKey(this.dashboard.id), JSON.stringify(map));
  }

  loadWidgets() {
    if (!this.dashboard) return;

    const persisted = this.readLayoutMap();
    const items = this.dashboardService.getDashboardItems(this.dashboard.id) || [];

    this.widgets = items.map((item) => {
      const base = item.layout || { x: 0, y: 0, cols: 6, rows: 3 };
      const saved = persisted[item.id];

      const finalLayout: Layout = {
        x: saved?.x ?? base.x ?? 0,
        y: saved?.y ?? base.y ?? 0,
        cols: saved?.cols ?? base.cols ?? 6,
        rows: saved?.rows ?? base.rows ?? 3,
      };

      return {
        x: finalLayout.x,
        y: finalLayout.y,
        cols: finalLayout.cols,
        rows: finalLayout.rows,
        item: { ...item, layout: finalLayout }, // keep item.layout synced
        isLoading: true,
      };
    });

    this.widgets.forEach((w) => this.loadWidgetData(w));
  }

  // Called by Gridster for drag + resize
  private onGridItemChanged(item: GridsterItem) {
    const widget = item as WidgetData;
    if (!widget?.item?.id) return;

    // Mark dirty
    this.dirtyWidgetIds.add(widget.item.id);

    // During resize, Gridster may spam callbacks -> debounce
    this.scheduleSave();
  }

  private scheduleSave() {
    if (this.saveDebounceTimer) clearTimeout(this.saveDebounceTimer);
    this.saveDebounceTimer = setTimeout(() => {
      this.flushSaveNow();
    }, 250);
  }

  private flushSaveNow() {
    if (this.saveDebounceTimer) {
      clearTimeout(this.saveDebounceTimer);
      this.saveDebounceTimer = null;
    }

    // Persist only dirty widgets
    const ids = Array.from(this.dirtyWidgetIds);
    this.dirtyWidgetIds.clear();

    for (const id of ids) {
      const w = this.widgets.find((x) => x.item.id === id);
      if (w) this.saveWidgetLayout(w);
    }
  }

  private normalizeLayout(widget: WidgetData): Layout {
    return {
      x: Number.isFinite(widget.x as any) ? (widget.x as number) : 0,
      y: Number.isFinite(widget.y as any) ? (widget.y as number) : 0,
      cols: Number.isFinite(widget.cols as any) ? (widget.cols as number) : 6,
      rows: Number.isFinite(widget.rows as any) ? (widget.rows as number) : 3,
    };
  }

  private saveWidgetLayout(widget: WidgetData) {
    const layout = this.normalizeLayout(widget);

    // 1) guaranteed persistence
    this.writeLayout(widget.item.id, layout);

    // 2) also update your DashboardService (if it persists, great; if not, localStorage still works)
    const updated = this.dashboardService.updateItem(widget.item.id, { layout });

    // Keep in-memory object synced (prevents re-render from snapping back)
    widget.item = updated ? updated : { ...widget.item, layout };
    widget.x = layout.x;
    widget.y = layout.y;
    widget.cols = layout.cols;
    widget.rows = layout.rows;
    widget.item.layout = layout;
  }

  // === Data loading ===
  loadWidgetData(widget: WidgetData) {
    widget.isLoading = true;
    widget.error = undefined;

    if (!widget.item.index_id || !widget.item.query_payload) {
      widget.isLoading = false;
      widget.error = 'Missing query configuration';
      return;
    }

    this.apiService.executeQuery(widget.item.index_id, widget.item.query_payload).subscribe({
      next: (result) => {
        widget.isLoading = false;
        widget.queryResult = this.normalizeQueryResultForWidget(result, widget.item);
      },
      error: (err) => {
        widget.isLoading = false;
        widget.error = err.error?.error || err.message || 'Failed to load data';
      }
    });
  }

  refreshWidget(widget: WidgetData) {
    this.loadWidgetData(widget);
  }

  refreshAll() {
    this.isRefreshing = true;
    this.widgets.forEach((w) => this.loadWidgetData(w));
    setTimeout(() => (this.isRefreshing = false), 1000);
  }

  editTitle(widget: WidgetData) {
    const newTitle = prompt('Enter new title:', widget.item.title);
    if (newTitle && newTitle.trim()) {
      const updated = this.dashboardService.updateItem(widget.item.id, { title: newTitle.trim() });
      if (updated) widget.item = updated;
    }
  }

  deleteWidget(widget: WidgetData) {
    if (confirm(`Delete "${widget.item.title}"?`)) {
      this.dashboardService.deleteItem(widget.item.id);
      this.widgets = this.widgets.filter((w) => w.item.id !== widget.item.id);

      // Remove from guaranteed persistence map too
      if (this.dashboard?.id) {
        const map = this.readLayoutMap();
        delete map[widget.item.id];
        localStorage.setItem(this.layoutStorageKey(this.dashboard.id), JSON.stringify(map));
      }
    }
  }

  deleteDashboard() {
    if (!this.dashboard) return;
    if (confirm(`Delete dashboard "${this.dashboard.name}"? This cannot be undone.`)) {
      localStorage.removeItem(this.layoutStorageKey(this.dashboard.id));
      this.dashboardService.deleteDashboard(this.dashboard.id);
      this.router.navigate(['/dashboards']);
    }
  }

  goBack() {
    this.router.navigate(['/']);
  }

  // === KPI / Table / Chart helpers (unchanged) ===
  extractKPIs(
    aggregations: Record<string, any>,
    metrics?: Record<string, { label: string; description: string }>
  ): Array<{ label: string; value: number; description?: string }> {
    const kpis: Array<{ label: string; value: number; description?: string }> = [];
    for (const [key, value] of Object.entries(aggregations)) {
      if (!value || typeof value !== 'object') continue;
      const metricInfo = metrics?.[key] as { label?: string; description?: string } | undefined;

      if ('value' in value) {
        kpis.push({ label: metricInfo?.label || this.humanize(key), value: value.value, description: metricInfo?.description });
      } else if ('doc_count' in value) {
        kpis.push({ label: metricInfo?.label || this.humanize(key), value: value.doc_count, description: metricInfo?.description });
      }
    }
    return kpis;
  }

  formatNumber(value: number): string {
    if (value >= 1000000) return (value / 1000000).toFixed(1) + 'M';
    if (value >= 1000) return (value / 1000).toFixed(1) + 'K';
    return value.toLocaleString();
  }

  humanize(str: string): string {
    return str
      .replace(/_/g, ' ')
      .replace(/([A-Z])/g, ' $1')
      .trim()
      .split(' ')
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
      .join(' ');
  }

  getChartData(aggregations: Record<string, any>, visualization: any): any {
    let labels: string[] = [];
    let data: number[] = [];

    for (const [_name, aggData] of Object.entries(aggregations)) {
      if (typeof aggData === 'object' && aggData && 'buckets' in aggData && Array.isArray((aggData as any).buckets)) {
        const validBuckets = (aggData as any).buckets.filter((b: any) => b.key && String(b.key).trim() !== '');
        const isDateHistogram = this.isDateHistogramBuckets(validBuckets);
        const intervalSeconds = isDateHistogram ? this.estimateBucketInterval(validBuckets) : null;
        
        labels = validBuckets.map((b: any, index: number) => {
          if (isDateHistogram) {
            return this.formatDateBucketLabel(validBuckets, index, intervalSeconds);
          }

          if (b._display_name) return String(b._display_name);
          if (b.key_as_string) return String(b.key_as_string);
          return String(b.key ?? '');
        });
        
        data = validBuckets.map((b: any) => {
          if (isDateHistogram) {
            // For date histograms, ALWAYS use doc_count (actual number of completion records per day)
            return b.doc_count || 0;
          } else {
            // For other aggregations, prefer _count over doc_count
            return (b._count !== undefined ? b._count : b.doc_count || 0);
          }
        });
        break;
      }
    }

    if (labels.length === 0) return null;

    return {
      type: visualization.type === 'bar_chart' ? 'bar' : visualization.type === 'pie_chart' ? 'pie' : 'line',
      title: visualization.title || 'Chart',
      labels,
      datasets: [{ label: 'Count', data }]
    };
  }

  private isDateHistogramBuckets(buckets: Array<Record<string, any>>): boolean {
    if (!buckets.length) return false;
    const first = buckets[0] as Record<string, any>;
    return (
      'key_as_string' in first ||
      (typeof first?.['key'] === 'number' && first['key'] > 1e8) ||
      (typeof first?.['key'] === 'string' && (
        first['key'].includes('-') ||
        first['key'].length === 10 ||
        !Number.isNaN(Number(first['key']))
      ))
    );
  }

  private parseBucketTimestampMs(bucket: Record<string, any>): number | null {
    const raw = bucket?.['key'];
    let num = typeof raw === 'number' ? raw : (typeof raw === 'string' ? Number(raw) : NaN);
    if (!Number.isFinite(num)) return null;
    // Convert epoch seconds to millis
    if (num < 1e11) {
      return Math.floor(num * 1000);
    }
    return Math.floor(num);
  }

  private estimateBucketInterval(buckets: Array<Record<string, any>>): number | null {
    if (buckets.length < 2) return null;
    const firstTs = this.parseBucketTimestampMs(buckets[0]);
    let secondTs: number | null = null;
    for (let i = 1; i < buckets.length; i += 1) {
      secondTs = this.parseBucketTimestampMs(buckets[i]);
      if (secondTs !== null) break;
    }
    if (firstTs === null || secondTs === null) return null;
    return Math.abs(secondTs - firstTs) / 1000;
  }

  private formatDateBucketLabel(
    buckets: Array<Record<string, any>>,
    index: number,
    intervalSeconds: number | null
  ): string {
    const ts = this.parseBucketTimestampMs(buckets[index]);
    if (ts !== null) {
      const date = new Date(ts);
      if (intervalSeconds !== null) {
        if (intervalSeconds > 2.5e6) {
          return date.toLocaleDateString('en-US', { month: 'short', year: 'numeric' });
        }
        if (intervalSeconds > 80000) {
          return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
        }
      }
      return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    }

    // Fallbacks if timestamp parsing fails
    const bucket = buckets[index];
    if (bucket['_display_name']) return String(bucket['_display_name']);
    if (bucket['key_as_string']) return String(bucket['key_as_string']);
    return String(bucket['key'] ?? '');
  }

  getTableColumns(row: Record<string, any> | undefined, preferredFields?: string[]): string[] {
    if (!row) return [];
    const skip = ['_id', '_score'];
    const keys = Object.keys(row).filter((k) => !skip.includes(k));
    if (preferredFields?.length) {
      const available = preferredFields.filter((f) => keys.includes(f));
      return available.length > 0 ? available : keys.slice(0, 8);
    }
    return keys.slice(0, 8);
  }

  formatFieldName(key: string): string {
    return key
      .replace(/_/g, ' ')
      .replace(/\./g, ' › ')
      .replace(/\b\w/g, (c) => c.toUpperCase());
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

  ngOnDestroy() {
    if (this.saveDebounceTimer) {
      clearTimeout(this.saveDebounceTimer);
      this.saveDebounceTimer = null;
    }
  }

  showHeaderTooltip(event: MouseEvent, title: string) {
    if (!title || title.trim().length === 0) return;
    const target = event.currentTarget as HTMLElement;
    const rect = target.getBoundingClientRect();
    this.headerTooltip = {
      visible: true,
      title,
      top: rect.top + window.scrollY - 8,
      left: rect.left + rect.width / 2 + window.scrollX,
    };
  }

  hideHeaderTooltip() {
    this.headerTooltip.visible = false;
  }

  private normalizeQueryResultForWidget(result: any, item: DashboardItem) {
    if (!result || item.type !== 'table') return result;
    if (Array.isArray(result.results) && result.results.length > 0) return result;
    if (!result.aggregations) return result;

    const rows = this.extractRowsFromAggregations(result.aggregations);
    if (!rows.length) return result;

    return {
      ...result,
      results: rows,
      total: rows.length,
      count: rows.length,
    };
  }

  private extractRowsFromAggregations(aggregations: Record<string, any>): Record<string, any>[] {
    if (!aggregations) return [];
    for (const agg of Object.values(aggregations)) {
      if (!agg || typeof agg !== 'object') continue;
      if (!Array.isArray((agg as any).buckets)) continue;
      const rows: Record<string, any>[] = [];
      for (const bucket of (agg as any).buckets) {
        const topHit = this.extractTopHit(bucket);
        if (!topHit) continue;

        const row: Record<string, any> = { ...topHit };

        for (const [nestedKey, nestedValue] of Object.entries(bucket)) {
          if (
            nestedKey === 'key' ||
            nestedKey === '_display_name' ||
            nestedKey === 'key_as_string' ||
            nestedKey === 'doc_count' ||
            nestedKey.startsWith('_')
          ) {
            continue;
          }
          if (nestedValue && typeof nestedValue === 'object') {
            const nested = nestedValue as Record<string, any>;
            if ('value' in nested) {
              row[nestedKey] = nested['value'];
            }
          }
        }

        rows.push(row);
      }
      if (rows.length) return rows;
    }
    return [];
  }

  private extractTopHit(bucket: Record<string, any>): Record<string, any> | null {
    const candidateKeys = ['user_info', 'user_details', 'top_hits', 'module_info'];
    for (const key of candidateKeys) {
      const topHits = bucket?.[key];
      const hits = topHits?.hits?.hits;
      if (Array.isArray(hits) && hits.length > 0) {
        const source = hits[0]?._source;
        if (source && typeof source === 'object') {
          return source as Record<string, any>;
        }
      }
    }
    return null;
  }
}