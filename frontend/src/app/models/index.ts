export interface OpenSearchIndex {
  id: string;
  name: string;
  display_name: string;
}

export interface SchemaField {
  name: string;
  type: string;
  searchable: boolean;
  sortable: boolean;
  filterable: boolean;
  has_keyword?: boolean;
  keyword_field?: string;
}

export interface IndexSchema {
  index_id: string;
  index_name: string;
  fields: SchemaField[];
  field_count: number;
}

export interface MegaFilter {
  id: string;
  label: string;
  description: string;
}

export interface AdditionalFilter {
  id: string;
  field: string;
  operator: FilterOperator;
  value?: string | number;
  values?: string[];
  min_value?: number;
  max_value?: number;
}

export type FilterOperator = 'equals' | 'not_equals' | 'contains' | 'greater_than' | 'less_than' | 'between' | 'in';

export interface QueryRequest {
  index_id: string;
  prompt: string;
}

export interface QueryResult {
  success: boolean;
  total: number;
  count: number;
  results: Record<string, any>[];
  took: number;
  aggregations?: Record<string, any>;
}

export interface GeneratedQuery {
  success: boolean;
  query: Record<string, any>;
  raw_response?: string;
}

export interface PromptRequest {
  index_name: string;
  mega_filter?: string;
  additional_filters: AdditionalFilter[];
  selected_columns: string[];
  sort_field?: string;
  sort_order: 'asc' | 'desc';
}

export interface PromptResponse {
  prompt: string;
  title: string;
}

// New response type definitions for AI Reports Bot
export type ResponseType = 
  | 'kpi_widget' 
  | 'multi_kpi' 
  | 'table' 
  | 'bar_chart' 
  | 'line_chart' 
  | 'pie_chart' 
  | 'comparison'
  | 'text_response';

export interface KPIMetric {
  label: string;
  value: number | string;
  description?: string;
  trend?: 'up' | 'down' | 'neutral';
  percentage?: number;
}

export interface VisualizationConfig {
  type: 'kpi_widget' | 'multi_kpi' | 'bar' | 'line' | 'pie' | 'comparison' | 'table';
  title?: string;
  x_label?: string;
  y_label?: string;
}

export interface AIResponse {
  type: 'query' | 'message' | 'error';
  message: string;
  index_id?: string;
  query?: Record<string, any>;
  query_result?: QueryResult;
  metrics?: Record<string, { label: string; description: string }>;
  visualization?: VisualizationConfig;
  response_type?: ResponseType;
  time_period?: {
    description: string;
    is_lifetime: boolean;
  };
  error?: string;
}
