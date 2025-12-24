import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface VisualizationConfig {
  type: 'bar' | 'pie' | 'doughnut' | 'line' | 'horizontalBar' | 'polarArea' | 'kpi_widget' | 'multi_kpi' | 'comparison' | 'table';
  title?: string;
  x_field?: string;
  y_field?: string;
  x_label?: string;
  y_label?: string;
}

export type ResponseType = 
  | 'kpi_widget' 
  | 'multi_kpi' 
  | 'table' 
  | 'bar_chart' 
  | 'line_chart' 
  | 'pie_chart' 
  | 'comparison'
  | 'text_response';

export interface ChatResponse {
  timeframe_key?: string;
  timezone?: string;
  date_field?: string;
  date_mode?: string;
  type: 'clarification' | 'query' | 'summary' | 'message' | 'error';
  message: string;
  index_id?: string;
  query?: Record<string, any>;
  fields_to_show?: string[];
  title?: string;
  visualization?: VisualizationConfig;
  metrics?: Record<string, { label: string; description: string }>;
  response_type?: ResponseType;
  time_period?: {
    description: string;
    is_lifetime: boolean;
  };
  query_result?: {
    success: boolean;
    total: number;
    count: number;
    results: Record<string, any>[];
    took: number;
    error?: string;
    aggregations?: Record<string, any>;
  };
  summary?: string;
  error?: string;
}

export interface FieldInfo {
  name: string;
  type: string;
  searchable: boolean;
  sortable: boolean;
  filterable: boolean;
  has_keyword?: boolean;
  keyword_field?: string;
  sample_values?: string[];
  stats?: { min: number; max: number; avg: number; count: number };
}

export interface IndexSchema {
  index_id: string;
  index_name: string;
  document_count: number;
  fields: FieldInfo[];
  field_count: number;
  sample_documents?: Record<string, any>[];
}

export interface QueryRequest {
  index_id: string;
  mega_filter?: string;
  additional_filters?: { field: string; operator: string; value: string }[];
  selected_columns: string[];
  sort_field?: string;
  sort_order?: 'asc' | 'desc';
}

@Injectable({
  providedIn: 'root',
})
export class ApiService {
  private http = inject(HttpClient);
  private baseUrl = '/api';

  chat(message: string, history: ChatMessage[] = []): Observable<ChatResponse> {
    return this.http.post<ChatResponse>(`${this.baseUrl}/chat/`, {
      message,
      history,
    });
  }

  healthCheck(): Observable<{ status: string }> {
    return this.http.get<{ status: string }>(`${this.baseUrl}/health/`);
  }

  getSchema(indexId: string): Observable<IndexSchema> {
    return this.http.get<IndexSchema>(`${this.baseUrl}/indexes/${indexId}/enriched-schema/`);
  }

  getAllSchemas(): Observable<{ schemas: Record<string, IndexSchema>; count: number }> {
    return this.http.get<{ schemas: Record<string, IndexSchema>; count: number }>(`${this.baseUrl}/schemas/`);
  }

  executeQuery(
    indexId: string,
    query: Record<string, any>,
    size?: number,
    timeframeKey?: string,
    timezone?: string,
    dateField?: string,
    dateMode?: string
  ): Observable<ChatResponse['query_result']> {
    return this.http.post<ChatResponse['query_result']>(`${this.baseUrl}/query/execute/`, {
      index_id: indexId,
      query,
      ...(size !== undefined ? { size } : {}),
      ...(timeframeKey ? { timeframe_key: timeframeKey } : {}),
      ...(timezone ? { timezone } : {}),
      ...(dateField ? { date_field: dateField } : {}),
      ...(dateMode ? { date_mode: dateMode } : {}),
    });
  }

  exportQueryCsv(
    indexId: string,
    query: Record<string, any>,
    fields?: string[],
    filename?: string
  ): Observable<Blob> {
    return this.http.post(`${this.baseUrl}/query/export/`, {
      index_id: indexId,
      query,
      ...(fields?.length ? { fields } : {}),
      ...(filename ? { filename } : {})
    }, { responseType: 'blob' });
  }

  buildPromptAndQuery(request: QueryRequest): Observable<ChatResponse> {
    return this.http.post<ChatResponse>(`${this.baseUrl}/chat/`, {
      message: this.buildPromptFromRequest(request),
      history: [],
    });
  }

  private buildPromptFromRequest(request: QueryRequest): string {
    let prompt = 'Create a table report';
    
    if (request.mega_filter) {
      prompt += ` for ${request.mega_filter}`;
    }
    
    if (request.additional_filters?.length) {
      const filterStrs = request.additional_filters.map(f => `${f.field} ${f.operator} ${f.value}`);
      prompt += `. Filter by: ${filterStrs.join(', ')}`;
    }
    
    if (request.selected_columns.length) {
      prompt += `. Show columns: ${request.selected_columns.join(', ')}`;
    }
    
    if (request.sort_field) {
      prompt += `. Sort by ${request.sort_field} ${request.sort_order || 'desc'}`;
    }
    
    return prompt;
  }
}
