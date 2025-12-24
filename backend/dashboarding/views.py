"""API views for the dashboarding engine."""

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.http import StreamingHttpResponse
from .services import OpenSearchClient, BedrockClient, QueryBuilder
from .services.conversation_service import get_conversation_service, reload_schemas
import logging
import csv
import io
import json
import copy

logger = logging.getLogger(__name__)


class ChatView(APIView):
    """Conversational chat endpoint."""
    
    def post(self, request):
        data = request.data
        message = data.get('message', '')
        history = data.get('history', [])
        
        if not message:
            return Response(
                {'error': 'Message is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        service = get_conversation_service()
        result = service.process_message(message, history)
        
        return Response(result)


class HealthCheckView(APIView):
    """Health check endpoint."""
    
    def get(self, request):
        return Response({'status': 'healthy', 'service': 'AI Dashboarding Engine'})


class IndexListView(APIView):
    """List all available OpenSearch indexes."""
    
    def get(self, request):
        client = OpenSearchClient()
        indexes = client.get_available_indexes()
        return Response({
            'indexes': indexes,
            'count': len(indexes)
        })


class IndexSchemaView(APIView):
    """Get schema/mapping for a specific index."""
    
    def get(self, request, index_id: str):
        client = OpenSearchClient()
        schema = client.get_index_mapping(index_id)
        
        if 'error' in schema:
            return Response(schema, status=status.HTTP_400_BAD_REQUEST)
        
        return Response(schema)


class FieldSampleValuesView(APIView):
    """Get sample values for a field (for filter suggestions)."""
    
    def get(self, request, index_id: str, field_name: str):
        client = OpenSearchClient()
        size = int(request.query_params.get('size', 20))
        
        values = client.get_sample_values(index_id, field_name, size)
        
        return Response({
            'field': field_name,
            'values': values,
            'count': len(values)
        })


class MegaFiltersView(APIView):
    """Get available mega filters."""
    
    def get(self, request):
        filters = QueryBuilder.get_mega_filters()
        return Response({
            'filters': filters,
            'count': len(filters)
        })


class BuildPromptView(APIView):
    """Build a natural language prompt from UI selections."""
    
    def post(self, request):
        data = request.data
        
        index_name = data.get('index_name', '')
        mega_filter = data.get('mega_filter')
        additional_filters = data.get('additional_filters', [])
        selected_columns = data.get('selected_columns', [])
        sort_field = data.get('sort_field')
        sort_order = data.get('sort_order', 'desc')
        
        prompt = QueryBuilder.build_prompt(
            index_name=index_name,
            mega_filter=mega_filter,
            additional_filters=additional_filters,
            selected_columns=selected_columns,
            sort_field=sort_field,
            sort_order=sort_order
        )
        
        title = QueryBuilder.generate_report_title(mega_filter, additional_filters)
        
        return Response({
            'prompt': prompt,
            'title': title
        })


class GenerateQueryView(APIView):
    """Generate an Elasticsearch query using Claude."""
    
    def post(self, request):
        data = request.data
        
        index_id = data.get('index_id')
        prompt = data.get('prompt')
        
        if not index_id or not prompt:
            return Response(
                {'error': 'index_id and prompt are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get schema for context
        os_client = OpenSearchClient()
        schema = os_client.get_index_mapping(index_id)
        
        if 'error' in schema:
            return Response(schema, status=status.HTTP_400_BAD_REQUEST)
        
        # Generate query using Claude
        bedrock_client = BedrockClient()
        result = bedrock_client.generate_elastic_query(
            user_prompt=prompt,
            schema_context=schema,
            index_name=schema['index_name']
        )
        
        if 'error' in result and not result.get('success'):
            return Response(result, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        return Response({
            'success': True,
            'query': result.get('query'),
            'raw_response': result.get('raw_response')
        })


class ExecuteQueryView(APIView):
    """Execute an Elasticsearch query and return results."""
    
    def post(self, request):
        data = request.data
        
        index_id = data.get('index_id')
        query = data.get('query')
        size = data.get('size')
        
        # Extract timeframe metadata if present (for dashboard widgets)
        timeframe_key = data.get('timeframe_key')
        timeframe_timezone = data.get('timezone', 'Asia/Kolkata')
        timeframe_date_field = data.get('date_field', 'completed_date')
        timeframe_date_mode = data.get('date_mode', 'epoch_seconds')
        
        if not index_id or not query:
            return Response(
                {'error': 'index_id and query are required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Resolve timeframe if provided (for dynamic date ranges)
        if timeframe_key:
            from dashboarding.services.time_handler import TimeframeResolver
            resolved = TimeframeResolver.resolve(
                timeframe_key=timeframe_key,
                timezone_str=timeframe_timezone,
                date_mode=timeframe_date_mode
            )
            if resolved:
                # Inject the resolved date range into the query
                query = self._inject_timeframe_range(
                    query,
                    resolved,
                    timeframe_date_field,
                    timeframe_date_mode
                )

        # If a size is explicitly provided, force it onto the query even if the query already has "size"
        if size is not None:
            try:
                query['size'] = int(size)
            except Exception:
                pass
        
        client = OpenSearchClient()
        result = client.execute_query(index_id, query)
        
        if 'error' in result and not result.get('success'):
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        
        return Response(result)
    
    def _inject_timeframe_range(
        self,
        query: dict,
        resolved_timeframe,
        date_field: str,
        date_mode: str
    ) -> dict:
        """
        Inject resolved timeframe range into query.
        Handles both top-level query filters and nested aggregation filters.
        """
        # Ensure query has a bool structure
        if 'query' not in query:
            query['query'] = {'match_all': {}}
        
        query_body = query['query']
        
        # Ensure bool query structure
        if 'bool' not in query_body:
            if 'match_all' in query_body:
                query_body = {'bool': {'filter': []}}
            else:
                query_body = {'bool': {'must': [query_body], 'filter': []}}
            query['query'] = query_body
        
        bool_query = query_body['bool']
        if 'filter' not in bool_query:
            bool_query['filter'] = []
        
        # Build range filter
        # Handle both date math expressions (strings) and epoch timestamps (numbers)
        range_clause = {}
        if isinstance(resolved_timeframe.gte, str):
            # Date math expression (e.g., "now-3M/M")
            range_clause['gte'] = resolved_timeframe.gte
        else:
            # Epoch timestamp (number)
            range_clause['gte'] = resolved_timeframe.gte
        
        if isinstance(resolved_timeframe.lt, str):
            # Date math expression
            range_clause['lt'] = resolved_timeframe.lt
        else:
            # Epoch timestamp
            range_clause['lt'] = resolved_timeframe.lt
        
        range_filter = {
            'range': {
                date_field: range_clause
            }
        }
        
        # Add to filter array (avoid duplicates)
        filters = bool_query['filter']
        if not any(
            isinstance(f, dict) and f.get('range', {}).get(date_field) == range_filter['range'][date_field]
            for f in filters
        ):
            filters.append(range_filter)
        
        return query


class EnrichedSchemaView(APIView):
    """Get enriched schema with sample data for an index."""
    
    def get(self, request, index_id: str):
        client = OpenSearchClient()
        include_samples = request.query_params.get('samples', 'true').lower() == 'true'
        schema = client.get_enriched_schema(index_id, include_samples=include_samples)
        
        if 'error' in schema:
            return Response(schema, status=status.HTTP_400_BAD_REQUEST)
        
        return Response(schema)


class IndexExportView(APIView):
    """Stream an entire index as CSV."""
    
    def get(self, request, index_id: str):
        export_format = request.query_params.get('format', 'csv').lower()
        if export_format != 'csv':
            return Response(
                {'error': 'Only CSV export is supported currently.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        client = OpenSearchClient()
        schema = client.get_enriched_schema(index_id, include_samples=False)
        if 'error' in schema:
            return Response(schema, status=status.HTTP_400_BAD_REQUEST)
        
        fields = [field.get('name') for field in schema.get('fields', []) if field.get('name')]
        if not fields:
            return Response(
                {'error': 'No fields available for export.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        def row_generator():
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow(fields)
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)
            
            try:
                for doc in client.stream_documents(index_id):
                    row = [
                        IndexExportView._format_export_value(doc.get(field))
                        for field in fields
                    ]
                    writer.writerow(row)
                    yield buffer.getvalue()
                    buffer.seek(0)
                    buffer.truncate(0)
            except Exception as exc:
                logger.error(f"Error streaming index {index_id}: {exc}")
                raise
        
        response = StreamingHttpResponse(
            row_generator(),
            content_type='text/csv'
        )
        response['Content-Disposition'] = f'attachment; filename="{index_id}_export.csv"'
        return response
    
    @staticmethod
    def _format_export_value(value):
        if value is None:
            return ''
        if isinstance(value, (list, tuple)):
            return ', '.join(str(v) for v in value)
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False)
        return str(value)


class QueryExportView(APIView):
    """Stream query results as CSV (exports ALL matching rows via scroll)."""

    def post(self, request):
        data = request.data or {}
        index_id = data.get('index_id')
        query = data.get('query')
        fields = data.get('fields')  # optional ordered list of columns
        filename = data.get('filename')  # optional

        if not index_id or not query:
            return Response(
                {'error': 'index_id and query are required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if fields is not None and not isinstance(fields, list):
            return Response(
                {'error': 'fields must be a list of field names'},
                status=status.HTTP_400_BAD_REQUEST
            )

        client = OpenSearchClient()

        # Determine columns: request.fields > query._source > schema fields
        export_fields = list(fields) if fields else None
        if not export_fields:
            src = query.get('_source')
            if isinstance(src, list) and src:
                export_fields = list(src)
        if not export_fields:
            schema = client.get_enriched_schema(index_id, include_samples=False)
            if 'error' in schema:
                return Response(schema, status=status.HTTP_400_BAD_REQUEST)
            export_fields = [f.get('name') for f in schema.get('fields', []) if f.get('name')]

        if not export_fields:
            return Response(
                {'error': 'No fields available for export.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Sanitize filename
        safe_name = filename or f"{index_id}_query_export.csv"
        safe_name = ''.join(ch if ch.isalnum() or ch in ['-', '_', '.', ' '] else '_' for ch in safe_name)
        if not safe_name.lower().endswith('.csv'):
            safe_name += '.csv'

        try:
            export_query = copy.deepcopy(query)
        except Exception:
            export_query = dict(query)

        if not isinstance(export_query, dict):
            return Response(
                {'error': 'query must be a JSON object'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Ensure there is a top-level "query" key
        if 'query' not in export_query:
            export_query = {'query': export_query}

        # Remove pagination controls that break scroll searches
        export_query.pop('size', None)
        export_query.pop('from', None)

        # Aggregations aren't needed for export and can slow down scroll searches
        export_query.pop('aggs', None)

        # Collapse is incompatible with scroll API
        export_query.pop('collapse', None)

        # Force a scroll-friendly sort
        export_query['sort'] = [{"_doc": "asc"}]

        # Limit fields sent back to reduce payload size
        export_query['_source'] = export_fields

        def row_generator():
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow(export_fields)
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)

            try:
                for doc in client.stream_documents(index_id, query=export_query):
                    row = [
                        IndexExportView._format_export_value(doc.get(field))
                        for field in export_fields
                    ]
                    writer.writerow(row)
                    yield buffer.getvalue()
                    buffer.seek(0)
                    buffer.truncate(0)
            except Exception as exc:
                logger.error(f"Error streaming query export for {index_id}: {exc}")
                raise

        response = StreamingHttpResponse(
            row_generator(),
            content_type='text/csv'
        )
        response['Content-Disposition'] = f'attachment; filename="{safe_name}"'
        return response


class AllSchemasView(APIView):
    """Get enriched schemas for all configured indexes."""
    
    def get(self, request):
        client = OpenSearchClient()
        schemas = client.get_all_enriched_schemas()
        return Response({
            'schemas': schemas,
            'count': len(schemas)
        })


class ReloadSchemasView(APIView):
    """Reload all schemas - useful when data structure changes."""
    
    def post(self, request):
        result = reload_schemas()
        return Response(result)

