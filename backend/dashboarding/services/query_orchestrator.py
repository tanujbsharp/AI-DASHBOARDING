"""
Query Orchestrator for AI Reports Bot.
Central service that coordinates the two-phase LLM pipeline:
1. Query Generation (LLM creates query structure)
2. Response Generation (LLM creates message from actual data)

Aligned with usecase.md specification for query building and response handling.
"""

import copy
import json
import logging
import re
from typing import Dict, Any, List, Optional, Tuple, Set
from datetime import datetime

import boto3
from django.conf import settings

from .opensearch_client import OpenSearchClient
from .time_handler import TimeHandler, TimePeriod
from .response_types import ResponseType, ResponseTypeDetector
from .response_validator import ResponseValidator
from .conversation_context import (
    ConversationContext, 
    ConversationContextManager,
        QueryRecord,
        get_context_manager,
        FilterType,
        InferredFilter
    )
from .prompts import QueryPromptBuilder, ResponsePromptBuilder, SchemaContextBuilder

logger = logging.getLogger(__name__)


class QueryOrchestrator:
    """
    Orchestrates the complete query-response pipeline.
    
    Pipeline:
    1. Parse user message (time, entities, response type)
    2. Generate OpenSearch query via LLM
    3. Execute query and get real data
    4. Generate response using only real data
    5. Validate response for accuracy
    """
    
    DATE_FIELDS = {
        'created_on',
        'module_created_on',
        'module_updated_on',
        'published_date',
        'completed_date',
        'invited_date',
        'hired_on',
        'user_created_on'
    }

    MODULE_KEYWORDS = ConversationContextManager.MODULE_KEYWORDS
    MODULE_NON_PUBLISHED_KEYWORDS = ConversationContextManager.NON_PUBLISHED_MODULE_KEYWORDS

    # Fields that are natively keyword type and should NOT have .keyword appended
    NATIVE_KEYWORD_FIELDS = {
        'module_name', 'skill_name', 'product_name', 'city', 'country', 'state',
        'email_addr', 'first_name', 'last_name', 'designation', 'user_role',
        'trainer_email_addr', 'coach_email_addr', 'manager_email_addr',
        'module_type_name', 'module_desc', 'tags', 'staff', 'is_admin',
        'created_by', 'module_created_by', 'prod_master_name'
    }
    
    RATING_KEYWORDS = [
        'rating',
        'ratings',
        'avg rating',
        'average rating',
        'rated',
        'rate ',
        'rate?'
    ]
    RATING_FIELD = 'ratings'
    RATING_MIN_VALUE = 0  # Ratings > 0 implies actual rating event
    
    # Fields we want to retain when summarizing threshold queries into user rows
    COUNT_THRESHOLD_USER_FIELDS = [
        'uid',
        'email_addr',
        'employee_id',
        'first_name',
        'last_name',
        'designation',
        'department',
        'city',
        'country',
        'region',
        'manager_email_addr',
        'coach_email_addr',
        'trainer_email_addr'
    ]
    
    COUNT_THRESHOLD_DISPLAY_ORDER = [
        'uid',
        'email_addr',
        'first_name',
        'last_name',
        'modules_completed',
        'designation',
        'department',
        'city',
        'country',
        'manager_email_addr',
        'coach_email_addr',
        'trainer_email_addr'
    ]
    
    def __init__(self):
        self.os_client = OpenSearchClient()
        self.use_conversation_context = getattr(settings, 'USE_CONVERSATION_CONTEXT', False)
        self.context_manager = get_context_manager() if self.use_conversation_context else None
        self.schema_cache: Dict[str, Dict] = {}
        self._load_schemas()
    
    def _load_schemas(self):
        """Load schemas for all configured indexes."""
        logger.info("Loading schemas for query orchestrator...")
        for index_id in settings.OPENSEARCH_INDEXES.keys():
            try:
                schema = self.os_client.get_enriched_schema(index_id, include_samples=True)
                if 'error' not in schema:
                    self.schema_cache[index_id] = schema
                    logger.info(f"Loaded schema for {index_id}")
            except Exception as e:
                logger.error(f"Failed to load schema for {index_id}: {e}")
    
    def process(
        self,
        user_message: str,
        conversation_history: List[Dict[str, str]] = None,
        session_id: str = "default"
    ) -> Dict[str, Any]:
        """
        Process a user query through the complete pipeline.
        
        Args:
            user_message: Natural language query from user
            conversation_history: Previous messages for context
            session_id: Session identifier for context management
            
        Returns:
            Complete response with query results and formatted message
        """
        query_payload = None  # Initialize to capture query in case of errors
        try:
            # Step 1: Get/create conversation context (optional)
            if self.use_conversation_context:
                context = self.context_manager.get_or_create(session_id)
                context.add_message("user", user_message)
            else:
                context = ConversationContext()
            
            # Step 2: Extract basic intent using helper classes
            time_period = TimeHandler.parse(user_message)
            response_type = ResponseTypeDetector.detect(user_message)
            
            # Detect time field hint if available
            time_field_override = TimeHandler.detect_time_field_hint(user_message)
            if not time_field_override:
                time_field_override = 'created_on'

            # Extract timeframe key for dashboard widgets (if relative timeframe detected)
            from .time_handler import TimeframeResolver
            timeframe_key = TimeframeResolver.extract_timeframe_key(user_message)

            if self.use_conversation_context:
                self.context_manager.update_from_message(
                    context, 
                    user_message, 
                    {},  # No entities - Bedrock will handle everything
                    time_period.__dict__ if time_period else None,
                    time_field=time_field_override
                )
            
            logger.info(f"Detected: response_type={response_type.value}, time={time_period.description if time_period else 'lifetime'}")
            effective_time_period = self._resolve_time_period(time_period, context)
            
            # Step 3: Get schema for primary index
            index_id = "module_consumption_data"  # Primary index
            schema = self.schema_cache.get(index_id, {})
            
            if not schema:
                return self._error_response("Schema not available. Please try again later.")
            
            # Step 3a: Check for DATA_DICTIONARY request (Section B.7)
            if response_type == ResponseType.DATA_DICTIONARY:
                return self._handle_data_dictionary_request(schema, context)
            
            # Step 4: Generate query via LLM (Phase 1)
            # Let Bedrock handle all query generation based on the comprehensive prompt rules
            query_result = self._generate_query(
                user_message=user_message,
                schema=schema,
                response_type=response_type,
                time_period=effective_time_period,
                context=context,
                time_field=time_field_override
            )
            
            if 'error' in query_result:
                # Include the query if it was generated before error
                error_query = query_result.get('query') if isinstance(query_result, dict) else None
                return self._error_response(query_result['error'], query=error_query)
            
            query_payload = query_result
            
            # Resolve selected index early so we can pick the correct time field + sanitization behavior
            selected_index_id = query_payload.get('index_id', index_id)
            
            # Reject multi-index queries - only single index allowed
            if isinstance(selected_index_id, list):
                logger.error(f"Multi-index queries are not allowed. Received: {selected_index_id}")
                selected_index_id = selected_index_id[0] if selected_index_id else index_id
                query_payload['index_id'] = selected_index_id
                logger.warning(f"Using single index: {selected_index_id}")
            elif isinstance(selected_index_id, str) and ',' in selected_index_id:
                logger.error(f"Multi-index queries are not allowed. Received comma-separated: {selected_index_id}")
                first_index = selected_index_id.split(',')[0].strip()
                query_payload['index_id'] = first_index
                selected_index_id = first_index
                logger.warning(f"Using single index: {selected_index_id}")
            
            # Adjust default time field based on index needs (e.g., monthly activity → completed_on)
            time_field_override = self._resolve_time_field_for_index(time_field_override, selected_index_id, user_message)
            
            rating_query = self._is_rating_query(user_message, query_payload)
            sanitize_time_field = 'completed_date' if rating_query else time_field_override
            
            # Clean up unsupported constructs FIRST, before any other processing
            # This prevents errors from propagating through the pipeline
            # Sanitize BEFORE applying structured filters.
            # Pass time info so date_histogram extended_bounds can always match the requested time window
            # even if the generated query's time range is missing or hard to extract.
            self._sanitize_query_payload(query_payload, time_period=effective_time_period, time_field=sanitize_time_field)
            
            # CRITICAL: Validate and remove fields that don't exist in the selected index
            # This prevents queries from using fields from other indices (e.g., module_status in catalog index)
            # selected_index_id already resolved above (and query_payload normalized to a single index)
            
            # FIRST: Aggressively remove known invalid fields based on index type (safety net)
            self._remove_known_invalid_fields(query_payload, selected_index_id)
            
            # THEN: Validate against schema (more comprehensive)
            self._validate_and_remove_invalid_fields(query_payload, selected_index_id)
            
            # Replace hardcoded epoch timestamps with date math for relative timeframes
            # This ensures queries use dynamic date math (e.g., "now-3M/M") instead of fixed timestamps
            if timeframe_key:
                self._replace_epoch_with_date_math(query_payload, timeframe_key, effective_time_period)
            
            # Deduplicate filters to prevent duplicate clauses
            self._deduplicate_filters(query_payload)
            
            # CRITICAL: Detect and fix "haven't completed in time range" queries
            # Bedrock often generates simple filters instead of the required aggregation pattern
            # This MUST happen BEFORE applying structured filters to prevent time filters at top level
            is_havent_completed_query = self._is_havent_completed_in_time_query(user_message, effective_time_period)
            if is_havent_completed_query:
                logger.info("Detected 'haven't completed in time range' pattern - fixing query with aggregation pattern")
                self._fix_havent_completed_in_time_query(query_payload, effective_time_period, user_message)
            
            # Check if user is asking for completion rate of a specific module or user
            # This MUST happen before other processing to force the exact query structure
            is_module_completion_rate_request = self._is_module_completion_rate_request(user_message)
            is_user_completion_rate_request = self._is_user_completion_rate_request(user_message)
            
            if is_module_completion_rate_request:
                logger.info("Detected module completion rate request from user message - forcing exact query structure")
                self._force_module_completion_rate_query_structure(query_payload, user_message)
                # Mark as detected so we skip other processing
                is_module_completion_rate_kpi = True
                is_user_completion_rate_kpi = False
                is_single_completion_rate_kpi = True
            elif is_user_completion_rate_request:
                logger.info("Detected user completion rate request from user message - forcing exact query structure")
                self._force_user_completion_rate_query_structure(query_payload, user_message, index_id, effective_time_period)
                # Mark as detected so we skip other processing
                is_module_completion_rate_kpi = False
                is_user_completion_rate_kpi = True
                is_single_completion_rate_kpi = True
            
            # Ensure completion rate KPIs wrap pipeline agg inside multi-bucket scope
            self._ensure_completion_rate_scope_wrapper(query_payload)
            
            # CRITICAL POST-PROCESSING: Check if LLM generated module_name filter with a person name
            # This catches cases where detection failed but LLM still put person name in module_name
            query_body = query_payload.get('query', {})
            if isinstance(query_body, dict):
                query = query_body.get('query', {})
                if isinstance(query, dict):
                    bool_query = query.get('bool', {})
                    if isinstance(bool_query, dict):
                        filter_clauses = bool_query.get('filter', [])
                        for clause in filter_clauses:
                            if isinstance(clause, dict):
                                # Check for module_name with what looks like a person name
                                if 'match_phrase' in clause and isinstance(clause['match_phrase'], dict):
                                    if 'module_name' in clause['match_phrase']:
                                        module_name_clause = clause['match_phrase']['module_name']
                                        if isinstance(module_name_clause, dict):
                                            potential_name = module_name_clause.get('query', '')
                                            if potential_name:
                                                # Check if it looks like a person name (not a module name)
                                                import re
                                                module_keywords = ['module', 'training', 'course', 'leadership', 'management', 'situational', 'program', 'workshop', 'learning']
                                                potential_lower = potential_name.lower()
                                                
                                                # If it doesn't contain module keywords and looks like a name, it's a user!
                                                if (not any(kw in potential_lower for kw in module_keywords) and 
                                                    len(potential_name) >= 2 and len(potential_name) <= 30 and
                                                    (potential_name.replace(' ', '').replace('-', '').isalpha() or '@' in potential_name)):
                                                    logger.warning(f"CRITICAL: Found person name '{potential_name}' incorrectly in module_name filter - forcing user completion rate query structure")
                                                    # Force user completion rate structure
                                                    self._force_user_completion_rate_query_structure(query_payload, user_message, index_id, effective_time_period)
                                                    is_user_completion_rate_kpi = True
                                                    is_module_completion_rate_kpi = False
                                                    is_single_completion_rate_kpi = True
                                                    break
                                elif 'match' in clause and isinstance(clause['match'], dict):
                                    if 'module_name' in clause['match']:
                                        module_name_clause = clause['match']['module_name']
                                        if isinstance(module_name_clause, dict):
                                            potential_name = module_name_clause.get('query', '')
                                        else:
                                            potential_name = str(module_name_clause)
                                        
                                        if potential_name:
                                            import re
                                            module_keywords = ['module', 'training', 'course', 'leadership', 'management', 'situational', 'program', 'workshop', 'learning']
                                            potential_lower = potential_name.lower()
                                            
                                            if (not any(kw in potential_lower for kw in module_keywords) and 
                                                len(potential_name) >= 2 and len(potential_name) <= 30 and
                                                (potential_name.replace(' ', '').replace('-', '').isalpha() or '@' in potential_name)):
                                                logger.warning(f"CRITICAL: Found person name '{potential_name}' incorrectly in module_name match filter - forcing user completion rate query structure")
                                                # Force user completion rate structure
                                                self._force_user_completion_rate_query_structure(query_payload, user_message, index_id, effective_time_period)
                                                is_user_completion_rate_kpi = True
                                                is_module_completion_rate_kpi = False
                                                is_single_completion_rate_kpi = True
                                                break
            
            is_module_completion_rate_kpi = self._is_single_module_completion_rate_query(query_payload)
            is_user_completion_rate_kpi = self._is_single_user_completion_rate_query(query_payload)
            is_single_completion_rate_kpi = is_module_completion_rate_kpi or is_user_completion_rate_kpi
            if is_single_completion_rate_kpi:
                logger.info(
                    "Detected single-entity completion rate KPI query "
                    f"(module={is_module_completion_rate_kpi}, user={is_user_completion_rate_kpi})"
                )
                    # FORCE exact query structure for module completion rate
                if is_module_completion_rate_kpi:
                        self._force_module_completion_rate_query_structure(query_payload, user_message)
            
            # CRITICAL: Remove completed_status: 1 from top-level query filters
            # assigned_status: 0 should stay in main query (defines denominator)
            # completed_status: 1 should ONLY be in the aggregation filter
            # Otherwise the "assigned" denominator becomes "completed only" and you'll get bogus 100% rates
            # NOTE: This is a backup removal - _force_module_completion_rate_query_structure should have already removed them
            if is_single_completion_rate_kpi:
                query_body = query_payload.get('query', {})
                if isinstance(query_body, dict):
                    query = query_body.get('query', {})
                    if isinstance(query, dict):
                        bool_query = query.get('bool', {})
                        if isinstance(bool_query, dict):
                            # Remove from filter list
                            filter_list = bool_query.get('filter', [])
                            if isinstance(filter_list, list):
                                cleaned = [
                                    f for f in filter_list
                                    if not (
                                        (isinstance(f, dict) and 'term' in f and isinstance(f['term'], dict) and 'completed_status' in f['term']) or
                                        (isinstance(f, dict) and 'range' in f and isinstance(f['range'], dict) and 'ratings' in f['range']) or
                                        (isinstance(f, dict) and 'term' in f and isinstance(f['term'], dict) and 'ratings' in f['term'])
                                    )
                                ]
                                if len(cleaned) < len(filter_list):
                                    bool_query['filter'] = cleaned
                                    logger.info(f"Removed {len(filter_list) - len(cleaned)} invalid filters (completed_status/ratings) from completion rate query")
            
            # CRITICAL: Fix chart queries that don't have aggregations
            # Bedrock sometimes generates queries with size > 0 (hits) instead of size: 0 with aggregations
            if response_type in [ResponseType.BAR_CHART, ResponseType.PIE_CHART, ResponseType.LINE_CHART]:
                query_body = query_payload.get('query', {})
                if isinstance(query_body, dict):
                    has_aggs = 'aggs' in query_body and isinstance(query_body.get('aggs'), dict) and len(query_body.get('aggs', {})) > 0
                    query_size = query_body.get('size', 0)
                    if not has_aggs or query_size > 0:
                        logger.warning(f"Chart query detected ({response_type.value}) but missing aggregations (has_aggs={has_aggs}) or wrong size (size={query_size}) - converting to aggregation query")
                        self._fix_chart_query(query_payload, response_type, user_message, effective_time_period)
                        # Verify the fix worked
                        query_body_after = query_payload.get('query', {})
                        if isinstance(query_body_after, dict):
                            has_aggs_after = 'aggs' in query_body_after and isinstance(query_body_after.get('aggs'), dict) and len(query_body_after.get('aggs', {})) > 0
                            size_after = query_body_after.get('size', 0)
                            logger.info(f"After fix: has_aggs={has_aggs_after}, size={size_after}")
            
            # CRITICAL: Fix "most completed" queries to order by _count instead of unique_users
            self._fix_most_completed_ordering(query_payload, user_message)
            
            # CRITICAL: Remove collapse from any aggregation query (size: 0)
            # Collapse only works on hits, not aggregations. When size: 0, there are no hits, so collapse is useless.
            query_body = query_payload.get('query', {})
            if isinstance(query_body, dict):
                if query_body.get('size') == 0:
                    if 'collapse' in query_body:
                        logger.warning("Removed collapse from aggregation query (collapse only works on hits, not aggregations when size: 0)")
                        query_body.pop('collapse')
                # Also check if it has aggregations - if so, remove collapse regardless of size
                if 'aggs' in query_body and isinstance(query_body.get('aggs'), dict):
                    if 'collapse' in query_body:
                        logger.warning("Removed collapse from query with aggregations (collapse only works on hits, not aggregations)")
                        query_body.pop('collapse')
            
            # Apply structured filters (time, context filters)
            # SKIP time filters for "haven't completed" queries - time range is only in aggregation!
            skip_time_filter = is_havent_completed_query or is_single_completion_rate_kpi
            self._apply_structured_filters(
                query_payload=query_payload,
                time_period=effective_time_period if not skip_time_filter else None,  # Skip certain queries
                context=context,
                time_field=time_field_override,
                entities={},  # No entities - Bedrock handles everything
                user_message=user_message
            )
            
            # CRITICAL: Re-validate after applying structured filters
            # This ensures any fields added by _ensure_module_status_default or other functions
            # that don't exist in the selected index are removed
            self._remove_known_invalid_fields(query_payload, selected_index_id)
            self._validate_and_remove_invalid_fields(query_payload, selected_index_id)
            
            # CRITICAL: After applying structured filters, remove completed_status and ratings
            # from completion rate queries (they may have been added back by structured filters)
            if is_single_completion_rate_kpi:
                query_body = query_payload.get('query', {})
                if isinstance(query_body, dict):
                    query = query_body.get('query', {})
                    if isinstance(query, dict):
                        bool_query = query.get('bool', {})
                        if isinstance(bool_query, dict):
                            # Remove from filter list
                            filter_list = bool_query.get('filter', [])
                            if isinstance(filter_list, list):
                                cleaned = [
                                    f for f in filter_list
                                    if not (
                                        (isinstance(f, dict) and 'term' in f and isinstance(f['term'], dict) and 'completed_status' in f['term']) or
                                        (isinstance(f, dict) and 'range' in f and isinstance(f['range'], dict) and 'ratings' in f['range']) or
                                        (isinstance(f, dict) and 'term' in f and isinstance(f['term'], dict) and 'ratings' in f['term'])
                                    )
                                ]
                                if len(cleaned) < len(filter_list):
                                    bool_query['filter'] = cleaned
                                    logger.info(f"Removed {len(filter_list) - len(cleaned)} invalid filters (completed_status/ratings) from completion rate query after structured filters")

            # For completion rate KPIs, apply time range inside completed_bucket (numerator only)
            if (
                is_single_completion_rate_kpi 
                and effective_time_period 
                and not effective_time_period.is_lifetime
            ):
                self._add_time_range_to_completed_bucket(
                    query_payload=query_payload,
                    time_period=effective_time_period,
                    date_field='completed_date'
                )
            
            # CRITICAL: Final cleanup for "haven't completed" queries - remove any time filters that might have been added
            if is_havent_completed_query:
                query_body = query_payload.get('query', {})
                if isinstance(query_body, dict):
                    self._remove_prohibited_filters_from_top_level(query_body)
            
            # Enforce column order from fields_to_show if specified
            # This ensures the _source fields match the exact order the user specified in the report builder
            self._enforce_column_order(query_payload, user_message)
            
            # Sanitize again after applying filters (in case filters added unsupported constructs)
            # Sanitize again after applying filters (in case filters added unsupported constructs).
            # Pass time info again so histograms keep the full requested window.
            rating_query = self._is_rating_query(user_message, query_payload)
            sanitize_time_field = 'completed_date' if rating_query else time_field_override
            self._sanitize_query_payload(query_payload, time_period=effective_time_period, time_field=sanitize_time_field)
            
            # CRITICAL: Final cleanup for "haven't completed" queries - remove any time filters that might have been added
            # This MUST happen after all filter application to ensure no completed_date filters at top level
            if is_havent_completed_query:
                query_body = query_payload.get('query', {})
                if isinstance(query_body, dict):
                    self._remove_prohibited_filters_from_top_level(query_body)
                    logger.info("Final cleanup: Removed any prohibited filters from top-level for haven't completed query")

            # Step 5: Execute query against OpenSearch
            execution_result = self._execute_query(
                index_id=query_payload.get('index_id', index_id),
                query=query_payload.get('query', {})
            )
            
            if not execution_result.get('success', False):
                # Include the query in error response so UI can display it
                return self._error_response(
                    f"Query execution failed: {execution_result.get('error', 'Unknown error')}",
                    query=query_payload.get('query', {})
                )
            
            # Extract results from "zero completions in range" aggregation queries
            if self._is_havent_completed_in_time_query(user_message, effective_time_period):
                self._extract_zero_completions_in_range_results(execution_result)
            
            # Step 6: Generate response using actual data (Phase 2)
            response = self._generate_response(
                response_type=response_type,
                query_results=execution_result,
                original_query=user_message,
                time_period=time_period,
                query_info=query_payload
            )
            
            # Step 7: Validate response
            validation = ResponseValidator.validate_response(
                response.get('message', ''),
                execution_result
            )
            
            if not validation.is_valid:
                logger.warning(f"Response validation failed: {validation.issues}")
                # Use safe fallback response
                response['message'] = ResponseValidator.create_safe_response(
                    execution_result,
                    response_type.value,
                    time_period.description if time_period else None
                )
                response['validation_warning'] = validation.issues
            
            # Step 8: Record query in context
            primary_field, primary_label = self._infer_primary_entity(
                query_payload.get('query', {})
            )
            primary_count = self._extract_primary_entity_count(
                execution_result.get('aggregations', {})
            )
            filter_query = copy.deepcopy(
                (query_payload.get('query', {}) or {}).get('query')
            )

            if self.use_conversation_context:
                context.add_query_record(QueryRecord(
                    user_message=user_message,
                    query=query_payload.get('query', {}),
                    result_total=execution_result.get('total', 0),
                    aggregations=execution_result.get('aggregations', {}),
                    response_type=response_type.value,
                    primary_entity_field=primary_field,
                    primary_entity_label=primary_label,
                    primary_entity_count=primary_count,
                    filter_query=filter_query
                ))
                context.add_message("assistant", response.get('message', ''))
            
            # Step 9: Build final response
            # Use response_type from query_payload if available (module intent overrides detection)
            final_response_type = query_payload.get('response_type', response_type.value)
            if isinstance(final_response_type, ResponseType):
                final_response_type = final_response_type.value
            # Normalize common aliases or model-provided variants to our known ResponseType values
            final_response_type = self._normalize_response_type(final_response_type, fallback=response_type.value)
            
            # Always include the query in response, even if results are empty (0 records)
            # This allows the UI to display the query for debugging/verification
            response_query = query_payload.get('query', {})
            if not response_query:
                # Fallback: try to get query from query_payload directly
                response_query = query_payload if isinstance(query_payload, dict) else {}
            
            # CRITICAL: Always include results in query_result, especially for zero completions queries
            # The frontend needs the results array to display users, regardless of response_type
            # Also include aggregations for chart visualizations
            final_response = {
                'type': 'query',
                'message': response.get('message', ''),
                'index_id': query_payload.get('index_id', index_id),
                'query': response_query,  # Always include query, even for 0 results
                'query_result': execution_result,  # This includes 'results' array from extraction
                'response_type': final_response_type,
                'metrics': response.get('metrics', {}),
                'visualization': self._get_visualization_config(
                    ResponseType(final_response_type) if final_response_type else response_type, 
                    execution_result
                ),
                'time_context': time_period.description if time_period else 'all time',
                'title': query_payload.get('title', response.get('title', 'Query Results'))
            }
            
            # Include timeframe metadata for dashboard widgets (if relative timeframe detected)
            if timeframe_key:
                final_response['timeframe_key'] = timeframe_key
                final_response['timezone'] = 'Asia/Kolkata'  # Default timezone
                final_response['date_field'] = time_field_override
                # Default to "date" mode to use OpenSearch date math (e.g., "now-3M/M")
                # This allows dynamic date calculation at query time
                # If the field is stored as epoch, it can be overridden when creating the widget
                final_response['date_mode'] = 'date'  # Use date math expressions by default
            
            # CRITICAL: Include fields_to_show in response so frontend can display columns in correct order
            if 'fields_to_show' in query_payload:
                final_response['fields_to_show'] = query_payload['fields_to_show']
                logger.info(f"Included fields_to_show in response: {query_payload['fields_to_show']}")
            
            # CRITICAL: Include aggregations in response for chart visualizations
            # The frontend needs aggregations to display bar charts, pie charts, etc.
            aggregations = execution_result.get('aggregations', {})
            
            # CRITICAL: For completion rate queries, extract the value and replace scope aggregation
            # This prevents the UI from showing "[object Object]"
            if 'scope' in aggregations:
                scope_agg = aggregations['scope']
                if isinstance(scope_agg, dict):
                    buckets = scope_agg.get('buckets', {})
                    # Handle both dict and list formats for buckets
                    if isinstance(buckets, dict):
                        all_bucket = buckets.get('all', {})
                    elif isinstance(buckets, list) and len(buckets) > 0:
                        # If buckets is a list, use the first bucket
                        all_bucket = buckets[0]
                    else:
                        all_bucket = {}
                    
                    if isinstance(all_bucket, dict):
                        completion_rate = all_bucket.get('completion_rate', {})
                        
                        # Check for user completion rate structure FIRST (assigned_modules/completed_modules)
                        # This is the pattern used by _force_user_completion_rate_query_structure
                        assigned_modules = all_bucket.get('assigned_modules', {})
                        completed = all_bucket.get('completed', {})
                        if isinstance(assigned_modules, dict) and 'value' in assigned_modules and isinstance(completed, dict):
                            completed_modules = completed.get('completed_modules', {})
                            if isinstance(completed_modules, dict) and 'value' in completed_modules:
                                # This is a user completion rate query (uses cardinality on mid)
                                # Extract rate_value from completion_rate if available, otherwise calculate it
                                if isinstance(completion_rate, dict) and 'value' in completion_rate:
                                    rate_value = completion_rate.get('value', 0)
                                else:
                                    # Calculate completion rate manually if bucket_script didn't provide it
                                    assigned_count = assigned_modules.get('value', 0)
                                    completed_count = completed_modules.get('value', 0)
                                    rate_value = (completed_count / assigned_count * 100) if assigned_count > 0 else 0
                                    logger.warning(f"User completion rate structure detected but completion_rate.value missing. Calculated manually: {rate_value:.1f}%")
                                
                                aggregations['scope'] = {
                                    '_label': 'Completion Rate',
                                    'value': rate_value,
                                    'formatted_value': f"{rate_value:.1f}%"
                                }
                                logger.info(f"Extracted user completion rate {rate_value:.1f}% from scope aggregation for UI display (assigned: {assigned_modules.get('value')}, completed: {completed_modules.get('value')})")
                        # Check for module completion rate structure (assigned/completed_count)
                        # This is the pattern used by _force_module_completion_rate_query_structure
                        elif isinstance(completion_rate, dict) and 'value' in completion_rate:
                            rate_value = completion_rate.get('value', 0)
                            # Replace scope aggregation with just the completion rate value
                            aggregations['scope'] = {
                                '_label': 'Completion Rate',
                                'value': rate_value,
                                'formatted_value': f"{rate_value:.1f}%"
                            }
                            logger.info(f"Extracted completion rate {rate_value:.1f}% from scope aggregation for UI display")
                        else:
                            # Log warning if we have scope but can't extract completion rate
                            logger.warning(f"Scope aggregation found but couldn't extract completion rate. all_bucket keys: {list(all_bucket.keys())}, completion_rate: {completion_rate}")
            
            if final_response_type in ['bar_chart', 'pie_chart', 'line_chart']:
                if aggregations:
                    # Verify aggregations have buckets for chart rendering
                    has_buckets = False
                    for agg_name, agg_value in aggregations.items():
                        if isinstance(agg_value, dict) and 'buckets' in agg_value:
                            has_buckets = True
                            bucket_count = len(agg_value.get('buckets', []))
                            logger.info(f"Included aggregation '{agg_name}' with {bucket_count} buckets for {final_response_type} visualization")
                            break
                    
                    if not has_buckets:
                        logger.warning(f"Chart response type ({final_response_type}) but aggregations don't have buckets!")
                        logger.warning(f"Aggregations structure: {list(aggregations.keys())}")
                        for name, value in aggregations.items():
                            logger.warning(f"  {name}: {type(value).__name__}, keys: {list(value.keys()) if isinstance(value, dict) else 'N/A'}")
                    
                    final_response['aggregations'] = aggregations
                else:
                    logger.error(f"CRITICAL: Chart response type ({final_response_type}) but NO aggregations in execution result!")
                    logger.error(f"Execution result keys: {list(execution_result.keys())}")
                    query_body = query_payload.get('query', {})
                    logger.error(f"Query had aggregations: {'aggs' in query_body if isinstance(query_body, dict) else False}")
                    logger.error(f"Query size: {query_body.get('size') if isinstance(query_body, dict) else 'N/A'}")
                    # Still set it to empty dict so frontend knows to expect aggregations
                    final_response['aggregations'] = {}
            else:
                # Include aggregations even for non-chart types if they exist (might be useful)
                if aggregations:
                    final_response['aggregations'] = aggregations
            
            # Results should be present in execution_result
            # Bedrock handles all query types including zero completions queries
            
            return final_response
            
        except Exception as e:
            logger.exception(f"Error in query orchestrator: {e}")
            # Include query in error response if available
            error_query = query_payload.get('query', {}) if query_payload else None
            return self._error_response(f"An error occurred: {str(e)}", query=error_query)

    def _normalize_response_type(self, value: Any, fallback: str = ResponseType.TABLE.value) -> str:
        """
        Normalize response_type strings to known ResponseType enum values.
        Prevents ValueError crashes when upstream returns aliases like 'time_series'.
        """
        if isinstance(value, ResponseType):
            return value.value
        if not value:
            return fallback

        if isinstance(value, str):
            v = value.strip().lower()
        else:
            # Unknown type, return fallback
            return fallback

        alias_map = {
            # Common synonyms / legacy values
            'time_series': ResponseType.LINE_CHART.value,
            'timeseries': ResponseType.LINE_CHART.value,
            'trend': ResponseType.LINE_CHART.value,
            'line': ResponseType.LINE_CHART.value,
            'bar': ResponseType.BAR_CHART.value,
            'pie': ResponseType.PIE_CHART.value,
            'donut': ResponseType.PIE_CHART.value,
            'doughnut': ResponseType.PIE_CHART.value,
            'kpi': ResponseType.KPI_WIDGET.value,
            'kpi_single': ResponseType.KPI_WIDGET.value,
            'text': ResponseType.TEXT_RESPONSE.value,
        }

        v = alias_map.get(v, v)

        # Only return values that exist in the enum; otherwise fallback
        try:
            return ResponseType(v).value
        except Exception:
            return fallback
    
    def _generate_query(
        self,
        user_message: str,
        schema: Dict[str, Any],
        response_type: ResponseType,
        time_period: Optional[TimePeriod],
        context: ConversationContext,
        time_field: str = 'created_on'
    ) -> Dict[str, Any]:
        """
        Phase 1: Generate OpenSearch query using LLM.
        LLM only generates query structure, not data.
        """
        # Build system prompt
        # Conversation context disabled per user request
        system_prompt = QueryPromptBuilder.build_system_prompt(
            schema=schema,
            response_type=response_type,
            conversation_context=""  # Disabled - don't use previous query context
        )
        
        # Build user prompt - Bedrock will handle all entity extraction and query building
        user_prompt = QueryPromptBuilder.build_user_prompt(
            user_message=user_message,
            time_period=time_period,
            time_field=time_field,
            active_filters=context.get_active_filters_dict()
        )
        
        # Follow-up functionality disabled per user request
        # # Add follow-up context if available
        # last_query = context.get_last_query()
        # if last_query:
        #     follow_up_context = QueryPromptBuilder.get_follow_up_context(
        #         previous_query=last_query.query,
        #         previous_result_total=last_query.result_total,
        #         previous_message=last_query.user_message
        #     )
        #     user_prompt = follow_up_context + "\n\n" + user_prompt
        
        # Call LLM
        messages = [{"role": "user", "content": user_prompt}]
        llm_response = self._call_llm(system_prompt, messages)
        
        # Parse response
        return self._parse_query_response(llm_response)

    def _resolve_time_period(
        self,
        requested: Optional[TimePeriod],
        context: ConversationContext
    ) -> Optional[TimePeriod]:
        """
        Resolve time period from user query.
        
        CRITICAL: Do NOT carry forward time filters from previous queries.
        Only use time filters if explicitly mentioned in the current query.
        If no time is mentioned, assume all time (lifetime).
        """
        # If user explicitly requested a time period, use it
        if requested and not requested.is_lifetime:
            return requested
        
        # If no time mentioned or lifetime requested, return lifetime (all time)
        # DO NOT use stored time filters from context
        if requested and requested.is_lifetime:
            return requested
        
        # If requested is None, return lifetime (all time)
        # Import here to avoid circular dependency
        from .time_handler import TimeHandler
        return TimeHandler._lifetime()

    def _maybe_build_follow_up_query(
        self,
        user_message: str,
        response_type: ResponseType,
        context: ConversationContext
    ) -> Optional[Dict[str, Any]]:
        """Handle follow-up list requests (e.g., 'who are these users?')."""
        last_query = context.get_last_query()
        if not last_query:
            return None

        if not self._is_follow_up_list_request(user_message, response_type, last_query):
            return None

        return self._build_follow_up_list_query(last_query, context)
    
    def _generate_response(
        self,
        response_type: ResponseType,
        query_results: Dict[str, Any],
        original_query: str,
        time_period: Optional[TimePeriod],
        query_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Phase 2: Generate human-readable response using actual data.
        """
        # CRITICAL: For completion rate queries, extract the value directly and return it
        completion_rate_message = self._extract_completion_rate_message(query_results, original_query, time_period)
        if completion_rate_message:
            return {
                'message': completion_rate_message,
                'highlights': [],
                'warnings': [],
                'metrics': self._extract_metrics(query_results),
                'title': query_info.get('title', 'Results')
            }
        
        # Build prompt for response generation
        system_prompt = ResponsePromptBuilder.SYSTEM_PROMPT
        
        user_prompt = ResponsePromptBuilder.build_prompt(
            response_type=response_type,
            query_results=query_results,
            original_query=original_query,
            time_context=time_period.description if time_period else None,
            filters_applied=query_info.get('filters_applied')
        )
        
        # Call LLM
        messages = [{"role": "user", "content": user_prompt}]
        llm_response = self._call_llm(system_prompt, messages, temperature=0.3)
        
        # Parse response
        try:
            response = json.loads(llm_response)
            return {
                'message': response.get('message', self._create_fallback_message(query_results, time_period)),
                'highlights': response.get('highlights', []),
                'warnings': response.get('warnings', []),
                'metrics': self._extract_metrics(query_results),
                'title': query_info.get('title', 'Results')
            }
        except json.JSONDecodeError:
            # If LLM didn't return valid JSON, use the text as message
            # But validate it first
            return {
                'message': self._create_fallback_message(query_results, time_period),
                'metrics': self._extract_metrics(query_results),
                'title': query_info.get('title', 'Results')
            }

    def _is_follow_up_list_request(
        self,
        user_message: str,
        response_type: ResponseType,
        last_query: QueryRecord
    ) -> bool:
        """Determine if the user is asking for the actual entities behind the last metric."""
        if not last_query or not last_query.primary_entity_field:
            return False

        # Only handle when last response was aggregate (not already a table)
        if last_query.response_type in [ResponseType.TABLE.value]:
            return False

        message_lower = user_message.lower()
        entity_field = last_query.primary_entity_field

        if entity_field == 'email_addr':
            follow_up_keywords = [
                'who are these',
                'who are those',
                'who are the',
                'who are they',
                'who are the users',
                'who are the people',
                'who exactly',
                'which users',
                'which people',
                'list them',
                'list those',
                'list the users',
                'show them',
                'show those users',
                'show me the users',
                'show me them',
                'give me the users',
                'tell me who'
            ]

            matched = any(keyword in message_lower for keyword in follow_up_keywords)

            pronoun_terms = ['these', 'those', 'them']
            user_terms = ['users', 'people', 'trainees', 'learners', 'participants', 'staff']
            pronoun_match = any(p in message_lower for p in pronoun_terms) and any(u in message_lower for u in user_terms)

            starts_with_who = message_lower.startswith('who') and len(message_lower.split()) <= 6

            return matched or pronoun_match or starts_with_who

        if entity_field == 'module_name':
            module_terms = ['modules', 'module', 'trainings', 'courses', 'programs', 'lessons']
            list_terms = ['which', 'list', 'show', 'give me', 'display', 'what are', 'who all', 'which all']
            has_module = any(term in message_lower for term in module_terms)
            has_request = any(term in message_lower for term in list_terms)

            pronoun_terms = ['these', 'those']
            pronoun_match = any(p in message_lower for p in pronoun_terms) and has_module

            return (has_module and has_request) or pronoun_match

        return False

    def _build_follow_up_list_query(
        self,
        last_query: QueryRecord,
        context: ConversationContext
    ) -> Dict[str, Any]:
        """Build a deterministic query that lists the actual entities referenced earlier."""
        size = last_query.primary_entity_count or 100
        size = min(size, 200)

        base_query = last_query.filter_query or last_query.query.get('query') or {"match_all": {}}
        entity_field = last_query.primary_entity_field or 'email_addr'

        if entity_field == 'module_name':
            fields = [
                "module_name",
                "published_date",
                "module_created_on",
                "module_status",
                "skill_name",
                "product_name",
                "city",
                "country",
                "trainer_email_addr",
                "coach_email_addr"
            ]
            sort_field = self._infer_time_field_from_query(last_query.query) or "published_date"
        else:
            fields = [
                "email_addr",
                "first_name",
                "last_name",
                "city",
                "country",
                "designation",
                "module_name",
                "skill_name",
                "completed_status",
                "complete_percentage",
                "created_on"
            ]
            sort_field = "created_on"

        query = {
            "size": size,
            "_source": fields,
            "query": copy.deepcopy(base_query),
            "sort": [{sort_field: "desc"}]
        }

        # Note: Removed collapse to show all matching records.
        # If deduplication is needed, it should be handled by the frontend or explicitly requested.

        return {
            "index_id": "module_consumption_data",
            "query": query,
            "response_type": ResponseType.TABLE.value,
            "title": last_query.primary_entity_label or "Matching Records",
            "description": (
                "Listing the modules referenced in the previous metric."
                if entity_field == 'module_name' else
                "Listing the users referenced in the previous metric."
            ),
            "fields_to_show": fields,
            "filters_applied": context.get_active_filters_dict(),
            "is_follow_up": True
        }

    def _build_module_intent_query(
        self,
        module_intent: str,
        time_period: Optional[TimePeriod],
        context: ConversationContext,
        response_type: ResponseType
    ) -> Dict[str, Any]:
        """Handle module published/consumed questions deterministically."""
        if module_intent == 'created':
            time_field = 'module_created_on'
        elif module_intent == 'published':
            time_field = 'published_date'
        else:
            time_field = 'created_on'
        must_filters: List[Dict[str, Any]] = []
        
        if module_intent == 'consumed':
            must_filters.append({"term": {"completed_status": 1}})
        elif module_intent == 'published':
            must_filters.append({"term": {"module_status": 0}})
        
        if time_period and not time_period.is_lifetime:
            must_filters.append({
                "range": {
                    time_field: {
                        "gte": time_period.start_timestamp,
                        "lt": time_period.end_timestamp
                    }
                }
            })
        
        bool_query = {"bool": {"must": must_filters}} if must_filters else {"match_all": {}}
        
        agg_name_map = {
            'created': 'unique_modules_created',
            'published': 'unique_modules_published',
            'consumed': 'unique_modules_consumed'
        }
        title_map = {
            'created': "Modules Created",
            'published': "Modules Published",
            'consumed': "Modules Consumed"
        }
        description_map = {
            'created': "Unique modules created in the selected period.",
            'published': "Unique modules published/go-live in the selected period.",
            'consumed': "Unique modules completed/consumed in the selected period."
        }
        agg_name = agg_name_map.get(module_intent, 'unique_modules')
        title = title_map.get(module_intent, "Modules")
        description = description_map.get(module_intent, "Module metrics for the selected period.")
        
        # Smart detection: only show table if user EXPLICITLY asks for a list
        # "which modules were published" = wants COUNT (KPI)
        # "list which modules were published" = wants LIST (TABLE)
        # Check if user wants a table/list (Bedrock should have set response_type correctly)
        wants_table = response_type == ResponseType.TABLE
        
        if wants_table:
            size = 200
            size = max(10, min(size, 500))
            fields = [
                "module_name",
                "published_date",
                "module_created_on",
                "module_status",
                "skill_name",
                "product_name",
                "city",
                "country",
                "email_addr",
                "completed_status",
                "complete_percentage"
            ]
            
            query_body = {
                "size": size,
                "_source": fields,
                "query": bool_query,
                "sort": [{time_field: "desc"}]
            }
            
            # Note: Removed collapse to show all matching records instead of just unique modules.
            # Users expect to see all rows when requesting a table/list view.
            
            return {
                "index_id": "module_consumption_data",
                "query": query_body,
                "response_type": ResponseType.TABLE.value,
                "title": title,
                "description": description,
                "fields_to_show": fields,
                "filters_applied": context.get_active_filters_dict(),
                "intent": module_intent
            }
        
        # Check if this is a "total completions" query (count all records, not unique modules)
        # Check if this is a "total completions" query (count all records, not unique)
        count_total_records = 'total' in user_message.lower() and 'completion' in user_message.lower()
        
        if count_total_records and module_intent == 'consumed':
            # For "total completions", count ALL records, not unique modules
            # Use filter aggregation with match_all to get doc_count (safer than value_count on _id)
            return {
                "index_id": "module_consumption_data",
                "query": {
                    "size": 0,
                    "query": bool_query,
                    "aggs": {
                        "total_completions": {
                            "filter": {"match_all": {}}
                            # doc_count will give us the total number of records
                        }
                    }
                },
                "response_type": ResponseType.KPI_WIDGET.value,
                "title": "Total Module Completions",
                "description": "Total number of module completion records in the selected period.",
                "filters_applied": context.get_active_filters_dict(),
                "intent": module_intent
            }
        
        return {
            "index_id": "module_consumption_data",
            "query": {
                "size": 0,
                "query": bool_query,
                "aggs": {
                    agg_name: {
                        "cardinality": {"field": "mid"}
                    }
                }
            },
            "response_type": ResponseType.KPI_WIDGET.value,
            "title": title,
            "description": description,
            "filters_applied": context.get_active_filters_dict(),
            "intent": module_intent
        }
    
    def _build_ranking_query(
        self,
        time_period: Optional[TimePeriod],
        context: ConversationContext,
        user_message: str = ""
    ) -> Dict[str, Any]:
        """
        Build query for ranking questions like "which module has most completions".
        Returns a terms aggregation sorted by count.
        
        Key rules:
        - Always filter by user_status = 5 (active users only)
        - Use uid for counting unique users
        - Use mid for counting unique modules
        """
        # Detect what to rank by from the message
        message_lower = user_message.lower()
        if 'module' in message_lower or 'training' in message_lower or 'course' in message_lower:
            rank_by = 'module_name'
        elif 'user' in message_lower or 'learner' in message_lower:
            rank_by = 'email_addr'
        elif 'city' in message_lower:
            rank_by = 'city'
        elif 'skill' in message_lower:
            rank_by = 'skill_name'
        else:
            rank_by = 'module_name'  # default
        message_lower = user_message.lower()
        
        # Build the base filter
        must_filters: List[Dict[str, Any]] = []
        
        # ALWAYS filter for active users only (user_status = 5) - BUT ONLY FOR CONSUMPTION INDEX
        # CRITICAL: Field name varies by index
        # - Consumption index uses 'user_status' = 5
        # - User profile index uses 'status' = 5 (NOT user_status!)
        # - Catalog index doesn't have user status fields
        selected_index_id = query_payload.get('index_id') or 'module_consumption_data'  # Default to consumption if not set
        is_consumption_index = False
        is_user_profile_index = False
        
        if selected_index_id in settings.OPENSEARCH_INDEXES:
            if selected_index_id == 'module_consumption_data':
                is_consumption_index = True
            elif selected_index_id == 'user_profile_data':
                is_user_profile_index = True
        else:
            # Check if it's the actual index name
            if 'consumption' in str(selected_index_id).lower():
                is_consumption_index = True
            elif 'learnbee' in str(selected_index_id).lower() or 'user_summary' in str(selected_index_id).lower():
                is_user_profile_index = True
        
        if is_consumption_index:
            must_filters.append({"term": {"user_status": 5}})
            logger.info(f"Added user_status = 5 filter for consumption index")
        elif is_user_profile_index:
            must_filters.append({"term": {"status": 5}})
            logger.info(f"Added status = 5 filter for user profile index")
        else:
            logger.info(f"Skipping user status filter - not applicable for index: {selected_index_id}")
        
        # ALWAYS filter for assigned records only (assigned_status = 0) - BUT ONLY FOR CONSUMPTION INDEX
        # Catalog and user profile indices don't have assigned_status
        if is_consumption_index:
            # Users can only complete what's assigned to them
            must_filters.append({"term": {"assigned_status": 0}})
        
        # Detect what metric to count from the message
        metric_type = 'completions'  # default
        
        # Check what the user is asking about
        completion_keywords = ['completion', 'completions', 'completed', 'finished', 'done']
        
        if any(kw in message_lower for kw in completion_keywords):
            # Filter for completed records only
            must_filters.append({"term": {"completed_status": 1}})
            metric_type = 'completions'
        
        # Add time filter if specified
        if time_period and not time_period.is_lifetime:
            # Determine time field based on metric type
            time_field = 'completed_date' if metric_type == 'completions' else 'created_on'
            must_filters.append({
                "range": {
                    time_field: {
                        "gte": time_period.start_timestamp,
                        "lt": time_period.end_timestamp
                    }
                }
            })
        
        bool_query = {"bool": {"must": must_filters}} if must_filters else {"match_all": {}}
        
        # Determine the aggregation field
        agg_field = rank_by
        if rank_by in self.NATIVE_KEYWORD_FIELDS:
            agg_field = rank_by
        
        # Build the aggregation - group by NAME for display, count unique IDs inside
        agg_name = f"{metric_type}_count"
        
        # IMPORTANT: Group by the DISPLAY field (name), use ID for unique counting INSIDE
        # This ensures the chart shows names, not IDs
        if rank_by == 'module_name':
            # Group by module_name (for display), count unique users who completed
            # For "most completed", order by _count (total completions), not unique_users
            query = {
                "size": 0,
                "query": bool_query,
                "aggs": {
                    agg_name: {
                        "terms": {
                            "field": "module_name",  # Group by NAME for display
                            "size": 10,
                            "order": {"_count": "desc"}  # Order by total completions (doc_count)
                        },
                        "aggs": {
                            "unique_users": {"cardinality": {"field": "uid"}}  # Count unique users
                        }
                    }
                }
            }
        elif rank_by == 'email_addr':
            # Group by email (for display), count unique modules completed
            query = {
                "size": 0,
                "query": bool_query,
                "aggs": {
                    agg_name: {
                        "terms": {
                            "field": "email_addr",  # Group by EMAIL for display
                            "size": 10,
                            "order": {"_count": "desc"}  # Order by total completions (doc_count)
                        },
                        "aggs": {
                            "unique_modules": {"cardinality": {"field": "mid"}}  # Count unique modules
                        }
                    }
                }
            }
        else:
            # For other groupings (city, skill, etc.)
            query = {
                "size": 0,
                "query": bool_query,
                "aggs": {
                    agg_name: {
                        "terms": {
                            "field": agg_field,
                            "size": 10,
                            "order": {"_count": "desc"}  # Order by total completions (doc_count)
                        },
                        "aggs": {
                            "unique_users": {"cardinality": {"field": "uid"}}
                        }
                    }
                }
            }
        
        # Generate title based on what we're ranking
        field_labels_plural = {
            'module_name': 'Modules',
            'email_addr': 'Users',
            'city': 'Cities',
            'skill_name': 'Skills',
            'product_name': 'Products'
        }
        metric_label = metric_type.title()
        title = f"Top {field_labels_plural.get(rank_by, rank_by.replace('_', ' ').title())} by {metric_label}"
        
        return {
            "index_id": "module_consumption_data",
            "query": query,
            "response_type": ResponseType.BAR_CHART.value,
            "title": title,
            "description": f"Ranking of {field_labels_plural.get(rank_by, rank_by)} by number of {metric_type}",
            "filters_applied": context.get_active_filters_dict(),
            "is_ranking": True,
            "rank_by": rank_by,
            "metric_type": metric_type
        }
    
    def _build_assignment_vs_completion_query(
        self,
        context: ConversationContext
    ) -> Dict[str, Any]:
        """Deterministic comparison between assigned users and completions."""
        base_query = {
            "size": 0,
            "query": {"match_all": {}},
            "aggs": {
                "assigned_users": {
                    "filter": {"term": {"assigned_status": 0}},
                    "aggs": {
                        "count": {"cardinality": {"field": "email_addr"}}
                    }
                },
                "completed_users": {
                    "filter": {"term": {"completed_status": 1}},
                    "aggs": {
                        "count": {"cardinality": {"field": "email_addr"}}
                    }
                }
            }
        }
        
        return {
            "index_id": "module_consumption_data",
            "query": base_query,
            "response_type": ResponseType.COMPARISON.value,
            "title": "Assigned vs Completed Users",
            "description": "Compares distinct users who were assigned vs those who completed.",
            "filters_applied": context.get_active_filters_dict(),
            "intent": "assigned_vs_completed"
        }

    def _apply_structured_filters(
        self,
        query_payload: Dict[str, Any],
        time_period: Optional[TimePeriod],
        context: ConversationContext,
        time_field: str,
        entities: Optional[Dict[str, Any]] = None,
        user_message: Optional[str] = None
    ):
        """Enforce deterministic filters (time, context filters) on every query."""
        query_body = query_payload.get('query')
        if not isinstance(query_body, dict):
            return

        search_query = query_body.setdefault('query', {"match_all": {}})
        bool_query = self._ensure_bool_query(search_query)
        rating_query = self._is_rating_query(user_message or "", query_payload)
        effective_time_field = time_field if not rating_query else 'completed_date'
        
        if time_period and not time_period.is_lifetime:
            # Apply time filter - Bedrock should have already added the correct date field
            # But we'll ensure it's there with the suggested field as fallback
            # NOTE: For "haven't completed" queries, time filters should NOT be at top level
            # They should only be in the aggregation. This is handled by passing time_period=None
            # for those queries, so this code won't run.
            # Pass user_message to detect relative timeframes and use date math expressions
            self._ensure_time_filter_clause(bool_query, effective_time_field, time_period, user_message or "")

        # Apply inferred filters (cities, completion, products, etc.)
        # Enforce single-tenant scope (cmid=1)
        self._append_filter_clause(bool_query, {"term": {"cmid": 1}})
        
        # ALWAYS filter for active users only - BUT FIELD NAME VARIES BY INDEX
        # CRITICAL: 
        # - Consumption index uses 'user_status' = 5
        # - User profile index uses 'status' = 5 (NOT user_status!)
        # - Catalog index doesn't have user status fields
        selected_index_id = query_payload.get('index_id') or 'module_consumption_data'  # Default to consumption if not set
        is_consumption_index = False
        is_user_profile_index = False
        is_monthly_activity_index = False
        
        if selected_index_id in settings.OPENSEARCH_INDEXES:
            if selected_index_id == 'module_consumption_data':
                is_consumption_index = True
            elif selected_index_id == 'user_profile_data':
                is_user_profile_index = True
            elif selected_index_id == 'monthly_user_activity_data':
                is_monthly_activity_index = True
        else:
            # Check if it's the actual index name
            if 'consumption' in str(selected_index_id).lower():
                is_consumption_index = True
            elif 'learnbee' in str(selected_index_id).lower() or 'user_summary' in str(selected_index_id).lower():
                is_user_profile_index = True
            elif 'monthly_user_activity' in str(selected_index_id).lower():
                is_monthly_activity_index = True
        
        if is_consumption_index:
            self._append_filter_clause(bool_query, {"term": {"user_status": 5}})
            logger.info(f"Added user_status = 5 filter for consumption index")
        elif is_user_profile_index or is_monthly_activity_index:
            self._append_filter_clause(bool_query, {"term": {"status": 5}})
            logger.info(
                f"Added status = 5 filter for {'monthly activity' if is_monthly_activity_index else 'user profile'} index"
            )
        else:
            logger.info(f"Skipping user status filter - not applicable for index: {selected_index_id}")
        
        # Bedrock should have already added assigned_status and completed_status filters if needed
        # based on the query intent. We'll trust Bedrock's judgment here.

        filter_clauses = self._build_filter_clauses(context)
        for clause in filter_clauses:
            self._append_filter_clause(bool_query, clause)
        
        # Convert any term queries for module_name to match_phrase for fuzzy matching
        self._convert_module_name_terms_to_match(bool_query)

        # Enforce default module_status = 0 when user asks about modules without specifying a different status
        # CRITICAL: Pass index_id to ensure correct field name is used
        selected_index_id = query_payload.get('index_id') or 'module_consumption_data'  # Default to consumption if not set
        self._ensure_module_status_default(bool_query, user_message or "", selected_index_id)

        if rating_query:
            self._enforce_rating_filters(bool_query)
        
        # Trust Bedrock's collapse decision - it should follow the rules in the prompt
    
    def _should_filter_by_assigned_status(
        self, 
        entities: Optional[Dict[str, Any]], 
        user_message: Optional[str]
    ) -> bool:
        """
        Determine if we should filter by assigned_status = 0.
        
        Only filter when the query is about:
        - Completions
        - Assignments
        - Module interactions
        - Module progress/status
        
        Do NOT filter for:
        - General user information
        - User demographics
        - Schema/data dictionary queries
        - Generic user lists
        """
        if not user_message:
            user_message = ""
        
        message_lower = user_message.lower()
        
        # Check for completion/assignment-related keywords
        completion_keywords = [
            'completion', 'completions', 'completed', 'finished', 'done', 
            'passed', 'complete', 'finish'
        ]
        assignment_keywords = [
            'assignment', 'assignments', 'assigned', 'enrolled', 'delivered'
        ]
        module_interaction_keywords = [
            'module', 'modules', 'course', 'courses', 'progress', 
            'started', 'attempted', 'interaction'
        ]
        
        # Check message for keywords (Bedrock should have already added filters, but we check as fallback)
        has_completion_keyword = any(kw in message_lower for kw in completion_keywords)
        has_assignment_keyword = any(kw in message_lower for kw in assignment_keywords)
        has_module_keyword = any(kw in message_lower for kw in module_interaction_keywords)
        
        # If query mentions completions or assignments, definitely filter
        if has_completion_keyword or has_assignment_keyword:
            return True
        
        # If query mentions modules AND seems to be about interactions (not just listing users)
        # Generic "user information" or "list users" should NOT filter by assigned_status
        generic_user_keywords = ['user information', 'user info', 'list users', 'all users', 'user data', 'user details']
        is_generic_user_query = any(kw in message_lower for kw in generic_user_keywords)
        
        if is_generic_user_query:
            return False
        
        # If query mentions modules but is generic, don't filter
        # Only filter if it's clearly about module interactions/completions
        if has_module_keyword and (has_completion_keyword or has_assignment_keyword):
            return True
        
        # Default: don't filter for generic queries
        return False

    def _ensure_bool_query(self, search_query: Dict[str, Any]) -> Dict[str, Any]:
        """Ensure the search query is wrapped in a bool clause we can extend safely."""
        existing_bool = search_query.get('bool')
        if isinstance(existing_bool, dict):
            bool_query = existing_bool
            # CRITICAL: Remove invalid keys from bool query - OpenSearch only allows: must, must_not, should, filter, minimum_should_match, boost
            valid_bool_keys = {'must', 'must_not', 'should', 'filter', 'minimum_should_match', 'boost'}
            invalid_keys = [key for key in bool_query.keys() if key not in valid_bool_keys]
            if invalid_keys:
                logger.warning(f"Removing invalid keys from bool query: {invalid_keys}")
                for key in invalid_keys:
                    bool_query.pop(key, None)
            # CRITICAL: Check for nested bool queries - OpenSearch doesn't allow bool inside bool directly
            # If we find a nested bool, we need to flatten it
            for key in ('filter', 'must', 'should', 'must_not'):
                value = bool_query.get(key)
                if isinstance(value, list):
                    # Check each item for nested bool queries
                    for i, item in enumerate(value):
                        if isinstance(item, dict) and 'bool' in item:
                            # Found a nested bool - flatten it by extracting its clauses
                            nested_bool = item['bool']
                            if isinstance(nested_bool, dict):
                                # Extract clauses from nested bool and add them to the parent
                                for nested_key in ('filter', 'must', 'should', 'must_not'):
                                    nested_value = nested_bool.get(nested_key)
                                    if nested_value:
                                        if isinstance(nested_value, list):
                                            # Add all clauses from nested bool to parent
                                            if key not in bool_query:
                                                bool_query[key] = []
                                            bool_query[key].extend(nested_value)
                                        elif isinstance(nested_value, dict):
                                            # Single clause - add it
                                            if key not in bool_query:
                                                bool_query[key] = []
                                            bool_query[key].append(nested_value)
                                # Remove the nested bool item
                                value.pop(i)
                                # If there are other keys in the item besides 'bool', keep them
                                item.pop('bool')
                                if item:
                                    # If item still has other keys, add it back
                                    value.insert(i, item)
                                break
                elif isinstance(value, dict) and 'bool' in value:
                    # Single dict with nested bool - flatten it
                    nested_bool = value['bool']
                    if isinstance(nested_bool, dict):
                        # Extract clauses from nested bool
                        for nested_key in ('filter', 'must', 'should', 'must_not'):
                            nested_value = nested_bool.get(nested_key)
                            if nested_value:
                                if isinstance(nested_value, list):
                                    bool_query[nested_key] = nested_value
                                elif isinstance(nested_value, dict):
                                    bool_query[nested_key] = [nested_value]
                        # Remove the nested bool
                        value.pop('bool')
                        if value:
                            # If there are other keys, add them to filter
                            if 'filter' not in bool_query:
                                bool_query['filter'] = []
                            bool_query['filter'].append(value)
        else:
            previous = copy.deepcopy(search_query)
            search_query.clear()
            bool_query = {'must': [], 'filter': []}
            if previous and previous != {"match_all": {}}:
                bool_query['must'].append(previous)
            search_query['bool'] = bool_query

        for key in ('filter', 'must', 'should', 'must_not'):
            value = bool_query.get(key)
            if isinstance(value, list):
                # Clean the list - remove invalid entries and nested bool queries
                cleaned = []
                for item in value:
                    # Only keep valid query clauses (dicts that aren't empty)
                    if isinstance(item, dict) and item:
                        # Remove any invalid/empty clauses
                        if item != {} and item != {"match_all": {}}:
                            # Check for nested bool and flatten it
                            if 'bool' in item:
                                nested_bool = item['bool']
                                if isinstance(nested_bool, dict):
                                    # Flatten nested bool clauses into parent
                                    for nested_key in ('filter', 'must', 'should', 'must_not'):
                                        nested_value = nested_bool.get(nested_key)
                                        if nested_value:
                                            if isinstance(nested_value, list):
                                                cleaned.extend(nested_value)
                                            elif isinstance(nested_value, dict):
                                                cleaned.append(nested_value)
                                    # Remove bool key
                                    item.pop('bool')
                                    # If item still has other keys, add them
                                    if item:
                                        cleaned.append(item)
                                else:
                                    cleaned.append(item)
                            else:
                                cleaned.append(item)
                bool_query[key] = cleaned
                continue
            if value is None:
                bool_query[key] = []
            elif isinstance(value, dict):
                # Convert single dict to list, but only if it's valid
                if value and value != {"match_all": {}}:
                    # Check for nested bool
                    if 'bool' in value:
                        nested_bool = value['bool']
                        if isinstance(nested_bool, dict):
                            # Flatten nested bool
                            for nested_key in ('filter', 'must', 'should', 'must_not'):
                                nested_value = nested_bool.get(nested_key)
                                if nested_value:
                                    if isinstance(nested_value, list):
                                        bool_query[nested_key] = nested_value
                                    elif isinstance(nested_value, dict):
                                        bool_query[nested_key] = [nested_value]
                            value.pop('bool')
                            if value:
                                bool_query[key] = [value]
                            else:
                                bool_query[key] = []
                        else:
                            bool_query[key] = [value]
                    else:
                        bool_query[key] = [value]
                else:
                    bool_query[key] = []
            else:
                # Invalid type, set to empty list
                bool_query[key] = []
        return bool_query
    
    def _validate_and_fix_bool_queries(self, query_payload: Dict[str, Any]):
        """
        Recursively validate and fix bool query structures to prevent parsing errors.
        Removes invalid keys, flattens nested bool queries, and ensures all clauses are valid query types.
        """
        # Valid query clause types that can appear in bool.filter, bool.must, etc.
        valid_query_types = {
            'term', 'terms', 'range', 'match', 'match_phrase', 'match_phrase_prefix',
            'exists', 'missing', 'prefix', 'wildcard', 'regexp', 'fuzzy', 'ids',
            'type', 'bool', 'match_all', 'match_none', 'multi_match', 'query_string',
            'simple_query_string', 'common', 'more_like_this', 'script', 'geo_shape',
            'geo_bounding_box', 'geo_distance', 'geo_polygon', 'nested', 'has_child',
            'has_parent', 'function_score', 'boosting', 'constant_score', 'dis_max'
        }
        
        def is_valid_query_clause(obj: Any) -> bool:
            """Check if an object is a valid query clause."""
            if not isinstance(obj, dict):
                return False
            # Must have at least one valid query type key
            return any(key in valid_query_types for key in obj.keys())
        
        def fix_bool_query_recursive(obj: Any, path: str = ""):
            """Recursively find and fix bool queries."""
            if isinstance(obj, dict):
                # Check if this is a bool query
                if 'bool' in obj:
                    bool_query = obj['bool']
                    if isinstance(bool_query, dict):
                        # Remove invalid keys
                        valid_bool_keys = {'must', 'must_not', 'should', 'filter', 'minimum_should_match', 'boost'}
                        invalid_keys = [key for key in bool_query.keys() if key not in valid_bool_keys]
                        if invalid_keys:
                            logger.warning(f"Removing invalid keys from bool query at {path}: {invalid_keys}")
                            for key in invalid_keys:
                                bool_query.pop(key, None)
                        
                        # Check for nested bool queries in filter/must/should/must_not
                        for key in ('filter', 'must', 'should', 'must_not'):
                            value = bool_query.get(key)
                            if isinstance(value, list):
                                # Check each item for nested bool or invalid structures
                                new_items = []
                                for item in value:
                                    if isinstance(item, dict):
                                        # Check if item is a valid query clause
                                        if not is_valid_query_clause(item):
                                            # Invalid structure - try to fix it
                                            if 'query' in item:
                                                # Extract nested query
                                                nested_query = item.pop('query')
                                                if isinstance(nested_query, dict) and is_valid_query_clause(nested_query):
                                                    new_items.append(nested_query)
                                                elif isinstance(nested_query, dict) and 'bool' in nested_query:
                                                    # Extract bool clauses
                                                    nested_bool = nested_query['bool']
                                                    if isinstance(nested_bool, dict):
                                                        for nested_key in ('filter', 'must', 'should', 'must_not'):
                                                            nested_value = nested_bool.get(nested_key)
                                                            if nested_value:
                                                                if isinstance(nested_value, list):
                                                                    new_items.extend(nested_value)
                                                                elif isinstance(nested_value, dict) and is_valid_query_clause(nested_value):
                                                                    new_items.append(nested_value)
                                                # If item still has other keys, log warning
                                                if item:
                                                    logger.warning(f"Removed invalid query structure at {path}.{key}, remaining keys: {list(item.keys())}")
                                            elif 'bool' in item:
                                                # Nested bool - flatten it
                                                nested_bool = item.pop('bool')
                                                if isinstance(nested_bool, dict):
                                                    # Extract clauses from nested bool
                                                    for nested_key in ('filter', 'must', 'should', 'must_not'):
                                                        nested_value = nested_bool.get(nested_key)
                                                        if nested_value:
                                                            if isinstance(nested_value, list):
                                                                new_items.extend([v for v in nested_value if isinstance(v, dict) and is_valid_query_clause(v)])
                                                            elif isinstance(nested_value, dict) and is_valid_query_clause(nested_value):
                                                                new_items.append(nested_value)
                                                    # If item has other keys, keep them (but it should be empty now)
                                                    if item:
                                                        logger.warning(f"Flattened nested bool at {path}.{key}, remaining keys: {list(item.keys())}")
                                            else:
                                                # Invalid structure - log and skip
                                                logger.warning(f"Skipping invalid query clause at {path}.{key}: {list(item.keys())}")
                                                continue
                                        else:
                                            # Valid query clause - check for nested bool
                                            if 'bool' in item:
                                                # Recursively fix nested bool
                                                fix_bool_query_recursive(item, f"{path}.{key}")
                                            new_items.append(item)
                                    else:
                                        # Non-dict item - skip (invalid)
                                        logger.warning(f"Skipping non-dict item at {path}.{key}: {type(item)}")
                                bool_query[key] = new_items
                            elif isinstance(value, dict):
                                # Single dict - check if it's valid
                                if not is_valid_query_clause(value):
                                    # Try to fix it
                                    if 'query' in value:
                                        nested_query = value.pop('query')
                                        if isinstance(nested_query, dict) and is_valid_query_clause(nested_query):
                                            bool_query[key] = [nested_query]
                                        elif isinstance(nested_query, dict) and 'bool' in nested_query:
                                            nested_bool = nested_query['bool']
                                            if isinstance(nested_bool, dict):
                                                for nested_key in ('filter', 'must', 'should', 'must_not'):
                                                    nested_value = nested_bool.get(nested_key)
                                                    if nested_value:
                                                        if isinstance(nested_value, list):
                                                            bool_query[nested_key] = [v for v in nested_value if isinstance(v, dict) and is_valid_query_clause(v)]
                                                        elif isinstance(nested_value, dict) and is_valid_query_clause(nested_value):
                                                            bool_query[nested_key] = [nested_value]
                                    elif 'bool' in value:
                                        nested_bool = value.pop('bool')
                                        if isinstance(nested_bool, dict):
                                            for nested_key in ('filter', 'must', 'should', 'must_not'):
                                                nested_value = nested_bool.get(nested_key)
                                                if nested_value:
                                                    if isinstance(nested_value, list):
                                                        bool_query[nested_key] = [v for v in nested_value if isinstance(v, dict) and is_valid_query_clause(v)]
                                                    elif isinstance(nested_value, dict) and is_valid_query_clause(nested_value):
                                                        bool_query[nested_key] = [nested_value]
                                else:
                                    # Valid - convert to list
                                    bool_query[key] = [value]
                
                # Recursively check all nested structures
                for key, val in obj.items():
                    fix_bool_query_recursive(val, f"{path}.{key}" if path else key)
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    fix_bool_query_recursive(item, f"{path}[{i}]" if path else f"[{i}]")
        
        # Start validation from the query object
        query_body = query_payload.get('query', {})
        if query_body:
            fix_bool_query_recursive(query_body, "query")

    def _ensure_time_filter_clause(
        self,
        bool_query: Dict[str, Any],
        field_name: str,
        time_period: TimePeriod,
        user_message: str = ""
    ):
        """Add a range clause on the requested time field unless already present."""
        normalized_field = self._normalize_field_for_filter(field_name)
        end_key = "lte" if getattr(time_period, "end_inclusive", False) else "lt"
        
        # Check if this is a relative timeframe that should use date math
        from .time_handler import TimeframeResolver
        timeframe_key = TimeframeResolver.extract_timeframe_key(user_message) if user_message else None
        
        # Use date math if relative timeframe detected and field is likely a date field (not epoch)
        # Default to date math for relative timeframes
        use_date_math = timeframe_key is not None
        
        if use_date_math:
            # Get date math expressions
            date_math = TimeframeResolver._get_date_math_expressions(timeframe_key)
            if date_math:
                range_clause = {
                    "range": {
                        normalized_field: {
                            "gte": date_math['gte'],  # String like "now-3M/M"
                            end_key: date_math['lt']  # String like "now"
                        }
                    }
                }
            else:
                # Fallback to epoch if date math not available
                range_clause = {
                    "range": {
                        normalized_field: {
                            "gte": time_period.start_timestamp,
                            end_key: time_period.end_timestamp
                        }
                    }
                }
        else:
            # Use epoch timestamps for absolute dates or when date math not applicable
            range_clause = {
            "range": {
                normalized_field: {
                    "gte": time_period.start_timestamp,
                    end_key: time_period.end_timestamp
                }
            }
        }

        filter_list = bool_query.setdefault('filter', [])
        if not isinstance(filter_list, list):
            filter_list = bool_query['filter'] = [filter_list]

        if not self._filter_exists(filter_list, range_clause):
            filter_list.append(range_clause)

    def _is_monthly_activity_index(self, index_id: Optional[str]) -> bool:
        """True if the index refers to the monthly per-user activity summary index."""
        if not index_id:
            return False
        # Index ID (key)
        if index_id in settings.OPENSEARCH_INDEXES:
            return index_id == 'monthly_user_activity_data'
        # Actual index name (value)
        return 'monthly_user_activity' in str(index_id).lower()

    def _resolve_time_field_for_index(
        self,
        requested_field: Optional[str],
        index_id: Optional[str],
        user_message: str = ""
    ) -> str:
        """
        Determine which time field should be used as the default/fallback based on the selected index.
        
        For MONTHLY USER ACTIVITY index:
        - Activity month questions → completed_on (epoch)
        - Operational freshness → updated_on / last_updated
        - Convert completion-oriented hints (completed_date) → completed_on
        """
        field = requested_field or 'created_on'
        if not index_id:
            return field
        
        if not self._is_monthly_activity_index(index_id):
            return field
        
        msg = (user_message or "").lower()
        
        # Operational freshness / sync questions
        if any(k in msg for k in ['last sync', 'sync time', 'last_updated', 'last updated', 'synced']):
            return 'last_updated'
        if any(k in msg for k in ['updated', 'modified', 'changed']):
            return 'updated_on'
        
        # If the user is explicitly asking about account creation, keep created_on
        if any(k in msg for k in ['user created', 'account created', 'signup', 'signed up', 'registered', 'registration']):
            return 'created_on'
        
        # Map completion-style hint to monthly activity time anchor
        if field == 'completed_date':
            return 'completed_on'
        if field == 'user_created_on':
            return 'created_on'
        
        # Default for monthly activity questions
        if field == 'created_on':
            return 'completed_on'
        
        return field

    def _validate_bool_query(self, query: Dict[str, Any]):
        """Validate and clean bool query structure to prevent parsing errors."""
        query_obj = query.get('query', {})
        if not isinstance(query_obj, dict):
            return
        
        bool_query = query_obj.get('bool', {})
        if not isinstance(bool_query, dict):
            return
        
        # Clean all bool query arrays
        for key in ('must', 'filter', 'should', 'must_not'):
            if key not in bool_query:
                continue
            
            value = bool_query[key]
            if not isinstance(value, list):
                # Convert to list if it's a dict
                if isinstance(value, dict):
                    bool_query[key] = [value] if value else []
                else:
                    bool_query[key] = []
                continue
            
            # Clean the list - remove invalid entries
            cleaned = []
            for item in value:
                # Only keep valid query clauses
                if isinstance(item, dict) and item:
                    # Remove empty dicts, match_all (shouldn't be in arrays), and invalid structures
                    if item != {} and item != {"match_all": {}}:
                        # Check if it's a valid query clause (has at least one query type key)
                        valid_query_keys = ['term', 'terms', 'range', 'match', 'match_phrase', 'bool', 
                                           'multi_match', 'exists', 'prefix', 'wildcard', 'regexp']
                        if any(k in item for k in valid_query_keys):
                            cleaned.append(item)
            
            bool_query[key] = cleaned
            
            # If must array is empty after cleaning, remove it (OpenSearch doesn't like empty must arrays)
            if key == 'must' and len(cleaned) == 0:
                del bool_query[key]

    def _build_filter_clauses(self, context: ConversationContext) -> List[Dict[str, Any]]:
        """Convert inferred filters (except time) into concrete query clauses."""
        clauses: List[Dict[str, Any]] = []
        for filter_type, inferred in context.active_filters.items():
            if filter_type == FilterType.TIME_PERIOD:
                continue
            clause = self._inferred_filter_to_clause(inferred)
            if clause:
                clauses.append(clause)
        return clauses

    def _inferred_filter_to_clause(self, inferred: InferredFilter) -> Optional[Dict[str, Any]]:
        """Translate an inferred filter into an OpenSearch clause."""
        field_name = self._normalize_field_for_filter(inferred.field_name)
        value = inferred.value
        
        # Special handling for person name - search across multiple name fields
        # Use match queries instead of wildcard for better performance and precision
        if inferred.filter_type == FilterType.PERSON_NAME and value:
            name_value = str(value).strip()
            return {
                "bool": {
                    "should": [
                        {"match": {"first_name": {"query": name_value, "fuzziness": "AUTO"}}},
                        {"match": {"last_name": {"query": name_value, "fuzziness": "AUTO"}}},
                        {"match": {"email_addr": {"query": name_value, "fuzziness": "AUTO"}}}
                    ],
                    "minimum_should_match": 1
                }
            }
        
        # Special handling for module_name - use match_phrase for fuzzy matching
        # Per usecase.md Section F.3: "best effort" matching for human-provided names
        if inferred.field_name == 'module_name' and value:
            module_value = str(value)
            # Use match_phrase for better fuzzy matching (handles case, spacing variations)
            return {
                "match_phrase": {
                    "module_name": {
                        "query": module_value,
                        "slop": 2  # Allow up to 2 words between terms
                    }
                }
            }

        if isinstance(value, dict) and any(k in value for k in ('gte', 'lte', 'lt', 'gt')):
            return {"range": {field_name: value}}

        if isinstance(value, list):
            # For module_name lists, use match_phrase for each
            if inferred.field_name == 'module_name':
                return {
                    "bool": {
                        "should": [
                            {
                                "match_phrase": {
                                    "module_name": {
                                        "query": str(v),
                                        "slop": 2
                                    }
                                }
                            }
                            for v in value
                        ],
                        "minimum_should_match": 1
                    }
                }
            return {"terms": {field_name: value}}

        if value is not None:
            return {"term": {field_name: value}}

        return None

    def _fix_total_completions_query(self, query_payload: Dict[str, Any]):
        """
        Fix queries for "total completions" that incorrectly use cardinality on mid.
        "Total completions" should count ALL records, not unique modules.
        """
        query_body = query_payload.get('query', {})
        if not isinstance(query_body, dict):
            return
        
        aggs = query_body.get('aggs', {})
        if not isinstance(aggs, dict):
            return
        
        # Find any aggregations using cardinality on mid and replace with filter aggregation
        # (value_count on _id doesn't work in OpenSearch, use filter with match_all instead)
        for agg_name, agg_def in aggs.items():
            if isinstance(agg_def, dict):
                # Check if it's a cardinality on mid
                if 'cardinality' in agg_def:
                    cardinality_field = agg_def.get('cardinality', {}).get('field', '')
                    if cardinality_field == 'mid':
                        # Replace with filter aggregation to count all records (doc_count)
                        aggs[agg_name] = {"filter": {"match_all": {}}}
                        logger.info(f"Fixed total completions query: replaced cardinality on mid with filter aggregation (doc_count)")
    
    def _fix_person_name_query(self, query_payload: Dict[str, Any], person_name: str):
        """
        Fix queries that incorrectly use email_addr for person names.
        Replace email_addr matches with first_name/last_name matches.
        """
        query = query_payload.get('query', {})
        if not query:
            return
        
        def fix_in_dict(obj: Any, depth: int = 0) -> bool:
            """Recursively find and fix email_addr matches for person names."""
            if depth > 10:  # Prevent infinite recursion
                return False
            
            if isinstance(obj, dict):
                # Check if this is a match query on email_addr with a person name
                if 'match' in obj and isinstance(obj['match'], dict):
                    if 'email_addr' in obj['match']:
                        email_match = obj['match']['email_addr']
                        query_value = email_match.get('query', '') if isinstance(email_match, dict) else email_match
                        # If the query value doesn't contain @, it's likely a person name, not an email
                        if isinstance(query_value, str) and '@' not in query_value:
                            logger.info(f"Fixing person name query: replacing email_addr match with first_name/last_name for '{query_value}'")
                            # Replace with bool query matching first_name and last_name
                            obj.clear()
                            obj['bool'] = {
                                'should': [
                                    {'match': {'first_name': {'query': query_value, 'fuzziness': 'AUTO'}}},
                                    {'match': {'last_name': {'query': query_value, 'fuzziness': 'AUTO'}}}
                                ],
                                'minimum_should_match': 1
                            }
                            return True
                
                # Recursively check nested structures
                for key, value in obj.items():
                    if fix_in_dict(value, depth + 1):
                        return True
            
            elif isinstance(obj, list):
                for item in obj:
                    if fix_in_dict(item, depth + 1):
                        return True
            
            return False
        
        fix_in_dict(query)
    
    def _fix_latest_query(self, query_payload: Dict[str, Any], entities: Dict[str, Any]):
        """
        Fix "latest" / "most recent" queries to:
        1. Filter for completed_status = 1 (only completed modules)
        2. Sort by completed_date descending
        3. Limit to size: 1 (only the most recent)
        
        This ensures queries like "what is the latest module X has completed" 
        return only the most recent completed module, not all records.
        """
        query_body = query_payload.get('query', {})
        if not isinstance(query_body, dict):
            return
        
        # Ensure we have a bool query structure
        search_query = query_body.get('query', {})
        if not search_query:
            search_query = {'match_all': {}}
            query_body['query'] = search_query
        
        bool_query = self._ensure_bool_query(search_query)
        
        # CRITICAL: Ensure completed_status = 1 filter is applied
        # Remove any existing completed_status filters first
        self._remove_filter_clause(bool_query, 'completed_status')
        # Add completed_status = 1 filter
        self._append_filter_clause(bool_query, {'term': {'completed_status': 1}})
        
        # Set size to 1 to return only the latest record
        query_body['size'] = 1
        
        # Add sort by completed_date descending
        # Remove any existing sort first
        if 'sort' in query_body:
            existing_sorts = query_body['sort']
            # Remove any sort on completed_date
            if isinstance(existing_sorts, list):
                query_body['sort'] = [s for s in existing_sorts if not (isinstance(s, dict) and 'completed_date' in s)]
            elif isinstance(existing_sorts, dict) and 'completed_date' in existing_sorts:
                query_body.pop('sort')
        
        # Add sort by completed_date descending
        if 'sort' not in query_body:
            query_body['sort'] = []
        if not isinstance(query_body['sort'], list):
            query_body['sort'] = [query_body['sort']]
        
        # Prepend completed_date sort (most important)
        query_body['sort'].insert(0, {'completed_date': {'order': 'desc'}})
        
        logger.info("Fixed latest query: added completed_status=1 filter, sort by completed_date desc, size=1")
    
    def _fix_completion_rate_query(self, query_payload: Dict[str, Any], entities: Dict[str, Any]):
        """
        Fix completion rate queries:
        1. Remove invalid ordering by pipeline aggregation (completion_rate)
        2. Remove incorrect name/email filters for "completion" (it's a metric term, not a person name!)
        3. Ensure bucket_sort is used to sort by completion_rate
        4. Remove completed_status: 1 from top-level query (otherwise denominator becomes "completed only")
        """
        query_body = query_payload.get('query', {})
        if not isinstance(query_body, dict):
            return
        
        # CRITICAL: Remove completed_status: 1 from top-level query filters
        # assigned_status: 0 should stay in main query (defines denominator)
        # completed_status: 1 should ONLY be in the aggregation filter
        # Otherwise the "assigned" denominator becomes "completed only" and you'll get bogus 100% rates
        query = query_body.get('query', {})
        if isinstance(query, dict):
            bool_query = query.get('bool', {})
            if isinstance(bool_query, dict):
                self._remove_filter_clause(bool_query, 'completed_status')
                # Remove ratings filter - ratings should not be in completion rate queries
                self._remove_filter_clause(bool_query, 'ratings')
                logger.info("Removed completed_status and ratings from top-level query filters for completion rate query (assigned_status: 0 should stay in main query)")
        
        # CRITICAL: Remove any name/email filters that incorrectly match "completion"
        # "completion" is a METRIC TERM, not a person name!
        if isinstance(query, dict):
            bool_query = query.get('bool', {})
            if isinstance(bool_query, dict):
                for clause_type in ['filter', 'must', 'should']:
                    clauses = bool_query.get(clause_type, [])
                    if not isinstance(clauses, list):
                        continue
                    # Remove clauses that match "completion" in name/email fields
                    filtered_clauses = []
                    for clause in clauses:
                        if not isinstance(clause, dict):
                            filtered_clauses.append(clause)
                            continue
                        # Check for match clauses on first_name, last_name, or email_addr with "completion"
                        should_remove = False
                        if 'match' in clause:
                            match_clause = clause['match']
                            if isinstance(match_clause, dict):
                                for field, match_value in match_clause.items():
                                    if field in ['first_name', 'last_name', 'email_addr']:
                                        query_value = match_value.get('query', '') if isinstance(match_value, dict) else str(match_value)
                                        if 'completion' in str(query_value).lower():
                                            should_remove = True
                                            logger.warning(f"Removed incorrect name/email filter for 'completion' (metric term, not person name): {clause}")
                                            break
                        elif 'bool' in clause:
                            # Check nested bool queries
                            bool_sub = clause['bool']
                            if isinstance(bool_sub, dict):
                                for sub_clause_type in ['should', 'must', 'filter']:
                                    sub_clauses = bool_sub.get(sub_clause_type, [])
                                    if isinstance(sub_clauses, list):
                                        for sub_clause in sub_clauses:
                                            if isinstance(sub_clause, dict) and 'match' in sub_clause:
                                                match_clause = sub_clause['match']
                                                if isinstance(match_clause, dict):
                                                    for field, match_value in match_clause.items():
                                                        if field in ['first_name', 'last_name', 'email_addr']:
                                                            query_value = match_value.get('query', '') if isinstance(match_value, dict) else str(match_value)
                                                            if 'completion' in str(query_value).lower():
                                                                should_remove = True
                                                                logger.warning(f"Removed incorrect name/email filter for 'completion' (metric term, not person name): {clause}")
                                                                break
                        if not should_remove:
                            filtered_clauses.append(clause)
                    
                    if len(filtered_clauses) < len(clauses):
                        bool_query[clause_type] = filtered_clauses
                        logger.info(f"Removed {len(clauses) - len(filtered_clauses)} incorrect name/email filters from {clause_type}")
        
        aggs = query_body.get('aggs', {})
        if not isinstance(aggs, dict):
            return
        
        def fix_aggregation(agg_dict: Dict[str, Any]):
            """Recursively fix aggregations that order by pipeline aggregations."""
            if not isinstance(agg_dict, dict):
                return
            
            # Check if this is a terms aggregation with invalid ordering
            if 'terms' in agg_dict:
                terms_agg = agg_dict['terms']
                if isinstance(terms_agg, dict) and 'order' in terms_agg:
                    order = terms_agg['order']
                    # Check if ordering by completion_rate (pipeline aggregation)
                    if isinstance(order, dict) and 'completion_rate' in order:
                        logger.warning("Removing invalid order by completion_rate (pipeline aggregation cannot be used for ordering)")
                        # Remove the order clause - application will sort by completion_rate after query
                        terms_agg.pop('order')
                        logger.info("Removed invalid order clause. Results will be sorted by application layer.")
                    # Also check for other pipeline aggregations in order
                    elif isinstance(order, dict):
                        for key in list(order.keys()):
                            # Check if this aggregation exists and is a pipeline aggregation
                            sub_aggs = agg_dict.get('aggs', {})
                            if isinstance(sub_aggs, dict) and key in sub_aggs:
                                sub_agg = sub_aggs[key]
                                if isinstance(sub_agg, dict):
                                    # Check if it's a pipeline aggregation
                                    if any(pipeline_type in sub_agg for pipeline_type in ['bucket_script', 'bucket_selector', 'derivative', 'cumulative_sum']):
                                        logger.warning(f"Removing invalid order by {key} (pipeline aggregation cannot be used for ordering)")
                                        order.pop(key)
                                        if not order:
                                            terms_agg.pop('order')
                    # Also check if order is a string (simple case: {"order": "completion_rate"})
                    elif isinstance(order, str) and order == 'completion_rate':
                        logger.warning("Removing invalid order by completion_rate (pipeline aggregation cannot be used for ordering)")
                        terms_agg.pop('order')
            
            # Recursively check sub-aggregations
            sub_aggs = agg_dict.get('aggs', {})
            if isinstance(sub_aggs, dict):
                for sub_agg in sub_aggs.values():
                    fix_aggregation(sub_agg)
        
        def ensure_bucket_sort(agg_dict: Dict[str, Any]):
            """Ensure bucket_sort is present for completion_rate queries."""
            if not isinstance(agg_dict, dict):
                return
            
            # Check if this aggregation has completion_rate
            sub_aggs = agg_dict.get('aggs', {})
            if not isinstance(sub_aggs, dict):
                return
            
            # Check for any completion rate bucket_script aggregation
            has_completion_rate = any(
                name in sub_aggs and isinstance(sub_aggs[name], dict) and 'bucket_script' in sub_aggs[name]
                for name in ['completion_rate', 'completion_percentage', 'calc_completion_rate']
            )
            has_bucket_sort = 'sort_by_completion_rate' in sub_aggs or any(
                isinstance(sub_agg, dict) and 'bucket_sort' in sub_agg 
                for sub_agg in sub_aggs.values()
            )
            
            # If completion_rate exists but bucket_sort doesn't, add it
            if has_completion_rate and not has_bucket_sort:
                # Find the completion rate aggregation name
                completion_agg_name = None
                for name in ['completion_rate', 'completion_percentage', 'calc_completion_rate']:
                    if name in sub_aggs and isinstance(sub_aggs[name], dict) and 'bucket_script' in sub_aggs[name]:
                        completion_agg_name = name
                        break
                
                if completion_agg_name:
                    logger.info(f"Adding bucket_sort for {completion_agg_name} query")
                sub_aggs['sort_by_completion_rate'] = {
                    'bucket_sort': {
                        'sort': [
                                {completion_agg_name: {'order': 'desc'}}
                        ],
                        'size': 10
                    }
                }
            
            # Also ensure the aggregation structure is correct:
            # - assigned_users filter aggregation with "value" cardinality inside
            # - completed_users filter aggregation with "value" cardinality inside
            # - buckets_path should reference "completed_users>value" and "assigned_users>value"
            if has_completion_rate:
                # Check if we need to fix the aggregation structure
                # Find the completion rate aggregation (could be completion_rate, completion_percentage, etc.)
                completion_rate_agg = None
                for name in ['completion_rate', 'completion_percentage', 'calc_completion_rate']:
                    if name in sub_aggs:
                        completion_rate_agg = sub_aggs.get(name, {})
                        break
                if isinstance(completion_rate_agg, dict):
                    bucket_script = completion_rate_agg.get('bucket_script', {})
                    if isinstance(bucket_script, dict):
                        buckets_path = bucket_script.get('buckets_path', {})
                        # If buckets_path uses old structure, fix it
                        if isinstance(buckets_path, dict):
                            needs_fix = False
                            if 'completed' in buckets_path:
                                old_path = buckets_path['completed']
                                # Check if it's using old structure like "completed>completed_users"
                                if '>' in str(old_path) and not old_path.endswith('>value'):
                                    needs_fix = True
                                    buckets_path['completed'] = 'completed_users>value'
                                    logger.info("Fixed buckets_path for completed_users")
                            if 'assigned' in buckets_path:
                                old_path = buckets_path['assigned']
                                # Check if it's using old structure like "assigned>assigned_users"
                                if '>' in str(old_path) and not old_path.endswith('>value'):
                                    needs_fix = True
                                    buckets_path['assigned'] = 'assigned_users>value'
                                    logger.info("Fixed buckets_path for assigned_users")
                            
                            # Ensure the aggregation names match: assigned_users and completed_users
                            if 'assigned' in sub_aggs and 'assigned_users' not in sub_aggs:
                                # Rename "assigned" to "assigned_users"
                                sub_aggs['assigned_users'] = sub_aggs.pop('assigned')
                                logger.info("Renamed 'assigned' aggregation to 'assigned_users'")
                            if 'completed' in sub_aggs and 'completed_users' not in sub_aggs:
                                # Rename "completed" to "completed_users"
                                sub_aggs['completed_users'] = sub_aggs.pop('completed')
                                logger.info("Renamed 'completed' aggregation to 'completed_users'")
                            
                            # Ensure inside each filter aggregation, cardinality is named "value"
                            for agg_name in ['assigned_users', 'completed_users']:
                                if agg_name in sub_aggs:
                                    filter_agg = sub_aggs[agg_name]
                                    if isinstance(filter_agg, dict) and 'aggs' in filter_agg:
                                        inner_aggs = filter_agg['aggs']
                                        if isinstance(inner_aggs, dict):
                                            # Check if there's a cardinality aggregation that's not named "value"
                                            for inner_name, inner_agg in inner_aggs.items():
                                                if isinstance(inner_agg, dict) and 'cardinality' in inner_agg:
                                                    if inner_name != 'value':
                                                        # Rename to "value"
                                                        inner_aggs['value'] = inner_aggs.pop(inner_name)
                                                        logger.info(f"Renamed cardinality aggregation '{inner_name}' to 'value' in {agg_name}")
                                                        # Update buckets_path if needed
                                                        if isinstance(buckets_path, dict):
                                                            if agg_name == 'completed_users' and 'completed' in buckets_path:
                                                                old_path = buckets_path['completed']
                                                                if '>' in str(old_path):
                                                                    buckets_path['completed'] = 'completed_users>value'
                                                                    logger.info("Updated buckets_path for completed_users>value")
                                                            elif agg_name == 'assigned_users' and 'assigned' in buckets_path:
                                                                old_path = buckets_path['assigned']
                                                                if '>' in str(old_path):
                                                                    buckets_path['assigned'] = 'assigned_users>value'
                                                                    logger.info("Updated buckets_path for assigned_users>value")
        
        # Fix all aggregations
        for agg_name, agg_value in aggs.items():
            if isinstance(agg_value, dict):
                fix_aggregation(agg_value)
                ensure_bucket_sort(agg_value)
        
        logger.info("Fixed completion rate query: removed invalid ordering, removed name/email filters, ensured bucket_sort")
    
    def _is_module_completion_rate_request(self, user_message: str) -> bool:
        """Detect if user is asking for completion rate of a specific module."""
        if not user_message:
            return False
        import re
        message_lower = user_message.lower()
        # Check for patterns like "completion rate of X" or "what is the completion rate of X"
        patterns = [
            r'completion\s+rate\s+(?:of|for)',
            r'what\s+(?:is|was)\s+(?:the\s+)?completion\s+rate',
            r'completion\s+%',
            r'completion\s+percentage',
        ]
        has_completion_rate = any(re.search(pattern, message_lower) for pattern in patterns)
        # Also check if it mentions a module name (not just general completion rate)
        # Look for module name patterns or check if there's text after "of" or "for"
        has_module_mention = (
            any(word in message_lower for word in ['module', 'training', 'course']) or
            bool(re.search(r'completion\s+rate\s+(?:of|for)\s+["\']?([^"\']+?)["\']?', message_lower))
        )
        # Exclude if it's clearly about a user (has user/learner/person keywords)
        has_user_mention = any(word in message_lower for word in ['user', 'learner', 'person', 'employee', 'trainee'])
        return has_completion_rate and has_module_mention and not has_user_mention
    
    def _is_user_completion_rate_request(self, user_message: str) -> bool:
        """Detect if user is asking for completion rate of a specific user."""
        if not user_message:
            return False
        import re
        message_lower = user_message.lower()
        
        # Check for completion rate patterns
        completion_patterns = [
            r'completion\s+rate',
            r'completion\s+%',
            r'completion\s+percentage',
            r'what\s+(?:is|was)\s+(?:the\s+)?completion\s+rate',
        ]
        has_completion_rate = any(re.search(pattern, message_lower) for pattern in completion_patterns)
        
        # Check if there's a name/email mentioned BEFORE timeframe keywords
        # Patterns like: "tanuj in the last 3 months", "tanuj's completion rate", "completion rate of tanuj"
        timeframe_keywords = ['in the last', 'this month', 'last month', 'this quarter', 'last quarter', 'this year', 'last year', 'in the past']
        has_timeframe = any(keyword in message_lower for keyword in timeframe_keywords)
        
        # Extract potential user name - look for word(s) before timeframe or completion keywords
        name_patterns = [
            r'\b([a-z]{2,30}(?:\s+[a-z]{2,30}){0,3})\s+(?:in\s+the\s+(?:last|this)|completion\s+rate)',  # "tanuj in the last" or "tanuj completion rate"
            r'\b([a-z]{2,30}(?:\s+[a-z]{2,30}){0,3})\'?s?\s+completion\s+rate',  # "Tanuj's completion rate"
            r'completion\s+rate\s+(?:of|for)\s+["\']?([^"\'\s]+(?:\s+[^"\'\s]+)*)["\']?',  # "completion rate of Tanuj"
            r'what\s+(?:is|was)\s+(?:the\s+)?completion\s+rate\s+(?:of|for)\s+["\']?([^"\'\s]+(?:\s+[^"\'\s]+)*)["\']?',  # "what is the completion rate of Tanuj"
            r'what\s+(?:is|was)\s+([a-z]+(?:\s+[a-z]+)?)\'?s?\s+completion\s+rate',  # "what is Tanuj's completion rate"
        ]
        has_name_mention = False
        for pattern in name_patterns:
            match = re.search(pattern, message_lower)
            if match:
                potential_name = match.group(1).strip()
                # Validate it's not a metric word
                metric_words = ['com', 'completion', 'rate', 'completions', 'percent', 'percentage']
                if potential_name.lower() not in metric_words and len(potential_name) > 2:
                    has_name_mention = True
                    break
        
        # Check if it mentions a user/learner/person (not a module)
        has_user_mention = any(word in message_lower for word in ['user', 'learner', 'person', 'employee', 'trainee', "'s", "s'", "'s completion"])
        
        # Check if it mentions module/training/course
        has_module_mention = any(word in message_lower for word in ['module', 'training', 'course'])
        
        # CRITICAL: If there's a person name detected, prioritize it over module mention
        # "module completion rate of tanuj" should be detected as USER completion rate, not module
        # Person names take precedence - if we found a name, it's a user query
        if has_name_mention and has_completion_rate:
            # Even if "module" is mentioned, if there's a person name, it's a user query
            logger.info(f"Detected person name in completion rate query - treating as USER completion rate (name: {potential_name if 'potential_name' in locals() else 'unknown'})")
            return True
        
        # If it has completion rate OR (name + timeframe), and a name/user mention, and no module mention, it's a user query
        return (has_completion_rate or (has_name_mention and has_timeframe)) and (has_user_mention or has_name_mention) and not has_module_mention
    
    def _lookup_user_uid(self, user_identifier: str, index_id: str) -> Optional[int]:
        """
        SIMPLE: Match input (email, first name, or full name) to UID.
        Returns the uid if found, None otherwise.
        """
        if not user_identifier or not index_id:
            return None
        
        try:
            # Clean up the identifier
            user_identifier = user_identifier.strip()
            # Remove possessive markers
            user_identifier = re.sub(r"'s\s*$", "", user_identifier, flags=re.IGNORECASE)
            user_identifier = user_identifier.strip()
            
            # Use OpenSearchClient directly for simpler execution
            os_client = self.os_client
            
            # Determine if it's an email or name
            is_email = '@' in user_identifier
            
            if is_email:
                # EMAIL: Simple direct match on email_addr field (case-insensitive)
                email_lower = user_identifier.lower().strip()
                lookup_query = {
                    "size": 1,
                    "query": {
                        "bool": {
                            "filter": [
                                {"term": {"user_status": 5}},
                                {"term": {"email_addr": email_lower}}  # CRITICAL: Use term query for exact match on email_addr
                            ]
                        }
                    },
                    "_source": ["uid", "email_addr", "first_name", "last_name"]
                }
                logger.info(f"Looking up UID by email: {email_lower}")
            else:
                # NAME: Split into parts
                name_parts = user_identifier.split()
                if len(name_parts) >= 2:
                    # FULL NAME: first_name AND last_name
                    first_name = name_parts[0].strip()
                    last_name = ' '.join(name_parts[1:]).strip()
                    lookup_query = {
                        "size": 10,
                        "query": {
                            "bool": {
                                "filter": [
                                    {"term": {"user_status": 5}}
                                ],
                                "must": [
                                    {"match": {"first_name": {"query": first_name, "operator": "and"}}},
                                    {"match": {"last_name": {"query": last_name, "operator": "and"}}}
                                ]
                            }
                        },
                        "_source": ["uid", "first_name", "last_name", "email_addr"]
                    }
                else:
                    # SINGLE NAME: first_name OR last_name
                    single_name = name_parts[0].strip() if name_parts else user_identifier
                    lookup_query = {
                        "size": 10,
                        "query": {
                            "bool": {
                                "filter": [
                                    {"term": {"user_status": 5}}
                                ],
                                "should": [
                                    {"match": {"first_name": {"query": single_name, "operator": "and"}}},
                                    {"match": {"last_name": {"query": single_name, "operator": "and"}}}
                                ],
                                "minimum_should_match": 1
                            }
                        },
                        "_source": ["uid", "first_name", "last_name", "email_addr"]
                    }
            
            # Execute lookup query directly via OpenSearchClient
            lookup_result = os_client.execute_query(index_id=index_id, query=lookup_query, size=10)
            
            if lookup_result.get('success') and lookup_result.get('results'):
                results = lookup_result['results']
                
                # For email: get uid directly from first result (query already filtered by exact email)
                if is_email:
                    if results:
                        uid = results[0].get('uid')
                        if uid:
                            result_email = results[0].get('email_addr', '')
                            logger.info(f"Found uid {uid} for email '{user_identifier}' (matched: {result_email})")
                            return int(uid)
                    logger.warning(f"No user found for email '{user_identifier}'")
                    return None
                else:
                    # For names: use first result (already filtered by query)
                    if results:
                        uid = results[0].get('uid')
                        if uid:
                            name = f"{results[0].get('first_name', '')} {results[0].get('last_name', '')}".strip()
                            logger.info(f"Found uid {uid} for name '{user_identifier}' (matched: {name})")
                            if len(results) > 1:
                                logger.warning(f"Multiple matches for '{user_identifier}': {len(results)} results. Using first: uid={uid}")
                            return int(uid)
            
            logger.warning(f"Could not find uid for user identifier '{user_identifier}'")
            return None
            
        except Exception as e:
            logger.error(f"Error looking up user uid for '{user_identifier}': {str(e)}", exc_info=True)
            return None
    
    def _force_module_completion_rate_query_structure(self, query_payload: Dict[str, Any], user_message: str):
        """
        Force the exact query structure for module completion rate queries.
        This ensures the query uses the exact working pattern.
        """
        query_body = query_payload.get('query', {})
        if not isinstance(query_body, dict):
            return
        
        # Extract module name from existing query or user message
        module_name = None
        query = query_body.get('query', {})
        if isinstance(query, dict):
            bool_query = query.get('bool', {})
            if isinstance(bool_query, dict):
                filter_clauses = bool_query.get('filter', [])
                for clause in filter_clauses:
                    if isinstance(clause, dict) and 'match_phrase' in clause:
                        match_phrase = clause['match_phrase']
                        if isinstance(match_phrase, dict) and 'module_name' in match_phrase:
                            module_name_clause = match_phrase['module_name']
                            if isinstance(module_name_clause, dict):
                                module_name = module_name_clause.get('query')
        
        # If no module_name found in query, try to extract from user message
        if not module_name and user_message:
            # Simple extraction - look for quoted text or text after "of" or "for"
            import re
            # Look for patterns like "completion rate of X" or "completion rate for X"
            patterns = [
                r'completion\s+rate\s+(?:of|for)\s+["\']?([^"\']+)["\']?',
                r'completion\s+rate\s+["\']?([^"\']+)["\']?',
            ]
            for pattern in patterns:
                match = re.search(pattern, user_message, re.IGNORECASE)
                if match:
                    module_name = match.group(1).strip()
                    # Remove trailing punctuation
                    module_name = re.sub(r'[?.!]+$', '', module_name)
                    break
        
        # Build the exact query structure
        query_body['size'] = 0
        query_body['query'] = {
            "bool": {
                "filter": [
                    {"term": {"user_status": 5}},
                    {"term": {"assigned_status": 0}},
                    {"term": {"module_status": 0}},
                    {"term": {"cmid": 1}}
                ],
                "should": [],
                "must_not": []
            }
        }
        
        # CRITICAL: Remove completed_status and ratings from filters if they exist
        # These should NOT be in the main query for completion rate calculations
        # This must happen AFTER building the structure to ensure clean query
        bool_query = query_body['query']['bool']
        filter_list = bool_query.get('filter', [])
        if isinstance(filter_list, list):
            # Remove completed_status and ratings filters
            cleaned_filters = []
            for f in filter_list:
                should_remove = False
                if isinstance(f, dict):
                    # Check for completed_status term filter
                    if 'term' in f and isinstance(f['term'], dict) and 'completed_status' in f['term']:
                        should_remove = True
                        logger.info("Removed completed_status filter from completion rate query (should only be in aggregation)")
                    # Check for ratings range filter
                    elif 'range' in f and isinstance(f['range'], dict) and 'ratings' in f['range']:
                        should_remove = True
                        logger.info("Removed ratings filter from completion rate query")
                    # Check for ratings term filter
                    elif 'term' in f and isinstance(f['term'], dict) and 'ratings' in f['term']:
                        should_remove = True
                        logger.info("Removed ratings filter from completion rate query")
                if not should_remove:
                    cleaned_filters.append(f)
            bool_query['filter'] = cleaned_filters
            if len(cleaned_filters) < len(filter_list):
                logger.info(f"Removed {len(filter_list) - len(cleaned_filters)} invalid filters from completion rate query")
        
        # Add module_name filter if we found it
        if module_name:
            query_body['query']['bool']['filter'].append({
                "match_phrase": {
                    "module_name": {
                        "query": module_name,
                        "slop": 2
                    }
                }
            })
        
        # Force the exact aggregation structure
        query_body['aggs'] = {
            "scope": {
                "filters": {
                    "filters": {
                        "all": {"match_all": {}}
                    }
                },
                "aggs": {
                    "assigned": {
                        "value_count": {"field": "uid"}
                    },
                    "completed": {
                        "filter": {"term": {"completed_status": 1}},
                        "aggs": {
                            "completed_count": {
                                "value_count": {"field": "uid"}
                            }
                        }
                    },
                    "completion_rate": {
                        "bucket_script": {
                            "buckets_path": {
                                "completed": "completed>completed_count",
                                "assigned": "assigned"
                            },
                            "script": "params.assigned > 0 ? (params.completed / params.assigned) * 100 : 0"
                        }
                    }
                }
            }
        }
        
        logger.info(f"Forced exact module completion rate query structure (module_name: {module_name})")
    
    def _force_user_completion_rate_query_structure(self, query_payload: Dict[str, Any], user_message: str, index_id: str, time_period=None):
        """
        Force the exact query structure for user completion rate queries.
        This ensures the query uses the exact working pattern.
        If time_period is provided, applies it to the completed filter inside the aggregation.
        """
        query_body = query_payload.get('query', {})
        if not isinstance(query_body, dict):
            return
        
        # CRITICAL: Extract user identifier from user message FIRST
        # Do NOT trust the query - LLM often puts user names in module_name by mistake
        # We extract from message, map to UID, then rebuild query completely
        user_identifier = None
        if user_message:
            import re
            message_lower = user_message.lower()
            
            # CRITICAL: Extract user name BEFORE timeframe phrases
            # Patterns to extract user name - handle cases like:
            # - "tanuj completion rate"
            # - "tanuj's completion rate"  
            # - "completion rate of tanuj"
            # - "tanuj in the last 3 months" (completion rate implied)
            # - "what is tanuj's completion rate in the last 3 months"
            
            # First, try to find a name/email at the start or before completion/timeframe keywords
            # Extract text before "completion rate", "in the last", "this month", etc.
            timeframe_keywords = ['in the last', 'this month', 'last month', 'this quarter', 'last quarter', 'this year', 'last year', 'completion rate', 'completion']
            
            # Find the earliest occurrence of any timeframe/completion keyword
            earliest_keyword_pos = len(user_message)
            for keyword in timeframe_keywords:
                pos = message_lower.find(keyword)
                if pos != -1 and pos < earliest_keyword_pos:
                    earliest_keyword_pos = pos
            
            # Extract potential user identifier before the keyword
            if earliest_keyword_pos < len(user_message):
                before_keyword = user_message[:earliest_keyword_pos].strip()
                # Remove leading question words
                before_keyword = re.sub(r'^(?:what|is|was|the|a|an|for|of|show|get|give)\s+', '', before_keyword, flags=re.IGNORECASE)
                before_keyword = before_keyword.strip()
                
                # Look for name patterns in the text before the keyword
                # Pattern: word(s) that could be a name (2-30 chars, letters/spaces/hyphens)
                if before_keyword:
                    # Try to extract a name - could be single word or multiple words
                    # Match: word(s) that are likely names (not common words)
                    name_match = re.search(r'\b([a-z]{2,30}(?:[\s-][a-z]{2,30}){0,3})\b', before_keyword, re.IGNORECASE)
                    if name_match:
                        potential_name = name_match.group(1).strip()
                        # Remove trailing punctuation
                        potential_name = re.sub(r'[?.!,;]+$', '', potential_name)
                        potential_name = potential_name.strip()
                        
                        # Check if it's a valid name/email
                        if potential_name and len(potential_name) >= 2:
                            metric_words = ['com', 'completion', 'rate', 'completions', 'percent', 'percentage', 'the', 'a', 'an']
                            if potential_name.lower() not in metric_words:
                                # Check if it looks like an email or name
                                if '@' in potential_name:
                                    user_identifier = potential_name
                                    logger.info(f"Extracted email identifier '{user_identifier}' from message before timeframe keyword")
                                elif potential_name.replace(' ', '').replace('-', '').isalpha() and len(potential_name.split()) <= 5:
                                    user_identifier = potential_name
                                    logger.info(f"Extracted name identifier '{user_identifier}' from message before timeframe keyword")
            
            # If still no identifier, try standard patterns
            if not user_identifier:
                patterns = [
                    r'\b([a-z]{2,30}(?:\s+[a-z]{2,30}){0,3})\'?s?\s+completion\s+rate\b',  # "Tanuj's completion rate" or "Tanuj Sadasivam's completion rate"
                    r'completion\s+rate\s+(?:of|for)\s+["\']?([^"\'\s]+(?:\s+[^"\'\s]+)*)["\']?',  # "completion rate of Tanuj"
                    r'what\s+(?:is|was)\s+(?:the\s+)?completion\s+rate\s+(?:of|for)\s+["\']?([^"\'\s]+(?:\s+[^"\'\s]+)*)["\']?',  # "what is the completion rate of Tanuj"
                    r'what\s+(?:is|was)\s+\b([a-z]{2,30}(?:\s+[a-z]{2,30}){0,3})\'?s?\s+completion\s+rate\b',  # "what is Tanuj's completion rate"
                    # Pattern for "X in the last Y" - extract X (MUST be before timeframe)
                    r'\b([a-z]{2,30}(?:\s+[a-z]{2,30}){0,3})\s+in\s+the\s+(?:last|this)',  # "tanuj in the last 3 months"
                    # Pattern for just a name at the start (simple case like "tanuj")
                    r'^(?:what\s+(?:is|was)\s+)?([a-z]{2,30}(?:\s+[a-z]{2,30}){0,3})(?:\s|$)',  # "tanuj" or "what is tanuj"
                ]
                for pattern in patterns:
                    match = re.search(pattern, user_message, re.IGNORECASE)
                    if match:
                        potential = match.group(1).strip()
                        # Remove trailing punctuation
                        potential = re.sub(r'[?.!,;]+$', '', potential)
                        # Remove common words
                        potential = re.sub(r'^(?:the|a|an|what|is|was|for|of)\s+', '', potential, flags=re.IGNORECASE)
                        # Remove possessive markers if any
                        potential = re.sub(r"'s\s*$", "", potential, flags=re.IGNORECASE)
                        potential = potential.strip()
                        
                        # CRITICAL: Reject short identifiers and metric words
                        metric_words = ['com', 'completion', 'rate', 'completions', 'percent', 'percentage', 'the', 'a', 'an', 'what', 'is', 'was']
                        if potential and len(potential) >= 2 and potential.lower() not in metric_words:
                            # Check if it looks like an email (has @) or a real name
                            if '@' in potential:
                                user_identifier = potential
                                logger.info(f"Extracted user identifier '{user_identifier}' using pattern matching (email)")
                                break
                            elif potential.replace(' ', '').replace('-', '').isalpha() and len(potential.split()) <= 5:
                                user_identifier = potential
                                logger.info(f"Extracted user identifier '{user_identifier}' using pattern matching (name)")
                                break
        
        # FALLBACK: If still no identifier, check if LLM incorrectly put it in module_name
        # This is a LAST RESORT - we extract it, then immediately remove module_name and use UID
        if not user_identifier:
            query = query_body.get('query', {})
            if isinstance(query, dict):
                bool_query = query.get('bool', {})
                if isinstance(bool_query, dict):
                    filter_clauses = bool_query.get('filter', [])
                    for clause in filter_clauses:
                        if isinstance(clause, dict):
                            # Check for module_name match_phrase (LLM often puts user names here)
                            if 'match_phrase' in clause and isinstance(clause['match_phrase'], dict):
                                if 'module_name' in clause['match_phrase']:
                                    module_name_clause = clause['match_phrase']['module_name']
                                    if isinstance(module_name_clause, dict):
                                        potential_name = module_name_clause.get('query', '')
                                    else:
                                        potential_name = str(module_name_clause)
                                    
                                    if potential_name:
                                        # Validate it's likely a person name, not a module name
                                        import re
                                        module_keywords = ['module', 'training', 'course', 'leadership', 'management', 'situational', 'program', 'workshop']
                                        potential_lower = potential_name.lower()
                                        
                                        # If it doesn't contain module keywords and looks like a name, use it
                                        if (not any(kw in potential_lower for kw in module_keywords) and 
                                            len(potential_name) >= 2 and len(potential_name) <= 30 and
                                            (potential_name.replace(' ', '').replace('-', '').isalpha() or '@' in potential_name)):
                                            user_identifier = potential_name.strip()
                                            logger.warning(f"FALLBACK: Found user name '{user_identifier}' incorrectly placed in module_name filter - extracting it")
                                            break
                            
                            # Check for module_name match query
                            elif 'match' in clause and isinstance(clause['match'], dict):
                                if 'module_name' in clause['match']:
                                    module_name_clause = clause['match']['module_name']
                                    if isinstance(module_name_clause, dict):
                                        potential_name = module_name_clause.get('query', '')
                                    else:
                                        potential_name = str(module_name_clause)
                                    
                                    if potential_name:
                                        import re
                                        module_keywords = ['module', 'training', 'course', 'leadership', 'management', 'situational', 'program', 'workshop']
                                        potential_lower = potential_name.lower()
                                        
                                        if (not any(kw in potential_lower for kw in module_keywords) and 
                                            len(potential_name) >= 2 and len(potential_name) <= 30 and
                                            (potential_name.replace(' ', '').replace('-', '').isalpha() or '@' in potential_name)):
                                            user_identifier = potential_name.strip()
                                            logger.warning(f"FALLBACK: Found user name '{user_identifier}' incorrectly placed in module_name match filter - extracting it")
                                            break
        
        # Look up the user's uid
        uid = None
        if user_identifier:
            logger.info(f"Looking up UID for user identifier: '{user_identifier}'")
            uid = self._lookup_user_uid(user_identifier, index_id)
            if uid:
                logger.info(f"Successfully found UID {uid} for user identifier '{user_identifier}'")
            else:
                logger.error(f"Could not find UID for user identifier '{user_identifier}' - tried email_addr, first_name, last_name fields")
        else:
            logger.error("No user identifier extracted from message - cannot build user completion rate query")
        
        if not uid:
            logger.error(f"Could not find uid for user '{user_identifier}', cannot build user completion rate query")
            return
        
        # CRITICAL: Completely rebuild the query structure from scratch
        # Do NOT modify existing query - replace it entirely to avoid any module_name contamination
        query_body['size'] = 0
        query_body['query'] = {
            "bool": {
                "filter": [
                    {"term": {"user_status": 5}},
                    {"term": {"module_status": 0}},  # CRITICAL: Always include for assigned modules
                    {"term": {"assigned_status": 0}},
                    {"term": {"cmid": 1}},
                    {"term": {"uid": uid}}  # CRITICAL: Use UID, NOT module_name or name fields
                ],
                "should": [],
                "must_not": []
            }
        }
        
        logger.info(f"Completely rebuilt query structure with UID filter (uid: {uid}) - removed ALL old filters including any module_name filters")
        
        # Extract completed_date range from original query if it exists
        completed_date_range = None
        original_query = query_body.get('query', {})
        if isinstance(original_query, dict):
            original_bool = original_query.get('bool', {})
            if isinstance(original_bool, dict):
                original_filters = original_bool.get('filter', [])
                for f in original_filters:
                    if isinstance(f, dict) and 'range' in f:
                        range_clause = f.get('range', {})
                        if isinstance(range_clause, dict) and 'completed_date' in range_clause:
                            completed_date_range = range_clause['completed_date']
                            logger.info(f"Extracted completed_date range from original query: {completed_date_range}")
                            break
        
        # Build completed filter - include completed_status and optionally completed_date range
        completed_filter = {"term": {"completed_status": 1}}
        
        # If time_period is provided and not lifetime, add completed_date range
        if time_period and not time_period.is_lifetime:
            # Check if we should use date math (for relative timeframes)
            from dashboarding.services.time_handler import TimeframeResolver
            timeframe_key = TimeframeResolver.extract_timeframe_key(user_message)
            date_math = None
            if timeframe_key:
                date_math = TimeframeResolver._get_date_math_expressions(timeframe_key)
            
            if date_math:
                # Use date math expressions
                completed_filter = {
                    "bool": {
                        "must": [
                            {"term": {"completed_status": 1}},
                            {
                                "range": {
                                    "completed_date": {
                                        "gte": date_math["gte"],
                                        "lt": date_math["lt"]
                                    }
                                }
                            }
                        ]
                    }
                }
                logger.info(f"Applied date math range to completed filter: {date_math}")
            else:
                # Use epoch timestamps
                end_key = "lte" if getattr(time_period, "end_inclusive", False) else "lt"
                completed_filter = {
                    "bool": {
                        "must": [
                            {"term": {"completed_status": 1}},
                            {
                                "range": {
                                    "completed_date": {
                                        "gte": time_period.start_timestamp,
                                        end_key: time_period.end_timestamp
                                    }
                                }
                            }
                        ]
                    }
                }
                logger.info(f"Applied time_period range to completed filter: {time_period.start_timestamp} to {time_period.end_timestamp}")
        elif completed_date_range:
            # Use range extracted from original query
            completed_filter = {
                "bool": {
                    "must": [
                        {"term": {"completed_status": 1}},
                        {"range": {"completed_date": completed_date_range}}
                    ]
                }
            }
            logger.info(f"Applied extracted completed_date range to completed filter: {completed_date_range}")
        
        # Force the exact aggregation structure for user completion rate
        query_body['aggs'] = {
            "scope": {
                "filters": {
                    "filters": {
                        "all": {"match_all": {}}
                    }
                },
                "aggs": {
                    "assigned_modules": {
                        "cardinality": {"field": "mid"}
                    },
                    "completed": {
                        "filter": completed_filter,
                        "aggs": {
                            "completed_modules": {
                                "cardinality": {"field": "mid"}
                            }
                        }
                    },
                    "completion_rate": {
                        "bucket_script": {
                            "buckets_path": {
                                "assigned": "assigned_modules",
                                "completed": "completed>completed_modules"
                            },
                            "script": "params.assigned == 0 ? 0 : (params.completed * 100.0 / params.assigned)"
                        }
                    }
                }
            }
        }
        
        logger.info(f"Forced exact user completion rate query structure (uid: {uid}, user_identifier: {user_identifier})")
    
    def _is_single_module_completion_rate_query(self, query_payload: Dict[str, Any]) -> bool:
        """Detect if the query matches the single-module completion rate KPI structure."""
        query_body = (query_payload or {}).get('query')
        if not isinstance(query_body, dict):
            return False
        aggs = query_body.get('aggs')
        if not isinstance(aggs, dict):
            return False
        
        # Check for new pattern: scope (filters aggregation) with assigned, completed, and completion_rate
        scope = aggs.get('scope', {})
        if isinstance(scope, dict):
            scope_aggs = scope.get('aggs', {})
            if isinstance(scope_aggs, dict):
                # Check for assigned (value_count), completed (filter aggregation), and completion_rate bucket_script
                has_assigned = 'assigned' in scope_aggs and isinstance(scope_aggs.get('assigned'), dict) and 'value_count' in scope_aggs.get('assigned', {})
                has_completed = 'completed' in scope_aggs and isinstance(scope_aggs.get('completed'), dict) and 'filter' in scope_aggs.get('completed', {})
                has_completion_agg = any(
                    name in scope_aggs and isinstance(scope_aggs[name], dict) and 'bucket_script' in scope_aggs[name]
                    for name in ['completion_rate', 'completion_percentage', 'calc_completion_rate']
                )
                if has_assigned and has_completed and has_completion_agg:
                    return True
        
        # Also check old patterns for backward compatibility
        # Old pattern: assigned_scope with nested completed_scope
        assigned_scope = aggs.get('assigned_scope', {})
        if isinstance(assigned_scope, dict):
            assigned_aggs = assigned_scope.get('aggs', {})
            if isinstance(assigned_aggs, dict):
                has_completed_scope = 'completed_scope' in assigned_aggs
                has_completion_agg = any(
                    name in assigned_aggs and isinstance(assigned_aggs[name], dict) and 'bucket_script' in assigned_aggs[name]
                    for name in ['completion_rate', 'completion_percentage', 'calc_completion_rate']
                )
                if has_completed_scope and has_completion_agg:
                    return True
        
        # Very old pattern: completion_scope wrapper
        aggs_inner = self._get_completion_rate_inner_aggs(aggs)
        if isinstance(aggs_inner, dict):
            has_completion_agg = any(
                name in aggs_inner and isinstance(aggs_inner[name], dict) and 'bucket_script' in aggs_inner[name]
                for name in ['completion_rate', 'completion_percentage', 'calc_completion_rate']
            )
            required_aggs = {'assigned_users', 'completed_bucket'}
            if required_aggs.issubset(aggs_inner.keys()) and has_completion_agg:
                return True
        
        return False
    
    def _is_single_user_completion_rate_query(self, query_payload: Dict[str, Any]) -> bool:
        """Detect if the query matches the single-user completion rate KPI structure."""
        query_body = (query_payload or {}).get('query')
        if not isinstance(query_body, dict):
            return False
        aggs = query_body.get('aggs')
        if not isinstance(aggs, dict):
            return False
        
        # Check for the pattern used by _force_user_completion_rate_query_structure:
        # scope (filters aggregation) with assigned_modules (cardinality), completed (filter with completed_modules), and completion_rate
        scope = aggs.get('scope', {})
        if isinstance(scope, dict):
            scope_aggs = scope.get('aggs', {})
            if isinstance(scope_aggs, dict):
                has_assigned_modules = 'assigned_modules' in scope_aggs
                has_completed = 'completed' in scope_aggs
                has_completion_rate = 'completion_rate' in scope_aggs
                if has_assigned_modules and has_completed and has_completion_rate:
                    # Verify the structure matches
                    assigned_modules_agg = scope_aggs.get('assigned_modules', {})
                    completed_agg = scope_aggs.get('completed', {})
                    completion_rate_agg = scope_aggs.get('completion_rate', {})
                    if (isinstance(assigned_modules_agg, dict) and 'cardinality' in assigned_modules_agg and
                        isinstance(completed_agg, dict) and 'filter' in completed_agg and
                        isinstance(completion_rate_agg, dict) and 'bucket_script' in completion_rate_agg):
                        return True
        
        # Check for old pattern: completion_scope (filters aggregation) with total_assigned, completion_stats, and completion_rate
        completion_scope = aggs.get('completion_scope', {})
        if isinstance(completion_scope, dict):
            scope_aggs = completion_scope.get('aggs', {})
            if isinstance(scope_aggs, dict):
                has_total_assigned = 'total_assigned' in scope_aggs
                has_completion_stats = 'completion_stats' in scope_aggs
                has_completion_agg = any(
                    name in scope_aggs and isinstance(scope_aggs[name], dict) and 'bucket_script' in scope_aggs[name]
                    for name in ['completion_rate', 'completion_percentage', 'calc_completion_rate']
                )
                if has_total_assigned and has_completion_stats and has_completion_agg:
                    return True
        
        # Also check old patterns for backward compatibility
        # Old pattern: assigned_scope with nested completed_scope
        assigned_scope = aggs.get('assigned_scope', {})
        if isinstance(assigned_scope, dict):
            assigned_aggs = assigned_scope.get('aggs', {})
            if isinstance(assigned_aggs, dict):
                has_completed_scope = 'completed_scope' in assigned_aggs
                has_completion_agg = any(
                    name in assigned_aggs and isinstance(assigned_aggs[name], dict) and 'bucket_script' in assigned_aggs[name]
                    for name in ['completion_rate', 'completion_percentage', 'calc_completion_rate']
                )
                if has_completed_scope and has_completion_agg:
                    return True
        
        # Very old pattern: completion_scope wrapper
        aggs_inner = self._get_completion_rate_inner_aggs(aggs)
        if isinstance(aggs_inner, dict):
            has_assigned = 'assigned_docs' in aggs_inner or 'assigned_modules' in aggs_inner
            required_aggs = {'completed_bucket', 'completion_rate'}
            if required_aggs.issubset(aggs_inner.keys()) and has_assigned:
                assigned_docs = aggs_inner.get('assigned_docs', {})
                assigned_modules = aggs_inner.get('assigned_modules', {})
                has_valid_assigned = (
                    (isinstance(assigned_docs, dict) and isinstance(assigned_docs.get('value_count'), dict)) or
                    (isinstance(assigned_modules, dict) and isinstance(assigned_modules.get('cardinality'), dict))
                )
                if has_valid_assigned:
                    completed_bucket = aggs_inner.get('completed_bucket', {})
                    if isinstance(completed_bucket, dict):
                        return True
        
        return False
    
    def _add_time_range_to_completed_bucket(
        self,
        query_payload: Dict[str, Any],
        time_period: TimePeriod,
        date_field: str = 'completed_date'
    ):
        """
        Inject completed_date range filter inside completed aggregation for completion rate KPIs.
        This keeps the numerator time-bounded without limiting assigned counts.
        """
        if not isinstance(query_payload, dict):
            return
        query_body = query_payload.get('query')
        if not isinstance(query_body, dict):
            return
        aggs = query_body.get('aggs')
        if not isinstance(aggs, dict):
            return
        
        # Check for new pattern: scope (filters aggregation) with completed filter aggregation
        scope = aggs.get('scope', {})
        if isinstance(scope, dict):
            scope_aggs = scope.get('aggs', {})
            if isinstance(scope_aggs, dict):
                completed = scope_aggs.get('completed', {})
                if isinstance(completed, dict) and 'filter' in completed:
                    filter_clause = completed.get('filter', {})
                    if not isinstance(filter_clause, dict):
                        filter_clause = completed['filter'] = {}
                    
                    must_clauses = self._ensure_filter_bool_must(filter_clause)
                    if must_clauses is not None:
                        end_key = "lte" if getattr(time_period, "end_inclusive", False) else "lt"
                        range_clause = {
                            "range": {
                                date_field: {
                                    "gte": time_period.start_timestamp,
                                    end_key: time_period.end_timestamp
                                }
                            }
                        }
                        
                        if not self._filter_exists(must_clauses, range_clause):
                            must_clauses.append(range_clause)
                            logger.info("Added completed_date range inside scope.completed filter for completion rate KPI query")
                        return
        
        # Check for old pattern: assigned_scope > completed_scope
        assigned_scope = aggs.get('assigned_scope', {})
        if isinstance(assigned_scope, dict):
            assigned_aggs = assigned_scope.get('aggs', {})
            if isinstance(assigned_aggs, dict):
                completed_scope = assigned_aggs.get('completed_scope', {})
                if isinstance(completed_scope, dict):
                    filter_clause = completed_scope.setdefault('filter', {})
                    if not isinstance(filter_clause, dict):
                        filter_clause = completed_scope['filter'] = {}
                    
                    must_clauses = self._ensure_filter_bool_must(filter_clause)
                    if must_clauses is not None:
                        end_key = "lte" if getattr(time_period, "end_inclusive", False) else "lt"
                        range_clause = {
                            "range": {
                                date_field: {
                                    "gte": time_period.start_timestamp,
                                    end_key: time_period.end_timestamp
                                }
                            }
                        }
                        
                        if not self._filter_exists(must_clauses, range_clause):
                            must_clauses.append(range_clause)
                            logger.info("Added completed_date range inside completed_scope for completion rate KPI query")
                        return
        
        # Fall back to very old pattern: completion_scope or direct completed_bucket
        aggs_inner = self._get_completion_rate_inner_aggs(aggs)
        if not isinstance(aggs_inner, dict):
            return
        completed_bucket = aggs_inner.get('completed_bucket')
        if not isinstance(completed_bucket, dict):
            return
        
        filter_clause = completed_bucket.setdefault('filter', {})
        if not isinstance(filter_clause, dict):
            filter_clause = completed_bucket['filter'] = {}
        
        must_clauses = self._ensure_filter_bool_must(filter_clause)
        if must_clauses is None:
            return
        
        end_key = "lte" if getattr(time_period, "end_inclusive", False) else "lt"
        range_clause = {
            "range": {
                date_field: {
                    "gte": time_period.start_timestamp,
                    end_key: time_period.end_timestamp
                }
            }
        }
        
        if not self._filter_exists(must_clauses, range_clause):
            must_clauses.append(range_clause)
            logger.info("Added completed_date range inside completed_bucket for completion rate KPI query")
    
    def _ensure_filter_bool_must(self, filter_clause: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        """
        Ensure the provided filter clause is expressed as a bool with a mutable must array.
        Returns the must array for easy appending.
        """
        if not isinstance(filter_clause, dict):
            return None
        
        bool_clause = filter_clause.get('bool')
        if not isinstance(bool_clause, dict):
            # Convert existing filter into bool->must structure
            existing_clause = copy.deepcopy(filter_clause) if filter_clause else {}
            filter_clause.clear()
            bool_clause = {'must': []}
            filter_clause['bool'] = bool_clause
            if existing_clause and existing_clause not in ({}, {'bool': {}}):
                bool_clause['must'].append(existing_clause)
        must_clauses = bool_clause.get('must')
        if must_clauses is None:
            must_clauses = []
            bool_clause['must'] = must_clauses
        elif not isinstance(must_clauses, list):
            must_clauses = [must_clauses] if must_clauses else []
            bool_clause['must'] = must_clauses
        return must_clauses
    
    def _ensure_completion_rate_scope_wrapper(self, query_payload: Dict[str, Any]):
        """
        Wrap single-entity completion rate aggregations in a multi-bucket scope so bucket_script is valid.
        CRITICAL: Must use filters aggregation (multi-bucket), NOT filter aggregation (single-bucket).
        Only wraps if the new pattern (assigned_scope) is not already present.
        """
        query_body = (query_payload or {}).get('query')
        if not isinstance(query_body, dict):
            return
        aggs = query_body.get('aggs')
        if not isinstance(aggs, dict) or not aggs:
            return
        
        # Don't wrap if new pattern (assigned_scope) is already present
        if 'assigned_scope' in aggs:
            return
        
        # Don't wrap if already wrapped with filters aggregation
        if 'completion_scope' in aggs or 'scope' in aggs:
            scope_agg = aggs.get('completion_scope') or aggs.get('scope')
            if isinstance(scope_agg, dict) and 'filters' in scope_agg:
                return  # Already properly wrapped with filters
        
        # Check for ANY bucket_script at top level (any name: completion_rate, calculate_rate, etc.)
        if not self._has_top_level_completion_rate_pipeline(aggs):
            return
        
        # CRITICAL: Use filters aggregation (multi-bucket), NOT filter aggregation (single-bucket)
        # bucket_script requires a multi-bucket parent aggregation
        query_body['aggs'] = {
            'completion_scope': {
                'filters': {
                    'filters': {
                        'all': {'match_all': {}}
                    }
                },
                'aggs': aggs
            }
        }
        logger.info("Wrapped completion rate KPI aggregations inside completion_scope filters aggregator (multi-bucket)")
    
    def _has_top_level_completion_rate_pipeline(self, aggs: Dict[str, Any]) -> bool:
        """
        Return True if any bucket_script aggregation exists at the top level (needs wrapping).
        Checks for ANY bucket_script regardless of name (completion_rate, calculate_rate, etc.).
        """
        if not isinstance(aggs, dict):
            return False
        
        # SIMPLE: Recursively check ALL aggregations for bucket_script
        def check_for_bucket_script(agg_dict: Dict[str, Any], path: str = "") -> bool:
            if not isinstance(agg_dict, dict):
                return False
            # Check if this is a bucket_script
            if 'bucket_script' in agg_dict:
                logger.info(f"Found bucket_script at: {path}")
                return True
            # Check all sub-aggregations
            for key, value in agg_dict.items():
                if key == 'aggs' and isinstance(value, dict):
                    for sub_key, sub_value in value.items():
                        if check_for_bucket_script(sub_value, f"{path}.{sub_key}" if path else sub_key):
                            return True
        return False
        
        return check_for_bucket_script(aggs)
    
    def _get_completion_rate_inner_aggs(self, aggs: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Return the inner aggregation map for completion rate KPIs, handling scope wrapper if present."""
        if not isinstance(aggs, dict):
            return None
        if 'completion_scope' in aggs:
            scope = aggs.get('completion_scope', {})
            if isinstance(scope, dict):
                inner = scope.get('aggs')
                if isinstance(inner, dict):
                    return inner
            return None
        return aggs
    
    def _fix_most_completed_ordering(self, query_payload: Dict[str, Any], user_message: str):
        """
        Fix "most completed" queries to order by _count (total completions) instead of unique_users.
        
        When user asks for "most completed modules" or "graph of most completed modules",
        they want modules sorted by total completion count (doc_count), not by unique users.
        """
        message_lower = user_message.lower()
        
        # Check if this is a "most completed" query
        most_completed_keywords = ['most completed', 'most completions', 'top completed', 'most finished']
        if not any(kw in message_lower for kw in most_completed_keywords):
            return
        
        query_body = query_payload.get('query', {})
        if not isinstance(query_body, dict):
            return
        
        aggs = query_body.get('aggs', {})
        if not isinstance(aggs, dict):
            return
        
        def fix_ordering(agg_dict: Dict[str, Any]):
            """Recursively fix ordering in aggregations."""
            if not isinstance(agg_dict, dict):
                return
            
            # Check if this is a terms aggregation with order
            if 'terms' in agg_dict:
                terms_agg = agg_dict['terms']
                if isinstance(terms_agg, dict) and 'order' in terms_agg:
                    order = terms_agg['order']
                    
                    # Check if ordering by unique_users (wrong for "most completed")
                    if isinstance(order, dict) and 'unique_users' in order:
                        logger.info("Fixing 'most completed' query: changing order from unique_users to _count")
                        terms_agg['order'] = {'_count': 'desc'}
                    elif isinstance(order, dict) and 'unique_modules' in order:
                        # For user-based queries, also use _count
                        logger.info("Fixing 'most completed' query: changing order from unique_modules to _count")
                        terms_agg['order'] = {'_count': 'desc'}
            
            # Recursively check sub-aggregations
            sub_aggs = agg_dict.get('aggs', {})
            if isinstance(sub_aggs, dict):
                for sub_agg in sub_aggs.values():
                    fix_ordering(sub_agg)
        
        # Fix all aggregations
        for agg_name, agg_value in aggs.items():
            if isinstance(agg_value, dict):
                fix_ordering(agg_value)
        
        logger.info("Fixed 'most completed' query ordering: changed from unique_users/unique_modules to _count")
    
    def _fix_unique_modules_query(self, query_payload: Dict[str, Any], entities: Dict[str, Any]):
        """
        Fix "what modules were completed" queries to use aggregations for unique modules.
        
        CRITICAL: These queries should return UNIQUE modules (one per mid), not completion rows.
        If multiple users completed the same module, we should only return that module once.
        """
        query_body = query_payload.get('query', {})
        if not isinstance(query_body, dict):
            return
        
        # ALWAYS rebuild the structure to match the exact query provided
        # Don't check anything - just ALWAYS rebuild to ensure it's exactly correct
        logger.info("FORCING complete rebuild of unique modules query structure - ignoring LLM output")
        
        # Remove size and _source (we'll use aggregations instead)
        query_body['size'] = 0
        
        # CRITICAL: Remove collapse if present - collapse only works on hits, not aggregations!
        # When size: 0, there are no hits, so collapse is useless dead code
        if 'collapse' in query_body:
            logger.warning("Removed collapse from aggregation query (collapse only works on hits, not aggregations when size: 0)")
            query_body.pop('collapse')
        
        # Build aggregation structure for unique modules (EXACT structure as provided - no variations)
        # This is the EXACT structure from the user's specification
        query_body['aggs'] = {
            'modules': {
                'terms': {
                    'field': 'mid',
                    'size': 1000,
                    'order': {'unique_users': 'desc'}  # Order by unique_users descending
                },
                'aggs': {
                    'module_info': {
                        'top_hits': {
                            'size': 1,
                            '_source': [
                                'mid',
                                'module_name',
                                'module_type_name',
                                'skill_name',
                                'sub_skill_name',
                                'module_points',
                                'estd_time'
                            ]
                        }
                    },
                    'unique_users': {
                        'cardinality': {'field': 'uid'}  # Count unique users per module
                    }
                }
            }
        }
        logger.info("FORCED rebuild: aggregation structure now matches exact specification")
        
        # FINAL CHECK: Ensure collapse is removed (in case it was added after our fix)
        if 'collapse' in query_body:
            logger.warning("Removed collapse from unique modules query (final check)")
            query_body.pop('collapse')
        
        # Also remove any incorrect person name filters (e.g., "were" treated as person name)
        query = query_body.get('query', {})
        if isinstance(query, dict):
            bool_query = query.get('bool', {})
            if isinstance(bool_query, dict):
                for clause_type in ['filter', 'must', 'should']:
                    clauses = bool_query.get(clause_type, [])
                    if not isinstance(clauses, list):
                        continue
                    # Remove clauses that match filler words in name/email fields
                    filtered_clauses = []
                    for clause in clauses:
                        if not isinstance(clause, dict):
                            filtered_clauses.append(clause)
                            continue
                        # Check for match clauses on first_name, last_name, or email_addr with filler words
                        should_remove = False
                        if 'match' in clause:
                            match_clause = clause['match']
                            if isinstance(match_clause, dict):
                                for field, match_value in match_clause.items():
                                    if field in ['first_name', 'last_name', 'email_addr']:
                                        query_value = match_value.get('query', '') if isinstance(match_value, dict) else str(match_value)
                                        filler_words = ['were', 'was', 'is', 'are', 'the', 'in', 'on', 'at', 'by', 'for', 'with', 'from', 'to', 'of', 'about', 'what', 'which']
                                        if str(query_value).lower() in filler_words:
                                            should_remove = True
                                            logger.warning(f"Removed incorrect name/email filter for filler word '{query_value}': {clause}")
                                            break
                        elif 'bool' in clause:
                            # Check nested bool queries
                            bool_sub = clause['bool']
                            if isinstance(bool_sub, dict):
                                for sub_clause_type in ['should', 'must', 'filter']:
                                    sub_clauses = bool_sub.get(sub_clause_type, [])
                                    if isinstance(sub_clauses, list):
                                        for sub_clause in sub_clauses:
                                            if isinstance(sub_clause, dict) and 'match' in sub_clause:
                                                match_clause = sub_clause['match']
                                                if isinstance(match_clause, dict):
                                                    for field, match_value in match_clause.items():
                                                        if field in ['first_name', 'last_name', 'email_addr']:
                                                            query_value = match_value.get('query', '') if isinstance(match_value, dict) else str(match_value)
                                                            filler_words = ['were', 'was', 'is', 'are', 'the', 'in', 'on', 'at', 'by', 'for', 'with', 'from', 'to', 'of', 'about', 'what', 'which']
                                                            if str(query_value).lower() in filler_words:
                                                                should_remove = True
                                                                logger.warning(f"Removed incorrect name/email filter for filler word '{query_value}': {clause}")
                                                                break
                        if not should_remove:
                            filtered_clauses.append(clause)
                    
                    if len(filtered_clauses) < len(clauses):
                        bool_query[clause_type] = filtered_clauses
                        logger.info(f"Removed {len(clauses) - len(filtered_clauses)} incorrect name/email filters from {clause_type}")
        
        logger.info("Fixed unique modules query: ensured aggregation structure and removed filler word filters")
    
    def _build_zero_completions_in_range_query(
        self, 
        query_payload: Dict[str, Any], 
        time_period: TimePeriod,
        entities: Dict[str, Any]
    ):
        """
        Build aggregation-based query for users who have NOT completed ANY module in a time range.
        
        CRITICAL: This requires user-level aggregation logic, NOT row-level filtering.
        
        The query must:
        1. Group by uid (user-level evaluation)
        2. Count assigned modules (assigned_status = 0, ANY date - no published_date filter!)
        3. Count recent completions (completed_status = 1 AND completed_date in time range)
        4. Filter users where: assignedCount > 0 AND recentCompletedCount == 0
        
        DO NOT filter by published_date - assigned modules from ANY time still count!
        
        This query fixes the core logic flaw where:
        - Row-level filtering fails because it only inspects single rows, not all rows per user
        - published_date filtering excludes modules assigned outside the date window
        - Simple must/filter conditions cannot determine "Has this user completed ANY module in last 30 days?"
        
        The solution uses terms aggregation on uid with bucket_selector to evaluate at user level.
        """
        # CRITICAL: The LLM might have generated a query with prohibited filters at the top level:
        # - completed_status = 1
        # - completed_date range
        # - assigned_status = 0
        #
        # These MUST be DELETED from the top-level query.bool.filter array.
        # They MUST ONLY exist inside the aggregation, not at top-level!
        #
        # First, clean up any existing prohibited filters from the LLM-generated query
        existing_query_body = query_payload.get('query', {})
        if isinstance(existing_query_body, dict):
            self._remove_prohibited_filters_from_top_level(existing_query_body)
        
        # Build time range for the aggregation (NOT for top-level filter)
        time_range = {
            'gte': time_period.start_timestamp,
            'lt': time_period.end_timestamp
        }
        
        # CRITICAL: Completely replace query_payload['query'] with a fresh structure
        # The top-level query.bool.filter MUST contain ONLY:
        # - user_status = 5
        # - cmid = 1
        #
        # DO NOT include (DELETE these if present):
        # - completed_status (any value)
        # - completed_date (any range)
        # - assigned_status (any value)
        #
        # These filters MUST ONLY exist inside the aggregation, not at top-level!
        query_payload['query'] = {
            'size': 0,  # We only need aggregations, not hits
            'query': {
                'bool': {
                    'filter': [
                        {'term': {'user_status': 5}},
                        {'term': {'cmid': 1}}  # Tenant restriction
                    ]
                }
            }
        }
        
        query_body = query_payload['query']
        
        # Build the aggregation structure
        # Time range is for completed_date only (checking for completions in this period)
        # NOT for published_date (we don't care when modules were published)
        # NOTE: time_range was already built above
        query_body['aggs'] = {
            'users': {
                'terms': {
                    'field': 'uid',
                    'size': 50000  # Large size to get all users
                },
                'aggs': {
                    'assigned': {
                        'filter': {
                            'term': {'assigned_status': 0}
                        }
                    },
                    'recent_completions': {
                        'filter': {
                            'bool': {
                                'must': [
                                    {'term': {'completed_status': 1}},
                                    {'range': {'completed_date': time_range}}
                                ]
                            }
                        }
                    },
                    'only_inactive_users': {
                        'bucket_selector': {
                            'buckets_path': {
                                'assignedCount': 'assigned._count',
                                'novCount': 'recent_completions._count'  # Changed to match user's example
                            },
                            'script': 'params.assignedCount > 0 && params.novCount == 0'
                        }
                    },
                    'user_info': {
                        'top_hits': {
                            'size': 1,
                            '_source': [
                                'uid',
                                'email_addr',
                                'first_name',
                                'last_name',
                                'designation',
                                'city',
                                'country'
                            ]
                        }
                    }
                }
            }
        }
        
        # CRITICAL FINAL SAFEGUARD: Explicitly remove any filters that should NOT be at top-level
        # Even if the LLM or other code added them, we must remove them here.
        # These filters MUST only exist inside the aggregation, not at the top level.
        self._remove_prohibited_filters_from_top_level(query_body)
        
        # VERIFY: Double-check that the top-level filter array contains ONLY user_status and cmid
        query = query_body.get('query', {})
        if isinstance(query, dict):
            bool_query = query.get('bool', {})
            if isinstance(bool_query, dict):
                filter_clauses = bool_query.get('filter', [])
                if isinstance(filter_clauses, list):
                    # Count how many filters we have and what they are
                    allowed_fields = {'user_status', 'status', 'cmid'}
                    for clause in filter_clauses:
                        if isinstance(clause, dict) and 'term' in clause:
                            term_dict = clause.get('term', {})
                            if isinstance(term_dict, dict):
                                for field_name in term_dict.keys():
                                    if field_name not in allowed_fields:
                                        logger.error(f"PROHIBITED FILTER STILL PRESENT: {field_name} in top-level filter! Removing now...")
                                        # Emergency removal
                                        bool_query['filter'] = [
                                            c for c in filter_clauses 
                                            if not (isinstance(c, dict) and 'term' in c and isinstance(c.get('term'), dict) and field_name in c.get('term', {}))
                                        ]
                        elif isinstance(clause, dict) and 'range' in clause:
                            range_dict = clause.get('range', {})
                            if isinstance(range_dict, dict) and 'completed_date' in range_dict:
                                logger.error("PROHIBITED RANGE FILTER ON completed_date STILL PRESENT! Removing now...")
                                bool_query['filter'] = [c for c in filter_clauses if not (isinstance(c, dict) and 'range' in c and 'completed_date' in c.get('range', {}))]
        
        # CRITICAL: Verify the aggregation was added
        if 'aggs' not in query_body or 'users' not in query_body.get('aggs', {}):
            logger.error("CRITICAL: Aggregation 'users' was NOT added to query! This will cause query to fail.")
        else:
            logger.info(f"Built aggregation-based query for users with zero completions in range: {time_range}")
            logger.debug(f"Query structure: size={query_body.get('size')}, has_query={bool(query_body.get('query'))}, has_aggs={bool(query_body.get('aggs'))}")
    
    def _extract_zero_completions_in_range_results(self, execution_result: Dict[str, Any]):
        """
        Extract user results from the "zero completions in range" aggregation query.
        The aggregation structure: users -> buckets (filtered by bucket_selector) -> user_info (top_hits)
        """
        aggregations = execution_result.get('aggregations', {})
        if not aggregations:
            logger.error("CRITICAL: No aggregations found in execution result for zero completions query!")
            logger.debug(f"Execution result keys: {list(execution_result.keys())}")
            return
        
        users_agg = aggregations.get('users', {})
        if not isinstance(users_agg, dict):
            logger.error(f"CRITICAL: 'users' aggregation not found or invalid! Available aggregations: {list(aggregations.keys())}")
            return
        
        buckets = users_agg.get('buckets', [])
        if not buckets:
            logger.warning(f"No buckets found in users aggregation. Aggregation structure: {users_agg.keys()}")
            execution_result['results'] = []
            execution_result['total'] = 0
            execution_result['count'] = 0
            return
        
        logger.info(f"Found {len(buckets)} buckets in users aggregation")
        
        # Extract user info from each bucket
        results = []
        for bucket in buckets:
            user_key = bucket.get('key')  # This is the uid
            if not user_key:
                continue
            
            # Get user details from top_hits
            user_info = bucket.get('user_info', {})
            user_data = {}
            if 'hits' in user_info and 'hits' in user_info['hits']:
                hits = user_info['hits']['hits']
                if hits and len(hits) > 0:
                    user_data = hits[0].get('_source', {})
            
            # Build result row
            row = {
                'uid': int(user_key) if isinstance(user_key, (int, str)) and str(user_key).isdigit() else None
            }
            
            # Add all user fields from top_hits
            for field in self.COUNT_THRESHOLD_USER_FIELDS:
                if field in user_data:
                    row[field] = user_data[field]
            
            # Ensure we have at least uid
            if row.get('uid'):
                results.append(row)
        
        execution_result['results'] = results
        execution_result['total'] = len(results)
        execution_result['count'] = len(results)
        
        logger.info(f"Extracted {len(results)} users who have zero completions in the specified time range")
    
    def _apply_count_threshold(self, query_payload: Dict[str, Any], entities: Dict[str, Any]):
        """
        Apply count threshold filtering for queries like "users who have finished more than 5 modules".
        
        CRITICAL: For module thresholds, we MUST use cardinality(mid) + bucket_selector, NOT min_doc_count.
        min_doc_count counts documents (rows), not unique modules. A user could have 6+ documents
        but only 3 unique modules, which would incorrectly pass the filter.
        """
        query_body = query_payload.get('query', {})
        if not isinstance(query_body, dict):
            return
        
        aggs = query_body.get('aggs', {})
        if not isinstance(aggs, dict):
            return
        
        # This method is no longer used - Bedrock handles threshold queries directly
        # Keeping for backwards compatibility but it won't be called
        return
        is_module_threshold = any(kw in user_message for kw in ['module', 'modules', 'training', 'course', 'completed', 'finished'])
        
        # First pass: Find and fix any bucket_selectors that reference module_count but module_count is missing
        def ensure_module_count_exists(agg_dict: Dict[str, Any]):
            """Recursively ensure module_count exists before any bucket_selector that references it."""
            if not isinstance(agg_dict, dict):
                return
            
            # Check if this aggregation has sub-aggregations
            sub_aggs = agg_dict.get('aggs', {})
            if not isinstance(sub_aggs, dict):
                return
            
            # Check for bucket_selectors that reference module_count
            needs_module_count = False
            for key, value in sub_aggs.items():
                if isinstance(value, dict) and 'bucket_selector' in value:
                    buckets_path = value.get('bucket_selector', {}).get('buckets_path', {})
                    # Check if it references module_count (buckets_path is like {"mc": "module_count"})
                    if isinstance(buckets_path, dict):
                        # Check if any value in buckets_path is "module_count"
                        if any(v == 'module_count' for v in buckets_path.values()):
                            needs_module_count = True
                            break
            
            # If we found bucket_selectors referencing module_count, ensure module_count exists
            if needs_module_count and 'module_count' not in sub_aggs:
                sub_aggs['module_count'] = {
                    "cardinality": {"field": "mid"}
                }
                logger.info(f"Fixed missing module_count aggregation (found bucket_selector referencing it)")
            
            # Recursively check nested aggregations
            for key, value in sub_aggs.items():
                if isinstance(value, dict):
                    ensure_module_count_exists(value)
        
        # Apply the fix recursively to all aggregations
        for agg_name, agg_def in aggs.items():
            if isinstance(agg_def, dict):
                ensure_module_count_exists(agg_def)
        
        # Find terms aggregations that group by user (uid or email_addr)
        for agg_name, agg_def in aggs.items():
            if isinstance(agg_def, dict) and 'terms' in agg_def:
                terms_agg = agg_def['terms']
                field = terms_agg.get('field', '')
                
                # Check if grouping by user
                if field in ['uid', 'email_addr']:
                    # For module thresholds, use bucket_selector with cardinality(mid)
                    if is_module_threshold:
                        # Ensure we have a module_count aggregation
                        sub_aggs = agg_def.setdefault('aggs', {})
                        
                        # CRITICAL: Check if LLM already added bucket_selector - if so, remove it first
                        # Then add module_count, then re-add bucket_selector
                        existing_bucket_selector = None
                        bucket_selector_key = None
                        for key, value in list(sub_aggs.items()):
                            if isinstance(value, dict) and 'bucket_selector' in value:
                                existing_bucket_selector = value
                                bucket_selector_key = key
                                # Remove it temporarily - we'll add it back after module_count
                                del sub_aggs[key]
                                logger.info(f"Found existing bucket_selector, removing temporarily to fix order")
                        
                        # CRITICAL: Add cardinality aggregation for unique modules FIRST
                        # bucket_selector must come AFTER the aggregation it references
                        if 'module_count' not in sub_aggs:
                            sub_aggs['module_count'] = {
                                "cardinality": {"field": "mid"}
                            }
                            logger.info(f"Added module_count aggregation")
                        
                        # Now add bucket_selector AFTER module_count (pipeline aggregation)
                        if operator == 'gt':
                            # For "more than X", filter where module_count > X
                            script = f"params.mc > {threshold}"
                        elif operator == 'lt':
                            # For "less than X", filter where module_count < X
                            script = f"params.mc < {threshold}"
                        else:
                            # Default to greater than
                            script = f"params.mc > {threshold}"
                        
                        # Use the existing key if it existed, otherwise use default
                        selector_key = bucket_selector_key or 'filter_by_threshold'
                        
                        # Add bucket_selector as a pipeline aggregation AFTER module_count
                        sub_aggs[selector_key] = {
                            "bucket_selector": {
                                "buckets_path": {
                                    "mc": "module_count"
                                },
                                "script": script
                            }
                        }
                        
                        # Ensure we have top_hits to extract user details from filtered buckets
                        if 'user_details' not in sub_aggs:
                            sub_aggs['user_details'] = {
                                "top_hits": {
                                    "size": 1,
                                    "_source": ["uid", "email_addr", "first_name", "last_name", "designation"]
                                }
                            }
                        
                        logger.info(f"Applied module count threshold: {script} for {agg_name} (using bucket_selector with cardinality)")
                    else:
                        # For non-module thresholds (e.g., "users with more than 5 completions")
                        # min_doc_count is acceptable since we're counting completion records
                        if operator == 'gt':
                            min_doc_count = threshold + 1
                            terms_agg['min_doc_count'] = min_doc_count
                            logger.info(f"Applied document count threshold: min_doc_count={min_doc_count} for {agg_name}")
                        elif operator == 'lt':
                            logger.warning(f"Less than threshold not fully supported without bucket_selector")
                            terms_agg['min_doc_count'] = 1
        
        # Ensure the hits include the fields we need to summarize results per user
        source_fields = query_body.get('_source')
        required_fields = ['uid', 'email_addr', 'mid', 'module_name', 'first_name', 'last_name']
        if source_fields is None:
            query_body['_source'] = required_fields
        elif isinstance(source_fields, list):
            for field in required_fields:
                if field not in source_fields:
                    source_fields.append(field)
    
    def _summarize_count_threshold_results(self, execution_result: Dict[str, Any]):
        """
        Deduplicate raw hit results into one row per user and add modules_completed column.
        Triggered for queries that ask for users who have crossed a completion threshold.
        
        CRITICAL: If aggregations are present, extract from buckets (already filtered by bucket_selector).
        Otherwise, process raw hits and filter by threshold.
        """
        aggregations = execution_result.get('aggregations', {})
        
        # If we have aggregations with buckets, extract from those (they're already filtered by bucket_selector)
        if aggregations and isinstance(aggregations, dict):
            for agg_name, agg_value in aggregations.items():
                if isinstance(agg_value, dict) and 'buckets' in agg_value:
                    buckets = agg_value['buckets']
                    if buckets:
                        # Extract results from aggregation buckets
                        summarized_rows = []
                        skipped_count = 0
                        for bucket in buckets:
                            # Get user identifier from bucket key
                            user_key = bucket.get('key')
                            if not user_key:
                                skipped_count += 1
                                continue
                            
                            # Get module_count from aggregation (already calculated)
                            module_count = 0
                            if 'module_count' in bucket:
                                module_count = bucket['module_count'].get('value', 0)
                            
                            # Extract user details from top_hits if available
                            user_data = {}
                            if 'user_details' in bucket and 'hits' in bucket['user_details']:
                                hits = bucket['user_details']['hits'].get('hits', [])
                                if hits and len(hits) > 0:
                                    user_data = hits[0].get('_source', {})
                            
                            # Build row
                            # Check if key is uid (numeric) or email_addr (contains @)
                            is_uid = isinstance(user_key, (int, str)) and str(user_key).isdigit()
                            is_email = '@' in str(user_key)
                            
                            row = {
                                'uid': int(user_key) if is_uid else None,
                                'email_addr': str(user_key) if is_email else None,
                                'modules_completed': int(module_count)
                            }
                            
                            # Add user fields from top_hits
                            for field in self.COUNT_THRESHOLD_USER_FIELDS:
                                if field in user_data:
                                    row[field] = user_data[field]
                            
                            # If we don't have uid/email from key, try to get from user_data
                            if not row.get('uid') and 'uid' in user_data:
                                row['uid'] = user_data['uid']
                            if not row.get('email_addr') and 'email_addr' in user_data:
                                row['email_addr'] = user_data['email_addr']
                            
                            # Only add if we have a valid user identifier
                            if row.get('uid') or row.get('email_addr'):
                                summarized_rows.append(row)
                            else:
                                skipped_count += 1
                                logger.warning(f"Skipped bucket with key {user_key} - no valid uid or email_addr found")
                        
                        if skipped_count > 0:
                            logger.warning(f"Skipped {skipped_count} buckets out of {len(buckets)} - missing user identifiers")
                        
                        if summarized_rows:
                            # Sort by modules_completed descending
                            summarized_rows.sort(key=lambda r: r.get('modules_completed', 0), reverse=True)
                            execution_result['results'] = summarized_rows
                            execution_result['count'] = len(summarized_rows)
                            execution_result['total'] = len(summarized_rows)
                            
                            # Update aggregation to reflect actual extracted count (not bucket count)
                            # This prevents confusion where bucket count != extracted count
                            if agg_name in aggregations:
                                # Update the bucket count to match extracted rows
                                agg_value['buckets'] = buckets[:len(summarized_rows)]  # Truncate to actual extracted count
                                # Or remove the aggregation entirely since we have the results
                                # aggregations.pop(agg_name, None)
                            
                            metadata = execution_result.setdefault('metadata', {})
                            metadata['count_threshold_summary'] = True
                            logger.info(f"Extracted {len(summarized_rows)} users from {len(buckets)} aggregation buckets (already filtered by bucket_selector)")
                            return
        
        # Fallback: Process raw hits (for queries without aggregations)
        results = execution_result.get('results') or []
        if not results:
            return
        
        sample = results[0] if isinstance(results, list) else None
        if not isinstance(sample, dict):
            return
        
        if 'uid' not in sample and 'email_addr' not in sample:
            # Only summarize when user identifiers are present
            return
        
        aggregated: Dict[Any, Dict[str, Any]] = {}
        for row in results:
            if not isinstance(row, dict):
                continue
            user_key = row.get('uid') or row.get('email_addr')
            if not user_key:
                continue
            
            user_entry = aggregated.setdefault(user_key, {
                'modules_completed': 0,
                '_module_tracker': set()
            })
            
            # Capture user profile data once
            for field in self.COUNT_THRESHOLD_USER_FIELDS:
                value = row.get(field)
                if value is None or value == '':
                    continue
                if field not in user_entry or not user_entry[field]:
                    user_entry[field] = value
            
            module_identifier = row.get('mid') or row.get('module_name') or row.get('module_id')
            tracker: Set[Any] = user_entry['_module_tracker']
            if module_identifier is not None:
                if module_identifier not in tracker:
                    tracker.add(module_identifier)
                    user_entry['modules_completed'] += 1
            else:
                user_entry['modules_completed'] += 1
        
        if not aggregated:
            return
        
        summarized_rows: List[Dict[str, Any]] = []
        for entry in aggregated.values():
            entry.pop('_module_tracker', None)
            ordered_row: Dict[str, Any] = {}
            for field in self.COUNT_THRESHOLD_DISPLAY_ORDER:
                if field == 'modules_completed':
                    ordered_row[field] = entry.get('modules_completed', 0)
                elif entry.get(field) is not None:
                    ordered_row[field] = entry[field]
            # Append any remaining captured user fields
            for field, value in entry.items():
                if field in ordered_row or field == 'modules_completed':
                    continue
                if value is not None and value != '':
                    ordered_row[field] = value
            summarized_rows.append(ordered_row)
        
        summarized_rows.sort(key=lambda r: r.get('modules_completed', 0), reverse=True)
        execution_result['results'] = summarized_rows
        execution_result['count'] = len(summarized_rows)
        execution_result['total'] = len(summarized_rows)
        metadata = execution_result.setdefault('metadata', {})
        metadata['count_threshold_summary'] = True
    
    def _sanitize_query_payload(
        self,
        query_payload: Dict[str, Any],
        time_period: Optional[TimePeriod] = None,
        time_field: Optional[str] = None
    ):
        """Remove/convert unsupported query constructs (e.g., terms_lookup)."""
        # Always run recursive removal - it's safe and catches everything
        self._remove_terms_lookup_recursive(query_payload)
        # Fix date histograms to ensure all days are included
        self._fix_date_histograms(query_payload, time_period=time_period, time_field=time_field)
        # CRITICAL: Validate and fix bool query structures to prevent "Unknown key" errors
        self._validate_and_fix_bool_queries(query_payload)
    
    def _validate_and_remove_invalid_fields(
        self,
        query_payload: Dict[str, Any],
        index_id: str
    ):
        """
        Validate that all fields in the query exist in the selected index schema.
        Remove any filters/clauses that reference fields not in the schema.
        
        Args:
            query_payload: The query payload to validate
            index_id: The index ID to validate against
        """
        # Get schema for the selected index
        # Handle both index ID (key) and actual index name (value)
        schema = None
        actual_index_id = index_id  # Default to provided index_id
        
        if index_id in self.schema_cache:
            schema = self.schema_cache[index_id]
            actual_index_id = index_id
        else:
            # Try to find by actual index name
            for key, value in settings.OPENSEARCH_INDEXES.items():
                if value == index_id:
                    schema = self.schema_cache.get(key)
                    if schema:
                        actual_index_id = key
                        logger.info(f"Mapped index name '{index_id}' to index ID '{key}' for validation")
                        break
        
        if not schema:
            logger.warning(f"Schema not found for index '{index_id}', skipping field validation. Available: {list(self.schema_cache.keys())}")
            return
        
        # Extract all available field names from schema
        fields = schema.get('fields', [])
        available_fields = {field.get('name', '') for field in fields}
        
        # Also add .keyword versions for keyword/text fields (OpenSearch automatically creates these)
        # This allows queries to use either "field" or "field.keyword" for aggregations
        for field in fields:
            field_name = field.get('name', '')
            field_type = field.get('type', '')
            if field_type in ['keyword', 'text'] and field_name:
                available_fields.add(f"{field_name}.keyword")
        
        # Also include common OpenSearch fields that are always available
        available_fields.update(['_id', '_score', '_source', '_index', '_type'])
        
        logger.info(f"Validating query fields against index {index_id}. Available fields: {len(available_fields)}")
        logger.debug(f"Sample available fields: {sorted(list(available_fields))[:30]}")
        
        # Track removed fields for logging
        removed_fields = set()
        
        # Recursively validate and remove invalid field references
        def validate_and_remove(obj: Any, path: str = "") -> bool:
            """Recursively validate fields and remove invalid ones. Returns True if object was modified."""
            modified = False
            
            if isinstance(obj, dict):
                keys_to_remove = []
                
                for key, value in list(obj.items()):
                    current_path = f"{path}.{key}" if path else key
                    
                    # Check if this is a field reference in a query clause
                    # Common patterns: {"term": {"field_name": value}}, {"range": {"field_name": {...}}}, etc.
                    # CRITICAL: Only validate if this is actually a query clause, not an aggregation config
                    # Aggregation configs have keys like "field", "size", "order" which are NOT index fields
                    if key in ['term', 'terms', 'range', 'match', 'match_phrase', 'exists', 'missing', 'prefix', 'wildcard', 'regexp']:
                        if isinstance(value, dict):
                            # This is a field reference - check if field exists
                            # BUT: Skip if we're inside an aggregation definition (terms, cardinality, etc.)
                            # because those have config keys that aren't field names
                            # Check if path contains aggregation config (e.g., "aggs.designations.terms")
                            # If we're inside a terms/cardinality/etc. config, the keys (field, size, order) are NOT field names
                            is_agg_config = False
                            if path:
                                path_parts = path.split('.')
                                # Check if we're inside an aggregation config (terms, cardinality, etc.)
                                # Pattern: "aggs.designations.terms" or "query.aggs.designations.terms"
                                for i, part in enumerate(path_parts):
                                    if part in ['terms', 'cardinality', 'avg', 'sum', 'min', 'max', 'stats', 'value_count', 'date_histogram']:
                                        # We're inside an aggregation config - keys like "field", "size", "order" are config, not field names
                                        is_agg_config = True
                                        break
                            
                            if not is_agg_config:
                                for field_name in list(value.keys()):
                                    if field_name not in available_fields:
                                        logger.warning(f"Removing invalid field '{field_name}' from {current_path} (not in index {index_id})")
                                        removed_fields.add(field_name)
                                        value.pop(field_name, None)
                                        modified = True
                                        # If this was the only field, remove the entire clause
                                        if not value:
                                            keys_to_remove.append(key)
                    
                    # CRITICAL: Check inside bool query clauses (filter, must, should, must_not)
                    # These contain arrays of query clauses that need validation
                    elif key in ['filter', 'must', 'should', 'must_not']:
                        if isinstance(value, list):
                            items_to_remove = []
                            for i, item in enumerate(value):
                                if isinstance(item, dict):
                                    # Check if this item is a query clause with a field reference
                                    # Pattern: {"term": {"field_name": value}}
                                    for clause_type in ['term', 'terms', 'range', 'match', 'match_phrase', 'exists', 'missing', 'prefix', 'wildcard', 'regexp']:
                                        if clause_type in item:
                                            clause_value = item[clause_type]
                                            if isinstance(clause_value, dict):
                                                # Check each field in the clause
                                                for field_name in list(clause_value.keys()):
                                                    if field_name not in available_fields:
                                                        logger.warning(f"Removing invalid field '{field_name}' from {current_path}[{i}].{clause_type} (not in index {index_id})")
                                                        removed_fields.add(field_name)
                                                        clause_value.pop(field_name, None)
                                                        modified = True
                                                        # If clause is now empty, mark for removal
                                                        if not clause_value:
                                                            items_to_remove.append(i)
                                                            break
                                    
                                    # Also recursively check nested bool queries
                                    if 'bool' in item:
                                        if validate_and_remove(item['bool'], f"{current_path}[{i}].bool"):
                                            modified = True
                            
                            # Remove empty items (in reverse order to maintain indices)
                            for i in reversed(items_to_remove):
                                value.pop(i)
                                modified = True
                    
                    # Check bool queries recursively
                    elif key == 'bool':
                        if validate_and_remove(value, current_path):
                            modified = True
                    
                    # Check aggregations - field references in aggregations
                    elif key == 'aggs' or key == 'aggregations':
                        if isinstance(value, dict):
                            # Collect names of aggregations we're about to remove
                            removed_agg_names = set()
                            
                            for agg_name, agg_def in value.items():
                                if isinstance(agg_def, dict):
                                    # Check terms aggregation field
                                    if 'terms' in agg_def:
                                        terms_def = agg_def['terms']
                                        if isinstance(terms_def, dict) and 'field' in terms_def:
                                            field_name = terms_def['field']
                                            # Handle .keyword fields - check base field name
                                            base_field_name = field_name.replace('.keyword', '')
                                            if base_field_name not in available_fields:
                                                logger.warning(f"Removing invalid field '{field_name}' from aggregation '{agg_name}.terms.field' (not in index {index_id})")
                                                removed_fields.add(field_name)
                                                keys_to_remove.append(agg_name)
                                                removed_agg_names.add(agg_name)
                                                modified = True
                                            elif field_name != base_field_name and base_field_name in available_fields:
                                                # Field exists but .keyword version doesn't - use base field
                                                logger.info(f"Replacing '{field_name}' with '{base_field_name}' in aggregation '{agg_name}.terms.field'")
                                                terms_def['field'] = base_field_name
                                                modified = True
                                    
                                    # Check cardinality aggregation field
                                    if 'cardinality' in agg_def:
                                        card_def = agg_def['cardinality']
                                        if isinstance(card_def, dict) and 'field' in card_def:
                                            field_name = card_def['field']
                                            # Handle .keyword fields
                                            base_field_name = field_name.replace('.keyword', '')
                                            if base_field_name not in available_fields:
                                                logger.warning(f"Removing invalid field '{field_name}' from aggregation '{agg_name}.cardinality.field' (not in index {index_id})")
                                                removed_fields.add(field_name)
                                                keys_to_remove.append(agg_name)
                                                removed_agg_names.add(agg_name)
                                                modified = True
                                    
                                    # Check other aggregation types with field references
                                    for agg_type in ['avg', 'sum', 'min', 'max', 'stats', 'value_count', 'date_histogram']:
                                        if agg_type in agg_def:
                                            agg_type_def = agg_def[agg_type]
                                            if isinstance(agg_type_def, dict) and 'field' in agg_type_def:
                                                field_name = agg_type_def['field']
                                                # Handle .keyword fields
                                                base_field_name = field_name.replace('.keyword', '')
                                                if base_field_name not in available_fields:
                                                    logger.warning(f"Removing invalid field '{field_name}' from aggregation '{agg_name}.{agg_type}.field' (not in index {index_id})")
                                                    removed_fields.add(field_name)
                                                    keys_to_remove.append(agg_name)
                                                    removed_agg_names.add(agg_name)
                                                    modified = True
                                    
                                    # CRITICAL: Check if aggregation definition became empty after field removal
                                    # If so, remove the entire aggregation to prevent "Missing definition" errors
                                    if not agg_def or (isinstance(agg_def, dict) and not agg_def):
                                        logger.warning(f"Removing empty aggregation '{agg_name}' (definition became empty after field removal)")
                                        keys_to_remove.append(agg_name)
                                        removed_agg_names.add(agg_name)
                                        modified = True
                                    else:
                                        # Recursively check nested aggregations (aggs inside this aggregation)
                                        # BUT: Don't recursively validate aggregation config (terms, cardinality, etc.)
                                        # because those contain config keys (field, size, order) that aren't index fields
                                        if 'aggs' in agg_def or 'aggregations' in agg_def:
                                            nested_aggs = agg_def.get('aggs') or agg_def.get('aggregations')
                                            if isinstance(nested_aggs, dict):
                                                if validate_and_remove({'aggs': nested_aggs}, f"{current_path}.{agg_name}.aggs"):
                                                    modified = True
                                        # Don't recursively validate the aggregation config itself (terms, cardinality, etc.)
                                        # because those have config keys like "field", "size", "order" that aren't field names
                            
                            # CRITICAL: After identifying removed aggregations, check for orphaned bucket_scripts
                            # that reference the removed aggregations
                            for agg_name, agg_def in list(value.items()):
                                if agg_name in removed_agg_names:
                                    continue  # Skip aggregations we're already removing
                                if isinstance(agg_def, dict):
                                    # Check for bucket_script that references removed aggregations
                                    if 'bucket_script' in agg_def:
                                        bucket_script = agg_def['bucket_script']
                                        if isinstance(bucket_script, dict) and 'buckets_path' in bucket_script:
                                            buckets_path = bucket_script['buckets_path']
                                            if isinstance(buckets_path, dict):
                                                # Check if any referenced aggregation was removed
                                                for path_key, path_value in list(buckets_path.items()):
                                                    # Extract aggregation name from path (e.g., "agg_name>sub_agg")
                                                    referenced_agg = path_value.split('>')[0] if '>' in path_value else path_value
                                                    if referenced_agg in removed_agg_names:
                                                        logger.warning(f"Removing bucket_script '{agg_name}' - references removed aggregation '{referenced_agg}'")
                                                        keys_to_remove.append(agg_name)
                                                        modified = True
                                                        break
                    
                    # Check _source fields
                    elif key == '_source':
                        if isinstance(value, list):
                            # Remove fields that don't exist in schema
                            original_fields = set(value)
                            value[:] = [field for field in value if field in available_fields]
                            removed = original_fields - available_fields
                            if removed:
                                for field in removed:
                                    logger.warning(f"Removing invalid field '{field}' from _source (not in index {index_id})")
                                    removed_fields.add(field)
                                modified = True
                    
                    # Check sort fields
                    elif key == 'sort':
                        if isinstance(value, list):
                            for sort_item in value:
                                if isinstance(sort_item, dict):
                                    for sort_field in list(sort_item.keys()):
                                        if sort_field not in available_fields and sort_field not in ['_score', '_doc']:
                                            logger.warning(f"Removing invalid sort field '{sort_field}' (not in index {index_id})")
                                            removed_fields.add(sort_field)
                                            sort_item.pop(sort_field, None)
                                            modified = True
                    
                    # CRITICAL: Check inside bool query clauses (filter, must, should, must_not)
                    # These contain arrays of query clauses that need validation
                    elif key in ['filter', 'must', 'should', 'must_not']:
                        if isinstance(value, list):
                            items_to_remove = []
                            for i, item in enumerate(value):
                                if isinstance(item, dict):
                                    # Check if this item is a query clause with a field reference
                                    # Pattern: {"term": {"field_name": value}}
                                    for clause_type in ['term', 'terms', 'range', 'match', 'match_phrase', 'exists', 'missing', 'prefix', 'wildcard', 'regexp']:
                                        if clause_type in item:
                                            clause_value = item[clause_type]
                                            if isinstance(clause_value, dict):
                                                # Check each field in the clause
                                                for field_name in list(clause_value.keys()):
                                                    if field_name not in available_fields:
                                                        logger.warning(f"Removing invalid field '{field_name}' from {current_path}[{i}].{clause_type} (not in index {index_id})")
                                                        removed_fields.add(field_name)
                                                        clause_value.pop(field_name, None)
                                                        modified = True
                                                        # If clause is now empty, mark for removal
                                                        if not clause_value:
                                                            items_to_remove.append(i)
                                                            break
                                    
                                    # Also recursively check nested bool queries
                                    if 'bool' in item:
                                        if validate_and_remove(item['bool'], f"{current_path}[{i}].bool"):
                                            modified = True
                            
                            # Remove empty items (in reverse order to maintain indices)
                            for i in reversed(items_to_remove):
                                value.pop(i)
                                modified = True
                    
                    # Check bool queries recursively
                    elif key == 'bool':
                        if validate_and_remove(value, current_path):
                            modified = True
                    
                    # Recursively validate nested structures
                    # BUT: Skip recursive validation for aggregation configs (terms, cardinality, etc.)
                    # because those contain config keys (field, size, order) that aren't field names
                    else:
                        # Check if we're inside an aggregation config - if so, don't recursively validate
                        # Aggregation configs have keys like "field", "size", "order" that aren't index fields
                        is_agg_config_key = key in ['terms', 'cardinality', 'avg', 'sum', 'min', 'max', 'stats', 'value_count', 'date_histogram', 'bucket_script', 'bucket_selector', 'top_hits']
                        if not is_agg_config_key:
                            if validate_and_remove(value, current_path):
                                modified = True
                
                # Remove empty clauses and aggregations
                for key_to_remove in keys_to_remove:
                    obj.pop(key_to_remove, None)
                    if keys_to_remove:
                        modified = True
            
            elif isinstance(obj, list):
                items_to_remove = []
                for i, item in enumerate(obj):
                    if validate_and_remove(item, f"{path}[{i}]"):
                        modified = True
                        # If item became empty dict, mark for removal
                        if isinstance(item, dict) and not item:
                            items_to_remove.append(i)
                
                # Remove empty items (in reverse order to maintain indices)
                for i in reversed(items_to_remove):
                    obj.pop(i)
                    modified = True
            
            return modified
        
        # Validate the entire query payload
        # Start from the query object to catch nested structures in bool.filter, etc.
        query_obj = query_payload.get('query', {})
        if query_obj:
            validate_and_remove(query_obj, "query")
        
        # Also validate _source and sort at root level
        if '_source' in query_payload:
            validate_and_remove({'_source': query_payload['_source']}, "")
        if 'sort' in query_payload:
            validate_and_remove({'sort': query_payload['sort']}, "")
        
        if removed_fields:
            logger.warning(f"Removed {len(removed_fields)} invalid field(s) from query: {removed_fields}")
        else:
            logger.info(f"Query validation passed - all fields exist in index {actual_index_id}")
    
    def _remove_known_invalid_fields(
        self,
        query_payload: Dict[str, Any],
        index_id: str
    ):
        """
        Aggressively remove known invalid fields based on index type.
        This is a safety net to catch fields that shouldn't exist in specific indices.
        
        Args:
            query_payload: The query payload to clean
            index_id: The index ID (key from OPENSEARCH_INDEXES)
        """
        # Map index IDs to their invalid fields
        invalid_fields_map = {
            'module_catalog_data': {
                # Catalog index does NOT have these fields
                'invalid_fields': ['module_status', 'user_status', 'assigned_status', 'completed_status', 
                                 'completed_date', 'complete_percentage', 'complete_type', 'complete_version',
                                 'comments', 'invited_date', 'invited_time', 'ratings'],
                'correct_field': {'module_status': 'status'}  # module_status should be 'status'
            },
            'user_profile_data': {
                # User profile index does NOT have these fields
                'invalid_fields': ['module_status', 'assigned_status', 'completed_status',
                                 'completed_date', 'complete_percentage', 'module_name', 'mid',
                                 'published_date', 'estd_time', 'module_points', 'ratings'],
                'correct_field': {'user_status': 'status'}  # Convert user_status to status for user profile index
            },
            'monthly_user_activity_data': {
                # Monthly user activity index stores MONTHLY per-user COUNTS/POINTS (not event-level completions)
                'invalid_fields': ['module_status', 'assigned_status', 'completed_status',
                                 'complete_percentage', 'complete_type', 'complete_version', 'comments',
                                 'module_name', 'mid', 'published_date', 'estd_time', 'module_points',
                                 'ratings', 'invited_date', 'invited_time'],
                'correct_field': {
                    'user_status': 'status',
                    'completed_date': 'completed_on',
                    'uid': 'id'
                }
            },
            'module_consumption_data': {
                # Consumption index has module_status and user_status, but NOT plain 'status'
                'invalid_fields': ['status'],  # Only if it's used for module/user status
                'correct_field': {}
            }
        }
        
        config = invalid_fields_map.get(index_id)
        if not config:
            # Try to find by actual index name
            for key, value in settings.OPENSEARCH_INDEXES.items():
                if value == index_id:
                    config = invalid_fields_map.get(key)
                    if config:
                        index_id = key
                        break
        
        if not config:
            return
        
        invalid_fields = config.get('invalid_fields', [])
        correct_field_map = config.get('correct_field', {})
        
        removed_count = 0
        
        def remove_invalid_fields_recursive(obj: Any, path: str = "") -> int:
            """Recursively remove invalid fields. Returns count of removed fields."""
            count = 0
            
            if isinstance(obj, dict):
                keys_to_remove = []
                
                for key, value in list(obj.items()):
                    current_path = f"{path}.{key}" if path else key
                    
                    # Replace field aliases in aggregation configs, collapse, _source, sort, etc.
                    # (Needed for monthly_user_activity_data where the user id field is "id" and time anchor is "completed_on")
                    if key in ['terms', 'cardinality', 'avg', 'sum', 'min', 'max', 'stats', 'value_count', 'date_histogram']:
                        if isinstance(value, dict) and isinstance(value.get('field'), str):
                            field_value = value.get('field')
                            if field_value in correct_field_map:
                                correct_field = correct_field_map[field_value]
                                logger.warning(f"REPLACING field '{field_value}' with '{correct_field}' in {current_path}.field")
                                value['field'] = correct_field
                                count += 1
                    
                    if key == 'collapse' and isinstance(value, dict) and isinstance(value.get('field'), str):
                        field_value = value.get('field')
                        if field_value in correct_field_map:
                            correct_field = correct_field_map[field_value]
                            logger.warning(f"REPLACING collapse field '{field_value}' with '{correct_field}' in {current_path}.field")
                            value['field'] = correct_field
                            count += 1
                    
                    if key == '_source' and isinstance(value, list):
                        new_source = []
                        for f in value:
                            if isinstance(f, str) and f in correct_field_map:
                                new_source.append(correct_field_map[f])
                                count += 1
                            else:
                                new_source.append(f)
                        obj[key] = new_source
                    
                    if key == 'sort' and isinstance(value, list):
                        for i, sort_item in enumerate(value):
                            if isinstance(sort_item, dict):
                                for sort_field in list(sort_item.keys()):
                                    if sort_field in correct_field_map:
                                        correct_field = correct_field_map[sort_field]
                                        sort_item[correct_field] = sort_item.pop(sort_field)
                                        logger.warning(f"REPLACING sort field '{sort_field}' with '{correct_field}' in {current_path}[{i}]")
                                        count += 1
                    
                    # Check query clauses (term, range, etc.)
                    if key in ['term', 'terms', 'range', 'match', 'match_phrase']:
                        if isinstance(value, dict):
                            for field_name in list(value.keys()):
                                if field_name in invalid_fields:
                                    logger.warning(f"AGGRESSIVELY REMOVING invalid field '{field_name}' from {current_path} (not in {index_id})")
                                    value.pop(field_name, None)
                                    count += 1
                                    if not value:
                                        keys_to_remove.append(key)
                                elif field_name in correct_field_map:
                                    # Replace with correct field name
                                    correct_field = correct_field_map[field_name]
                                    logger.warning(f"REPLACING '{field_name}' with '{correct_field}' in {current_path}")
                                    value[correct_field] = value.pop(field_name)
                                    count += 1
                    
                    # Check filter/must/should/must_not arrays
                    elif key in ['filter', 'must', 'should', 'must_not']:
                        if isinstance(value, list):
                            items_to_remove = []
                            for i, item in enumerate(value):
                                if isinstance(item, dict):
                                    for clause_type in ['term', 'terms', 'range', 'match', 'match_phrase']:
                                        if clause_type in item:
                                            clause_value = item[clause_type]
                                            if isinstance(clause_value, dict):
                                                for field_name in list(clause_value.keys()):
                                                    if field_name in invalid_fields:
                                                        logger.warning(f"AGGRESSIVELY REMOVING invalid field '{field_name}' from {current_path}[{i}].{clause_type} (not in {index_id})")
                                                        clause_value.pop(field_name, None)
                                                        count += 1
                                                        if not clause_value:
                                                            items_to_remove.append(i)
                                                            break
                                                    elif field_name in correct_field_map:
                                                        correct_field = correct_field_map[field_name]
                                                        logger.warning(f"REPLACING '{field_name}' with '{correct_field}' in {current_path}[{i}].{clause_type}")
                                                        clause_value[correct_field] = clause_value.pop(field_name)
                                                        count += 1
                                    
                                    # Recursively check nested bool queries
                                    if 'bool' in item:
                                        count += remove_invalid_fields_recursive(item['bool'], f"{current_path}[{i}].bool")
                            
                            # Remove empty items
                            for i in reversed(items_to_remove):
                                value.pop(i)
                    
                    # Check bool queries
                    elif key == 'bool':
                        count += remove_invalid_fields_recursive(value, current_path)
                    
                    # Check aggregations
                    elif key == 'aggs' or key == 'aggregations':
                        if isinstance(value, dict):
                            for agg_name, agg_def in value.items():
                                if isinstance(agg_def, dict):
                                    # Check terms.field, cardinality.field, etc.
                                    for agg_type in ['terms', 'cardinality', 'avg', 'sum', 'min', 'max', 'date_histogram']:
                                        if agg_type in agg_def:
                                            agg_type_def = agg_def[agg_type]
                                            if isinstance(agg_type_def, dict) and 'field' in agg_type_def:
                                                field_name = agg_type_def['field']
                                                if field_name in invalid_fields:
                                                    logger.warning(f"AGGRESSIVELY REMOVING invalid field '{field_name}' from aggregation '{agg_name}.{agg_type}.field' (not in {index_id})")
                                                    # Remove the entire aggregation if field is invalid
                                                    keys_to_remove.append(agg_name)
                                                    count += 1
                                                    break
                                                elif field_name in correct_field_map:
                                                    correct_field = correct_field_map[field_name]
                                                    logger.warning(f"REPLACING '{field_name}' with '{correct_field}' in aggregation '{agg_name}.{agg_type}.field'")
                                                    agg_type_def['field'] = correct_field
                                                    count += 1
                                    
                                    # Recursively check nested aggregations
                                    count += remove_invalid_fields_recursive(agg_def, f"{current_path}.{agg_name}")
                    
                    # Check _source
                    elif key == '_source':
                        if isinstance(value, list):
                            original_len = len(value)
                            value[:] = [f for f in value if f not in invalid_fields]
                            if len(value) < original_len:
                                count += (original_len - len(value))
                    
                    # Check sort
                    elif key == 'sort':
                        if isinstance(value, list):
                            for sort_item in value:
                                if isinstance(sort_item, dict):
                                    for sort_field in list(sort_item.keys()):
                                        if sort_field in invalid_fields:
                                            logger.warning(f"AGGRESSIVELY REMOVING invalid sort field '{sort_field}' (not in {index_id})")
                                            sort_item.pop(sort_field, None)
                                            count += 1
                    
                    # Recursively check nested structures
                    else:
                        count += remove_invalid_fields_recursive(value, current_path)
                
                # Remove empty clauses
                for key_to_remove in keys_to_remove:
                    obj.pop(key_to_remove, None)
            
            elif isinstance(obj, list):
                for item in obj:
                    count += remove_invalid_fields_recursive(item, path)
            
            return count
        
        # Remove invalid fields from the entire query payload
        # Start from the query object to catch nested structures in bool.filter, etc.
        query_obj = query_payload.get('query', {})
        if query_obj:
            removed_count = remove_invalid_fields_recursive(query_obj, "query")
        else:
            removed_count = remove_invalid_fields_recursive(query_payload)
        
        # Also clean _source and sort at root level
        if '_source' in query_payload:
            removed_count += remove_invalid_fields_recursive({'_source': query_payload['_source']}, "")
        if 'sort' in query_payload:
            removed_count += remove_invalid_fields_recursive({'sort': query_payload['sort']}, "")
        
        # Clean up empty filter arrays
        if query_obj and 'bool' in query_obj:
            bool_query = query_obj['bool']
            for clause_type in ['filter', 'must', 'should', 'must_not']:
                if clause_type in bool_query and isinstance(bool_query[clause_type], list):
                    # Remove empty dicts from filter arrays
                    bool_query[clause_type] = [item for item in bool_query[clause_type] 
                                             if not (isinstance(item, dict) and not item)]
        
        if removed_count > 0:
            logger.warning(f"AGGRESSIVELY removed {removed_count} known invalid field(s) from query for index {index_id}")
    
    def _remove_terms_lookup_recursive(self, obj: Any, parent_key: str = None) -> None:
        """Aggressively remove ALL terms_lookup structures from anywhere in the object."""
        if isinstance(obj, dict):
            keys_to_remove = []
            for key, value in list(obj.items()):
                # Remove direct terms_lookup keys - CRITICAL: catch this first
                if key == 'terms_lookup':
                    logger.warning(f"Removing terms_lookup key: {key}")
                    keys_to_remove.append(key)
                    continue
                
                # Check if value is a terms_lookup structure
                if isinstance(value, dict):
                    # CRITICAL: Check if this dict contains terms_lookup anywhere inside it
                    # This catches nested cases like {"filter": {"terms_lookup": {...}}}
                    if 'terms_lookup' in value:
                        logger.warning(f"Removing dict containing terms_lookup at key: {key}")
                        keys_to_remove.append(key)
                        continue
                    
                    # CRITICAL: Don't remove cardinality aggregations - they're valid and needed!
                    # Cardinality looks like {"field": "mid"} which matches suspicious pattern
                    # but it's a valid aggregation type
                    if key == 'cardinality':
                        # This is a cardinality aggregation - keep it! Just recurse into it
                        self._remove_terms_lookup_recursive(value, key)
                        continue
                    
                    # More aggressive detection: if it has 'field' and looks like a lookup structure
                    # Check for common terms_lookup patterns
                    has_field = 'field' in value
                    has_lookup_keys = any(k in value for k in ['index', 'id', 'path', 'routing', 'terms'])
                    
                    # Also check if the structure looks suspicious (has field but not standard query structure)
                    # BUT exclude valid aggregation types which are valid
                    valid_agg_types = ['cardinality', 'value_count', 'avg', 'sum', 'min', 'max', 'stats', 'bucket_selector', 'top_hits']
                    is_valid_agg = key in valid_agg_types or (parent_key == 'aggs' and key not in ['terms', 'filter', 'range', 'bool'])
                    is_suspicious = has_field and not is_valid_agg and (
                        has_lookup_keys or 
                        (len(value) == 1 and key not in valid_agg_types) or  # Exclude valid single-field aggs
                        (len(value) == 2 and 'field' in value and any(k in value for k in ['index', 'id', 'path', 'routing']))
                    )
                    
                    if is_suspicious:
                        logger.warning(f"Removing suspicious terms_lookup-like structure at key: {key}")
                        keys_to_remove.append(key)
                        continue
                    
                    # Also check if it's nested inside terms
                    if key == 'terms' and isinstance(value, dict):
                        # Check each field in terms
                        for field_name, field_value in list(value.items()):
                            if isinstance(field_value, dict):
                                # Remove if it has terms_lookup key (at any level)
                                if 'terms_lookup' in field_value:
                                    logger.warning(f"Removing terms_lookup from terms.{field_name}")
                                    value.pop(field_name, None)
                                    continue
                                # Also recursively check nested structures for terms_lookup
                                if self._has_terms_lookup_nested(field_value):
                                    logger.warning(f"Removing nested terms_lookup from terms.{field_name}")
                                    value.pop(field_name, None)
                                    continue
                                # Remove if it looks like a lookup structure
                                elif 'field' in field_value and any(k in field_value for k in ['index', 'id', 'path', 'routing']):
                                    logger.warning(f"Removing terms_lookup structure from terms.{field_name}")
                                    value.pop(field_name, None)
                                # Also check for suspicious single-field structures (but not cardinality!)
                                elif 'field' in field_value and len(field_value) <= 2 and 'cardinality' not in str(field_value):
                                    logger.warning(f"Removing suspicious single-field structure from terms.{field_name}")
                                    value.pop(field_name, None)
                    
                    # Recursively check nested structures
                    self._remove_terms_lookup_recursive(value, key)
                elif isinstance(value, list):
                    for item in value:
                        self._remove_terms_lookup_recursive(item, key)
            
            # Remove all identified keys
            for key in keys_to_remove:
                obj.pop(key, None)
                
        elif isinstance(obj, list):
            # Process list items
            for item in obj:
                self._remove_terms_lookup_recursive(item, parent_key)
    
    def _has_terms_lookup_nested(self, obj: Any) -> bool:
        """Check if obj or any nested structure contains terms_lookup."""
        if isinstance(obj, dict):
            if 'terms_lookup' in obj:
                return True
            for value in obj.values():
                if self._has_terms_lookup_nested(value):
                    return True
        elif isinstance(obj, list):
            for item in obj:
                if self._has_terms_lookup_nested(item):
                    return True
        return False
    
    def _fix_date_histograms(
        self,
        query_payload: Dict[str, Any],
        time_period: Optional[TimePeriod] = None,
        time_field: Optional[str] = None
    ):
        """
        Fix date histogram aggregations to ensure all days are included.
        Adds min_doc_count: 0 and extended_bounds when time period is detected.
        """
        query = query_payload.get('query', {})
        if not isinstance(query, dict):
            return
        
        # Prefer the explicitly parsed time period (most reliable for "last N days").
        explicit_start = None
        explicit_end = None
        if time_period and not getattr(time_period, "is_lifetime", False):
            explicit_start = getattr(time_period, "start_timestamp", None)
            explicit_end = getattr(time_period, "end_timestamp", None)

        # Otherwise, extract time range from query filters if present
        time_range = None
        extracted_time_field = None
        
        def extract_range_from_query(q: Dict[str, Any]):
            """Recursively extract time range from query."""
            if isinstance(q, dict):
                if 'range' in q:
                    for field, range_spec in q['range'].items():
                        if isinstance(range_spec, dict) and ('gte' in range_spec or 'gt' in range_spec):
                            return field, range_spec
                for value in q.values():
                    # IMPORTANT: recurse into *all* list items, not just the first one.
                    # Previously we only inspected value[0] for lists, which often missed the time range
                    # because the range clause can appear later in bool.filter arrays.
                    if isinstance(value, (dict, list)):
                        result = extract_range_from_query(value)
                        if result:
                            return result
            elif isinstance(q, list):
                for item in q:
                    result = extract_range_from_query(item)
                    if result:
                        return result
            return None
        
        if explicit_start is None or explicit_end is None:
            result = extract_range_from_query(query)
            if result:
                extracted_time_field, time_range = result
        
        # Fix date histograms in aggregations
        def fix_histogram_recursive(obj: Any):
            if isinstance(obj, dict):
                if 'date_histogram' in obj:
                    date_hist = obj['date_histogram']
                    if isinstance(date_hist, dict):
                        # Ensure min_doc_count: 0 to show all days
                        if 'min_doc_count' not in date_hist:
                            date_hist['min_doc_count'] = 0
                        elif date_hist.get('min_doc_count', 0) > 0:
                            date_hist['min_doc_count'] = 0

                        # Normalize daily histograms to fixed_interval: "1d"
                        # calendar_interval: "day" can behave differently across DST/timezones and is less predictable.
                        if date_hist.get('calendar_interval') == 'day':
                            date_hist.pop('calendar_interval', None)
                            date_hist['fixed_interval'] = '1d'

                        # If the requested time window spans multiple months, show monthly buckets (not daily).
                        # This makes "last 2 months" aggregate by month instead of producing 60+ daily points.
                        try:
                            if explicit_start is not None and explicit_end is not None:
                                span_days = (int(explicit_end) - int(explicit_start)) / 86400
                                if span_days >= 45:
                                    # Switch to monthly histogram
                                    date_hist.pop('fixed_interval', None)
                                    date_hist['calendar_interval'] = 'month'
                                    # For month buckets, show year-month labels
                                    date_hist['format'] = 'yyyy-MM'
                        except Exception:
                            pass
                        
                        # Add/override extended_bounds so OpenSearch returns ALL buckets in the requested time window,
                        # even when some edge days have 0 activity.
                        bounds_start = None
                        bounds_end = None

                        if explicit_start is not None and explicit_end is not None:
                            bounds_start = explicit_start
                            bounds_end = explicit_end
                        elif time_range:
                            bounds_start = time_range.get('gte') or time_range.get('gt')
                            bounds_end = time_range.get('lte') or time_range.get('lt')

                        if bounds_start is not None and bounds_end is not None:
                            # OpenSearch date_histogram returns bucket keys in epoch_millis, and extended_bounds are
                            # interpreted in that same unit. Even if the field format is epoch_second, supplying
                            # seconds here will be treated as millis and can explode the bucket range (e.g., 1970→now).
                            def to_millis(v: Any) -> Any:
                                try:
                                    # If it's already millis (>= ~year 5138 in seconds threshold), keep as-is.
                                    # We use 1e11 as a safe cutover between epoch seconds (~1e10) and millis (~1e13).
                                    if isinstance(v, (int, float)) and v < 1e11:
                                        return int(v * 1000)
                                except Exception:
                                    pass
                                return v

                            bounds_start = to_millis(bounds_start)
                            bounds_end = to_millis(bounds_end)
                            if 'extended_bounds' not in date_hist:
                                date_hist['extended_bounds'] = {'min': bounds_start, 'max': bounds_end}
                            else:
                                date_hist['extended_bounds']['min'] = bounds_start
                                date_hist['extended_bounds']['max'] = bounds_end
                        
                        # Ensure format is set for readable dates
                        if 'format' not in date_hist:
                            date_hist['format'] = 'yyyy-MM-dd'
                        
                        # Ensure fixed_interval for daily data
                        if 'fixed_interval' not in date_hist and 'calendar_interval' not in date_hist:
                            date_hist['fixed_interval'] = '1d'
                
                # Recurse into nested structures
                for value in obj.values():
                    fix_histogram_recursive(value)
            elif isinstance(obj, list):
                for item in obj:
                    fix_histogram_recursive(item)
        
        # Aggregations live inside the OpenSearch query body (query["aggs"]/query["aggregations"]).
        # Some callers also place them at the envelope level, so we support both.
        aggs = (
            (query.get('aggs') if isinstance(query, dict) else None)
            or (query.get('aggregations') if isinstance(query, dict) else None)
            or query_payload.get('aggs')
            or query_payload.get('aggregations')
        )
        if aggs:
            fix_histogram_recursive(aggs)
    
    def _sanitize_terms_lookup(self, node: Any) -> bool:
        """Recursively remove unsupported terms_lookup structures - DEPRECATED, use _remove_terms_lookup_recursive."""
        """Recursively remove unsupported terms_lookup structures."""
        changed = False
        if isinstance(node, dict):
            keys = list(node.keys())
            for key in keys:
                value = node[key]
                
                # Remove terms_lookup entirely - it's not supported
                # Check for both direct key and nested structures
                if key == 'terms_lookup':
                    logger.warning(f"Removing unsupported terms_lookup from query at key: {key}")
                    node.pop('terms_lookup', None)
                    changed = True
                    continue
                
                # Check if value itself is a terms_lookup structure (dict with 'field' and other lookup keys)
                if isinstance(value, dict):
                    # Detect terms_lookup by common keys: 'field', 'index', 'id', 'path', 'routing'
                    lookup_keys = {'field', 'index', 'id', 'path', 'routing', 'terms'}
                    if lookup_keys.intersection(set(value.keys())):
                        # This might be a terms_lookup - check if it looks like one
                        if 'field' in value and ('index' in value or 'id' in value or 'path' in value):
                            logger.warning(f"Removing terms_lookup-like structure at key: {key}")
                            node.pop(key, None)
                            changed = True
                            continue
                    
                    # Handle terms queries that might contain terms_lookup
                    if key == 'terms' and isinstance(value, dict):
                        # Check if this terms dict contains terms_lookup
                        if 'terms_lookup' in value:
                            logger.warning("Removing terms_lookup from terms query")
                            value.pop('terms_lookup', None)
                            changed = True
                        
                        # Also check each field in the terms dict
                        for field_name, field_clause in list(value.items()):
                            if isinstance(field_clause, dict):
                                # Check if field_clause is a terms_lookup structure
                                if 'terms_lookup' in field_clause:
                                    logger.warning(f"Removing terms_lookup from terms field: {field_name}")
                                    value.pop(field_name, None)
                                    changed = True
                                elif 'field' in field_clause and ('index' in field_clause or 'id' in field_clause):
                                    # This looks like a terms_lookup structure
                                    logger.warning(f"Removing terms_lookup structure from terms field: {field_name}")
                                    value.pop(field_name, None)
                                    changed = True
                                else:
                                    sanitized_clause, clause_changed = self._normalize_terms_clause(field_clause)
                                    if clause_changed:
                                        if sanitized_clause is None:
                                            value.pop(field_name, None)
                                        else:
                                            value[field_name] = sanitized_clause
                                        changed = True
                    
                    # Recursively sanitize nested structures
                    if self._sanitize_terms_lookup(value):
                        changed = True
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict) and self._sanitize_terms_lookup(item):
                            changed = True
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, dict) and self._sanitize_terms_lookup(item):
                    changed = True
        
        return changed
    
    def _normalize_terms_clause(self, clause: Any) -> Tuple[Optional[Any], bool]:
        """Normalize a single terms clause, converting lookup syntax when possible."""
        if isinstance(clause, dict):
            # Direct terms array stored under 'terms'
            if 'terms' in clause and isinstance(clause['terms'], list):
                return clause['terms'], True
            
            if 'terms_lookup' in clause:
                values = self._extract_terms_from_lookup(clause['terms_lookup'])
                return (values if values is not None else None, True)
            
            # Clause might itself be lookup-like (no explicit keyword)
            values = self._extract_terms_from_lookup(clause)
            if values is not None:
                return values, True
        
        return clause, False
    
    def _extract_terms_from_lookup(self, lookup: Any) -> Optional[List[Any]]:
        """Extract literal term values from a lookup dict if provided."""
        if not isinstance(lookup, dict):
            return None
        
        values = lookup.get('terms') or lookup.get('values') or lookup.get('list')
        if isinstance(values, list):
            return values
        
        # Sometimes provided as comma-separated string
        if isinstance(values, str):
            split_values = [v.strip() for v in values.split(',') if v.strip()]
            if split_values:
                return split_values
        
        return None
    
    def _normalize_field_for_filter(self, field_name: str) -> str:
        """Use keyword subfield where possible to keep filters exact."""
        if not field_name:
            return field_name

        if field_name.endswith('.keyword'):
            return field_name

        # Known keyword fields - use directly without .keyword suffix
        if field_name in self.NATIVE_KEYWORD_FIELDS:
            return field_name

        field_info = self._get_field_info(field_name)
        if field_info and field_info.get('has_keyword'):
            return field_info.get('keyword_field', f"{field_name}.keyword")
        return field_name

    def _append_filter_clause(self, bool_query: Dict[str, Any], clause: Dict[str, Any]):
        """Append a clause if it's not already present."""
        filters = bool_query.setdefault('filter', [])
        if not isinstance(filters, list):
            filters = bool_query['filter'] = [filters]

        if not self._filter_exists(filters, clause):
            filters.append(clause)

    def _filter_exists(self, existing_filters: List[Any], new_clause: Dict[str, Any]) -> bool:
        """Detect if a semantically identical clause already exists."""
        for existing in existing_filters:
            if existing == new_clause:
                return True
        return False
    
    def _remove_filter_clause(self, bool_query: Dict[str, Any], field_name: str):
        """Remove any filter clauses that match the given field name (term, range, etc.)."""
        # Check both 'filter' and 'must' arrays
        for clause_type in ['filter', 'must']:
            clauses = bool_query.get(clause_type, [])
            if not isinstance(clauses, list):
                continue
            
            # Remove any filter that matches the field (term, range, etc.)
            filtered_clauses = []
            for clause in clauses:
                if not isinstance(clause, dict):
                    filtered_clauses.append(clause)
                    continue
                
                # Check for term filter
                if 'term' in clause and isinstance(clause['term'], dict) and field_name in clause['term']:
                    continue
                
                # Check for range filter
                if 'range' in clause and isinstance(clause['range'], dict) and field_name in clause['range']:
                    continue
                
                # Keep the clause if it doesn't match
                filtered_clauses.append(clause)
            
            bool_query[clause_type] = filtered_clauses

    def _ensure_module_status_default(self, bool_query: Dict[str, Any], user_message: str, index_id: str = None):
        """
        Ensure module queries default to module_status = 0 (or status = 0 for catalog) unless explicitly overridden.
        
        CRITICAL: Only applies to consumption index. For catalog index, uses 'status' field.
        Does NOT apply to user profile index (no module fields).
        """
        # CRITICAL: Only enforce for consumption index - catalog index uses 'status', user profile has no modules
        if not index_id:
            # Try to get from query_payload if available
            return
        
        # Map index_id to check if it's consumption index
        is_consumption_index = False
        is_catalog_index = False
        
        if index_id in settings.OPENSEARCH_INDEXES:
            if index_id == 'module_consumption_data':
                is_consumption_index = True
            elif index_id == 'module_catalog_data':
                is_catalog_index = True
            elif index_id in ('user_profile_data', 'monthly_user_activity_data'):
                # User profile / monthly activity indices have no module fields - do nothing
                return
        else:
            # Check if it's the actual index name
            if 'consumption' in str(index_id).lower():
                is_consumption_index = True
            elif 'summary_reports' in str(index_id).lower() and 'consumption' not in str(index_id).lower():
                is_catalog_index = True
            elif 'monthly_user_activity' in str(index_id).lower():
                return
        
        # CRITICAL: For catalog index, DO NOT add module_status - it doesn't exist!
        # Only enforce for consumption index
        if not is_consumption_index:
            if is_catalog_index:
                logger.info(f"Skipping module_status enforcement for catalog index {index_id} - uses 'status' field instead")
            return
        
        if not user_message:
            return
        message_lower = user_message.lower()
        
        # CRITICAL: Check for assigned modules queries - these MUST always have module_status = 0 (consumption only)
        assigned_keywords = ['assigned', 'assignment', 'assignments', 'enrolled', 'delivered']
        has_assigned_keyword = any(keyword in message_lower for keyword in assigned_keywords)
        
        # Check if this is about modules (either explicit module keywords OR assigned keywords)
        has_module_keyword = any(keyword in message_lower for keyword in self.MODULE_KEYWORDS)
        
        # If query mentions assigned modules or assignments, ALWAYS enforce module_status = 0 (consumption index only)
        if has_assigned_keyword or has_module_keyword:
            # Skip if user explicitly asked for non-published modules
            if any(keyword in message_lower for keyword in self.MODULE_NON_PUBLISHED_KEYWORDS):
                return
            
            # Skip if module_status filter already exists
            if self._has_field_filter(bool_query, 'module_status'):
                return
            
            # Add module_status = 0 filter (ONLY for consumption index)
            self._append_filter_clause(bool_query, {"term": {"module_status": 0}})
            if has_assigned_keyword:
                logger.info(f"Enforced module_status = 0 for assigned modules query (index: {index_id})")

    def _has_module_status_filter(self, bool_query: Dict[str, Any]) -> bool:
        return self._has_field_filter(bool_query, 'module_status')

    def _has_field_filter(self, bool_query: Dict[str, Any], field_name: str) -> bool:
        for clause_type in ['filter', 'must']:
            clauses = bool_query.get(clause_type, [])
            if not isinstance(clauses, list):
                clauses = [clauses]
            for clause in clauses:
                if not isinstance(clause, dict):
                    continue
                term_clause = clause.get('term')
                if isinstance(term_clause, dict):
                    for key in term_clause.keys():
                        if self._field_matches_target(key, field_name):
                            return True
                terms_clause = clause.get('terms')
                if isinstance(terms_clause, dict):
                    for key in terms_clause.keys():
                        if self._field_matches_target(key, field_name):
                            return True
        return False

    def _has_field_range_clause(self, bool_query: Dict[str, Any], field_name: str) -> bool:
        for clause_type in ['filter', 'must']:
            clauses = bool_query.get(clause_type, [])
            if not isinstance(clauses, list):
                clauses = [clauses]
            for clause in clauses:
                if not isinstance(clause, dict):
                    continue
                range_clause = clause.get('range')
                if isinstance(range_clause, dict):
                    for key in range_clause.keys():
                        if self._field_matches_target(key, field_name):
                            return True
        return False

    @staticmethod
    def _field_matches_target(field_name: str, target: str) -> bool:
        if field_name == target:
            return True
        if field_name.endswith('.keyword') and field_name[:-8] == target:
            return True
        return False
    
    def _remove_prohibited_filters_from_top_level(self, query_body: Dict[str, Any]):
        """
        Remove prohibited filters from top-level query for "zero completions in range" queries.
        
        CRITICAL: For inactivity queries, these filters MUST NOT be at the top level:
        - completed_status (any value)
        - completed_date (any range)
        - assigned_status (any value)
        
        These filters must ONLY exist inside the per-user aggregations, not at the top level.
        If they're at the top level, they filter out users before aggregation can see them,
        making it impossible to find users with zero completions.
        """
        query = query_body.get('query', {})
        if not isinstance(query, dict):
            return
        
        bool_query = query.get('bool', {})
        if not isinstance(bool_query, dict):
            return
        
        # Remove from both 'filter' and 'must' arrays
        for clause_type in ['filter', 'must']:
            clauses = bool_query.get(clause_type, [])
            if not isinstance(clauses, list):
                continue
            
            # Remove prohibited filters
            filtered_clauses = []
            for clause in clauses:
                if not isinstance(clause, dict):
                    filtered_clauses.append(clause)
                    continue
                
                # Remove term filters on completed_status or assigned_status
                if 'term' in clause and isinstance(clause['term'], dict):
                    if 'completed_status' in clause['term'] or 'assigned_status' in clause['term']:
                        logger.warning(f"Removed prohibited {clause_type} filter: {clause}")
                        continue
                
                # Remove range filters on completed_date
                if 'range' in clause and isinstance(clause['range'], dict):
                    if 'completed_date' in clause['range']:
                        logger.warning(f"Removed prohibited {clause_type} range filter on completed_date: {clause}")
                        continue
                
                # Keep all other clauses
                filtered_clauses.append(clause)
            
            if len(filtered_clauses) < len(clauses):
                bool_query[clause_type] = filtered_clauses
                logger.info(f"Removed {len(clauses) - len(filtered_clauses)} prohibited filters from top-level {clause_type}")

    def _is_rating_query(self, user_message: Optional[str], query_payload: Optional[Dict[str, Any]]) -> bool:
        message_lower = (user_message or "").lower()
        if any(keyword in message_lower for keyword in self.RATING_KEYWORDS):
            return True
        if query_payload and self._object_references_field(query_payload, self.RATING_FIELD):
            return True
        return False

    def _object_references_field(self, obj: Any, field_name: str) -> bool:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key == 'field' and isinstance(value, str) and self._field_matches_target(value, field_name):
                    return True
                if self._object_references_field(value, field_name):
                    return True
        elif isinstance(obj, list):
            for item in obj:
                if self._object_references_field(item, field_name):
                    return True
        return False

    def _enforce_rating_filters(self, bool_query: Dict[str, Any]):
        """Ensure rating queries only include valid rating events."""
        self._append_filter_clause(bool_query, {"term": {"completed_status": 1}})
        self._append_filter_clause(bool_query, {"exists": {"field": self.RATING_FIELD}})
        if not self._has_field_range_clause(bool_query, self.RATING_FIELD) and self.RATING_MIN_VALUE is not None:
            rating_range_clause = {"range": {self.RATING_FIELD: {"gt": self.RATING_MIN_VALUE}}}
            self._append_filter_clause(bool_query, rating_range_clause)
        self._remove_field_range_filters(bool_query, 'created_on')

    def _remove_field_range_filters(self, bool_query: Dict[str, Any], field_name: str):
        """Remove range filters targeting a specific field."""
        for clause_type in ['filter', 'must']:
            clauses = bool_query.get(clause_type, [])
            if not isinstance(clauses, list):
                continue
            cleaned: List[Any] = []
            removed = 0
            for clause in clauses:
                if (
                    isinstance(clause, dict)
                    and 'range' in clause
                    and isinstance(clause['range'], dict)
                    and any(self._field_matches_target(key, field_name) for key in clause['range'].keys())
                ):
                    removed += 1
                    logger.info(f"Removed range filter on {field_name} from {clause_type}: {clause}")
                    continue
                cleaned.append(clause)
            if removed > 0:
                bool_query[clause_type] = cleaned
    
    def _replace_epoch_with_date_math(
        self,
        query_payload: Dict[str, Any],
        timeframe_key: str,
        time_period: Optional[TimePeriod]
    ):
        """
        Replace hardcoded epoch timestamps with OpenSearch date math expressions
        for relative timeframes. This ensures queries stay dynamic.
        
        Only replaces timestamps that are approximately within the expected timeframe range
        to avoid replacing unrelated date filters.
        """
        from .time_handler import TimeframeResolver
        
        # Get date math expressions for this timeframe
        date_math = TimeframeResolver._get_date_math_expressions(timeframe_key)
        if not date_math:
            return
        
        # For relative timeframes, we want to replace ANY recent epoch timestamps
        # with date math, as they're likely from the LLM generating hardcoded values
        # Calculate a reasonable range - timestamps from last 2 years are likely relative
        from datetime import datetime, timezone
        now_ts = int(datetime.now(timezone.utc).timestamp())
        two_years_ago = now_ts - (2 * 365 * 24 * 60 * 60)
        
        # If we have time_period, use it for more precise matching
        if time_period and not time_period.is_lifetime:
            expected_gte = time_period.start_timestamp
            expected_lt = time_period.end_timestamp
            # Use larger tolerance to catch approximate matches
            tolerance = 60 * 24 * 60 * 60  # 60 days tolerance
        else:
            # For relative timeframes without precise time_period, replace any recent timestamps
            expected_gte = two_years_ago
            expected_lt = now_ts
            tolerance = 90 * 24 * 60 * 60  # 90 days tolerance
        
        # Recursively replace epoch timestamps with date math in the query
        def replace_in_dict(obj: Any, path: str = "") -> None:
            """Recursively replace epoch timestamps with date math in range queries."""
            if isinstance(obj, dict):
                # Check if this is a range query
                if 'range' in obj:
                    range_obj = obj['range']
                    if isinstance(range_obj, dict):
                        for field_name, range_clause in range_obj.items():
                            if isinstance(range_clause, dict):
                                replaced = False
                                
                                # Check if gte is a numeric timestamp that should be replaced
                                if 'gte' in range_clause and isinstance(range_clause['gte'], (int, float)):
                                    gte_value = range_clause['gte']
                                    # Replace if it's a recent timestamp (within last 2 years) or matches expected range
                                    is_recent = two_years_ago <= gte_value <= now_ts
                                    matches_expected = abs(gte_value - expected_gte) <= tolerance if time_period else False
                                    
                                    if is_recent or matches_expected:
                                        range_clause['gte'] = date_math['gte']
                                        logger.info(f"Replaced epoch timestamp {gte_value} with date math '{date_math['gte']}' in field '{field_name}' (relative timeframe: {timeframe_key})")
                                        replaced = True
                                
                                # Check if lt/lte is a numeric timestamp that should be replaced
                                for end_key in ['lt', 'lte']:
                                    if end_key in range_clause and isinstance(range_clause[end_key], (int, float)):
                                        end_value = range_clause[end_key]
                                        # Replace if it's a recent timestamp (within last 2 years) or matches expected range
                                        is_recent = two_years_ago <= end_value <= now_ts
                                        matches_expected = abs(end_value - expected_lt) <= tolerance if time_period else False
                                        
                                        if is_recent or matches_expected:
                                            range_clause[end_key] = date_math['lt']
                                            logger.info(f"Replaced epoch timestamp {end_value} with date math '{date_math['lt']}' in field '{field_name}' (relative timeframe: {timeframe_key})")
                                            replaced = True
                                
                                if replaced:
                                    logger.info(f"Replaced hardcoded timestamps with date math expressions for timeframe '{timeframe_key}' in field '{field_name}'")
                
                # Recursively process nested structures (query, aggs, etc.)
                for key, value in obj.items():
                    replace_in_dict(value, f"{path}.{key}" if path else key)
            
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    replace_in_dict(item, f"{path}[{i}]" if path else f"[{i}]")
        
        # Process the entire query payload
        replace_in_dict(query_payload)
    
    def _deduplicate_filters(self, query_payload: Dict[str, Any]):
        """Remove duplicate filter clauses from the query."""
        query_body = query_payload.get('query', {})
        if not isinstance(query_body, dict):
            return
        
        query = query_body.get('query', {})
        if not isinstance(query, dict):
            return
        
        bool_query = query.get('bool', {})
        if not isinstance(bool_query, dict):
            return
        
        # Deduplicate 'must' and 'filter' arrays
        for clause_type in ['must', 'filter', 'should', 'must_not']:
            clauses = bool_query.get(clause_type, [])
            if not isinstance(clauses, list):
                continue
            
            # Remove duplicates by converting to set of JSON strings, then back
            seen = set()
            unique_clauses = []
            for clause in clauses:
                # Convert clause to JSON string for comparison
                clause_str = str(sorted(clause.items())) if isinstance(clause, dict) else str(clause)
                if clause_str not in seen:
                    seen.add(clause_str)
                    unique_clauses.append(clause)
            
            if len(unique_clauses) < len(clauses):
                bool_query[clause_type] = unique_clauses
                logger.info(f"Removed {len(clauses) - len(unique_clauses)} duplicate {clause_type} clauses")
    
    def _convert_module_name_terms_to_match(self, bool_query: Dict[str, Any]):
        """Convert term queries for module_name to match_phrase for fuzzy matching."""
        def convert_clause(clause: Any) -> Any:
            """Recursively convert module_name term queries to match_phrase."""
            if isinstance(clause, dict):
                # Check if this is a term query for module_name
                if 'term' in clause and isinstance(clause['term'], dict) and 'module_name' in clause['term']:
                    module_value = clause['term']['module_name']
                    return {
                        "match_phrase": {
                            "module_name": {
                                "query": str(module_value),
                                "slop": 2
                            }
                        }
                    }
                # Check if this is a nested bool query
                elif 'bool' in clause:
                    nested_bool = clause['bool']
                    if isinstance(nested_bool, dict):
                        for clause_type in ['filter', 'must', 'should', 'must_not']:
                            if clause_type in nested_bool:
                                nested_clauses = nested_bool[clause_type]
                                if isinstance(nested_clauses, list):
                                    nested_bool[clause_type] = [convert_clause(c) for c in nested_clauses]
                                else:
                                    nested_bool[clause_type] = convert_clause(nested_clauses)
                    return clause
            return clause
        
        # Convert clauses in filter and must arrays
        for clause_type in ['filter', 'must']:
            clauses = bool_query.get(clause_type, [])
            if isinstance(clauses, list):
                bool_query[clause_type] = [convert_clause(c) for c in clauses]
            elif clauses:
                bool_query[clause_type] = convert_clause(clauses)
    
    def _format_date_fields(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert epoch timestamps into readable ISO strings for known date fields."""
        if not records:
            return records
        
        for record in records:
            for field_name in self.DATE_FIELDS:
                if field_name in record:
                    formatted = self._format_timestamp(record[field_name])
                    if formatted:
                        record[field_name] = formatted
        return records
    
    def _format_timestamp(self, value: Any) -> Optional[str]:
        """Convert numeric timestamp (seconds or ms) into readable date string."""
        if value in (None, '', 0):
            return value
        
        try:
            if isinstance(value, str):
                stripped = value.strip()
                if not stripped:
                    return value
                # Check if it's a digit string (epoch timestamp)
                if stripped.isdigit():
                    timestamp = int(stripped)
                else:
                    # Try to parse as date string first
                    try:
                        # If it's already a formatted date, return as-is
                        datetime.strptime(stripped, '%Y-%m-%d %H:%M:%S UTC')
                        return value
                    except ValueError:
                        # Not a recognized format, try as epoch
                        try:
                            timestamp = int(float(stripped))
                        except (ValueError, TypeError):
                            return value
            elif isinstance(value, (int, float)):
                timestamp = int(value)
            else:
                return value
            
            # Convert ms to seconds if needed (timestamps > year 2001 in ms are > 1e12)
            if timestamp > 1e12:
                timestamp = int(timestamp / 1000)
            
            if timestamp <= 0:
                return value
            
            # Format as readable date (e.g., "Dec 3, 2025")
            dt = datetime.utcfromtimestamp(timestamp)
            return dt.strftime('%b %d, %Y')
        except Exception:
            return value
    
    def _infer_time_field_from_query(self, query_body: Dict[str, Any]) -> Optional[str]:
        """Inspect a stored query to determine which date field was used."""
        if not query_body:
            return None
        
        query_section = query_body.get('query') if 'query' in query_body else query_body
        if not isinstance(query_section, dict):
            return None
        
        bool_clause = query_section.get('bool')
        if isinstance(bool_clause, dict):
            for clause_name in ['filter', 'must', 'should']:
                clauses = bool_clause.get(clause_name, [])
                if isinstance(clauses, dict):
                    clauses = [clauses]
                for clause in clauses or []:
                    if not isinstance(clause, dict):
                        continue
                    if 'range' in clause:
                        range_body = clause['range']
                        if isinstance(range_body, dict):
                            for field_name in range_body.keys():
                                return field_name
        elif 'range' in query_section:
            range_body = query_section['range']
            if isinstance(range_body, dict):
                for field_name in range_body.keys():
                    return field_name
        return None

    def _get_collapse_field(self, field_name: str) -> Optional[str]:
        """Determine the correct field to use for collapsing unique records."""
        # ID fields (uid, mid, cmid) are numeric and can be used directly for collapse
        if field_name in ['uid', 'mid', 'cmid']:
            return field_name
        
        # Known keyword fields - use directly without .keyword suffix
        if field_name in self.NATIVE_KEYWORD_FIELDS:
            return field_name
        
        field_info = self._get_field_info(field_name)
        if not field_info:
            return field_name

        if field_info.get('type') == 'keyword':
            return field_name

        if field_info.get('has_keyword'):
            return field_info.get('keyword_field', f"{field_name}.keyword")

        # Return None if we can't collapse on this field
        return None

    def _get_field_info(self, field_name: str) -> Optional[Dict[str, Any]]:
        """Lookup field metadata from loaded schemas."""
        for schema in self.schema_cache.values():
            for field in schema.get('fields', []):
                if field.get('name') == field_name:
                    return field
        return None

    def _infer_primary_entity(
        self,
        query_body: Dict[str, Any]
    ) -> Tuple[Optional[str], Optional[str]]:
        """Infer which field the primary metric represents (e.g., unique users)."""
        aggs = query_body.get('aggs', {})
        for agg in aggs.values():
            field = self._find_cardinality_field(agg)
            if field:
                return field, self._derive_entity_label(field)
        return None, None

    def _find_cardinality_field(self, agg_body: Any) -> Optional[str]:
        """Recursively search for a cardinality aggregation field."""
        if not isinstance(agg_body, dict):
            return None

        if 'cardinality' in agg_body:
            return agg_body['cardinality'].get('field')

        for value in agg_body.values():
            field = self._find_cardinality_field(value)
            if field:
                return field
        return None

    def _derive_entity_label(self, field_name: str) -> str:
        """Human-readable label for the primary entity field."""
        label_map = {
            'email_addr': 'Users',
            'module_name': 'Modules',
            'city': 'Cities',
            'country': 'Countries',
            'skill_name': 'Skills'
        }
        if field_name in label_map:
            return label_map[field_name]
        return field_name.replace('_', ' ').title()

    def _extract_primary_entity_count(
        self,
        aggregations: Dict[str, Any]
    ) -> Optional[int]:
        """Extract the numeric value associated with the primary entity."""
        if not aggregations:
            return None

        for agg in aggregations.values():
            count = self._extract_count_from_aggregation(agg)
            if count is not None:
                return int(count)

        return None

    def _extract_count_from_aggregation(self, agg_value: Any) -> Optional[int]:
        """Get numeric count from an aggregation result."""
        if not isinstance(agg_value, dict):
            return None

        if 'value' in agg_value and isinstance(agg_value['value'], (int, float)):
            return int(agg_value['value'])

        # Check nested aggregations first (to find cardinality inside filters)
        for value in agg_value.values():
            if isinstance(value, dict):
                nested = self._extract_count_from_aggregation(value)
                if nested is not None:
                    return nested

        if 'doc_count' in agg_value:
            return int(agg_value['doc_count'])

        if 'buckets' in agg_value:
            # Not ideal for counts, skip buckets
            return None

        return None
    
    def _execute_query(self, index_id: str, query: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute query against OpenSearch.
        
        Args:
            index_id: Single index_id string OR comma-separated string OR list of index_ids for multi-index queries
            query: OpenSearch query dictionary
        """
        if not query:
            return {'success': False, 'error': 'Empty query'}
        
        # Final sanitization pass right before execution - catch anything that slipped through
        self._remove_terms_lookup_recursive(query)
        
        # Validate and clean bool query structure
        self._validate_bool_query(query)
        
        # Additional aggressive pass: remove any dict that contains 'terms_lookup' key
        def remove_any_terms_lookup(obj: Any) -> bool:
            """Remove any structure containing terms_lookup, return True if something was removed."""
            changed = False
            if isinstance(obj, dict):
                keys_to_remove = []
                for key, value in list(obj.items()):
                    if key == 'terms_lookup':
                        keys_to_remove.append(key)
                        changed = True
                        logger.warning(f"Final pass: Removing terms_lookup at key: {key}")
                    elif isinstance(value, dict) and 'terms_lookup' in value:
                        keys_to_remove.append(key)
                        changed = True
                        logger.warning(f"Final pass: Removing dict containing terms_lookup at key: {key}")
                    elif isinstance(value, (dict, list)):
                        if remove_any_terms_lookup(value):
                            changed = True
                
                for key in keys_to_remove:
                    obj.pop(key, None)
            elif isinstance(obj, list):
                for item in obj:
                    if remove_any_terms_lookup(item):
                        changed = True
            return changed
        
        # Run the aggressive pass
        remove_any_terms_lookup(query)
        
        # CRITICAL: Final check - ensure module_count exists if any bucket_selector references it
        # This catches cases where sanitization might have removed it
        aggs = query.get('aggs', {})
        if isinstance(aggs, dict):
            def ensure_module_count_final(agg_dict: Dict[str, Any]):
                """Final pass to ensure module_count exists before bucket_selector."""
                if not isinstance(agg_dict, dict):
                    return
                sub_aggs = agg_dict.get('aggs', {})
                if not isinstance(sub_aggs, dict):
                    return
                
                # Check for bucket_selectors that reference module_count
                needs_module_count = False
                for key, value in sub_aggs.items():
                    if isinstance(value, dict) and 'bucket_selector' in value:
                        buckets_path = value.get('bucket_selector', {}).get('buckets_path', {})
                        if isinstance(buckets_path, dict):
                            if any(v == 'module_count' for v in buckets_path.values()):
                                needs_module_count = True
                                break
                
                # If we found bucket_selectors referencing module_count, ensure module_count exists
                if needs_module_count and 'module_count' not in sub_aggs:
                    sub_aggs['module_count'] = {
                        "cardinality": {"field": "mid"}
                    }
                    logger.info(f"FINAL FIX: Added missing module_count aggregation before execution")
                
                # Recursively check nested aggregations
                for key, value in sub_aggs.items():
                    if isinstance(value, dict):
                        ensure_module_count_final(value)
            
            for agg_name, agg_def in aggs.items():
                if isinstance(agg_def, dict):
                    ensure_module_count_final(agg_def)
        
        # Handle multiple indices - support string (single or comma-separated) or list
        if isinstance(index_id, list):
            index_ids = index_id
        elif isinstance(index_id, str) and ',' in index_id:
            index_ids = [idx.strip() for idx in index_id.split(',')]
        else:
            index_ids = [index_id]
        
        # Validate all indices exist and map them
        # Accept both index IDs (keys) and actual index names (values)
        validated_index_ids = []
        for idx_id in index_ids:
            # First check if it's an index ID (key)
            if idx_id in settings.OPENSEARCH_INDEXES:
                validated_index_ids.append(idx_id)
            else:
                # Check if it's an actual index name (value)
                found = False
                for key, value in settings.OPENSEARCH_INDEXES.items():
                    if value == idx_id:
                        # They passed the actual index name, use the key
                        validated_index_ids.append(key)
                        logger.info(f"Mapped actual index name '{idx_id}' to index ID '{key}'")
                        found = True
                        break
        
                if not found:
                    available_ids = list(settings.OPENSEARCH_INDEXES.keys())
                    available_names = list(settings.OPENSEARCH_INDEXES.values())
                    error_msg = (
                        f"Index '{idx_id}' not found in configuration. "
                        f"Available index IDs: {available_ids}. "
                        f"Available index names: {available_names}"
                    )
                    logger.error(error_msg)
                    return {'success': False, 'error': error_msg}
        
        # Pass single index_id or comma-separated string to execute_query
        # execute_query will handle the conversion to actual index names
        index_param = ','.join(validated_index_ids) if len(validated_index_ids) > 1 else validated_index_ids[0]
        logger.info(f"Executing query on {len(validated_index_ids)} index(es): {index_param}")
        
        result = self.os_client.execute_query(index_param, query)
        
        if result.get('success'):
            if 'results' in result:
                result['results'] = self._format_date_fields(result.get('results', []))
            if result.get('aggregations'):
                result['aggregations'] = self._enhance_aggregations(result['aggregations'])
        
        return result
    
    def _enhance_aggregations(self, aggregations: Dict[str, Any]) -> Dict[str, Any]:
        """Add human-readable labels to aggregation results and extract display names from sub-aggs."""
        enhanced = {}
        
        label_map = {
            'unique_users': 'Unique Users',
            'unique_modules': 'Unique Modules',
            'unique_emails': 'Unique Users',
            'total_records': 'Total Records',
            'completed_count': 'Completed',
            'completions_count': 'Completions',
            'assignments_count': 'Assignments',
            'by_city': 'By City',
            'by_module': 'By Module',
            'by_skill': 'By Skill',
            'completions_by_module': 'Completions per Module',
            'completions_by_city': 'Completions per City',
            'assigned_users': 'Assigned Users',
            'completed_users': 'Completed Users',
            'unique_modules_created': 'Unique Modules Created',
            'unique_modules_published': 'Unique Modules Published',
            'unique_modules_consumed': 'Unique Modules Consumed',
        }
        
        for name, value in aggregations.items():
            enhanced_value = dict(value) if isinstance(value, dict) else {'value': value}
            
            # Add label
            label = label_map.get(name)
            if not label:
                # Generate from name
                label = name.replace('_', ' ').title()
            enhanced_value['_label'] = label
            
            # Extract display names from sub-aggregations in buckets
            # When we group by mid/uid, we need to get the name from sub-aggs for display
            if 'buckets' in enhanced_value:
                buckets = enhanced_value['buckets']
                # Handle both list and dict formats for buckets
                if isinstance(buckets, dict):
                    # If buckets is a dict, iterate over values (bucket objects)
                    bucket_list = list(buckets.values())
                elif isinstance(buckets, list):
                    # If buckets is a list, use it directly
                    bucket_list = buckets
                else:
                    bucket_list = []
                
                for bucket in bucket_list:
                    # Skip if bucket is not a dict (shouldn't happen, but be safe)
                    if not isinstance(bucket, dict):
                        continue
                    
                    # FIRST: Extract module_name from top_hits (for unique modules queries)
                    # Structure: bucket['module_info']['hits']['hits'][0]['_source']['module_name']
                    if 'module_info' in bucket and isinstance(bucket['module_info'], dict):
                        module_info = bucket['module_info']
                        if 'hits' in module_info and isinstance(module_info['hits'], dict):
                            hits = module_info['hits']
                            if 'hits' in hits and isinstance(hits['hits'], list) and len(hits['hits']) > 0:
                                first_hit = hits['hits'][0]
                                if '_source' in first_hit and isinstance(first_hit['_source'], dict):
                                    source = first_hit['_source']
                                    module_name = source.get('module_name')
                                    if module_name:
                                        bucket['_display_name'] = module_name
                                        # CRITICAL: Also store original key before replacing
                                        bucket['_original_key'] = bucket.get('key')
                                        # Replace key with module_name so frontend displays the name
                                        bucket['key'] = module_name
                                        logger.info(f"Extracted module_name '{module_name}' from top_hits, replaced key {bucket.get('_original_key')} with module name")
                                        # ALSO set key to module_name so frontend definitely uses it
                                        # The frontend might be using 'key' instead of '_display_name'
                                        bucket['key'] = module_name
                                        logger.info(f"Extracted module_name '{module_name}' from top_hits for bucket key {bucket.get('key_as_string', bucket.get('key'))}")
                                    else:
                                        logger.warning(f"module_info top_hits found but module_name is missing in _source: {source.keys()}")
                                else:
                                    logger.warning(f"module_info top_hits found but _source is missing or not a dict")
                            else:
                                logger.warning(f"module_info hits found but hits['hits'] is empty or not a list")
                        else:
                            logger.warning(f"module_info found but 'hits' is missing or not a dict: {module_info.keys() if isinstance(module_info, dict) else type(module_info)}")
                    else:
                        # Log what we actually have in the bucket for debugging
                        if 'module_info' not in bucket:
                            logger.debug(f"Bucket key {bucket.get('key')} does not have 'module_info' aggregation. Available keys: {list(bucket.keys())}")
                    
                    # SECOND: Extract user name/email from top_hits (for unique users queries)
                    # Structure: bucket['user_details']['hits']['hits'][0]['_source']['first_name'/'last_name'/'email_addr']
                    if '_display_name' not in bucket:
                        for top_hits_name in ['user_details', 'user_info', 'top_hits']:
                            if top_hits_name in bucket and isinstance(bucket[top_hits_name], dict):
                                user_info = bucket[top_hits_name]
                                if 'hits' in user_info and isinstance(user_info['hits'], dict):
                                    hits = user_info['hits']
                                    if 'hits' in hits and isinstance(hits['hits'], list) and len(hits['hits']) > 0:
                                        first_hit = hits['hits'][0]
                                        if '_source' in first_hit and isinstance(first_hit['_source'], dict):
                                            source = first_hit['_source']
                                            first_name = source.get('first_name', '')
                                            last_name = source.get('last_name', '')
                                            email = source.get('email_addr', '')
                                            
                                            # Prefer name over email, but fall back to email if no name
                                            if first_name or last_name:
                                                display_name = f"{first_name} {last_name}".strip()
                                                bucket['_display_name'] = display_name
                                                # Also replace key so frontend uses the name
                                                bucket['key'] = display_name
                                                logger.info(f"Extracted user name '{display_name}' from {top_hits_name} top_hits, replaced key {bucket.get('_original_key', 'original')} with name")
                                                break
                                            elif email:
                                                bucket['_display_name'] = email
                                                bucket['key'] = email
                                                logger.info(f"Extracted user email '{email}' from {top_hits_name} top_hits, replaced key {bucket.get('_original_key', 'original')} with email")
                                                break
                    
                    # THIRD: Extract module_name from sub-aggregation (terms aggregation on module_name)
                    if '_display_name' not in bucket and 'module_name' in bucket and 'buckets' in bucket['module_name']:
                        name_buckets = bucket['module_name']['buckets']
                        if name_buckets:
                            bucket['_display_name'] = name_buckets[0].get('key', bucket.get('key'))
                    
                    # FOURTH: Extract user email/name from sub-aggregation (terms aggregation)
                    if '_display_name' not in bucket:
                        if 'user_email' in bucket and 'buckets' in bucket['user_email']:
                            email_buckets = bucket['user_email']['buckets']
                            if email_buckets:
                                bucket['_display_name'] = email_buckets[0].get('key', bucket.get('key'))
                        elif 'user_name' in bucket and 'buckets' in bucket['user_name']:
                            name_buckets = bucket['user_name']['buckets']
                            if name_buckets:
                                bucket['_display_name'] = name_buckets[0].get('key', bucket.get('key'))
                    
                    # FIFTH: Format date histogram timestamps (for time-based charts)
                    # Date histograms return epoch timestamps as keys - convert to readable dates
                    # NEVER show epoch timestamps in UI - always format to readable dates
                    if '_display_name' not in bucket:
                        key = bucket.get('key')
                        key_as_string = bucket.get('key_as_string')
                        
                        # Check if key is a timestamp (epoch seconds > 1e8 or milliseconds > 1e11)
                        # This catches timestamps from year 1973 onwards (seconds) or 2001 onwards (milliseconds)
                        is_timestamp = False
                        timestamp_seconds = None
                        
                        if isinstance(key, (int, float)):
                            # Check if it's a timestamp in seconds (1e8 to 1e10 range) or milliseconds (1e11+)
                            if 1e8 <= key <= 1e10:
                                # Epoch seconds
                                is_timestamp = True
                                timestamp_seconds = int(key)
                            elif key > 1e11:
                                # Epoch milliseconds
                                is_timestamp = True
                                timestamp_seconds = int(key / 1000)
                        
                        if is_timestamp:
                            # It's a timestamp - ALWAYS format it, never show epoch
                            try:
                                dt = datetime.utcfromtimestamp(timestamp_seconds)
                                
                                # Use key_as_string if available (from date_histogram format), otherwise format ourselves
                                if key_as_string:
                                    # key_as_string might be in "yyyy-MM-dd" format, convert to readable
                                    if re.match(r'^\d{4}-\d{2}-\d{2}$', key_as_string):
                                        # Parse yyyy-MM-dd and format to readable date
                                        try:
                                            date_parts = key_as_string.split('-')
                                            formatted_date = dt.strftime('%b %d, %Y')
                                            bucket['_display_name'] = formatted_date
                                            bucket['key'] = formatted_date
                                        except:
                                            bucket['_display_name'] = key_as_string
                                            bucket['key'] = key_as_string
                                    else:
                                        bucket['_display_name'] = key_as_string
                                        bucket['key'] = key_as_string
                                else:
                                    # Format based on likely interval (detect from bucket spacing if possible)
                                    # Default to readable date format
                                    formatted_date = dt.strftime('%b %d, %Y')
                                    bucket['_display_name'] = formatted_date
                                    bucket['key'] = formatted_date
                                
                                logger.debug(f"Formatted timestamp {key} to date: {bucket['_display_name']}")
                            except (ValueError, OSError) as e:
                                logger.warning(f"Failed to format timestamp {key}: {e}")
                                # Even if formatting fails, don't show raw epoch - use a placeholder
                                bucket['_display_name'] = f"Date {key}"
                                bucket['key'] = f"Date {key}"
                        
                        # If key_as_string exists but we haven't set display_name yet, use it
                        if '_display_name' not in bucket and key_as_string:
                            bucket['_display_name'] = key_as_string
                            bucket['key'] = key_as_string
                    
                    # Extract the count value
                    # For date histograms, ALWAYS use doc_count (actual number of completion records per day)
                    # For other aggregations, prefer nested cardinality over doc_count
                    is_date_histogram = 'key_as_string' in bucket or isinstance(bucket.get('key'), (int, float)) and bucket.get('key', 0) > 1e8
                    
                    if is_date_histogram:
                        # Date histogram: use doc_count (actual completions per day)
                        bucket['_count'] = bucket.get('doc_count', 0)
                    else:
                        # Other aggregations: prefer nested cardinality
                        if 'unique_users' in bucket:
                            bucket['_count'] = bucket['unique_users'].get('value', bucket.get('doc_count', 0))
                        elif 'unique_modules' in bucket:
                            bucket['_count'] = bucket['unique_modules'].get('value', bucket.get('doc_count', 0))
                        else:
                            bucket['_count'] = bucket.get('doc_count', 0)
                    
                    # If no display name extracted, use the key
                    if '_display_name' not in bucket:
                        bucket['_display_name'] = bucket.get('key')
            
            enhanced[name] = enhanced_value
        
        return enhanced
    
    def _extract_metrics(self, query_results: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
        """Extract metrics with labels from query results."""
        metrics = {}
        
        aggregations = query_results.get('aggregations', {})
        for name, value in aggregations.items():
            if isinstance(value, dict):
                label = value.get('_label', name.replace('_', ' ').title())
                
                # CRITICAL: For completion rate queries, extract the completion_rate value directly
                if name == 'scope':
                    buckets = value.get('buckets', {})
                    # Handle both dict and list formats for buckets
                    if isinstance(buckets, dict):
                        all_bucket = buckets.get('all', {})
                    elif isinstance(buckets, list) and len(buckets) > 0:
                        # If buckets is a list, use the first bucket
                        all_bucket = buckets[0]
                    else:
                        all_bucket = {}
                    
                    if isinstance(all_bucket, dict):
                        completion_rate = all_bucket.get('completion_rate', {})
                        
                        # Check for user completion rate structure FIRST (assigned_modules/completed_modules)
                        # This is the pattern used by _force_user_completion_rate_query_structure
                        assigned_modules = all_bucket.get('assigned_modules', {})
                        completed = all_bucket.get('completed', {})
                        if isinstance(assigned_modules, dict) and 'value' in assigned_modules and isinstance(completed, dict):
                            completed_modules = completed.get('completed_modules', {})
                            if isinstance(completed_modules, dict) and 'value' in completed_modules:
                                # This is a user completion rate query (uses cardinality on mid)
                                # Extract rate_value from completion_rate if available, otherwise calculate it
                                if isinstance(completion_rate, dict) and 'value' in completion_rate:
                                    rate_value = completion_rate.get('value', 0)
                                else:
                                    # Calculate completion rate manually if bucket_script didn't provide it
                                    assigned_count = assigned_modules.get('value', 0)
                                    completed_count = completed_modules.get('value', 0)
                                    rate_value = (completed_count / assigned_count * 100) if assigned_count > 0 else 0
                                
                                metrics[name] = {
                                    'label': 'Completion Rate',
                                    'value': f"{rate_value:.1f}%",
                                    'type': 'percentage'
                                }
                                continue  # Skip normal processing for completion rate
                        
                        # Check for module completion rate structure (assigned/completed_count)
                        # This is the pattern used by _force_module_completion_rate_query_structure
                        if isinstance(completion_rate, dict) and 'value' in completion_rate:
                            rate_value = completion_rate.get('value', 0)
                            metrics[name] = {
                                'label': 'Completion Rate',
                                'value': f"{rate_value:.1f}%",
                                'type': 'percentage'
                            }
                            continue  # Skip normal processing for completion rate
                
                if 'value' in value:
                    metrics[name] = {
                        'label': label,
                        'value': int(value['value']),
                        'type': 'count'
                    }
                elif 'buckets' in value:
                    buckets = value['buckets']
                    # Handle both list and dict formats for buckets
                    if isinstance(buckets, dict):
                        # If buckets is a dict, convert to list of bucket objects
                        bucket_list = list(buckets.values())
                    elif isinstance(buckets, list):
                        # If buckets is a list, use it directly
                        bucket_list = buckets
                    else:
                        bucket_list = []
                    
                    # Check if this is a date histogram (has key_as_string or date-like keys)
                    is_date_histogram = len(bucket_list) > 0 and isinstance(bucket_list[0], dict) and (
                        'key_as_string' in bucket_list[0] or
                        (isinstance(bucket_list[0].get('key'), (int, float)) and bucket_list[0].get('key', 0) > 1e8) or
                        (isinstance(bucket_list[0].get('key'), str) and ('-' in bucket_list[0].get('key', '') or len(bucket_list[0].get('key', '')) == 10))
                    )
                    
                    if is_date_histogram:
                        # For date histograms, sum all doc_count values (total completions)
                        total_completions = sum(bucket.get('doc_count', 0) for bucket in bucket_list if isinstance(bucket, dict))
                        metrics[name] = {
                            'label': label,
                            'value': total_completions,
                            'type': 'count'
                        }
                    else:
                        # For other aggregations, use number of buckets (categories)
                        metrics[name] = {
                            'label': label,
                            'value': len(bucket_list),
                            'type': 'breakdown'
                        }
                elif 'doc_count' in value:
                    metrics[name] = {
                        'label': label,
                        'value': int(value['doc_count']),
                        'type': 'count'
                    }
        
        return metrics
    
    def _extract_completion_rate_message(
        self,
        query_results: Dict[str, Any],
        original_query: str,
        time_period: Optional[TimePeriod]
    ) -> Optional[str]:
        """
        Extract completion rate directly from query results and return formatted message.
        Returns None if not a completion rate query.
        """
        aggregations = query_results.get('aggregations', {})
        if not aggregations:
            return None
        
        # Check for scope.buckets.all.completion_rate.value structure
        scope_agg = aggregations.get('scope', {})
        if not isinstance(scope_agg, dict):
            return None
        
        buckets = scope_agg.get('buckets', {})
        # Handle both dict and list formats for buckets
        if isinstance(buckets, dict):
            all_bucket = buckets.get('all', {})
        elif isinstance(buckets, list) and len(buckets) > 0:
            # If buckets is a list, use the first bucket
            all_bucket = buckets[0]
        else:
            return None
        
        if not isinstance(all_bucket, dict):
            return None
        
        completion_rate = all_bucket.get('completion_rate', {})
        if not isinstance(completion_rate, dict) or 'value' not in completion_rate:
            return None
        
        # Extract values - check for both module completion rate (value_count on uid) and user completion rate (cardinality on mid)
        rate_value = completion_rate.get('value', 0)
        
        # Check if this is user completion rate FIRST (has assigned_modules.cardinality and completed.completed_modules.cardinality)
        # This is the pattern used by _force_user_completion_rate_query_structure
        assigned_modules = all_bucket.get('assigned_modules', {})
        completed = all_bucket.get('completed', {})
        
        if isinstance(assigned_modules, dict) and 'value' in assigned_modules and isinstance(completed, dict):
            completed_modules = completed.get('completed_modules', {})
            if isinstance(completed_modules, dict) and 'value' in completed_modules:
                # User completion rate structure
                assigned_count = assigned_modules.get('value', 0)
                completed_count = completed_modules.get('value', 0)
                
                # Extract user name from query if available
                user_name = None
                import re
                # Look for patterns like "completion rate of X", "X's completion rate", "what is X's completion rate"
                patterns = [
                    r'([a-z]+(?:\s+[a-z]+)?)\'?s?\s+completion\s+rate',  # "Tanuj's completion rate" or "Tanuj Sadasivam's completion rate"
                    r'completion\s+rate\s+(?:of|for)\s+["\']?([^"\']+?)["\']?',  # "completion rate of Tanuj"
                    r'what\s+(?:is|was)\s+(?:the\s+)?completion\s+rate\s+(?:of|for)\s+["\']?([^"\']+?)["\']?',  # "what is the completion rate of Tanuj"
                    r'what\s+(?:is|was)\s+([a-z]+(?:\s+[a-z]+)?)\'?s?\s+completion\s+rate',  # "what is Tanuj's completion rate"
                ]
                for pattern in patterns:
                    match = re.search(pattern, original_query, re.IGNORECASE)
                    if match:
                        user_name = match.group(1).strip()
                        # Remove trailing punctuation
                        user_name = re.sub(r'[?.!]+$', '', user_name)
                        # Remove common words
                        user_name = re.sub(r'^(?:the|a|an)\s+', '', user_name, flags=re.IGNORECASE)
                        # Remove possessive markers if any
                        user_name = re.sub(r"'s\s*$", "", user_name, flags=re.IGNORECASE)
                        user_name = user_name.strip()
                        if user_name and len(user_name) > 2:
                            break
                
                time_context = f" {time_period.description}" if time_period and not time_period.is_lifetime else ""
                
                if user_name:
                    message = f"The completion rate for {user_name} is {rate_value:.1f}% ({int(completed_count)} modules completed out of {int(assigned_count)} modules assigned){time_context}."
                else:
                    message = f"The completion rate is {rate_value:.1f}% ({int(completed_count)} modules completed out of {int(assigned_count)} modules assigned){time_context}."
                
                return message
        
        # Check if this is module completion rate (has assigned.value_count and completed.completed_count.value_count)
        assigned = all_bucket.get('assigned', {})
        if isinstance(assigned, dict) and 'value' in assigned:
            # Module completion rate structure
            assigned_count = assigned.get('value', 0)
            completed_count_agg = completed.get('completed_count', {}) if isinstance(completed, dict) else {}
            completed_count = completed_count_agg.get('value', 0) if isinstance(completed_count_agg, dict) else 0
            
            # Extract module name from query if available
            module_name = None
            if 'situational leadership' in original_query.lower():
                module_name = "Situational Leadership"
            elif 'module' in original_query.lower():
                import re
                match = re.search(r'(?:completion rate|rate).*?(?:of|for)\s+["\']?([^"\']+?)["\']?', original_query, re.IGNORECASE)
                if match:
                    module_name = match.group(1).strip()
            
            time_context = f" {time_period.description}" if time_period and not time_period.is_lifetime else ""
            
            if module_name:
                message = f"The completion rate for {module_name} module is {rate_value:.1f}% ({int(completed_count)} completed out of {int(assigned_count)} assigned){time_context}."
            else:
                message = f"The completion rate is {rate_value:.1f}% ({int(completed_count)} completed out of {int(assigned_count)} assigned){time_context}."
            
            return message
        
        return None
    
    def _create_fallback_message(
        self,
        query_results: Dict[str, Any],
        time_period: Optional[TimePeriod]
    ) -> str:
        """Create a safe fallback message using only query results."""
        return ResponseValidator.create_safe_response(
            query_results,
            'query',
            time_period.description if time_period else None
        )
    
    def _get_visualization_config(
        self,
        response_type: ResponseType,
        query_results: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Get visualization configuration based on response type."""
        viz_map = {
            ResponseType.BAR_CHART: {'type': 'bar'},
            ResponseType.LINE_CHART: {'type': 'line'},
            ResponseType.PIE_CHART: {'type': 'pie'},
            ResponseType.COMPARISON: {'type': 'comparison_bar'},
        }
        
        config = viz_map.get(response_type)
        if config:
            # Add data source info
            aggregations = query_results.get('aggregations', {})
            if aggregations:
                # Find the first aggregation with buckets
                for name, value in aggregations.items():
                    if isinstance(value, dict) and 'buckets' in value:
                        config['data_key'] = name
                        logger.info(f"Found aggregation '{name}' with {len(value.get('buckets', []))} buckets for {response_type.value}")
                        break
                else:
                    # No buckets found - log warning
                    logger.warning(f"Chart response type {response_type.value} but no aggregations with buckets found!")
                    logger.warning(f"Aggregations keys: {list(aggregations.keys())}")
                    for name, value in aggregations.items():
                        logger.warning(f"  {name}: {type(value).__name__}, keys: {list(value.keys()) if isinstance(value, dict) else 'N/A'}")
        
        return config
    
    def _call_llm(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.2
    ) -> str:
        """Call Claude via AWS Bedrock."""
        try:
            client = boto3.client(
                'bedrock-runtime',
                region_name=settings.AWS_REGION,
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            )
            
            request_body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 4096,
                "system": system_prompt,
                "messages": messages,
                "temperature": temperature,
            }
            
            response = client.invoke_model(
                modelId=settings.BEDROCK_MODEL_ID,
                body=json.dumps(request_body),
                contentType="application/json",
                accept="application/json"
            )
            
            response_body = json.loads(response['body'].read())
            return response_body.get('content', [{}])[0].get('text', '')
            
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            raise
    
    def _parse_query_response(self, llm_response: str) -> Dict[str, Any]:
        """Parse the LLM's query generation response."""
        cleaned = llm_response.strip()
        
        # Try direct JSON parse
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        
        # Try extracting from code blocks
        import re
        for pattern in [r'```json\s*([\s\S]*?)\s*```', r'```\s*([\s\S]*?)\s*```']:
            matches = re.findall(pattern, cleaned)
            for match in matches:
                try:
                    return json.loads(match.strip())
                except json.JSONDecodeError:
                    continue
        
        # Try finding JSON object
        try:
            start = cleaned.find('{')
            end = cleaned.rfind('}')
            if start != -1 and end != -1 and end > start:
                return json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError:
            pass
        
        return {'error': 'Failed to parse LLM response as JSON'}
    
    def _error_response(self, message: str, query: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Create an error response with optional query for UI display."""
        response = {
            'type': 'error',
            'message': message,
            'error': message
        }
        # Include query in error response so UI can display it
        if query:
            response['query'] = query
        return response
    
    def _handle_data_dictionary_request(
        self,
        schema: Dict[str, Any],
        context: ConversationContext
    ) -> Dict[str, Any]:
        """
        Handle DATA_DICTIONARY requests - explain available fields.
        Implements usecase.md Section B.7 - DATA DICTIONARY / WHAT FIELDS EXIST.
        """
        # Build a comprehensive field documentation
        field_docs = []
        
        field_groups = {
            'User Dimensions': [
                ('uid', 'Unique User ID - use for counting unique users'),
                ('email_addr', 'User email address'),
                ('first_name', 'User first name'),
                ('last_name', 'User last name'),
                ('user_role', 'Role: admin / learner / manager'),
                ('user_status', 'Status: 5=active, 4=deleted, 1=invited'),
                ('designation', 'Job title'),
                ('manager_email_addr', 'Manager email'),
                ('hired_on', 'Hire/join date'),
            ],
            'Module/Course Dimensions': [
                ('mid', 'Unique Module ID - use for counting unique modules'),
                ('cmid', 'Course/Module ID - use for counting courses'),
                ('module_name', 'Training module name'),
                ('module_type_name', 'Module type/category'),
                ('module_status', 'Status: 0=published, 1=deleted, 2=draft'),
                ('skill_name', 'Skill category'),
                ('product_name', 'Product category'),
                ('trainer_email_addr', 'Trainer email'),
                ('coach_email_addr', 'Coach email'),
                ('published_date', 'Module publish date'),
                ('estd_time', 'Estimated completion time (minutes)'),
                ('ratings', 'Module ratings'),
            ],
            'Completion Dimensions': [
                ('assigned_status', 'Assignment: 0=assigned, 1=not assigned'),
                ('completed_status', 'Completion: 0=not completed, 1=completed'),
                ('complete_percentage', 'Completion percentage (0-100)'),
                ('completed_date', 'Completion date'),
                ('complete_type', 'Type: learning / assessment / equivalence'),
            ],
            'Location Dimensions': [
                ('city', 'City (lowercase, e.g., "bengaluru")'),
                ('state', 'State/province'),
                ('country', 'Country (full name)'),
            ],
            'Time Fields': [
                ('created_on', 'Record/assignment date'),
                ('published_date', 'Module publish date'),
                ('completed_date', 'Completion date'),
                ('invited_date', 'Invitation date'),
                ('module_created_on', 'Module creation date'),
            ],
        }
        
        message_parts = ["Here are the available fields you can filter and query by:\n"]
        
        for group_name, fields in field_groups.items():
            message_parts.append(f"\n**{group_name}:**")
            for field_name, description in fields:
                message_parts.append(f"  • `{field_name}`: {description}")
        
        message_parts.append("\n\n**Example queries you can ask:**")
        message_parts.append("  • How many unique users completed training in November?")
        message_parts.append("  • Show me completions by city")
        message_parts.append("  • List users who haven't completed module X")
        message_parts.append("  • Compare assigned vs completed users")
        message_parts.append("  • What's the completion trend over the last 6 months?")
        
        message = '\n'.join(message_parts)
        
        context.add_message("assistant", message)
        
        return {
            'type': 'data_dictionary',
            'message': message,
            'response_type': ResponseType.DATA_DICTIONARY.value,
            'field_groups': field_groups,
            'title': 'Available Fields'
        }
    
    def _is_havent_completed_in_time_query(
        self,
        user_message: str,
        time_period: Optional[TimePeriod]
    ) -> bool:
        """
        Detect if this is a "haven't completed in time range" query.
        These require special aggregation logic, not simple filters.
        """
        if not time_period or time_period.is_lifetime:
            return False
        
        message_lower = user_message.lower()
        
        # Pattern keywords
        havent_patterns = [
            "haven't completed", "havent completed", "hasn't completed", "hasnt completed",
            "didn't complete", "didnt complete", "hasn't finished", "hasnt finished",
            "not completed any", "no completions"
        ]
        
        # Check if message contains haven't completed pattern
        has_havent = any(pattern in message_lower for pattern in havent_patterns)
        
        # Check if it's asking about users/people
        user_keywords = ["who", "users", "people", "learners", "trainees"]
        has_user_focus = any(keyword in message_lower for keyword in user_keywords)
        
        # Check if it mentions "any module" or similar
        any_module_patterns = ["any module", "any training", "any course", "a single module"]
        has_any_module = any(pattern in message_lower for pattern in any_module_patterns)
        
        return has_havent and has_user_focus and (has_any_module or "in" in message_lower)
    
    def _fix_havent_completed_in_time_query(
        self,
        query_payload: Dict[str, Any],
        time_period: TimePeriod,
        user_message: str
    ):
        """
        Fix "haven't completed in time range" queries by replacing with correct aggregation pattern.
        Calls the existing _build_zero_completions_in_range_query method.
        """
        logger.info(f"Fixing 'haven't completed' query with aggregation pattern for time range: {time_period.description}")
        
        # Use the existing method that builds the correct query structure
        # Pass empty entities dict since we don't need it anymore
        self._build_zero_completions_in_range_query(
            query_payload=query_payload,
            time_period=time_period,
            entities={}  # Not needed, but method signature requires it
        )
        
        # Force response type to TABLE so users see the list
        query_payload['response_type'] = ResponseType.TABLE.value
        logger.info("Fixed query: replaced with aggregation pattern for zero completions in time range")
    
    def _fix_chart_query(
        self,
        query_payload: Dict[str, Any],
        response_type: ResponseType,
        user_message: str,
        time_period: Optional[TimePeriod]
    ):
        """
        Fix chart queries that don't have aggregations.
        Converts hit-based queries (size > 0) to aggregation queries (size: 0 with aggs).
        """
        query_body = query_payload.get('query', {})
        if not isinstance(query_body, dict):
            return
        
        message_lower = user_message.lower()
        
        # Detect what to group by from the message
        group_by_field = None
        count_field = None
        
        if 'module' in message_lower or 'training' in message_lower or 'course' in message_lower:
            group_by_field = 'module_name'
            count_field = 'uid'  # Count unique users per module
        elif 'city' in message_lower or 'cities' in message_lower:
            group_by_field = 'city'
            count_field = 'uid'
        elif 'user' in message_lower or 'learner' in message_lower:
            group_by_field = 'email_addr'
            count_field = 'mid'  # Count unique modules per user
        elif 'skill' in message_lower:
            group_by_field = 'skill_name'
            count_field = 'uid'
        elif 'product' in message_lower:
            group_by_field = 'product_name'
            count_field = 'uid'
        else:
            # Default: group by module
            group_by_field = 'module_name'
            count_field = 'uid'
        
        # Extract the base query (filters) from the existing query
        base_query = query_body.get('query', {})
        if not base_query:
            base_query = {"match_all": {}}
        
        # Ensure bool query structure
        if not isinstance(base_query, dict) or 'bool' not in base_query:
            base_query = {"bool": {"filter": [base_query] if base_query != {"match_all": {}} else []}}
        
        # Ensure we have the required filters
        bool_query = base_query.get('bool', {})
        filter_clauses = bool_query.get('filter', [])
        
        # Add required filters if not present
        has_user_status = any(
            isinstance(c, dict) and 'term' in c and isinstance(c.get('term'), dict) and 'user_status' in c.get('term', {})
            for c in filter_clauses
        )
        if not has_user_status:
            filter_clauses.append({"term": {"user_status": 5}})
        
        # Add completion filter if asking about completions
        if 'complet' in message_lower or 'finish' in message_lower or 'done' in message_lower:
            has_completed = any(
                isinstance(c, dict) and 'term' in c and isinstance(c.get('term'), dict) and 'completed_status' in c.get('term', {})
                for c in filter_clauses
            )
            if not has_completed:
                filter_clauses.append({"term": {"completed_status": 1}})
            
            has_assigned = any(
                isinstance(c, dict) and 'term' in c and isinstance(c.get('term'), dict) and 'assigned_status' in c.get('term', {})
                for c in filter_clauses
            )
            if not has_assigned:
                filter_clauses.append({"term": {"assigned_status": 0}})
        
        # Add time filter if specified
        if time_period and not time_period.is_lifetime:
            # Determine time field
            time_field = 'completed_date' if 'complet' in message_lower else 'created_on'
            has_time_filter = any(
                isinstance(c, dict) and 'range' in c and isinstance(c.get('range'), dict) and time_field in c.get('range', {})
                for c in filter_clauses
            )
            if not has_time_filter:
                filter_clauses.append({
                    "range": {
                        time_field: {
                            "gte": time_period.start_timestamp,
                            "lt": time_period.end_timestamp
                        }
                    }
                })
        
        bool_query['filter'] = filter_clauses
        base_query['bool'] = bool_query
        
        # Determine aggregation name
        agg_name = 'by_module' if group_by_field == 'module_name' else f'by_{group_by_field}'
        
        # Determine order direction
        order_dir = 'desc' if 'most' in message_lower or 'top' in message_lower or 'best' in message_lower or 'highest' in message_lower else 'asc'
        
        # Build the aggregation query
        query_body['size'] = 0  # Charts need aggregations, not hits
        query_body['query'] = base_query
        
        # For terms aggregation, we can't order by nested aggregation directly
        # Instead, we'll use bucket_sort or order by _count
        # For "most" queries, order by doc_count descending
        if order_dir == 'desc':
            # Order by doc_count (number of documents in each bucket)
            query_body['aggs'] = {
                agg_name: {
                    "terms": {
                        "field": group_by_field,
                        "size": 10,
                        "order": {"_count": "desc"}
                    },
                    "aggs": {
                        "unique_count": {
                            "cardinality": {"field": count_field}
                        }
                    }
                }
            }
        else:
            # For ascending, order by _count ascending
            query_body['aggs'] = {
                agg_name: {
                    "terms": {
                        "field": group_by_field,
                        "size": 10,
                        "order": {"_count": "asc"}
                    },
                    "aggs": {
                        "unique_count": {
                            "cardinality": {"field": count_field}
                        }
                    }
                }
            }
        
        logger.info(f"Fixed chart query: converted to aggregation on {group_by_field}, counting {count_field}, order={order_dir}")
    
    def _enforce_column_order(self, query_payload: Dict[str, Any], user_message: str = ""):
        """
        Enforce column order from fields_to_show if specified, or extract from user message.
        This ensures the _source fields in the query match the exact order
        the user specified in the report builder.
        
        NOTE: This should NOT run for completion rate queries (KPI widgets) as they don't have _source fields.
        """
        # Skip column order enforcement for completion rate queries (they're KPI widgets, not tables)
        # Completion rate queries have size: 0 and aggregations, but no _source fields
        query_body = query_payload.get('query', {})
        if isinstance(query_body, dict):
            # If query has size: 0 and no _source, it's a KPI widget (not a table) - skip column ordering
            query_size = query_body.get('size', None)
            has_source = '_source' in query_body and query_body.get('_source')
            aggs = query_body.get('aggs') or query_body.get('aggregations', {})
            
            # Check if this is a completion rate query:
            # 1. Has size: 0 (KPI widget, not table)
            # 2. Has aggregations with completion_rate pattern
            # 3. No _source field (or empty _source)
            if query_size == 0 and isinstance(aggs, dict):
                # Check for completion rate aggregation patterns
                has_completion_rate_agg = False
                for agg_name in ['scope', 'completion_scope', 'user']:
                    if agg_name in aggs:
                        agg_value = aggs[agg_name]
                        if isinstance(agg_value, dict):
                            # Check if it has completion_rate nested inside
                            if 'aggs' in agg_value:
                                nested_aggs = agg_value['aggs']
                                if isinstance(nested_aggs, dict) and 'completion_rate' in nested_aggs:
                                    has_completion_rate_agg = True
                                    break
                            # Also check buckets structure for completion_rate
                            if 'buckets' in agg_value:
                                buckets = agg_value['buckets']
                                if isinstance(buckets, dict):
                                    # Check first bucket for completion_rate
                                    first_bucket = list(buckets.values())[0] if buckets else {}
                                    if isinstance(first_bucket, dict) and 'completion_rate' in first_bucket:
                                        has_completion_rate_agg = True
                                        break
                                elif isinstance(buckets, list) and len(buckets) > 0:
                                    first_bucket = buckets[0]
                                    if isinstance(first_bucket, dict) and 'completion_rate' in first_bucket:
                                        has_completion_rate_agg = True
                                        break
                
                if has_completion_rate_agg or not has_source:
                    # This is a completion rate KPI widget - skip column ordering
                    logger.debug("Skipping column order enforcement for completion rate query (KPI widget, no _source)")
                    return
        
        # First, try to get fields_to_show from query_payload
        fields_to_show = query_payload.get('fields_to_show')
        
        # If not in query_payload, try to extract from user message
        if (not fields_to_show or not isinstance(fields_to_show, list)) and user_message:
            import re
            items = None  # Initialize to avoid UnboundLocalError
            
            # Look for "Show columns in this order:" followed by numbered list
            # Pattern matches multiple formats:
            # - "Show columns in this order:\n  1. Field Name\n  2. Another Field\n ..."
            # - "Columns required in this sequence:\n  - field1\n  - field2\n ..."
            # - "Show columns: field1, field2, field3"
            
            # Try pattern 1: Numbered list format
            pattern1 = r'(?:Show columns in this order|Columns required in this sequence)[:\s]*\n((?:\s*\d+\.\s*[^\n]+\n?)+)'
            match = re.search(pattern1, user_message, re.IGNORECASE | re.MULTILINE)
            
            if match:
                # Extract the numbered list
                list_text = match.group(1)
                # Extract each numbered item: "1. Field Name" -> "Field Name"
                items = re.findall(r'\d+\.\s*(.+)', list_text)
                if items:
                    logger.info(f"Extracted column list from numbered format: {items}")
            
            if not items:
                # Try pattern 2: Bullet list format (with dashes)
                pattern2 = r'(?:Show columns in this order|Columns required in this sequence)[:\s]*\n((?:\s*[-•]\s*[^\n]+\n?)+)'
                match = re.search(pattern2, user_message, re.IGNORECASE | re.MULTILINE)
                if match:
                    list_text = match.group(1)
                    items = re.findall(r'[-•]\s*(.+)', list_text)
                    if items:
                        logger.info(f"Extracted column list from bullet format: {items}")
            
            if not items:
                # Try pattern 3: Comma-separated format
                pattern3 = r'(?:Show columns|Columns required)[:\s]+([^\n]+)'
                match = re.search(pattern3, user_message, re.IGNORECASE)
                if match:
                    list_text = match.group(1)
                    # Split by comma and clean up
                    items = [item.strip() for item in list_text.split(',') if item.strip()]
                    if items:
                        logger.info(f"Extracted column list from comma-separated format: {items}")
            
            if items:
                # Convert formatted names to field names (e.g., "First Name" -> "first_name")
                def to_field_name(formatted: str) -> str:
                    # Remove extra whitespace and convert to lowercase
                    formatted = formatted.strip().lower()
                    # Replace spaces with underscores
                    formatted = formatted.replace(' ', '_')
                    # Handle common field name variations
                    field_map = {
                        'uid': 'uid',
                        'user_id': 'uid',
                        'first_name': 'first_name',
                        'firstname': 'first_name',
                        'last_name': 'last_name',
                        'lastname': 'last_name',
                        'email_addr': 'email_addr',
                        'email': 'email_addr',
                        'email_address': 'email_addr',
                        'completed_date': 'completed_date',
                        'completion_date': 'completed_date',
                    }
                    # Check if it's a known variation
                    if formatted in field_map:
                        return field_map[formatted]
                    # Otherwise return as-is (might already be a field name)
                    return formatted
                
                fields_to_show = [to_field_name(item) for item in items]
                if fields_to_show:
                    logger.info(f"Extracted column order from user message: {fields_to_show}")
        
        # Even if fields_to_show is not provided, we should still prioritize first_name, last_name, email_addr
        # So we'll continue processing to reorder the _source fields
        
        query_body = query_payload.get('query', {})
        if not isinstance(query_body, dict):
            return
        
        # Get current _source from query
        current_source = query_body.get('_source', [])
        if not isinstance(current_source, list):
            return
        
        # Normalize field names for matching (handles variations like "first_name" vs "First Name")
        def normalize_field(field: str) -> str:
            # Convert to lowercase, remove underscores and spaces
            normalized = field.lower().replace('_', '').replace(' ', '').replace('-', '')
            # Handle common variations
            variations = {
                'uid': 'uid',
                'userid': 'uid',
                'firstname': 'firstname',
                'first_name': 'firstname',
                'lastname': 'lastname',
                'last_name': 'lastname',
                'emailaddr': 'emailaddr',
                'email_addr': 'emailaddr',
                'emailaddress': 'emailaddr',
                'completeddate': 'completeddate',
                'completed_date': 'completeddate',
                'completiondate': 'completeddate',
            }
            return variations.get(normalized, normalized)
        
        # Create a mapping of normalized field names to actual field names from current_source
        source_map = {normalize_field(f): f for f in current_source}
        
        # CRITICAL: Always prioritize first_name, last_name, email_addr at the start
        # These should always be grouped together at the beginning of tables
        priority_fields = ['first_name', 'last_name', 'email_addr']
        ordered_source = []
        seen_fields = set()
        
        # First, add priority fields if they exist in current_source
        for priority_field in priority_fields:
            if priority_field in current_source and priority_field not in seen_fields:
                ordered_source.append(priority_field)
                seen_fields.add(priority_field)
        
        # Then, add fields from fields_to_show (excluding priority fields already added)
        if fields_to_show and isinstance(fields_to_show, list):
            for field in fields_to_show:
                # Skip if already added as priority field
                if field in seen_fields:
                    continue
                    
                # Try exact match first
                if field in current_source:
                    if field not in seen_fields:
                        ordered_source.append(field)
                        seen_fields.add(field)
                        continue
                
                # Try normalized match
                normalized = normalize_field(field)
                if normalized in source_map:
                    actual_field = source_map[normalized]
                    # Skip if it's a priority field that was already added
                    if actual_field in priority_fields and actual_field in seen_fields:
                        continue
                    if actual_field not in seen_fields:
                        ordered_source.append(actual_field)
                        seen_fields.add(actual_field)
                        continue
                
                # If no match found, log a warning but don't add it
                logger.warning(f"Could not match field '{field}' from fields_to_show to any field in _source: {current_source}")
        
        # If there are any fields in current_source that weren't in fields_to_show or priority fields,
        # add them at the end (though ideally the LLM shouldn't add extra fields)
        remaining = [f for f in current_source if f not in seen_fields]
        ordered_source.extend(remaining)
        
        # Update the query with the ordered _source
        if ordered_source != current_source:
            query_body['_source'] = ordered_source
            logger.info(f"Enforced column order: {ordered_source} (from fields_to_show: {fields_to_show})")
        
        # CRITICAL: Set fields_to_show in query_payload so frontend knows the order
        # Preserve only the user-requested fields in the exact order they specified
        # Map from requested field names to actual field names in the query
        if fields_to_show and ordered_source:
            # Build a list of actual field names in the order specified by fields_to_show
            # This ensures we only include user-requested fields, in their specified order
            mapped_fields = []
            for requested_field in fields_to_show:
                # Find the matching actual field in ordered_source
                for actual_field in ordered_source:
                    if normalize_field(requested_field) == normalize_field(actual_field):
                        if actual_field not in mapped_fields:
                            mapped_fields.append(actual_field)
                            break
            
            # Only set fields_to_show if we successfully mapped all requested fields
            # This ensures the frontend gets the exact order the user specified
            if mapped_fields:
                query_payload['fields_to_show'] = mapped_fields
                logger.info(f"Set fields_to_show in query_payload (preserving user order): {mapped_fields}")
            elif ordered_source:
                # Fallback: if mapping failed, use ordered_source but filter to user-requested fields
                # This preserves order from ordered_source (which respects fields_to_show order)
                user_requested = [f for f in ordered_source if any(normalize_field(f) == normalize_field(req) for req in fields_to_show)]
                if user_requested:
                    query_payload['fields_to_show'] = user_requested
                    logger.info(f"Set fields_to_show in query_payload (fallback): {user_requested}")
    
    def _check_for_clarification_needed(
        self,
        entities: Dict[str, Any],
        user_message: str
    ) -> Optional[str]:
        """
        Check if the query has ambiguous terms that need clarification.
        Implements usecase.md Section I - Fail-Safes.
        
        Returns:
            Clarification question if needed, None otherwise
        """
        # This method is no longer used - Bedrock handles ambiguity detection
        # Keeping for backwards compatibility but it won't be called
        return None
        
        # Build clarification question
        questions = []
        for term, interpretations in ambiguity_details:
            if term in ['sent', 'delivered', 'pushed']:
                questions.append(
                    f'When you say "{term}", do you mean:\n'
                    '  a) When invitations were sent (filter by invited_date)\n'
                    '  b) Assignment status (filter by assigned_status)'
                )
            elif term == 'score':
                questions.append(
                    'When you say "score", do you mean:\n'
                    '  a) Completion percentage (complete_percentage)\n'
                    '  b) Module ratings (ratings)'
                )
        
        if questions:
            return "I need a quick clarification:\n\n" + "\n\n".join(questions)
        
        return None


# Singleton instance
_orchestrator: Optional[QueryOrchestrator] = None


def get_orchestrator() -> QueryOrchestrator:
    """Get the singleton orchestrator instance."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = QueryOrchestrator()
    return _orchestrator


def reload_orchestrator():
    """Reload the orchestrator (e.g., after schema changes)."""
    global _orchestrator
    _orchestrator = None
    return get_orchestrator()
