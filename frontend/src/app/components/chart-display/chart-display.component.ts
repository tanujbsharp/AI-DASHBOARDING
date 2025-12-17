import {
  Component,
  Input,
  OnChanges,
  SimpleChanges,
  ElementRef,
  ViewChild,
  AfterViewInit,
  OnDestroy,
  NgZone,
} from '@angular/core';
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

      <div class="chart-wrapper" #chartWrapper>
        <canvas #chartCanvas></canvas>
      </div>
    </div>
  `,
  styles: [`
    :host {
      display: block;
      width: 100%;
      height: 100%;
      min-height: 0;
      min-width: 0;
      /* If the parent doesn't provide an explicit height (chat cards), enforce a sensible default
         so the canvas has space and Chart.js can initialize. Can be overridden by parents. */
      --chart-min-height: 360px;
    }

    .chart-container {
      background: white;
      border-radius: var(--radius-lg);
      border: 1px solid var(--border-primary);
      overflow: hidden;
      height: 100%;
      min-height: 0;
      min-width: 0;
      display: flex;
      flex-direction: column;
    }

    .chart-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: var(--spacing-md) var(--spacing-lg);
      border-bottom: 1px solid var(--border-primary);
      background: var(--bg-tertiary);
      flex: 0 0 auto;
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
      flex: 1 1 0;
      min-height: 0;
      min-width: 0;
      position: relative;
      /* key: ensure wrapper actually occupies space even under zoom/layout */
      height: 100%;
      min-height: var(--chart-min-height);
    }

    canvas {
      display: block; /* avoids inline-canvas layout weirdness */
      max-width: 100%;
      max-height: 100%;
    }
  `]
})
export class ChartDisplayComponent implements OnChanges, AfterViewInit, OnDestroy {
  @Input() chartData: ChartData | null = null;

  @ViewChild('chartCanvas') canvasRef!: ElementRef<HTMLCanvasElement>;
  @ViewChild('chartWrapper') wrapperRef!: ElementRef<HTMLDivElement>;

  private chart: Chart | null = null;
  private tooltipEl: HTMLDivElement | null = null;

  currentType: ChartTypeOption = 'bar';

  private isViewInitialized = false;

  private resizeObserver: ResizeObserver | null = null;
  private resizeRaf: number | null = null;
  private initRaf: number | null = null;
  private boundWindowResize = this.onWindowResize.bind(this);
  private boundHideTooltip = this.hideTooltip.bind(this);
  private hasAnimatedOnce = false;

  // palette
  private colorPalette = [
    'rgba(8, 145, 178, 0.8)',
    'rgba(168, 85, 247, 0.8)',
    'rgba(34, 197, 94, 0.8)',
    'rgba(249, 115, 22, 0.8)',
    'rgba(236, 72, 153, 0.8)',
    'rgba(59, 130, 246, 0.8)',
    'rgba(234, 179, 8, 0.8)',
    'rgba(239, 68, 68, 0.8)',
    'rgba(20, 184, 166, 0.8)',
    'rgba(139, 92, 246, 0.8)',
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

  constructor(private zone: NgZone) {}

  ngAfterViewInit() {
    this.isViewInitialized = true;

    // Hide tooltip when leaving chart area
    this.canvasRef?.nativeElement?.addEventListener('mouseleave', this.boundHideTooltip);

    // Observe wrapper resizes (Gridster / zoom / layout)
    if (this.wrapperRef?.nativeElement && 'ResizeObserver' in window) {
      this.resizeObserver = new ResizeObserver(() => {
        // if chart exists -> resize
        // if chart doesn't exist yet but data is present and now we have size -> init
        this.scheduleChartResizeOrInit();
      });
      this.resizeObserver.observe(this.wrapperRef.nativeElement);
    }

    window.addEventListener('resize', this.boundWindowResize);

    // Important: delay init until wrapper has non-zero size
    this.scheduleChartResizeOrInit(true);
  }

  ngOnChanges(changes: SimpleChanges) {
    if (!changes['chartData']) return;

    if (!this.isViewInitialized) return;

    // data changed: re-init chart, but only when wrapper has size
    this.scheduleChartResizeOrInit(true);
  }

  ngOnDestroy() {
    this.canvasRef?.nativeElement?.removeEventListener('mouseleave', this.boundHideTooltip);
    window.removeEventListener('resize', this.boundWindowResize);

    if (this.resizeObserver) {
      this.resizeObserver.disconnect();
      this.resizeObserver = null;
    }

    if (this.resizeRaf) cancelAnimationFrame(this.resizeRaf);
    if (this.initRaf) cancelAnimationFrame(this.initRaf);

    this.resizeRaf = null;
    this.initRaf = null;

    if (this.tooltipEl) {
      this.tooltipEl.remove();
      this.tooltipEl = null;
    }

    if (this.chart) {
      this.chart.destroy();
      this.chart = null;
    }
  }

  changeType(type: ChartTypeOption) {
    this.currentType = type;
    this.scheduleChartResizeOrInit(true);
  }

  private onWindowResize() {
    this.scheduleChartResizeOrInit();
  }

  /** Schedules either init (if no chart) or resize (if chart) after layout settles. */
  private scheduleChartResizeOrInit(forceRecreate = false) {
    // Run outside Angular to avoid triggering change detection on every resize tick
    this.zone.runOutsideAngular(() => {
      if (this.initRaf) cancelAnimationFrame(this.initRaf);

      // double rAF: gives Gridster/DOM time to settle at 100% zoom too
      this.initRaf = requestAnimationFrame(() => {
        this.initRaf = requestAnimationFrame(() => {
          const size = this.getWrapperSize();
          if (!size) return; // still zero; ResizeObserver/window resize will try again

          if (!this.chartData) return;

          if (!this.chart || forceRecreate) {
            this.createChart();     // will create with correct container size
          } else {
            this.resizeChart();     // stable resize
          }
        });
      });
    });
  }

  private getWrapperSize(): { width: number; height: number } | null {
    const el = this.wrapperRef?.nativeElement;
    if (!el) return null;

    const rect = el.getBoundingClientRect();
    const w = Math.floor(rect.width);
    const h = Math.floor(rect.height);

    // Chart.js can bug out if it initializes at 0 or tiny px (happens at 100% zoom in some layouts)
    if (w < 50 || h < 50) return null;

    return { width: w, height: h };
  }

  private resizeChart() {
    if (!this.chart) return;

    if (this.resizeRaf) cancelAnimationFrame(this.resizeRaf);

    this.resizeRaf = requestAnimationFrame(() => {
      this.chart?.resize();
      this.chart?.update('none');
    });
  }

  private hideTooltip() {
    if (this.tooltipEl) this.tooltipEl.style.opacity = '0';
  }

  private getOrCreateTooltip(): HTMLDivElement {
    if (!this.tooltipEl) {
      this.tooltipEl = document.createElement('div');
      this.tooltipEl.id = 'chartjs-tooltip-' + Math.random().toString(36).slice(2);
      this.tooltipEl.style.cssText = `
        position: fixed;
        background: rgba(15, 23, 42, 0.95);
        border-radius: 8px;
        color: white;
        pointer-events: none;
        transform: translate(-50%, -100%);
        transition: opacity 0.15s ease;
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

    if (tooltip.opacity === 0) {
      tooltipEl.style.opacity = '0';
      return;
    }

    if (tooltip.body) {
      const titleLines = tooltip.title || [];
      const bodyLines = tooltip.body.map(b => b.lines);

      let innerHtml = '';

      titleLines.forEach(title => {
        innerHtml += `<div style="font-weight: 600; margin-bottom: 4px; color: #94a3b8;">${this.formatTooltipTitle(title)}</div>`;
      });

      bodyLines.forEach((body, i) => {
        const colors = tooltip.labelColors[i];
        const colorBox = `<span style="display:inline-block;width:10px;height:10px;margin-right:8px;border-radius:2px;background:${colors.backgroundColor};"></span>`;
        body.forEach(line => {
          innerHtml += `<div style="display:flex;align-items:center;">${colorBox}<span>${line}</span></div>`;
        });
      });

      tooltipEl.innerHTML = innerHtml;
    }

    const canvasRect = chart.canvas.getBoundingClientRect();
    const left = canvasRect.left + tooltip.caretX;
    const top = canvasRect.top + tooltip.caretY - 10;

    tooltipEl.style.opacity = '1';
    tooltipEl.style.left = left + 'px';
    tooltipEl.style.top = top + 'px';
  };

  private formatTooltipTitle(title: string): string {
    // Accept 10-digit seconds or 13-digit ms timestamps
    if (/^\d{10}$/.test(title)) {
      const date = new Date(parseInt(title, 10) * 1000);
      return date.toLocaleDateString('en-US', { day: '2-digit', month: 'short', year: 'numeric' });
    }
    if (/^\d{13}$/.test(title)) {
      const date = new Date(parseInt(title, 10));
      return date.toLocaleDateString('en-US', { day: '2-digit', month: 'short', year: 'numeric' });
    }
    return title;
  }

  private createChart() {
    if (!this.chartData || !this.canvasRef?.nativeElement) return;

    // Destroy existing chart
    if (this.chart) {
      this.chart.destroy();
      this.chart = null;
    }

    const ctx = this.canvasRef.nativeElement.getContext('2d');
    if (!ctx) return;

    const chartType: ChartTypeOption = this.currentType || this.chartData.type || 'bar';
    this.currentType = chartType;

    const isHorizontal = chartType === 'horizontalBar';
    const actualType: ChartType = isHorizontal ? 'bar' : (chartType as ChartType);

    const dataLength = this.chartData.labels.length;
    const backgroundColors = this.colorPalette.slice(0, Math.max(1, dataLength));
    const borderColors = this.borderPalette.slice(0, Math.max(1, dataLength));

    const datasets = this.chartData.datasets.map((ds, i) => ({
      ...ds,
      backgroundColor: ds.backgroundColor || (actualType === 'line' ? this.colorPalette[i % this.colorPalette.length] : backgroundColors),
      borderColor: ds.borderColor || (actualType === 'line' ? this.borderPalette[i % this.borderPalette.length] : borderColors),
      borderWidth: actualType === 'line' ? 2 : 1,
      tension: actualType === 'line' ? 0.3 : undefined,
      fill: actualType === 'line' ? false : undefined,
      pointRadius: actualType === 'line' ? 2 : undefined,
    }));

    const shouldAnimate = !this.hasAnimatedOnce;
    this.hasAnimatedOnce = true;

    const config: ChartConfiguration = {
      type: actualType,
      data: {
        labels: this.chartData.labels,
        datasets,
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
              padding: 16,
              usePointStyle: true,
            },
          },
          tooltip: {
            enabled: false,
            external: this.externalTooltipHandler,
          },
        },
        scales: ['pie', 'doughnut', 'polarArea'].includes(actualType)
          ? {}
          : {
              x: {
                grid: { display: false },
                ticks: {
                  maxRotation: 45,
                  minRotation: 0,
                  autoSkip: (this.chartData?.labels.length ?? 0) > 45,
                },
              },
              y: {
                beginAtZero: true,
                grid: { color: 'rgba(0, 0, 0, 0.05)' },
              },
            },
        animation: shouldAnimate
          ? {
              duration: 550,
              easing: 'easeOutQuart',
            }
          : false,
      },
    };

    this.chart = new Chart(ctx, config);

    // Immediately resize once the chart exists (important for Gridster/zoom)
    this.resizeChart();
  }
}