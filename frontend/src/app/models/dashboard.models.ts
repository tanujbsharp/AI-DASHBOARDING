export interface Dashboard {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
}

export interface DashboardItem {
  id: string;
  dashboard_id: string;
  type: 'kpi_widget' | 'multi_kpi' | 'comparison' | 'bar_chart' | 'pie_chart' | 'line_chart' | 'table';
  title: string;
  index_id?: string;
  query_payload: Record<string, any>;
  render_config: {
    fields_to_show?: string[];
    visualization?: {
      type: string;
      title?: string;
      x_field?: string;
      y_field?: string;
    };
    metrics?: Record<string, { label: string; description: string }>;
    response_type?: string;
  };
  layout: {
    x: number;
    y: number;
    cols: number;
    rows: number;
  };
  // Timeframe metadata for dynamic date ranges
  timeframe_key?: string;  // e.g., "LAST_3_MONTHS", "THIS_QUARTER"
  timezone?: string;       // e.g., "Asia/Kolkata" (default)
  date_field?: string;     // e.g., "completed_date" (default)
  date_mode?: string;      // e.g., "epoch_seconds" (default)
  created_at: string;
  updated_at: string;
}

