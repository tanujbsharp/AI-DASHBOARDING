"""OpenSearch client for schema discovery and query execution."""

from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth
from django.conf import settings
from typing import Dict, List, Any, Optional
import logging

logger = logging.getLogger(__name__)


class OpenSearchClient:
    """Client for interacting with OpenSearch cluster."""
    
    _instance: Optional['OpenSearchClient'] = None
    _client: Optional[OpenSearch] = None
    _schema_cache: Dict[str, Dict] = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._client is None:
            self._initialize_client()
    
    def _initialize_client(self):
        """Initialize the OpenSearch client with credentials from settings."""
        endpoint = settings.OPENSEARCH_DOMAIN_ENDPOINT
        if not endpoint:
            logger.warning("OpenSearch endpoint not configured")
            return
            
        # Remove https:// prefix if present for host
        host = endpoint.replace('https://', '').replace('http://', '').rstrip('/')
        
        self._client = OpenSearch(
            hosts=[{'host': host, 'port': 443}],
            http_auth=(settings.OPENSEARCH_MASTER_USER, settings.OPENSEARCH_MASTER_PASS),
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
            timeout=30,
        )
    
    @property
    def client(self) -> Optional[OpenSearch]:
        return self._client
    
    def get_available_indexes(self) -> List[Dict[str, str]]:
        """Get list of configured indexes with their display names."""
        return [
            {
                'id': key,
                'name': value,
                'display_name': self._format_display_name(key)
            }
            for key, value in settings.OPENSEARCH_INDEXES.items()
        ]
    
    def _format_display_name(self, key: str) -> str:
        """Convert snake_case key to Title Case display name."""
        return ' '.join(word.capitalize() for word in key.split('_'))
    
    def get_index_name(self, index_id: str) -> Optional[str]:
        """Get the actual index name from the index ID."""
        # First try direct lookup
        index_name = settings.OPENSEARCH_INDEXES.get(index_id)
        if index_name:
            return index_name
        
        # If not found, check if index_id is already an actual index name
        # (for backwards compatibility or if someone passes the actual name)
        for key, value in settings.OPENSEARCH_INDEXES.items():
            if value == index_id:
                # They passed the actual index name, return it
                return index_id
        
        # Not found
        logger.warning(f"Index ID '{index_id}' not found in configuration. Available: {list(settings.OPENSEARCH_INDEXES.keys())}")
        return None
    
    def get_index_mapping(self, index_id: str) -> Dict[str, Any]:
        """
        Fetch the mapping/schema for a given index.
        Returns field names, types, and metadata.
        """
        if not self._client:
            return {'error': 'OpenSearch client not initialized'}
        
        index_name = self.get_index_name(index_id)
        if not index_name:
            return {'error': f'Index {index_id} not found in configuration'}
        
        try:
            mapping_response = self._client.indices.get_mapping(index=index_name)
            
            if index_name not in mapping_response:
                return {'error': f'Index {index_name} not found in OpenSearch'}
            
            properties = mapping_response[index_name].get('mappings', {}).get('properties', {})
            
            fields = self._parse_mapping_properties(properties)
            
            return {
                'index_id': index_id,
                'index_name': index_name,
                'fields': fields,
                'field_count': len(fields)
            }
            
        except Exception as e:
            logger.error(f"Error fetching mapping for {index_name}: {str(e)}")
            return {'error': str(e)}
    
    def _parse_mapping_properties(self, properties: Dict, prefix: str = '') -> List[Dict[str, Any]]:
        """Parse OpenSearch mapping properties into a flat list of fields."""
        fields = []
        
        for field_name, field_info in properties.items():
            full_name = f"{prefix}{field_name}" if prefix else field_name
            field_type = field_info.get('type', 'object')
            
            field_data = {
                'name': full_name,
                'type': field_type,
                'searchable': field_type in ['text', 'keyword', 'search_as_you_type'],
                'sortable': field_type in ['keyword', 'date', 'integer', 'long', 'float', 'double', 'boolean'],
                'filterable': field_type != 'text',
            }
            
            # Check for keyword subfield (common for text fields)
            if 'fields' in field_info and 'keyword' in field_info.get('fields', {}):
                field_data['has_keyword'] = True
                field_data['keyword_field'] = f"{full_name}.keyword"
            
            fields.append(field_data)
            
            # Handle nested properties
            if 'properties' in field_info:
                nested_fields = self._parse_mapping_properties(
                    field_info['properties'],
                    prefix=f"{full_name}."
                )
                fields.extend(nested_fields)
        
        return fields
    
    def execute_query(self, index_id: str, query: Dict[str, Any], size: int = 100) -> Dict[str, Any]:
        """
        Execute an Elasticsearch query on the specified index(es).
        
        Args:
            index_id: Single index_id string OR comma-separated string of index_ids OR list of index_ids
            query: OpenSearch query dictionary
            size: Default size if not in query
            
        Returns:
            Query results dictionary
        """
        if not self._client:
            return {'error': 'OpenSearch client not initialized'}
        
        # Handle multiple indices - support string (single or comma-separated) or list
        if isinstance(index_id, list):
            index_ids = index_id
        elif ',' in index_id:
            index_ids = [idx.strip() for idx in index_id.split(',')]
        else:
            index_ids = [index_id]
        
        # Convert all index_ids to actual index names
        index_names = []
        for idx_id in index_ids:
            index_name = self.get_index_name(idx_id)
            if not index_name:
                available = list(settings.OPENSEARCH_INDEXES.keys())
                logger.error(f"Index {idx_id} not found in configuration. Available indexes: {available}")
                return {'error': f'Index {idx_id} not found in configuration. Available: {available}'}
            index_names.append(index_name)
        
        # Ensure query doesn't have any index references that could confuse OpenSearch
        # Remove any index fields from query body if present
        query_clean = dict(query)
        if 'index' in query_clean:
            logger.warning(f"Removing 'index' field from query body (should use index parameter, not body)")
            query_clean.pop('index')
        
        try:
            # Add size if not in query
            if 'size' not in query_clean:
                query_clean['size'] = size
            
            # OpenSearch supports multiple indices - pass as comma-separated string or list
            index_param = ','.join(index_names) if len(index_names) > 1 else index_names[0]
            logger.debug(f"Executing query on index(es): {index_param} (index_id(s): {index_id})")
            response = self._client.search(index=index_param, body=query_clean)
            
            hits = response.get('hits', {})
            total = hits.get('total', {})
            total_count = total.get('value', 0) if isinstance(total, dict) else total
            
            results = []
            for hit in hits.get('hits', []):
                result = hit.get('_source', {})
                result['_id'] = hit.get('_id')
                result['_score'] = hit.get('_score')
                results.append(result)
            
            # Build response
            result = {
                'success': True,
                'total': total_count,
                'count': len(results),
                'results': results,
                'took': response.get('took', 0)
            }
            
            # Include aggregations if present
            aggregations = response.get('aggregations', {})
            if aggregations:
                result['aggregations'] = aggregations
            
            return result
            
        except Exception as e:
            logger.error(f"Error executing query on {index_name}: {str(e)}")
            return {'error': str(e), 'success': False}

    def stream_documents(self, index_id: str, query: Optional[Dict[str, Any]] = None, batch_size: int = 500):
        """
        Stream all documents from an index using the scroll API.
        Yields _source dicts.
        """
        if not self._client:
            raise RuntimeError('OpenSearch client not initialized')
        
        index_name = self.get_index_name(index_id)
        if not index_name:
            raise ValueError(f'Index {index_id} not found in configuration')
        
        search_body = query or {"query": {"match_all": {}}}
        scroll = "2m"
        
        response = self._client.search(
            index=index_name,
            body=search_body,
            scroll=scroll,
            size=batch_size,
            _source=True
        )
        
        scroll_id = response.get('_scroll_id')
        
        try:
            while True:
                hits = response.get('hits', {}).get('hits', [])
                if not hits:
                    break
                
                for hit in hits:
                    yield hit.get('_source', {})
                
                if not scroll_id:
                    break
                
                response = self._client.scroll(scroll_id=scroll_id, scroll=scroll)
                scroll_id = response.get('_scroll_id')
        finally:
            if scroll_id:
                try:
                    self._client.clear_scroll(scroll_id=scroll_id)
                except Exception as e:
                    logger.debug(f"Failed to clear scroll: {e}")
    
    def get_sample_values(self, index_id: str, field_name: str, size: int = 20) -> List[str]:
        """Get sample unique values for a field (useful for filter suggestions)."""
        if not self._client:
            return []
        
        index_name = self.get_index_name(index_id)
        if not index_name:
            return []
        
        try:
            # Use keyword field if available
            agg_field = f"{field_name}.keyword" if not field_name.endswith('.keyword') else field_name
            
            query = {
                "size": 0,
                "aggs": {
                    "unique_values": {
                        "terms": {
                            "field": agg_field,
                            "size": size
                        }
                    }
                }
            }
            
            response = self._client.search(index=index_name, body=query)
            buckets = response.get('aggregations', {}).get('unique_values', {}).get('buckets', [])
            
            return [bucket['key'] for bucket in buckets]
            
        except Exception as e:
            logger.error(f"Error fetching sample values for {field_name}: {str(e)}")
            return []
    
    def get_sample_documents(self, index_id: str, size: int = 3) -> List[Dict[str, Any]]:
        """Get sample documents from an index to understand data structure."""
        if not self._client:
            return []
        
        index_name = self.get_index_name(index_id)
        if not index_name:
            return []
        
        try:
            query = {
                "size": size,
                "query": {"match_all": {}},
                "sort": [{"_doc": "desc"}]  # Get recent documents
            }
            
            response = self._client.search(index=index_name, body=query)
            hits = response.get('hits', {}).get('hits', [])
            
            return [hit.get('_source', {}) for hit in hits]
            
        except Exception as e:
            logger.error(f"Error fetching sample documents for {index_name}: {str(e)}")
            return []
    
    def get_enriched_schema(self, index_id: str, include_samples: bool = True) -> Dict[str, Any]:
        """
        Get comprehensive schema information including:
        - Field mappings with types
        - Sample values for key fields
        - Sample documents to understand data structure
        - Document count
        """
        # Check cache first
        cache_key = f"{index_id}_{include_samples}"
        if cache_key in self._schema_cache:
            return self._schema_cache[cache_key]
        
        if not self._client:
            return {'error': 'OpenSearch client not initialized'}
        
        index_name = self.get_index_name(index_id)
        if not index_name:
            return {'error': f'Index {index_id} not found in configuration'}
        
        try:
            # Get basic mapping
            mapping = self.get_index_mapping(index_id)
            if 'error' in mapping:
                return mapping
            
            # Get document count
            count_response = self._client.count(index=index_name)
            doc_count = count_response.get('count', 0)
            
            # Enhance fields with sample values for filterable fields
            fields = mapping.get('fields', [])
            if include_samples:
                for field in fields:
                    if field.get('filterable') and field.get('type') in ['keyword', 'text']:
                        # Get sample values for categorical fields
                        samples = self.get_sample_values(index_id, field['name'], 10)
                        if samples:
                            field['sample_values'] = samples
                    elif field.get('type') in ['date', 'long', 'integer', 'float', 'double']:
                        # Get min/max for numeric/date fields
                        stats = self._get_field_stats(index_name, field['name'], field.get('type'))
                        if stats:
                            field['stats'] = stats
            
            # Get sample documents
            sample_docs = self.get_sample_documents(index_id, 3) if include_samples else []
            
            enriched_schema = {
                'index_id': index_id,
                'index_name': index_name,
                'document_count': doc_count,
                'fields': fields,
                'field_count': len(fields),
                'sample_documents': sample_docs
            }
            
            # Cache the result
            self._schema_cache[cache_key] = enriched_schema
            
            return enriched_schema
            
        except Exception as e:
            logger.error(f"Error fetching enriched schema for {index_name}: {str(e)}")
            return {'error': str(e)}
    
    def _get_field_stats(self, index_name: str, field_name: str, field_type: str) -> Optional[Dict]:
        """Get statistics for numeric or date fields."""
        try:
            agg_type = "stats" if field_type in ['long', 'integer', 'float', 'double'] else "stats"
            query = {
                "size": 0,
                "aggs": {
                    "field_stats": {
                        agg_type: {"field": field_name}
                    }
                }
            }
            
            response = self._client.search(index=index_name, body=query)
            stats = response.get('aggregations', {}).get('field_stats', {})
            
            if stats.get('count', 0) > 0:
                return {
                    'min': stats.get('min'),
                    'max': stats.get('max'),
                    'avg': stats.get('avg'),
                    'count': stats.get('count')
                }
            return None
            
        except Exception as e:
            logger.debug(f"Could not get stats for {field_name}: {str(e)}")
            return None
    
    def get_all_enriched_schemas(self) -> Dict[str, Dict[str, Any]]:
        """Get enriched schemas for all configured indexes."""
        schemas = {}
        for index_id in settings.OPENSEARCH_INDEXES.keys():
            schema = self.get_enriched_schema(index_id, include_samples=True)
            if 'error' not in schema:
                schemas[index_id] = schema
        return schemas

