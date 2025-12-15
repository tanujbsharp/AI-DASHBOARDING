import { Component, Input, OnChanges, SimpleChanges, ElementRef, ViewChild, AfterViewInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Chart, ChartConfiguration, ChartType, registerables, TooltipModel } from 'chart.js';

// Register all Chart.js components
Chart.register(...registerables);

export type ChartTypeOption = 'bar' | 'pie' | 'doughnut' | 'line' | 'polarArea' | 'horizontalBar';

export interface ChartData {
  type: ChartTypeOption;
  title: string;
  labels: string[];
  datasets: {
    label: string;
    data: number[];
    backgroundColor?: string | string[];
    borderColor?: string | string[];
  }[];
}

@Component({
  selector: 'app-chart-display',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="chart-container">
      <div class="chart-header">
        <h3 class="chart-title">{{ chartData?.title }}</h3>
        <div class="chart-actions">
          <button class="chart-type-btn" [class.active]="currentType === 'bar'" (click)="changeType('bar')" title="Bar Chart">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="3" y="12" width="4" height="9"/><rect x="10" y="6" width="4" height="15"/><rect x="17" y="9" width="4" height="12"/>
            </svg>
          </button>
          <button class="chart-type-btn" [class.active]="currentType === 'pie'" (click)="changeType('pie')" title="Pie Chart">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21.21 15.89A10 10 0 1 1 8 2.83"/><path d="M22 12A10 10 0 0 0 12 2v10z"/>
            </svg>
          </button>
          <button class="chart-type-btn" [class.active]="currentType === 'line'" (click)="changeType('line')" title="Line Chart">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
            </svg>
          </button>
          <button class="chart-type-btn" [class.active]="currentType === 'doughnut'" (click)="changeType('doughnut')" title="Doughnut Chart">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="4"/>
            </svg>
          </button>
        </div>
      </div>
      <div class="chart-wrapper">
        <canvas #chartCanvas></canvas>
      </div>
    </div>
  `,
  styles: [`
    .chart-container {
      background: white;
      border-radius: var(--radius-lg);
      border: 1px solid var(--border-primary);
      overflow: hidden;
    }

    .chart-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: var(--spacing-md) var(--spacing-lg);
      border-bottom: 1px solid var(--border-primary);
      background: var(--bg-tertiary);
    }

    .chart-title {
      margin: 0;
      font-size: 1rem;
      font-weight: 600;
      color: var(--text-primary);
    }

    .chart-actions {
      display: flex;
      gap: 4px;
    }

    .chart-type-btn {
      width: 32px;
      height: 32px;
      border-radius: var(--radius-sm);
      border: 1px solid var(--border-primary);
      background: white;
      color: var(--text-tertiary);
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: all 0.15s;

      &:hover {
        border-color: var(--accent-primary);
        color: var(--accent-primary);
      }

      &.active {
        background: var(--accent-primary);
        border-color: var(--accent-primary);
        color: white;
      }
    }

    .chart-wrapper {
      padding: var(--spacing-lg);
      height: 350px;
      position: relative;
    }

    canvas {
      max-width: 100%;
      max-height: 100%;
    }
  `]
})
export class ChartDisplayComponent implements OnChanges, AfterViewInit, OnDestroy {
  @Input() chartData: ChartData | null = null;
  @ViewChild('chartCanvas') canvasRef!: ElementRef<HTMLCanvasElement>;

  private chart: Chart | null = null;
  private tooltipEl: HTMLDivElement | null = null;
  currentType: ChartTypeOption = 'bar';

  // Beautiful color palette
  private colorPalette = [
    'rgba(8, 145, 178, 0.8)',    // Cyan
    'rgba(168, 85, 247, 0.8)',   // Purple
    'rgba(34, 197, 94, 0.8)',    // Green
    'rgba(249, 115, 22, 0.8)',   // Orange
    'rgba(236, 72, 153, 0.8)',   // Pink
    'rgba(59, 130, 246, 0.8)',   // Blue
    'rgba(234, 179, 8, 0.8)',    // Yellow
    'rgba(239, 68, 68, 0.8)',    // Red
    'rgba(20, 184, 166, 0.8)',   // Teal
    'rgba(139, 92, 246, 0.8)',   // Violet
  ];

  private borderPalette = [
    'rgba(8, 145, 178, 1)',
    'rgba(168, 85, 247, 1)',
    'rgba(34, 197, 94, 1)',
    'rgba(249, 115, 22, 1)',
    'rgba(236, 72, 153, 1)',
    'rgba(59, 130, 246, 1)',
    'rgba(234, 179, 8, 1)',
    'rgba(239, 68, 68, 1)',
    'rgba(20, 184, 166, 1)',
    'rgba(139, 92, 246, 1)',
  ];

  private boundHideTooltip = this.hideTooltip.bind(this);

  ngAfterViewInit() {
    if (this.chartData) {
      this.createChart();
    }
    // Add mouseleave listener to hide tooltip when leaving chart area
    this.canvasRef?.nativeElement?.addEventListener('mouseleave', this.boundHideTooltip);
  }

  ngOnChanges(changes: SimpleChanges) {
    if (changes['chartData'] && this.canvasRef) {
      this.createChart();
    }
  }

  ngOnDestroy() {
    // Remove event listener
    this.canvasRef?.nativeElement?.removeEventListener('mouseleave', this.boundHideTooltip);
    
    if (this.tooltipEl) {
      this.tooltipEl.remove();
      this.tooltipEl = null;
    }
    if (this.chart) {
      this.chart.destroy();
      this.chart = null;
    }
  }

  private hideTooltip() {
    if (this.tooltipEl) {
      this.tooltipEl.style.opacity = '0';
    }
  }

  changeType(type: ChartTypeOption) {
    this.currentType = type;
    this.createChart();
  }

  private getOrCreateTooltip(): HTMLDivElement {
    if (!this.tooltipEl) {
      this.tooltipEl = document.createElement('div');
      this.tooltipEl.id = 'chartjs-tooltip-' + Math.random().toString(36).substr(2, 9);
      this.tooltipEl.style.cssText = `
        position: fixed;
        background: rgba(15, 23, 42, 0.95);
        border-radius: 8px;
        color: white;
        pointer-events: none;
        transform: translate(-50%, -100%);
        transition: opacity 0.15s ease, transform 0.15s ease;
        padding: 10px 14px;
        font-family: 'Plus Jakarta Sans', sans-serif;
        font-size: 13px;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.25);
        z-index: 99999;
        white-space: nowrap;
        opacity: 0;
      `;
      document.body.appendChild(this.tooltipEl);
    }
    return this.tooltipEl;
  }

  private externalTooltipHandler = (context: { chart: Chart; tooltip: TooltipModel<any> }) => {
    const { chart, tooltip } = context;
    const tooltipEl = this.getOrCreateTooltip();

    // Hide if no tooltip
    if (tooltip.opacity === 0) {
      tooltipEl.style.opacity = '0';
      return;
    }

    // Set Text
    if (tooltip.body) {
      const titleLines = tooltip.title || [];
      const bodyLines = tooltip.body.map(b => b.lines);

      let innerHtml = '';

      titleLines.forEach(title => {
        // Format timestamp titles as readable dates
        const formattedTitle = this.formatTooltipTitle(title);
        innerHtml += `<div style="font-weight: 600; margin-bottom: 4px; color: #94a3b8;">${formattedTitle}</div>`;
      });

      bodyLines.forEach((body, i) => {
        const colors = tooltip.labelColors[i];
        const colorBox = `<span style="display: inline-block; width: 10px; height: 10px; margin-right: 8px; border-radius: 2px; background: ${colors.backgroundColor};"></span>`;
        body.forEach(line => {
          innerHtml += `<div style="display: flex; align-items: center;">${colorBox}<span>${line}</span></div>`;
        });
      });

      tooltipEl.innerHTML = innerHtml;
    }

    // Position tooltip using fixed positioning (no scroll offset needed)
    const canvas = chart.canvas;
    const canvasRect = canvas.getBoundingClientRect();
    
    const left = canvasRect.left + tooltip.caretX;
    const top = canvasRect.top + tooltip.caretY - 10;

    tooltipEl.style.opacity = '1';
    tooltipEl.style.left = left + 'px';
    tooltipEl.style.top = top + 'px';
  };

  private formatTooltipTitle(title: string): string {
    // Check if title looks like a Unix timestamp (13 digits)
    if (/^\d{13}$/.test(title)) {
      const date = new Date(parseInt(title, 10));
      return date.toLocaleDateString('en-US', { month: 'short', year: 'numeric' });
    }
    return title;
  }

  private createChart() {
    if (!this.chartData || !this.canvasRef) return;

    // Destroy existing chart
    if (this.chart) {
      this.chart.destroy();
    }

    const ctx = this.canvasRef.nativeElement.getContext('2d');
    if (!ctx) return;

    // Use the type from chartData or current selected type
    const chartType: ChartTypeOption = this.currentType || this.chartData.type || 'bar';
    this.currentType = chartType;

    // Map horizontalBar to bar with indexAxis
    const isHorizontal = chartType === 'horizontalBar';
    const actualType: ChartType = isHorizontal ? 'bar' : chartType as ChartType;

    // Prepare colors
    const dataLength = this.chartData.labels.length;
    const backgroundColors = this.colorPalette.slice(0, dataLength);
    const borderColors = this.borderPalette.slice(0, dataLength);

    const datasets = this.chartData.datasets.map((ds, i) => ({
      ...ds,
      backgroundColor: ds.backgroundColor || (actualType === 'line' ? this.colorPalette[i] : backgroundColors),
      borderColor: ds.borderColor || (actualType === 'line' ? this.borderPalette[i] : borderColors),
      borderWidth: actualType === 'line' ? 2 : 1,
      tension: actualType === 'line' ? 0.3 : undefined,
      fill: actualType === 'line' ? false : undefined,
    }));

    const config: ChartConfiguration = {
      type: actualType,
      data: {
        labels: this.chartData.labels,
        datasets: datasets,
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        indexAxis: isHorizontal ? 'y' : 'x',
        plugins: {
          legend: {
            display: this.chartData.datasets.length > 1 || ['pie', 'doughnut', 'polarArea'].includes(actualType),
            position: 'bottom',
            labels: {
              padding: 20,
              usePointStyle: true,
            }
          },
          tooltip: {
            enabled: false,
            external: this.externalTooltipHandler,
          }
        },
        scales: ['pie', 'doughnut', 'polarArea'].includes(actualType) ? {} : {
          x: {
            grid: {
              display: false,
            },
            ticks: {
              maxRotation: 45,
              minRotation: 0,
            }
          },
          y: {
            beginAtZero: true,
            grid: {
              color: 'rgba(0, 0, 0, 0.05)',
            }
          }
        },
        animation: {
          duration: 750,
          easing: 'easeOutQuart',
        }
      }
    };

    this.chart = new Chart(ctx, config);
  }
}

