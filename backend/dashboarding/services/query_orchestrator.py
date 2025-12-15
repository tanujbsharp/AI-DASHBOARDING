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
from typing import Dict, Any, List, Optional, Tuple, Set
from datetime import datetime

import boto3
from django.conf import settings

from .opensearch_client import OpenSearchClient
from .time_handler import TimeHandler, TimePeriod
from .semantic_mapper import SemanticFieldMapper
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
    
    # Fields that are natively keyword type and should NOT have .keyword appended
    NATIVE_KEYWORD_FIELDS = {
        'module_name', 'skill_name', 'product_name', 'city', 'country', 'state',
        'email_addr', 'first_name', 'last_name', 'designation', 'user_role',
        'trainer_email_addr', 'coach_email_addr', 'manager_email_addr',
        'module_type_name', 'module_desc', 'tags', 'staff', 'is_admin',
        'created_by', 'module_created_by', 'prod_master_name'
    }
    
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
        self.context_manager = get_context_manager()
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
            # Step 1: Get/create conversation context
            context = self.context_manager.get_or_create(session_id)
            context.add_message("user", user_message)
            
            # Step 2: Extract intent using helper classes
            time_period = TimeHandler.parse(user_message)
            response_type = ResponseTypeDetector.detect(user_message)
            entities = SemanticFieldMapper.extract_entities(user_message)
            
            # CRITICAL: Force TABLE response type for zero completions queries
            # These queries need to show a list of users, so they must be TABLE type
            # regardless of what the detector thinks (e.g., "who hasn't" might be detected as KPI_WIDGET)
            if entities and entities.get('zero_completions_in_range'):
                response_type = ResponseType.TABLE
                logger.info("Forcing TABLE response type for zero completions query")
            
            # Update context with new filters
            time_field_override = entities.get('time_field_override')
            if not time_field_override:
                time_field_override = TimeHandler.detect_time_field_hint(user_message)
            if not time_field_override:
                time_field_override = 'created_on'
            entities['time_field_override'] = time_field_override

            self.context_manager.update_from_message(
                context, 
                user_message, 
                entities, 
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
            
            # Step 3b: Check for ambiguous terms that need clarification (Section I)
            clarification = self._check_for_clarification_needed(entities, user_message)
            if clarification:
                context.add_message("assistant", clarification)
                return {
                    'type': 'clarification',
                    'message': clarification,
                    'needs_clarification': True,
                    'ambiguous_terms': entities.get('ambiguous_terms', [])
                }
            
            # Step 4: Determine if this query should bypass LLM (module intents or follow-up lists)
            query_result = None
            module_intent = entities.get('module_intent')

            if entities.get('is_ranking_query'):
                # Handle "which module has most completions" type queries
                query_result = self._build_ranking_query(
                    entities=entities,
                    time_period=effective_time_period,
                    context=context,
                    user_message=user_message
                )
            elif module_intent:
                query_result = self._build_module_intent_query(
                    module_intent=module_intent,
                    time_period=effective_time_period,
                    entities=entities,
                    context=context,
                    response_type=response_type
                )
            else:
                comparison_type = entities.get('comparison_type')
                if comparison_type == 'assigned_vs_completed':
                    query_result = self._build_assignment_vs_completion_query(
                        context=context
                    )
                else:
                    # Follow-up functionality disabled per user request
                    # follow_up_override = self._maybe_build_follow_up_query(
                    #     user_message=user_message,
                    #     response_type=response_type,
                    #     entities=entities,
                    #     context=context
                    # )
                    # if follow_up_override:
                    #     query_result = follow_up_override
                    follow_up_override = None

            if query_result is None:
                # Generate query via LLM (Phase 1)
                query_result = self._generate_query(
                    user_message=user_message,
                    schema=schema,
                    response_type=response_type,
                    time_period=effective_time_period,
                    entities=entities,
                    context=context
                )
            
            if 'error' in query_result:
                # Include the query if it was generated before error
                error_query = query_result.get('query') if isinstance(query_result, dict) else None
                return self._error_response(query_result['error'], query=error_query)
            
            query_payload = query_result
            
            # Clean up unsupported constructs FIRST, before any other processing
            # This prevents errors from propagating through the pipeline
            self._sanitize_query_payload(query_payload)
            
            # Deduplicate filters to prevent duplicate clauses
            self._deduplicate_filters(query_payload)
            
            # Fix "total completions" queries that incorrectly use cardinality on mid
            # "total completions" should count ALL records, not unique modules
            if entities and entities.get('count_total_records'):
                self._fix_total_completions_query(query_payload)
            
            # Fix person name queries that incorrectly use email_addr instead of first_name/last_name
            if entities and entities.get('person_name'):
                person_name = str(entities['person_name'])
                # Only fix if it's NOT an email (doesn't contain @)
                if '@' not in person_name:
                    self._fix_person_name_query(query_payload, person_name)
            
            # Handle "users who haven't completed any module in [time range]" queries
            # This requires user-level aggregation, not row-level filtering
            # Must be done BEFORE structured filters to replace the query entirely
            skip_structured_filters = False
            if entities and entities.get('zero_completions_in_range') and effective_time_period and not effective_time_period.is_lifetime:
                self._build_zero_completions_in_range_query(query_payload, effective_time_period, entities)
                skip_structured_filters = True  # Aggregation query handles everything, skip filters
            
            # Handle count threshold queries (e.g., "users who have finished more than 5 modules")
            if entities and entities.get('count_threshold') is not None:
                # Pass original message to detect if it's about modules
                if 'original_message' not in entities:
                    entities['original_message'] = user_message
                self._apply_count_threshold(query_payload, entities)
            
            # Handle "latest" / "most recent" queries
            # These should return only the most recent completed module, sorted by completed_date descending
            # BUT: Skip if this is a zero completions query (they have incompatible filter requirements)
            if entities and entities.get('latest_query') and not entities.get('zero_completions_in_range'):
                self._fix_latest_query(query_payload, entities)
            
            # Handle completion rate queries - fix invalid ordering by pipeline aggregation
            if entities and entities.get('completion_rate_query'):
                self._fix_completion_rate_query(query_payload, entities)
            
            # Handle "what modules were completed" queries - ensure they use aggregations for unique modules
            if entities and entities.get('unique_modules_query'):
                self._fix_unique_modules_query(query_payload, entities)
            
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
            
            # CRITICAL: Final safeguard - if this is a zero completions query, ensure prohibited filters
            # are removed from top-level (even if other methods added them back)
            if entities and entities.get('zero_completions_in_range') and effective_time_period and not effective_time_period.is_lifetime:
                query_body = query_payload.get('query', {})
                if isinstance(query_body, dict):
                    self._remove_prohibited_filters_from_top_level(query_body)
            
            if not skip_structured_filters:
                self._apply_structured_filters(
                    query_payload=query_payload,
                    time_period=effective_time_period,
                    context=context,
                    time_field=time_field_override,
                    entities=entities,
                    user_message=user_message
                )
            
            # CRITICAL: Final cleanup for zero completions queries - remove any prohibited filters
            # that might have been added by _apply_structured_filters or other methods
            if entities and entities.get('zero_completions_in_range') and effective_time_period and not effective_time_period.is_lifetime:
                query_body = query_payload.get('query', {})
                if isinstance(query_body, dict):
                    self._remove_prohibited_filters_from_top_level(query_body)
                    # Verify the cleanup worked
                    query = query_body.get('query', {})
                    if isinstance(query, dict):
                        bool_query = query.get('bool', {})
                        if isinstance(bool_query, dict):
                            filter_clauses = bool_query.get('filter', [])
                            # Check for prohibited filters
                            for clause in filter_clauses:
                                if isinstance(clause, dict):
                                    if 'term' in clause and isinstance(clause.get('term'), dict):
                                        if 'completed_status' in clause['term'] or 'assigned_status' in clause['term']:
                                            logger.error(f"CRITICAL: Prohibited filter still present after cleanup: {clause}")
                                    if 'range' in clause and isinstance(clause.get('range'), dict):
                                        if 'completed_date' in clause['range']:
                                            logger.error(f"CRITICAL: Prohibited range filter still present after cleanup: {clause}")
            
            # Sanitize again after applying filters (in case filters added unsupported constructs)
            self._sanitize_query_payload(query_payload)
            
            # One more cleanup after sanitize (in case sanitize modified something)
            if entities and entities.get('zero_completions_in_range') and effective_time_period and not effective_time_period.is_lifetime:
                query_body = query_payload.get('query', {})
                if isinstance(query_body, dict):
                    self._remove_prohibited_filters_from_top_level(query_body)

            # Step 5: Execute query against OpenSearch
            # CRITICAL: For zero completions queries, verify aggregation is present AND top-level filters are correct
            if entities and entities.get('zero_completions_in_range'):
                query_body = query_payload.get('query', {})
                if 'aggs' not in query_body:
                    logger.error("CRITICAL ERROR: Zero completions query is missing 'aggs' section! Query will fail.")
                elif 'users' not in query_body.get('aggs', {}):
                    logger.error("CRITICAL ERROR: Zero completions query is missing 'users' aggregation! Query will fail.")
                else:
                    logger.info("Zero completions query has correct aggregation structure")
                
                # FINAL VERIFICATION: Check top-level filters - must ONLY have user_status and cmid
                query = query_body.get('query', {})
                if isinstance(query, dict):
                    bool_query = query.get('bool', {})
                    if isinstance(bool_query, dict):
                        filter_clauses = bool_query.get('filter', [])
                        prohibited_found = []
                        for clause in filter_clauses:
                            if isinstance(clause, dict):
                                if 'term' in clause and isinstance(clause.get('term'), dict):
                                    term_dict = clause['term']
                                    if 'completed_status' in term_dict:
                                        prohibited_found.append('completed_status')
                                    if 'assigned_status' in term_dict:
                                        prohibited_found.append('assigned_status')
                                if 'range' in clause and isinstance(clause.get('range'), dict):
                                    if 'completed_date' in clause['range']:
                                        prohibited_found.append('completed_date range')
                        
                        if prohibited_found:
                            logger.error(f"CRITICAL: Prohibited filters found in top-level query: {prohibited_found}")
                            logger.error(f"Removing them now as final safeguard...")
                            self._remove_prohibited_filters_from_top_level(query_body)
                        else:
                            logger.info("Zero completions query has correct top-level filters (only user_status and cmid)")
            
            # FINAL FINAL SAFEGUARD: Remove collapse and ensure correct structure for unique_modules_query
            query_body = query_payload.get('query', {})
            if isinstance(query_body, dict):
                # Remove collapse if present
                if 'collapse' in query_body:
                    logger.warning("FINAL SAFEGUARD: Removed collapse before query execution")
                    query_body.pop('collapse')
                
                # If this is a unique_modules_query, ensure structure is correct
                if entities and entities.get('unique_modules_query'):
                    if 'aggs' in query_body and isinstance(query_body.get('aggs'), dict):
                        modules_agg = query_body['aggs'].get('modules', {})
                        if isinstance(modules_agg, dict):
                            # Ensure unique_users aggregation exists
                            sub_aggs = modules_agg.get('aggs', {})
                            if 'unique_users' not in sub_aggs:
                                logger.warning("FINAL SAFEGUARD: Adding missing unique_users aggregation")
                                if not isinstance(sub_aggs, dict):
                                    modules_agg['aggs'] = {}
                                    sub_aggs = modules_agg['aggs']
                                sub_aggs['unique_users'] = {'cardinality': {'field': 'uid'}}
                            
                            # Ensure order is correct
                            terms_agg = modules_agg.get('terms', {})
                            if isinstance(terms_agg, dict) and 'order' not in terms_agg:
                                logger.warning("FINAL SAFEGUARD: Adding missing order by unique_users")
                                terms_agg['order'] = {'unique_users': 'desc'}
                            elif isinstance(terms_agg, dict) and isinstance(terms_agg.get('order'), dict):
                                order = terms_agg['order']
                                if 'unique_users' not in order:
                                    logger.warning("FINAL SAFEGUARD: Fixing order to use unique_users")
                                    terms_agg['order'] = {'unique_users': 'desc'}
            
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
            if entities and entities.get('zero_completions_in_range'):
                self._extract_zero_completions_in_range_results(execution_result)
            
            # Fix "latest" query results - ensure total matches count (should be 1)
            if entities and entities.get('latest_query'):
                if execution_result.get('success') and execution_result.get('count', 0) == 1:
                    # For latest queries, total should equal count (1)
                    execution_result['total'] = 1
            
            # Summarize threshold queries into one row per user with module counts
            if entities and entities.get('count_threshold') is not None:
                self._summarize_count_threshold_results(execution_result)
            
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
            
            # CRITICAL: Include aggregations in response for chart visualizations
            # The frontend needs aggregations to display bar charts, pie charts, etc.
            aggregations = execution_result.get('aggregations', {})
            if aggregations and final_response_type in ['bar_chart', 'pie_chart', 'line_chart']:
                final_response['aggregations'] = aggregations
                logger.info(f"Included aggregations in response for {final_response_type} visualization")
            
            # Ensure results are present (for zero completions queries, they should be extracted)
            if entities and entities.get('zero_completions_in_range'):
                if 'results' not in execution_result or not execution_result.get('results'):
                    logger.warning(f"Zero completions query has no results! Total: {execution_result.get('total', 0)}, Count: {execution_result.get('count', 0)}")
                else:
                    logger.info(f"Zero completions query returning {len(execution_result.get('results', []))} users in results array")
            
            return final_response
            
        except Exception as e:
            logger.exception(f"Error in query orchestrator: {e}")
            # Include query in error response if available
            error_query = query_payload.get('query', {}) if query_payload else None
            return self._error_response(f"An error occurred: {str(e)}", query=error_query)
    
    def _generate_query(
        self,
        user_message: str,
        schema: Dict[str, Any],
        response_type: ResponseType,
        time_period: Optional[TimePeriod],
        entities: Dict[str, Any],
        context: ConversationContext
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
        
        # Build user prompt with extracted context
        user_prompt = QueryPromptBuilder.build_user_prompt(
            user_message=user_message,
            time_period=time_period,
            entities=entities,
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
        entities: Dict[str, Any],
        context: ConversationContext
    ) -> Optional[Dict[str, Any]]:
        """Handle follow-up list requests (e.g., 'who are these users?')."""
        last_query = context.get_last_query()
        if not last_query:
            return None

        if not self._is_follow_up_list_request(user_message, response_type, last_query):
            return None

        return self._build_follow_up_list_query(last_query, entities, context)
    
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
        entities: Dict[str, Any],
        context: ConversationContext
    ) -> Dict[str, Any]:
        """Build a deterministic query that lists the actual entities referenced earlier."""
        size = entities.get('limit') or last_query.primary_entity_count or 100
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
        entities: Dict[str, Any],
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
        wants_table = entities.get('wants_list', False)
        
        if wants_table:
            size = entities.get('limit') or 200
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
        count_total_records = entities.get('count_total_records', False)
        
        if count_total_records and module_intent == 'consumed':
            # For "total completions", count ALL records, not unique modules
            # Use value_count on _id or just get total from query result
            return {
                "index_id": "module_consumption_data",
                "query": {
                    "size": 0,
                    "query": bool_query,
                    "aggs": {
                        "total_completions": {
                            "value_count": {"field": "_id"}  # Count all records, not unique modules
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
        entities: Dict[str, Any],
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
        rank_by = entities.get('rank_by', 'module_name')
        message_lower = user_message.lower()
        
        # Build the base filter
        must_filters: List[Dict[str, Any]] = []
        
        # ALWAYS filter for active users only (user_status = 5)
        must_filters.append({"term": {"user_status": 5}})
        
        # ALWAYS filter for assigned records only (assigned_status = 0)
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
            time_field = entities.get('time_field_override', 'completed_date' if metric_type == 'completions' else 'created_on')
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
            query = {
                "size": 0,
                "query": bool_query,
                "aggs": {
                    agg_name: {
                        "terms": {
                            "field": "module_name",  # Group by NAME for display
                            "size": 10,
                            "order": {"unique_users": "desc"}
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
                            "order": {"unique_modules": "desc"}
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
                            "order": {"unique_users": "desc"}
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

        # CRITICAL: For zero completions queries, DO NOT add ANY filters at top level
        # The query structure is already built by _build_zero_completions_in_range_query
        # Adding filters here would break the aggregation logic by filtering out users before aggregation
        if entities and entities.get('zero_completions_in_range') and time_period and not time_period.is_lifetime:
            # RETURN IMMEDIATELY - do not add any filters
            # The aggregation query already has the correct structure (only user_status and cmid at top level)
            logger.info("Skipping _apply_structured_filters for zero completions query - aggregation handles everything")
            return
        
        if time_period and not time_period.is_lifetime:
            # Normal time filter logic - only apply if explicitly requested in current query
            # DO NOT use stored time filters from context
            entity_time_field = (entities or {}).get('time_field_override') if entities else None
            field_name = entity_time_field or time_field or 'created_on'
            self._ensure_time_filter_clause(bool_query, field_name, time_period)

        # Apply inferred filters (cities, completion, products, etc.)
        # Enforce single-tenant scope (cmid=1)
        self._append_filter_clause(bool_query, {"term": {"cmid": 1}})
        
        # ALWAYS filter for active users only (user_status = 5)
        # This is a core business rule - we only report on active users
        self._append_filter_clause(bool_query, {"term": {"user_status": 5}})
        
        # CRITICAL: For zero completions queries, DO NOT add assigned_status or completed_status filters
        # These must ONLY exist in the aggregation, not at top-level
        if entities and entities.get('zero_completions_in_range'):
            # Skip adding assigned_status and completed_status filters - they're in aggregation only
            pass
        else:
            # Conditionally filter for assigned records (assigned_status = 0)
            # Only apply when query is about completions, assignments, or module interactions
            should_filter_assigned = self._should_filter_by_assigned_status(entities, user_message)
            
            # Remove any assigned_status filters that the LLM might have added incorrectly
            self._remove_filter_clause(bool_query, "assigned_status")
            
            # Add it back only if it should be there
            if should_filter_assigned:
                self._append_filter_clause(bool_query, {"term": {"assigned_status": 0}})
            
            # Explicitly apply completion filter if entities indicate completion intent
            # This ensures "did [module]" queries filter for completed_status = 1
            if entities and entities.get('completion_filter') is not None:
                completion_status = entities['completion_filter']
                # Remove any existing completed_status filters first (from both must and filter)
                self._remove_filter_clause(bool_query, "completed_status")
                # Add the correct one
                self._append_filter_clause(bool_query, {"term": {"completed_status": completion_status}})

        filter_clauses = self._build_filter_clauses(context)
        for clause in filter_clauses:
            # Skip completion_status clauses from context if we already set it from entities
            # This prevents conflicts between entity detection and context filters
            if entities and entities.get('completion_filter') is not None:
                # Check if this clause is a completed_status filter
                if isinstance(clause, dict) and 'term' in clause:
                    term_dict = clause.get('term', {})
                    if isinstance(term_dict, dict) and 'completed_status' in term_dict:
                        # Skip this clause - we already set it from entities
                        continue
            self._append_filter_clause(bool_query, clause)
        
        # Convert any term queries for module_name to match_phrase for fuzzy matching
        self._convert_module_name_terms_to_match(bool_query)
        
        # Smart collapse handling: respect LLM's decision, but override if detection disagrees
        llm_added_collapse = 'collapse' in query_body
        should_collapse = entities and entities.get('needs_unique', False)
        
        if llm_added_collapse and not should_collapse:
            # LLM added collapse but our detection says it shouldn't (e.g., "have modules in progress")
            # Remove it - user wants to see all records, not unique entities
            del query_body['collapse']
        elif should_collapse and not llm_added_collapse:
            # Our detection says it should collapse but LLM didn't add it - add it
            unique_field = entities.get('unique_field', 'uid')
            collapse_field = self._get_collapse_field(unique_field)
            if collapse_field:
                query_body['collapse'] = {
                    "field": collapse_field,
                    "inner_hits": {
                        "name": "most_recent",
                        "size": 1,
                        "sort": [{"created_on": "desc"}]
                    }
                }
        # If both agree (both want collapse or both don't), keep as is
    
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
        
        # Check entities for completion/assignment intent
        if entities:
            # If explicitly asking about completion status
            if entities.get('completion_filter') is not None:
                return True
            # If asking about assignments
            if entities.get('assigned_status_filter') is not None:
                return True
            # If this is a ranking query about completions
            if entities.get('is_ranking_query') and any(kw in message_lower for kw in completion_keywords):
                return True
            # If module intent detected
            if entities.get('module_intent'):
                return True
        
        # Check message for keywords
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
                # Clean the list - remove invalid entries
                cleaned = []
                for item in value:
                    # Only keep valid query clauses (dicts that aren't empty)
                    if isinstance(item, dict) and item:
                        # Remove any invalid/empty clauses
                        if item != {} and item != {"match_all": {}}:
                            cleaned.append(item)
                bool_query[key] = cleaned
                continue
            if value is None:
                bool_query[key] = []
            elif isinstance(value, dict):
                # Convert single dict to list, but only if it's valid
                if value and value != {"match_all": {}}:
                    bool_query[key] = [value]
                else:
                    bool_query[key] = []
            else:
                # Invalid type, set to empty list
                bool_query[key] = []
        return bool_query

    def _ensure_time_filter_clause(
        self,
        bool_query: Dict[str, Any],
        field_name: str,
        time_period: TimePeriod
    ):
        """Add a range clause on the requested time field unless already present."""
        normalized_field = self._normalize_field_for_filter(field_name)
        range_clause = {
            "range": {
                normalized_field: {
                    "gte": time_period.start_timestamp,
                    "lt": time_period.end_timestamp
                }
            }
        }

        filter_list = bool_query.setdefault('filter', [])
        if not isinstance(filter_list, list):
            filter_list = bool_query['filter'] = [filter_list]

        if not self._filter_exists(filter_list, range_clause):
            filter_list.append(range_clause)

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
        
        # Find any aggregations using cardinality on mid and replace with value_count
        for agg_name, agg_def in aggs.items():
            if isinstance(agg_def, dict):
                # Check if it's a cardinality on mid
                if 'cardinality' in agg_def:
                    cardinality_field = agg_def.get('cardinality', {}).get('field', '')
                    if cardinality_field == 'mid':
                        # Replace with value_count to count all records
                        aggs[agg_name] = {"value_count": {"field": "_id"}}
                        logger.info(f"Fixed total completions query: replaced cardinality on mid with value_count on _id")
    
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
        """
        query_body = query_payload.get('query', {})
        if not isinstance(query_body, dict):
            return
        
        # CRITICAL: Remove any name/email filters that incorrectly match "completion"
        # "completion" is a METRIC TERM, not a person name!
        query = query_body.get('query', {})
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
            
            has_completion_rate = 'completion_rate' in sub_aggs
            has_bucket_sort = 'sort_by_completion_rate' in sub_aggs or any(
                isinstance(sub_agg, dict) and 'bucket_sort' in sub_agg 
                for sub_agg in sub_aggs.values()
            )
            
            # If completion_rate exists but bucket_sort doesn't, add it
            if has_completion_rate and not has_bucket_sort:
                logger.info("Adding bucket_sort for completion_rate query")
                sub_aggs['sort_by_completion_rate'] = {
                    'bucket_sort': {
                        'sort': [
                            {'completion_rate': {'order': 'desc'}}
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
                completion_rate_agg = sub_aggs.get('completion_rate', {})
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
                                'recentCompletedCount': 'recent_completions._count'
                            },
                            'script': 'params.assignedCount > 0 && params.recentCompletedCount == 0'
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
                    allowed_fields = {'user_status', 'cmid'}
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
        
        threshold = entities.get('count_threshold')
        operator = entities.get('threshold_operator', 'gt')  # gt = greater than, lt = less than
        
        if threshold is None:
            return
        
        # Check if this is about modules (completed modules, finished modules, etc.)
        user_message = entities.get('original_message', '').lower() if entities.get('original_message') else ''
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
    
    def _sanitize_query_payload(self, query_payload: Dict[str, Any]):
        """Remove/convert unsupported query constructs (e.g., terms_lookup)."""
        # Always run recursive removal - it's safe and catches everything
        self._remove_terms_lookup_recursive(query_payload)
    
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
        """Remove any filter clauses that match the given field name."""
        # Check both 'filter' and 'must' arrays
        for clause_type in ['filter', 'must']:
            clauses = bool_query.get(clause_type, [])
            if not isinstance(clauses, list):
                continue
            
            # Remove any term filter that matches the field
            bool_query[clause_type] = [
                clause for clause in clauses
                if not (
                    isinstance(clause, dict) and 
                    'term' in clause and 
                    isinstance(clause['term'], dict) and
                    field_name in clause['term']
                )
            ]
    
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
        """Execute query against OpenSearch."""
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
        
        # Ensure index exists and is properly mapped
        if index_id not in settings.OPENSEARCH_INDEXES:
            # Try to find it - maybe they passed the actual index name
            for key in settings.OPENSEARCH_INDEXES.keys():
                if key == index_id or settings.OPENSEARCH_INDEXES[key] == index_id:
                    index_id = key
                    logger.info(f"Mapped index '{index_id}' to key '{key}'")
                    break
        
        # Verify the index_id is in our configuration
        if index_id not in settings.OPENSEARCH_INDEXES:
            available = list(settings.OPENSEARCH_INDEXES.keys())
            error_msg = f"Index '{index_id}' not found in configuration. Available indexes: {available}"
            logger.error(error_msg)
            return {'success': False, 'error': error_msg}
        
        result = self.os_client.execute_query(index_id, query)
        
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
                for bucket in enhanced_value['buckets']:
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
                    
                    # SECOND: Extract module_name from sub-aggregation (terms aggregation on module_name)
                    if '_display_name' not in bucket and 'module_name' in bucket and 'buckets' in bucket['module_name']:
                        name_buckets = bucket['module_name']['buckets']
                        if name_buckets:
                            bucket['_display_name'] = name_buckets[0].get('key', bucket.get('key'))
                    
                    # THIRD: Extract user email/name from sub-aggregation
                    if '_display_name' not in bucket:
                        if 'user_email' in bucket and 'buckets' in bucket['user_email']:
                            email_buckets = bucket['user_email']['buckets']
                            if email_buckets:
                                bucket['_display_name'] = email_buckets[0].get('key', bucket.get('key'))
                        elif 'user_name' in bucket and 'buckets' in bucket['user_name']:
                            name_buckets = bucket['user_name']['buckets']
                            if name_buckets:
                                bucket['_display_name'] = name_buckets[0].get('key', bucket.get('key'))
                    
                    # Extract the count value (unique_users or unique_modules)
                    # For unique modules queries, use unique_users count (number of completions per module)
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
                
                if 'value' in value:
                    metrics[name] = {
                        'label': label,
                        'value': int(value['value']),
                        'type': 'count'
                    }
                elif 'buckets' in value:
                    metrics[name] = {
                        'label': label,
                        'value': len(value['buckets']),
                        'type': 'breakdown'
                    }
                elif 'doc_count' in value:
                    metrics[name] = {
                        'label': label,
                        'value': int(value['doc_count']),
                        'type': 'count'
                    }
        
        return metrics
    
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
            for name, value in aggregations.items():
                if isinstance(value, dict) and 'buckets' in value:
                    config['data_key'] = name
                    break
        
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
        ambiguity_details = entities.get('ambiguity_details', [])
        
        if not ambiguity_details:
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
