"""
Schema Context Builder for AI Reports Bot.
Dynamically builds schema context for the LLM based on the actual index structure.

Aligned with usecase.md specification for Module Consumption / Completion Index.
"""

from typing import Dict, Any, List, Optional
from datetime import datetime


class SchemaContextBuilder:
    """
    Builds dynamic schema context for the LLM.
    Generates concise, relevant context based on the actual index schema.
    
    Field groups and descriptions aligned with usecase.md Section A.
    """
    
    # Field descriptions for better LLM understanding - aligned with usecase.md Section A
    FIELD_DESCRIPTIONS = {
        # USER DIMENSIONS
        'uid': 'Unique numeric User ID - USE THIS for counting unique users/learners (cardinality)',
        'first_name': 'User first name (display only)',
        'last_name': 'User last name (display only)',
        'email_addr': 'User email address (for display, not for counting)',
        'mobile_number': 'User mobile/phone number',
        'user_role': 'User role: admin / learner / manager',
        'user_status': 'User status code: 5=active, 4=deleted, 1=invited',
        'is_admin': 'Admin flag: yes/no',
        'designation': 'Job title/designation',
        'language_name': 'User preferred language',
        'hired_on': 'Date the user joined/was hired',
        'user_created_on': 'Account creation date',
        'manager_email_addr': 'Email of the user\'s manager',
        
        # MODULE / COURSE DIMENSIONS
        'mid': 'Unique numeric Module ID - USE THIS for counting unique modules (cardinality)',
        'cmid': 'Course or module ID - USE THIS for counting unique courses/containers',
        'module_name': 'Training module/course name (for display, NOT for counting)',
        'module_type_name': 'Type/category of module',
        'module_status': 'Module status: 0=published, 1=deleted, 2=draft',
        'module_points': 'Points awarded for completing the module',
        'module_created_by': 'User who created the module',
        'module_created_on': 'Date module was created in the LMS (not published)',
        'module_updated_on': 'Date module was last updated',
        'module_desc': 'Module description text',
        'module_imp_name': 'Module implementation name',
        'pre_req_module_name': 'Prerequisite module name',
        'prod_master_name': 'Master product grouping/family',
        'product_name': 'Product category/name',
        'skill_name': 'Skill category associated with module',
        'sub_skill_name': 'Sub-skill associated with module',
        'tags': 'Tags/labels applied to module',
        'trainer_email_addr': 'Trainer/instructor email address',
        'coach_email_addr': 'Coach email assigned to module',
        'staff': 'Staff/owner of the module',
        'published_date': 'Date the module was published/went live - USE THIS for "modules published" queries',
        'equivalence_module_name': 'Equivalent module name for cross-credit',
        'estd_time': 'Estimated minutes needed to complete module',
        'ratings': 'Ratings given to the module',
        
        # COMPLETION / CONSUMPTION DIMENSIONS
        'assigned_status': 'Assignment flag: 0=assigned, 1=not assigned',
        'completed_status': 'Completion status: 0=not completed, 1=completed',
        'complete_percentage': 'Completion percentage: 0-100',
        'completed_date': 'Date when user finished the module',
        'complete_type': 'Completion type: learning / assessment / equivalence',
        'complete_version': 'Version of the module that was completed',
        'comments': 'User comments/feedback on completion',
        'invited_date': 'Date invitation was sent to the learner',
        'invited_time': 'Time invitation was sent',
        
        # LOCATION DIMENSIONS
        'city': 'City name in LOWERCASE (e.g., "bengaluru", "mumbai")',
        'state': 'State or region of the user',
        'country': 'Full country name (e.g., "republic of india", "united arab emirates")',
        
        # META FIELDS
        'created_by': 'User who created the record',
        'created_on': 'Primary assignment/record date (Unix timestamp)',
        'updated_on': 'Timestamp when record was last updated',
        'version': 'Record version number',
        'id': 'Internal record identifier',
        
        # CUSTOM ATTRIBUTES (tenant-specific)
        'attribute_2': 'Custom attribute 2 (tenant-specific)',
        'attribute_3': 'Custom attribute 3 (tenant-specific)',
        'attribute_4': 'Custom attribute 4 (tenant-specific)',
        'attribute_5': 'Custom attribute 5 (tenant-specific)',
        # Note: attributes go up to attribute_42 - only include if user explicitly references them
    }
    
    # Value mappings that the LLM should know about
    VALUE_HINTS = {
        'city': {
            'note': 'Cities are stored in LOWERCASE',
            'examples': ['bengaluru', 'mumbai', 'chennai', 'delhi', 'pune'],
            'aliases': {
                'bangalore': 'bengaluru',
                'bombay': 'mumbai',
                'madras': 'chennai',
                'calcutta': 'kolkata'
            }
        },
        'country': {
            'note': 'Countries use full official names',
            'examples': ['republic of india', 'united arab emirates', 'japan'],
            'aliases': {
                'india': ['republic of india', 'india'],
                'uae': 'united arab emirates',
                'germany': 'federal republic of germany'
            }
        },
        'completed_status': {
            'note': 'Integer values only',
            'mapping': {'completed': 1, 'not completed': 0}
        },
        'user_status': {
            'note': 'Integer status codes',
            'mapping': {'active': 5, 'deleted': 4, 'invited': 1}
        },
        'module_status': {
            'note': 'Integer status codes',
            'mapping': {'published': 0, 'deleted': 1, 'draft': 2}
        },
        'assigned_status': {
            'note': 'Integer assignment codes',
            'mapping': {'assigned': 0, 'not assigned': 1}
        }
    }
    
    # Section summaries aligned with usecase.md Section A
    SECTION_SUMMARIES = [
        {
            'name': 'User Dimensions',
            'description': 'Profile level attributes for each learner. Use uid for counting unique users.',
            'fields': ['uid', 'first_name', 'last_name', 'email_addr', 'mobile_number', 
                      'user_role', 'user_status', 'is_admin', 'designation', 'language_name',
                      'hired_on', 'user_created_on', 'manager_email_addr']
        },
        {
            'name': 'Module / Course Dimensions',
            'description': 'Metadata about the training module. Use mid for counting unique modules, cmid for courses.',
            'fields': ['mid', 'cmid', 'module_name', 'module_type_name', 'module_status', 
                      'module_points', 'module_created_by', 'module_created_on', 'module_updated_on',
                      'module_desc', 'module_imp_name', 'pre_req_module_name', 'prod_master_name',
                      'product_name', 'skill_name', 'sub_skill_name', 'tags', 'trainer_email_addr',
                      'coach_email_addr', 'staff', 'published_date', 'equivalence_module_name',
                      'estd_time', 'ratings']
        },
        {
            'name': 'Completion / Consumption Dimensions',
            'description': 'User progress and completion tracking.',
            'fields': ['assigned_status', 'completed_status', 'complete_percentage', 
                      'completed_date', 'complete_type', 'complete_version', 'comments',
                      'invited_date', 'invited_time']
        },
        {
            'name': 'Location Dimensions',
            'description': 'Geography fields used for segmentation.',
            'fields': ['city', 'state', 'country']
        },
        {
            'name': 'Meta Information',
            'description': 'System metadata for each record.',
            'fields': ['created_by', 'created_on', 'updated_on', 'version', 'id']
        },
        {
            'name': 'Custom Attributes',
            'description': 'Tenant-specific custom fields (attribute_2 to attribute_42). Only use if explicitly referenced.',
            'fields': ['attribute_2', '...', 'attribute_42']
        }
    ]
    
    @classmethod
    def build_context(cls, schema: Dict[str, Any], include_samples: bool = True) -> str:
        """
        Build a comprehensive but concise schema context for the LLM.
        
        Args:
            schema: The enriched schema from OpenSearch
            include_samples: Whether to include sample values
            
        Returns:
            A formatted string context for the LLM prompt
        """
        parts = []
        
        # Header
        index_id = schema.get('index_id', 'unknown')
        doc_count = schema.get('document_count', 0)
        parts.append(f"INDEX: {index_id}")
        parts.append(f"TOTAL RECORDS: {doc_count:,}")
        parts.append("")
        
        # Fields section
        parts.append("AVAILABLE FIELDS:")
        parts.append("-" * 40)
        
        fields = schema.get('fields', [])
        for field in fields:
            field_name = field.get('name', '')
            field_type = field.get('type', 'unknown')
            
            # Get description
            desc = cls.FIELD_DESCRIPTIONS.get(field_name, '')
            
            # Build field line
            line = f"  {field_name} ({field_type})"
            if desc:
                line += f" - {desc}"
            parts.append(line)
            
            # Add sample values if available and requested
            if include_samples:
                samples = field.get('sample_values', [])
                if samples:
                    sample_str = ', '.join(str(s)[:25] for s in samples[:5])
                    parts.append(f"    Examples: [{sample_str}]")
                
                # Add stats for numeric fields
                stats = field.get('stats', {})
                if stats and field_type in ['long', 'integer', 'float', 'double']:
                    parts.append(f"    Range: {stats.get('min')} to {stats.get('max')}")
        
        parts.append("")
        parts.append("SCHEMA SECTIONS:")
        parts.append("-" * 40)
        for section in cls.SECTION_SUMMARIES:
            parts.append(f"{section['name']}: {section['description']}")
            fields_str = ', '.join(section['fields'])
            parts.append(f"  Fields: {fields_str}")
        parts.append("")
        
        # Query patterns section
        parts.append("QUERY PATTERNS:")
        parts.append("-" * 40)
        parts.append(cls._get_query_patterns())
        
        # Value mappings section
        parts.append("")
        parts.append("VALUE MAPPINGS:")
        parts.append("-" * 40)
        parts.append(cls._get_value_mappings())
        
        return '\n'.join(parts)
    
    @classmethod
    def _get_query_patterns(cls) -> str:
        """Get common query patterns for the LLM."""
        return """
  FILTERING:
    - Completed trainings: {"term": {"completed_status": 1}}
    - By city: {"term": {"city": "bengaluru"}}  (lowercase!)
    - By date range: {"range": {"created_on": {"gte": TIMESTAMP, "lt": TIMESTAMP}}}
    - Combined: {"bool": {"must": [filter1, filter2, ...]}}
  
  AGGREGATIONS (CRITICAL - use correct ID fields):
    - Count unique USERS: {"cardinality": {"field": "uid"}}  ← ALWAYS use uid, NOT email_addr
    - Count unique MODULES: {"cardinality": {"field": "mid"}}  ← ALWAYS use mid, NOT module_name
    - Count unique COURSES: {"cardinality": {"field": "cmid"}}  ← Use cmid for courses
    - Count unique MANAGERS: {"cardinality": {"field": "manager_email_addr"}}
    - Count unique TRAINERS: {"cardinality": {"field": "trainer_email_addr"}}
    - Count unique COACHES: {"cardinality": {"field": "coach_email_addr"}}
    - Group by field: {"terms": {"field": "city", "size": 100}}
    - Filter then count: {"filter": {"term": {...}}, "aggs": {"count": {"cardinality": {...}}}}
    
  GROUPING PATTERN (ID for query, NAME for display):
    When grouping by module/user, use ID field but add sub-agg for name:
    {"terms": {"field": "mid"}, "aggs": {"module_name": {"terms": {"field": "module_name", "size": 1}}}}
    {"terms": {"field": "uid"}, "aggs": {"user_email": {"terms": {"field": "email_addr", "size": 1}}}}
    
  IMPORTANT ID FIELDS:
    - uid = User ID (integer) - for counting unique users/learners
    - mid = Module ID (integer) - for counting unique modules/courses
    - cmid = Course/Module ID - for counting unique courses/containers
    - DO NOT use email_addr or module_name for cardinality counts
  
  FIELD USAGE:
    - Use "city" NOT "city.keyword" for aggregations
    - Use "module_name" NOT "module_name.keyword"
    - Use "match" for text search on module_name
    - Use "term" for exact matches on keyword fields"""
    
    @classmethod
    def _get_value_mappings(cls) -> str:
        """Get value mapping hints for the LLM."""
        return """
  CITIES (always lowercase):
    bangalore/Bangalore -> "bengaluru"
    bombay/Bombay -> "mumbai"
    madras/Madras -> "chennai"
    calcutta/Calcutta -> "kolkata"
  
  COUNTRIES (full names):
    india/India -> use BOTH: "republic of india" OR "india"
    uae/UAE -> "united arab emirates"
    germany -> "federal republic of germany"
  
  USER STATUS CODES:
    active/enabled -> user_status: 5
    deleted/inactive -> user_status: 4
    invited/pending invite -> user_status: 1
  
  MODULE STATUS CODES:
    published/live -> module_status: 0
    deleted/retired -> module_status: 1
    draft/unpublished -> module_status: 2
  
  COMPLETION STATUS:
    completed/done/finished -> completed_status: 1
    not completed/pending/incomplete -> completed_status: 0
  
  ASSIGNMENT STATUS:
    assigned/allocated -> assigned_status: 0
    not assigned/unassigned -> assigned_status: 1"""
    
    @classmethod
    def get_date_context(cls) -> str:
        """Get current date context for timestamp calculations."""
        now = datetime.now()
        
        # Pre-calculate common timestamp ranges
        current_year = now.year
        current_month = now.month
        
        return f"""
CURRENT DATE: {now.strftime('%B %d, %Y')}
CURRENT YEAR: {current_year}
CURRENT MONTH: {now.strftime('%B')}

TIMESTAMP REFERENCE (use created_on field):
  - Timestamps are Unix seconds since 1970
  - January 1, 2024 = 1704067200
  - January 1, 2025 = 1735689600
  - December 1, 2025 = 1764547200

DATE FIELD SELECTION (use the field matching the event asked):
  - COMPLETIONS/FINISHED -> use completed_date
  - MODULE PUBLISHING/LAUNCHED -> use published_date
  - RECORD CREATION/ASSIGNMENT -> use created_on
  - INVITES/SENDS/DELIVERED -> use invited_date
  - MODULE CREATION -> use module_created_on
  - MODULE UPDATES -> use module_updated_on

DATE INTERPRETATION RULES:
  1. Month only (e.g., "November") -> Assume {current_year}
  2. Month + Year (e.g., "November 2024") -> Use that specific month
  3. No time mentioned -> Query ALL data (no date filter)
  4. "last month" -> Previous calendar month
  5. "this month" -> Current month so far"""
    
    @classmethod
    def build_minimal_context(cls, schema: Dict[str, Any]) -> str:
        """
        Build a minimal context for simple queries.
        Used when query is straightforward and doesn't need full schema.
        """
        fields = schema.get('fields', [])
        field_names = [f.get('name') for f in fields if f.get('name')]
        
        return f"""
INDEX: {schema.get('index_id', 'unknown')}
FIELDS: {', '.join(field_names[:15])}

KEY ID FIELDS (ALWAYS use these for counting):
  - uid: User ID (integer) - USE FOR counting unique users/learners
  - mid: Module ID (integer) - USE FOR counting unique modules
  - cmid: Course/Module ID - USE FOR counting unique courses
  
OTHER KEY FIELDS:
  - email_addr: user email (for display only, NOT for counting)
  - module_name: training name (for display only, NOT for counting)
  - completed_status: 0=incomplete, 1=completed
  - assigned_status: 0=assigned, 1=not assigned
  - user_status: 5=active, 4=deleted, 1=invited
  - module_status: 0=published, 1=deleted, 2=draft
  - published_date: when module was published (for "published" queries)
  - completed_date: when user completed (for "completion" queries)
  - created_on: assignment/record date field (Unix timestamp)
  - city: lowercase city name
"""

