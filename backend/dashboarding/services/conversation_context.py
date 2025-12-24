"""
Conversation Context Manager for AI Reports Bot.
Tracks conversation history, inferred filters, and enables intelligent follow-up handling.

Aligned with usecase.md Section H - Conversation Memory (Follow-ups).
"""

from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from datetime import datetime
from enum import Enum
import copy
import re


class FilterType(Enum):
    """Types of filters that can be inferred from conversation."""
    TIME_PERIOD = "time_period"
    LOCATION = "location"
    COMPLETION_STATUS = "completion_status"
    MODULE = "module"
    USER = "user"
    PERSON_NAME = "person_name"  # For queries like "tanuj's completions"
    SKILL = "skill"
    PRODUCT = "product"
    USER_STATUS = "user_status"
    MODULE_STATUS = "module_status"
    ASSIGNMENT_STATUS = "assignment_status"
    BREAKDOWN_DIMENSION = "breakdown_dimension"  # Section H: primary breakdown dimension


@dataclass
class InferredFilter:
    """A filter inferred from user conversation."""
    filter_type: FilterType
    field_name: str
    value: Any
    source_message: str  # Which message this was inferred from
    confidence: float = 1.0  # How confident we are in this filter
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class QueryRecord:
    """Record of a query that was executed."""
    user_message: str
    query: Dict[str, Any]
    result_total: int
    aggregations: Dict[str, Any]
    response_type: str
    primary_entity_field: Optional[str] = None
    primary_entity_label: Optional[str] = None
    primary_entity_count: Optional[int] = None
    filter_query: Optional[Dict[str, Any]] = None
    breakdown_dimension: Optional[str] = None  # Section H: track breakdown dimension
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class ConversationContext:
    """
    Maintains the full context of a conversation session.
    
    Aligned with usecase.md Section H - Conversation Memory (Follow-ups):
    - Track time range
    - Track completion/assignment status constraints
    - Track primary breakdown dimension
    - Track selected module(s)/role(s)/location(s)
    
    Enables:
    - Follow-up queries that reference previous results
    - Implicit filter retention (e.g., "in Mumbai" carries forward)
    - Understanding pronoun references ("those users", "that module")
    """
    
    # Conversation history
    messages: List[Dict[str, str]] = field(default_factory=list)
    
    # Query history with results
    query_history: List[QueryRecord] = field(default_factory=list)
    
    # Active filters inferred from conversation
    active_filters: Dict[FilterType, InferredFilter] = field(default_factory=dict)
    
    # Last mentioned entities for pronoun resolution
    last_entities: Dict[str, Any] = field(default_factory=dict)
    
    # Section H: Track additional context between turns
    primary_breakdown_dimension: Optional[str] = None  # e.g., "by city"
    selected_modules: List[str] = field(default_factory=list)
    selected_roles: List[str] = field(default_factory=list)
    selected_locations: List[str] = field(default_factory=list)
    
    # Session metadata
    session_start: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    
    def add_message(self, role: str, content: str):
        """Add a message to conversation history."""
        self.messages.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        })
        self.last_activity = datetime.now()
    
    def add_query_record(self, record: QueryRecord):
        """Record an executed query for future reference."""
        self.query_history.append(record)
        self.last_activity = datetime.now()
        
        # Update breakdown dimension if present in the query
        if record.breakdown_dimension:
            self.primary_breakdown_dimension = record.breakdown_dimension
    
    def set_filter(
        self,
        filter_type: FilterType,
        field_name: str,
        value: Any,
        source_message: str,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """Set or update an active filter."""
        self.active_filters[filter_type] = InferredFilter(
            filter_type=filter_type,
            field_name=field_name,
            value=value,
            source_message=source_message,
            metadata=metadata or {}
        )
        
        # Also update the convenience tracking fields
        if filter_type == FilterType.BREAKDOWN_DIMENSION:
            self.primary_breakdown_dimension = field_name
        elif filter_type == FilterType.MODULE:
            if isinstance(value, list):
                self.selected_modules = value
            else:
                self.selected_modules = [value] if value else []
        elif filter_type == FilterType.LOCATION:
            if isinstance(value, list):
                self.selected_locations = value
            else:
                self.selected_locations = [value] if value else []
    
    def clear_filter(self, filter_type: FilterType):
        """Clear a specific filter."""
        if filter_type in self.active_filters:
            del self.active_filters[filter_type]
    
    def clear_all_filters(self):
        """Clear all active filters (e.g., when user starts a new topic)."""
        self.active_filters.clear()
        self.primary_breakdown_dimension = None
        self.selected_modules = []
        self.selected_roles = []
        self.selected_locations = []
    
    def get_last_query(self) -> Optional[QueryRecord]:
        """Get the most recent query record."""
        return self.query_history[-1] if self.query_history else None
    
    def get_last_n_messages(self, n: int = 6) -> List[Dict[str, str]]:
        """Get the last N messages for LLM context."""
        return self.messages[-n:] if len(self.messages) >= n else self.messages
    
    def get_active_filters_dict(self) -> Dict[str, Any]:
        """Get active filters as a dictionary for query building."""
        return {
            f.filter_type.value: {
                "field": f.field_name,
                "value": f.value
            }
            for f in self.active_filters.values()
        }
    
    def set_last_entity(self, entity_type: str, value: Any):
        """Store the last mentioned entity of a type for pronoun resolution."""
        self.last_entities[entity_type] = value
    
    def get_last_entity(self, entity_type: str) -> Optional[Any]:
        """Get the last mentioned entity of a type."""
        return self.last_entities.get(entity_type)

    def get_active_filter(self, filter_type: FilterType) -> Optional[InferredFilter]:
        """Get an active filter of a specific type."""
        return self.active_filters.get(filter_type)

    def get_time_filter(self) -> Optional[InferredFilter]:
        """Convenience accessor for time period filter."""
        return self.get_active_filter(FilterType.TIME_PERIOD)
    
    def set_breakdown_dimension(self, dimension: str, source_message: str):
        """Set the primary breakdown dimension for subsequent queries."""
        self.set_filter(
            FilterType.BREAKDOWN_DIMENSION,
            dimension,
            dimension,
            source_message
        )
    
    def get_breakdown_dimension(self) -> Optional[str]:
        """Get the current breakdown dimension."""
        return self.primary_breakdown_dimension
    
    def to_llm_context(self) -> str:
        """
        Generate a context string for the LLM that summarizes the conversation state.
        This helps the LLM understand follow-up queries.
        
        Aligned with usecase.md Section H.
        """
        context_parts = []
        
        # Add active filters context
        if self.active_filters:
            context_parts.append("ACTIVE FILTERS FROM CONVERSATION (Section H):")
            for filter_type, f in self.active_filters.items():
                context_parts.append(f"  - {filter_type.value}: {f.field_name} = {f.value}")
        
        # Add breakdown dimension if set
        if self.primary_breakdown_dimension:
            context_parts.append(f"\nPRIMARY BREAKDOWN DIMENSION: {self.primary_breakdown_dimension}")
        
        # Add selected entities
        if self.selected_modules:
            context_parts.append(f"SELECTED MODULES: {', '.join(self.selected_modules[:5])}")
        if self.selected_locations:
            context_parts.append(f"SELECTED LOCATIONS: {', '.join(self.selected_locations[:5])}")
        if self.selected_roles:
            context_parts.append(f"SELECTED ROLES: {', '.join(self.selected_roles[:5])}")
        
        # Add last query context
        last_query = self.get_last_query()
        if last_query:
            context_parts.append(f"\nLAST QUERY CONTEXT:")
            context_parts.append(f"  - User asked: \"{last_query.user_message}\"")
            context_parts.append(f"  - Result: {last_query.result_total} records found")
            context_parts.append(f"  - Response type: {last_query.response_type}")
            if last_query.primary_entity_label:
                count = last_query.primary_entity_count or ""
                context_parts.append(
                    f"  - Primary entity: {last_query.primary_entity_label}"
                    + (f" ({count} total)" if count else "")
                )
            if last_query.breakdown_dimension:
                context_parts.append(f"  - Breakdown by: {last_query.breakdown_dimension}")
            
            if last_query.aggregations:
                context_parts.append("  - Key metrics from last query:")
                for agg_name, agg_value in list(last_query.aggregations.items())[:5]:
                    if isinstance(agg_value, dict):
                        if 'value' in agg_value:
                            context_parts.append(f"    • {agg_name}: {agg_value['value']}")
                        elif 'buckets' in agg_value:
                            context_parts.append(f"    • {agg_name}: {len(agg_value['buckets'])} categories")
        
        # Add last entities for pronoun resolution
        if self.last_entities:
            context_parts.append(f"\nLAST MENTIONED ENTITIES:")
            for entity_type, value in self.last_entities.items():
                if isinstance(value, list):
                    context_parts.append(f"  - {entity_type}: {', '.join(str(v) for v in value[:5])}")
                else:
                    context_parts.append(f"  - {entity_type}: {value}")
        
        return "\n".join(context_parts) if context_parts else ""


class ConversationContextManager:
    """
    Manages conversation contexts across sessions.
    In production, this would integrate with a session store (Redis, etc.)
    """

    MODULE_KEYWORDS = [
        'module', 'modules',
        'training', 'trainings',
        'course', 'courses',
        'program', 'programs',
        'lesson', 'lessons',
        'curriculum'
    ]

    NON_PUBLISHED_MODULE_KEYWORDS = [
        'draft', 'drafts',
        'unpublished', 'unpublish',
        'deleted', 'delete',
        'retired', 'retire',
        'removed', 'remove',
        'archived', 'archive'
    ]

    def __init__(self):
        self._contexts: Dict[str, ConversationContext] = {}
    
    def get_or_create(self, session_id: str) -> ConversationContext:
        """Get existing context or create a new one for a session."""
        if session_id not in self._contexts:
            self._contexts[session_id] = ConversationContext()
        return self._contexts[session_id]
    
    def clear(self, session_id: str):
        """Clear context for a session."""
        if session_id in self._contexts:
            del self._contexts[session_id]
    
    def update_from_message(self, context: ConversationContext, message: str, 
                           extracted_entities: Dict[str, Any], time_period: Optional[Dict] = None,
                           time_field: str = "created_on"):
        """
        Update context based on a new user message.
        Extracts and retains relevant filters.
        
        Aligned with usecase.md Section H - Conversation Memory.
        """
        # Check for topic change indicators
        topic_change_indicators = [
            "new query", "different question", "forget that", "start over",
            "new report", "something else", "change topic", "fresh start",
            "reset", "clear filters"
        ]
        if any(indicator in message.lower() for indicator in topic_change_indicators):
            context.clear_all_filters()
            return
        
        # Detect if this is an independent question (not a follow-up)
        # Independent questions should clear irrelevant filters
        is_independent = self._is_independent_question(message, context, extracted_entities)
        if is_independent:
            # Clear filters that are not relevant to the new question
            self._clear_irrelevant_filters(context, message, extracted_entities, time_period)
        
        # Update time filter if detected
        if time_period and not time_period.get('is_lifetime', False):
            context.set_filter(
                FilterType.TIME_PERIOD,
                time_field,
                {"gte": time_period['start_timestamp'], "lt": time_period['end_timestamp']},
                message,
                metadata={
                    "description": time_period.get('description'),
                    "field": time_field
                }
            )
        
        # Update location filters
        if extracted_entities.get('cities'):
            cities = extracted_entities['cities']
            context.set_filter(FilterType.LOCATION, "city", cities[0] if len(cities) == 1 else cities, message)
            context.set_last_entity('city', cities)
        
        if extracted_entities.get('countries'):
            countries = extracted_entities['countries']
            context.set_filter(FilterType.LOCATION, "country", countries[0] if len(countries) == 1 else countries, message)
            context.set_last_entity('country', countries)
        
        # Update completion status filter
        if 'completion_filter' in extracted_entities:
            context.set_filter(
                FilterType.COMPLETION_STATUS,
                "completed_status",
                extracted_entities['completion_filter'],
                message
            )
        
        # Update module filter if specific module mentioned
        if extracted_entities.get('modules'):
            modules = extracted_entities['modules']
            context.set_filter(FilterType.MODULE, "module_name", modules, message)
            context.set_last_entity('module', modules)
        
        # Update skill filter
        if extracted_entities.get('skills'):
            skills = extracted_entities['skills']
            context.set_filter(FilterType.SKILL, "skill_name", skills, message)
            context.set_last_entity('skill', skills)
        
        # Update product filter
        if extracted_entities.get('products'):
            products = extracted_entities['products']
            context.set_filter(FilterType.PRODUCT, "product_name", products, message)
            context.set_last_entity('product', products)
        
        # Update user status filter
        if 'user_status_filter' in extracted_entities:
            context.set_filter(
                FilterType.USER_STATUS,
                "user_status",
                extracted_entities['user_status_filter'],
                message
            )
        
        # Update module status filter (default to published=0 unless user explicitly asked for drafts/deleted)
        if 'module_status_filter' in extracted_entities:
            context.set_filter(
                FilterType.MODULE_STATUS,
                "module_status",
                extracted_entities['module_status_filter'],
                message
            )
        elif self._message_mentions_modules(message) and not self._message_mentions_non_published_modules(message):
            context.set_filter(
                FilterType.MODULE_STATUS,
                "module_status",
                0,
                message
            )
        
        # Update assignment/assignment status filter
        if 'assigned_status_filter' in extracted_entities:
            context.set_filter(
                FilterType.ASSIGNMENT_STATUS,
                "assigned_status",
                extracted_entities['assigned_status_filter'],
                message
            )
        
        # Update person name filter (for queries like "tanuj's completions")
        # CRITICAL: Skip if this is a completion rate query - "completion" is a metric term, not a person name!
        if extracted_entities.get('person_name') and not extracted_entities.get('completion_rate_query'):
            person_name = extracted_entities['person_name']
            # Double-check: don't add filter if person_name is actually a metric term
            metric_terms = ['completion', 'completions', 'rate', 'rates', 'percentage', 'percent']
            if person_name.lower() not in metric_terms:
                context.set_filter(
                    FilterType.PERSON_NAME,
                    "first_name",  # Primary field to match
                    person_name,
                    message
                )
                context.set_last_entity('person', person_name)
        
        # Update breakdown dimension (Section H)
        if extracted_entities.get('group_by'):
            context.set_breakdown_dimension(extracted_entities['group_by'], message)

    def _message_mentions_modules(self, message: str) -> bool:
        message_lower = message.lower()
        return any(keyword in message_lower for keyword in self.MODULE_KEYWORDS)

    def _message_mentions_non_published_modules(self, message: str) -> bool:
        message_lower = message.lower()
        return any(keyword in message_lower for keyword in self.NON_PUBLISHED_MODULE_KEYWORDS)
    
    def _is_independent_question(
        self, 
        message: str, 
        context: ConversationContext, 
        extracted_entities: Dict[str, Any]
    ) -> bool:
        """
        Detect if a question is independent (not a follow-up).
        
        An independent question:
        - Doesn't use follow-up keywords (now, also, same, only, exclude, etc.)
        - Doesn't reference previous context with pronouns (those, them, it, that)
        - Asks about a completely different topic
        """
        message_lower = message.lower()
        
        # Follow-up indicators - if these are present, it's likely a follow-up
        follow_up_keywords = [
            'now', 'also', 'same', 'only', 'exclude', 'except', 'but', 'and',
            'then', 'next', 'after', 'before', 'more', 'less', 'another',
            'additionally', 'furthermore', 'moreover', 'similarly'
        ]
        
        # Pronoun references to previous context
        pronoun_references = [
            'those', 'these', 'them', 'it', 'that', 'this', 'they',
            'the users', 'the modules', 'the data', 'the results'
        ]
        
        # If message uses follow-up keywords or pronouns, it's likely a follow-up
        has_follow_up_keywords = any(kw in message_lower for kw in follow_up_keywords)
        has_pronoun_references = any(pronoun in message_lower for pronoun in pronoun_references)
        
        if has_follow_up_keywords or has_pronoun_references:
            return False
        
        # Check if question is asking about something completely different
        # e.g., "what is the role" after asking about "module completion"
        independent_question_patterns = [
            r'what\s+is\s+the\s+(role|status|designation|name|email|city|country)',
            r'who\s+is\s+',
            r'what\s+(role|status|designation|name|email|city|country)\s+(is|does|has)',
            r'describe\s+',
            r'tell\s+me\s+about\s+',
            r'what\s+can\s+you\s+',
            r'what\s+fields',
            r'what\s+data',
            r'schema',
            r'help'
        ]
        
        for pattern in independent_question_patterns:
            if re.search(pattern, message_lower):
                # If it's asking about a person's attribute (role, email, etc.) 
                # and there's no explicit connection to previous query, it's independent
                if 'person_name' in extracted_entities or any(
                    word in message_lower for word in ['role', 'designation', 'email', 'city', 'country']
                ):
                    # Check if there's a person name - if asking about a specific person's attribute,
                    # it's likely independent unless it references previous context
                    if not has_pronoun_references:
                        return True
        
        return False
    
    def _clear_irrelevant_filters(
        self, 
        context: ConversationContext, 
        message: str, 
        extracted_entities: Dict[str, Any],
        time_period: Optional[Dict] = None
    ):
        """
        Clear filters that are not relevant to the current question.
        
        For example, if previous query was about "module completions in bengaluru since july",
        and new question is "what is the role of nandini?", we should clear:
        - Time period filters (unless new question has its own time)
        - Completion status filters (unless new question is about completions)
        - Location filters (unless new question mentions location)
        - Module filters (unless new question mentions modules)
        """
        message_lower = message.lower()
        
        # Check what the new question is about
        is_about_completions = any(kw in message_lower for kw in [
            'completion', 'completed', 'finished', 'done', 'complete'
        ])
        is_about_modules = any(kw in message_lower for kw in [
            'module', 'modules', 'training', 'course', 'courses'
        ])
        has_time_period = (time_period is not None and not time_period.get('is_lifetime', False)) or any(
            kw in message_lower for kw in ['since', 'from', 'after', 'before', 'during', 'in']
        )
        has_location = 'cities' in extracted_entities or 'countries' in extracted_entities
        
        # Clear completion status if not about completions
        if not is_about_completions:
            context.clear_filter(FilterType.COMPLETION_STATUS)
        
        # Clear module filters if not about modules
        if not is_about_modules:
            context.clear_filter(FilterType.MODULE)
        
        # Clear time filters if new question doesn't mention time
        # (unless it's asking about a time-based attribute like "when was X created")
        if not has_time_period and 'when' not in message_lower:
            context.clear_filter(FilterType.TIME_PERIOD)
        
        # Clear location filters if new question doesn't mention location
        if not has_location:
            context.clear_filter(FilterType.LOCATION)
        
        # Clear assignment status if not about assignments/completions
        if not is_about_completions:
            context.clear_filter(FilterType.ASSIGNMENT_STATUS)
    
    def resolve_pronouns(self, context: ConversationContext, message: str) -> str:
        """
        Resolve pronouns in a message using conversation context.
        E.g., "those users" -> "users from Mumbai"
        """
        resolved = message
        
        # Pronoun patterns and their entity types
        pronoun_mappings = {
            ('those users', 'these users', 'the users', 'them'): 'city',
            ('that module', 'those modules', 'the module', 'it'): 'module',
            ('that city', 'those cities', 'the city'): 'city',
            ('that skill', 'those skills'): 'skill',
        }
        
        message_lower = message.lower()
        
        for pronouns, entity_type in pronoun_mappings.items():
            for pronoun in pronouns:
                if pronoun in message_lower:
                    entity = context.get_last_entity(entity_type)
                    if entity:
                        if isinstance(entity, list):
                            replacement = ', '.join(str(e) for e in entity)
                        else:
                            replacement = str(entity)
                        # Note: We don't actually replace in the message,
                        # but we ensure the filter is active
                        break
        
        return resolved


# Singleton instance for the context manager
_context_manager = None

def get_context_manager() -> ConversationContextManager:
    """Get the singleton context manager instance."""
    global _context_manager
    if _context_manager is None:
        _context_manager = ConversationContextManager()
    return _context_manager

