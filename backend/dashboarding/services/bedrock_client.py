"""AWS Bedrock client for Claude integration."""

import boto3
import json
import logging
from django.conf import settings
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class BedrockClient:
    """Client for interacting with AWS Bedrock and Claude."""
    
    _instance: Optional['BedrockClient'] = None
    _client = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._client is None:
            self._initialize_client()
    
    def _initialize_client(self):
        """Initialize the Bedrock client with AWS credentials."""
        try:
            self._client = boto3.client(
                'bedrock-runtime',
                region_name=settings.AWS_REGION,
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            )
        except Exception as e:
            logger.error(f"Failed to initialize Bedrock client: {str(e)}")
            self._client = None
    
    @property
    def client(self):
        return self._client
    
    def generate_elastic_query(
        self,
        user_prompt: str,
        schema_context: Dict[str, Any],
        index_name: str
    ) -> Dict[str, Any]:
        """
        Generate an Elasticsearch query using Claude.
        
        Args:
            user_prompt: The natural language description of the desired query
            schema_context: The index schema with field information
            index_name: The name of the target index
            
        Returns:
            Generated Elasticsearch query as a dictionary
        """
        if not settings.USE_LLM:
            return self._generate_fallback_query(schema_context)
        
        if not self._client:
            return {'error': 'Bedrock client not initialized'}
        
        system_prompt = self._build_system_prompt(schema_context, index_name)
        
        try:
            messages = [
                {
                    "role": "user",
                    "content": user_prompt
                }
            ]
            
            request_body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 4096,
                "system": system_prompt,
                "messages": messages,
                "temperature": 0.1,  # Low temperature for consistent query generation
            }
            
            response = self._client.invoke_model(
                modelId=settings.BEDROCK_MODEL_ID,
                body=json.dumps(request_body),
                contentType="application/json",
                accept="application/json"
            )
            
            response_body = json.loads(response['body'].read())
            assistant_message = response_body.get('content', [{}])[0].get('text', '')
            
            # Extract JSON from the response
            query = self._extract_json_from_response(assistant_message)
            
            return {
                'success': True,
                'query': query,
                'raw_response': assistant_message
            }
            
        except Exception as e:
            logger.error(f"Error generating query with Claude: {str(e)}")
            return {'error': str(e), 'success': False}
    
    def _build_system_prompt(self, schema_context: Dict[str, Any], index_name: str) -> str:
        """Build the system prompt with schema context."""
        fields_description = self._format_fields_for_prompt(schema_context.get('fields', []))
        
        return f"""You are an expert Elasticsearch query generator. Your task is to generate valid Elasticsearch queries based on natural language requests.

INDEX INFORMATION:
- Index Name: {index_name}
- Available Fields:
{fields_description}

RULES:
1. Only use fields that exist in the schema above
2. For text fields that need exact matching, use the .keyword subfield
3. Use appropriate query types:
   - term/terms for exact matches on keyword fields
   - match/match_phrase for text search
   - range for numeric/date comparisons
   - bool with must/should/filter for combining conditions
4. Always include _source with the requested fields
5. Include appropriate sort if mentioned
6. Use "gte"/"lte" for date ranges, calculate relative dates from today
7. Return ONLY valid JSON - no explanations, no markdown code blocks

OUTPUT FORMAT:
Return a valid Elasticsearch query JSON object with these possible keys:
- _source: array of field names to return
- query: the query object
- sort: array of sort specifications (optional)
- size: number of results (default 100 if not specified)

Example output format:
{{"_source": ["field1", "field2"], "query": {{"bool": {{"must": [...]}}}}, "sort": [{{"field1": "desc"}}], "size": 100}}
"""
    
    def _format_fields_for_prompt(self, fields: list) -> str:
        """Format field information for the prompt."""
        lines = []
        for field in fields:
            field_type = field.get('type', 'unknown')
            keyword_info = " (has .keyword subfield)" if field.get('has_keyword') else ""
            lines.append(f"  - {field['name']}: {field_type}{keyword_info}")
        return '\n'.join(lines)
    
    def _extract_json_from_response(self, response: str) -> Dict[str, Any]:
        """Extract JSON from Claude's response, handling various formats."""
        # Try direct JSON parse first
        try:
            return json.loads(response.strip())
        except json.JSONDecodeError:
            pass
        
        # Try to find JSON in code blocks
        import re
        json_patterns = [
            r'```json\s*([\s\S]*?)\s*```',
            r'```\s*([\s\S]*?)\s*```',
            r'\{[\s\S]*\}'
        ]
        
        for pattern in json_patterns:
            matches = re.findall(pattern, response)
            for match in matches:
                try:
                    return json.loads(match.strip())
                except json.JSONDecodeError:
                    continue
        
        # Return empty query if parsing fails
        logger.warning(f"Failed to extract JSON from response: {response[:200]}")
        return {"query": {"match_all": {}}}
    
    def _generate_fallback_query(self, schema_context: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a simple fallback query when LLM is disabled."""
        fields = schema_context.get('fields', [])
        source_fields = [f['name'] for f in fields[:10]]  # Limit to first 10 fields
        
        return {
            'success': True,
            'query': {
                '_source': source_fields,
                'query': {'match_all': {}},
                'size': 100
            },
            'raw_response': 'LLM disabled - using fallback query'
        }

