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
    
    # Index descriptions - what each index covers and what it's used for
    # This helps the LLM choose the right index based on user questions
    INDEX_DESCRIPTIONS = {
        'converse_lm_consumption_summary_reports_prod': (
            "🔴 MODULE CONSUMPTION & COMPLETION DATA - Use this ONLY for consumption/completion questions! "
            "This index tracks user learning progress, module assignments, completions, and training consumption. "
            "It contains records of users assigned to training modules, their completion status, completion "
            "dates, ratings, and progress. 🔴 KEY INDICATORS: 'completed', 'completion', 'assigned', 'ratings', "
            "'who completed', 'completion rate' → USE THIS INDEX! Use this index for queries about: user "
            "completions, module completion rates, assigned modules, training progress, user learning "
            "activity, module ratings, completion statistics, and any questions about who completed what "
            "training and when. DO NOT use this index for: 'modules published', 'publishing', 'publish date' "
            "→ Use catalog index instead!"
        ),
                'learnbee_module_reports_user_summary_prod': (
                    "User Profile & Directory Data - This index contains USER PROFILE + LOCATION + META + "
                    "CUSTOM ATTRIBUTES only. It does NOT contain module completion events, module assignments, "
                    "or training progress. Tenant scope is enforced via cmid=1 (ALWAYS include {\"term\": {\"cmid\": 1}} "
                    "when querying this index). Use this index for queries about: user directory lookups, user profile "
                    "information queries (\"info on [user name]\", \"give me info on tanuj\", \"user details\"), "
                    "user counts (active/invited/deleted), user segmentation by location (city/state/country), "
                    "user breakdowns by designation/role, finding users by email/uid/name, user creation/update "
                    "dates, and any query asking for user profile fields (first_name, last_name, email_addr, "
                    "country, hired_on, designation, user_role, manager_email_addr, etc.) WITHOUT module/completion "
                    "context. DO NOT use this index for: module completions, completion rates, assigned modules, "
                    "training progress, module names, scores, completion dates, or any question that combines "
                    "user profile + module activity. For those queries, use the module consumption index instead."
                ),
        'monthly_user_activity_summary_prod': (
            "🔴 MONTHLY USER ACTIVITY SUMMARY (COUNTS + POINTS) - Each record is ONE USER for ONE MONTH. "
            "This index stores MONTHLY per-user COUNTS and POINTS only (not event-level module records). "
            "Use it for monthly totals, monthly trends, leaderboards, and segmentation of: "
            "module (monthly module completions count), instant_answers, learning_pathway, lotd, points. "
            "⚠️ DOES NOT support: 'which modules were completed' / module_name breakdown (no module IDs/names). "
            "Tenant scope is enforced via cmid=1 (ALWAYS include {\"term\": {\"cmid\": 1}} when querying this index). "
            "Time filtering for activity months MUST use completed_on (epoch). Operational freshness questions can use updated_on / last_updated."
        ),
        'daily_user_activity_summary_prod': (
            "🔴 DAILY USER ACTIVITY SUMMARY (COUNTS + POINTS) - Each record is ONE USER for ONE DAY. "
            "This index stores DAILY per-user COUNTS and POINTS only (not event-level module records). "
            "Use it for day-level totals (today/yesterday/specific date ranges), daily trends, daily leaderboards, and segmentation of: "
            "module (daily module completion counts), instant_answers, learning_pathway, lotd, points. "
            "⚠️ DOES NOT support: 'which modules were completed' / module_name breakdown (no module IDs/names). "
            "Tenant scope is enforced via cmid=1 (ALWAYS include {\"term\": {\"cmid\": 1}} when querying this index). "
            "Time filtering for activity days MUST use completed_on (epoch). Operational freshness questions can use updated_on / last_updated. "
            "INDEX STRUCTURE NOTE: Even though the raw mapping stores boolean filters under \"must\", you must convert them to the documented nested bool/filter format before executing the query."
        ),
        'converse_lm_summary_reports_prod': (
            "🔴 MODULE CATALOG & METADATA INDEX - Use this for ALL 'published' / 'publishing' questions! "
            "This index contains MODULE/COURSE MASTER DATA only: module names, types, product/skill tags, "
            "estimated time, publish dates, status (published/draft/deleted), and metadata timestamps. "
            "🔴 KEY INDICATORS: 'modules published', 'published in', 'publishing', 'publish date' → USE THIS INDEX! "
            "It does NOT contain user completion events, completion dates, assigned_status, completed_status, "
            "or any learner data. Use this index for queries about: module catalog lookups, finding modules "
            "by name/type/product/skill/tags, module metadata summaries, publishing/content ops questions "
            "(modules published in a time period), taxonomy/tagging questions, module counts by product/"
            "skill/type, average estimated time. DO NOT use this index for: completion rates, who completed "
            "modules, completion statistics, assigned modules, or any learner-related queries. For those "
            "queries, use the module consumption index instead."
        ),
        # Add more index descriptions here as you add new indices
    }
    
    # Index-specific field mappings - CRITICAL: Fields differ by index!
    # This prevents confusion between similar field names in different indices
    INDEX_FIELD_MAPPINGS = {
        'converse_lm_consumption_summary_reports_prod': {
            # Module status field name in CONSUMPTION index
            'module_status': 'Module status: 0=published, 1=deleted, 2=draft - USE THIS FIELD NAME in consumption index',
            # User status field name
            'user_status': 'User status code: 5=active, 4=deleted, 1=invited',
            # NOTE: This index does NOT have a field called "status" - only "module_status" and "user_status"
        },
        'converse_lm_summary_reports_prod': {
            # Module status field name in CATALOG index
            'status': 'Module status: 0=published, 1=deleted, 2=draft - USE THIS FIELD NAME in catalog index',
            # NOTE: This index does NOT have "module_status" - only "status"
            # NOTE: This index does NOT have "user_status" - it's module catalog only
        },
        'learnbee_module_reports_user_summary_prod': {
            # User status field name in USER PROFILE index
            'status': 'User status code: 5=active, 4=deleted, 1=invited - USE THIS FIELD NAME in user profile index',
            # NOTE: This index does NOT have "module_status" - it's user profiles only
            # NOTE: This index does NOT have "user_status" - only "status"
        },
        'monthly_user_activity_summary_prod': {
            # User status field name in MONTHLY ACTIVITY index
            'status': 'User status code: 5=active, 4=deleted, 1=invited - USE THIS FIELD NAME in monthly activity index',
            'cmid': 'Tenant/container scope - ALWAYS filter cmid=1 in this deployment',
            'uid': 'Unique user record ID in this index - USE THIS for counting unique users (cardinality)',
            'completed_on': 'Activity month anchor timestamp (epoch) - USE THIS for monthly activity time filters and trends'
        },
        'daily_user_activity_summary_prod': {
            # User status field name in DAILY ACTIVITY index
            'status': 'User status code: 5=active, 4=deleted, 1=invited - USE THIS FIELD NAME in daily activity index',
            'cmid': 'Tenant/container scope - ALWAYS filter cmid=1 in this deployment',
            'uid': 'Unique user record ID in this index - USE THIS for counting unique users (cardinality)',
            'completed_on': 'Activity day anchor timestamp (epoch) - USE THIS for daily activity time filters, day-level trends, and date_histogram buckets'
        }
    }
    
    # Field descriptions for better LLM understanding - aligned with usecase.md Section A
    # NOTE: These are general descriptions. Always check INDEX_FIELD_MAPPINGS for index-specific field names!
    FIELD_DESCRIPTIONS = {
        # USER DIMENSIONS
        'uid': 'Unique numeric User ID - USE THIS for counting unique users/learners (cardinality)',
        'first_name': 'User first name (display only)',
        'last_name': 'User last name (display only)',
        'email_addr': 'User email address (for display, not for counting)',
        'mobile_number': 'User mobile/phone number',
        'user_role': 'User role: admin / learner / manager',
        'user_status': 'User status code: 5=active, 4=deleted, 1=invited (CONSUMPTION INDEX ONLY)',
        'status': 'Status field - MEANING VARIES BY INDEX: In catalog index = module status (0=published, 1=deleted, 2=draft). In user profile / monthly activity indices = user status (5=active, 4=deleted, 1=invited)',
        'is_admin': 'Admin flag: yes/no',
        'designation': 'Job title/designation',
        'language_name': 'User preferred language',
        'hired_on': 'Date the user joined/was hired',
        'user_created_on': 'Account creation date',
        'manager_email_addr': 'Email of the user\'s manager',
        'dob': 'Date of birth (keyword field, format may vary)',
        
        # MODULE / COURSE DIMENSIONS
        'mid': 'Unique numeric Module ID - USE THIS for counting unique modules (cardinality)',
        'cmid': 'Course/Container/Tenant scope ID (integer). In this deployment ALWAYS filter cmid=1 for tenant scope; use cardinality(cmid) only if user explicitly asks for unique courses/containers',
        'module_name': 'Training module/course name (for display, NOT for counting)',
        'module_type_name': 'Type/category of module',
        'module_status': 'Module status: 0=published, 1=deleted, 2=draft (CONSUMPTION INDEX ONLY - catalog index uses "status" instead)',
        'module_points': 'Points awarded for completing the module',
        'module_created_by': 'User who created the module',
        'module_created_on': 'Date module was created in the LMS (not published)',
        'module_updated_on': 'Date module was last updated',
        'module_desc': 'Module description text',
        'module_imp_name': 'Module implementation name',
        'pre_req_module_name': 'Prerequisite module name',
        'equivalence_module_name': 'Equivalent module name',
        'prod_master_name': 'Master product grouping/family',
        'product_name': 'Product category/name',
        'skill_name': 'Skill category associated with module',
        'sub_skill_name': 'Sub-skill associated with module',
        'tags': 'Tags/labels applied to module',
        'trainer_email_addr': 'Trainer/instructor email address',
        'coach_email_addr': 'Coach email assigned to module',
        'staff': 'Staff/owner of the module',
        'published_date': 'Date the module was published/went live - USE THIS for "modules published" queries',
        'estd_time': 'Estimated minutes needed to complete module',
        'ratings': 'Ratings given to the module',
        'version': 'Record version number',
        'created_by': 'User who created the record',
        'language_name': 'Language name (for modules in catalog index)',
        
        # COMPLETION / CONSUMPTION DIMENSIONS (CONSUMPTION INDEX ONLY)
        'assigned_status': 'Assignment flag: 0=assigned, 1=not assigned (CONSUMPTION INDEX ONLY)',
        'completed_status': 'Completion status: 0=not completed, 1=completed (CONSUMPTION INDEX ONLY)',
        'complete_percentage': 'Completion percentage: 0-100 (CONSUMPTION INDEX ONLY)',
        'completed_date': 'Date when user finished the module (CONSUMPTION INDEX ONLY)',
        'complete_type': 'Completion type: learning / assessment / equivalence (CONSUMPTION INDEX ONLY)',
        'complete_version': 'Version of the module that was completed (CONSUMPTION INDEX ONLY)',
        'comments': 'User comments/feedback on completion (CONSUMPTION INDEX ONLY)',
        'invited_date': 'Date invitation was sent to the learner (CONSUMPTION INDEX ONLY)',
        'invited_time': 'Time invitation was sent (CONSUMPTION INDEX ONLY)',
        
        # LOCATION DIMENSIONS
        'city': 'City name in LOWERCASE (e.g., "bengaluru", "mumbai")',
        'state': 'State or region of the user',
        'country': 'Full country name (e.g., "republic of india", "united arab emirates")',
        
        # META FIELDS
        'created_on': 'Primary assignment/record date (Unix timestamp)',
        'updated_on': 'Timestamp when record was last updated',
        'id': 'Internal record identifier (not used for monthly/daily activity unique-user counts).',
        'last_updated': 'Last sync/update timestamp (epoch) (activity summary indexes use this for freshness checks)',
        'completed_on': 'Activity day/month anchor timestamp (epoch) - use for day/month filters and trends in daily_user_activity_summary_prod and monthly_user_activity_summary_prod',
        'module': 'Monthly/Daily count of module completions in activity summary indexes (integer count) - use sum(module) for totals',
        'instant_answers': 'Monthly/Daily count of Instant Answer queries in activity summary indexes',
        'learning_pathway': 'Monthly/Daily count of Learning Pathway completions in activity summary indexes',
        'lotd': 'Monthly/Daily count of Learning Of The Day completions in activity summary indexes',
        'points': 'Total points earned during the period (monthly/daily activity indexes)',
        'total_points': 'Total points earned from the start (cumulative total in activity summary indexes)',
        
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
        
        # Header with index description
        index_id = schema.get('index_id', 'unknown')
        doc_count = schema.get('document_count', 0)
        parts.append(f"INDEX: {index_id}")
        parts.append(f"TOTAL RECORDS: {doc_count:,}")
        
        # Add index description if available
        index_description = cls.INDEX_DESCRIPTIONS.get(index_id)
        if index_description:
            parts.append("")
            parts.append("INDEX DESCRIPTION:")
            parts.append("-" * 40)
            parts.append(index_description)
        
        parts.append("")
        
        # Add critical field name warnings for this specific index
        index_specific_fields = cls.INDEX_FIELD_MAPPINGS.get(index_id, {})
        if index_specific_fields:
            parts.append("🔴 CRITICAL: INDEX-SPECIFIC FIELD NAMES:")
            parts.append("-" * 40)
            for field_name, field_desc in index_specific_fields.items():
                parts.append(f"  {field_name}: {field_desc}")
            parts.append("")
            parts.append("⚠️ WARNING: Field names differ between indices!")
            parts.append("  - Consumption index uses: module_status, user_status")
            parts.append("  - Catalog index uses: status (for modules)")
            parts.append("  - User profile index uses: status (for users, different codes!)")
            parts.append("  - Monthly/Daily activity indexes use: status (users) + completed_on (activity day/month anchor) + uid (user id) and ALWAYS need cmid=1")
            parts.append("  - ALWAYS use the field names that exist in the selected index!")
            parts.append("")
        
        # Fields section
        parts.append("ALL AVAILABLE FIELDS IN THIS INDEX:")
        parts.append("-" * 40)
        
        fields = schema.get('fields', [])
        for field in fields:
            field_name = field.get('name', '')
            field_type = field.get('type', 'unknown')
            
            # Get description - prioritize index-specific mapping
            if field_name in index_specific_fields:
                desc = index_specific_fields[field_name]
            else:
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
    - (MONTHLY/DAILY ACTIVITY INDEX) Count unique USERS: {"cardinality": {"field": "uid"}}  ← In monthly_user_activity_summary_prod & daily_user_activity_summary_prod use uid (NOT id)
    - Count unique MODULES: {"cardinality": {"field": "mid"}}  ← ALWAYS use mid, NOT module_name
    - Count unique COURSES: {"cardinality": {"field": "cmid"}}  ← Use cmid for courses
    - Count unique MANAGERS: {"cardinality": {"field": "manager_email_addr"}}
    - Count unique TRAINERS: {"cardinality": {"field": "trainer_email_addr"}}
    - Count unique COACHES: {"cardinality": {"field": "coach_email_addr"}}
    - Group by field: {"terms": {"field": "city", "size": 100}}
    - Filter then count: {"filter": {"term": {...}}, "aggs": {"count": {"cardinality": {...}}}}
    - (ACTIVITY SUMMARY INDEXES) Totals: {"sum": {"field": "module"}} / {"sum": {"field": "instant_answers"}} / {"sum": {"field": "points"}}  ← works for daily or monthly counts
    - (MONTHLY ACTIVITY INDEX) Monthly trend: {"date_histogram": {"field": "completed_on", "calendar_interval": "month"}} + sum(metric)
    - (DAILY ACTIVITY INDEX) Daily trend: {"date_histogram": {"field": "completed_on", "fixed_interval": "1d"}} + sum(metric)
    
  GROUPING PATTERN (ID for query, NAME for display):
    When grouping by module/user, use ID field but add sub-agg for name:
    {"terms": {"field": "mid"}, "aggs": {"module_name": {"terms": {"field": "module_name", "size": 1}}}}
    {"terms": {"field": "uid"}, "aggs": {"user_email": {"terms": {"field": "email_addr", "size": 1}}}}
    
  IMPORTANT ID FIELDS:
    - uid = User ID (integer) - for counting unique users/learners
    - uid = User record ID in monthly/daily activity indexes - for counting unique users in monthly_user_activity_summary_prod or daily_user_activity_summary_prod
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
  
  USER STATUS CODES (🔴 FIELD NAME VARIES BY INDEX):
    🔴 CONSUMPTION INDEX: Use "user_status" field
    🔴 USER PROFILE INDEX: Use "status" field (NOT user_status!)
    Status codes (same for both):
    active/enabled -> 5
    deleted/inactive -> 4
    invited/pending invite -> 1
  
  MODULE STATUS CODES (🔴 FIELD NAME VARIES BY INDEX):
    🔴 CONSUMPTION INDEX: Use "module_status" field
    🔴 CATALOG INDEX: Use "status" field (NOT module_status!)
    Status codes (same for both):
    published/live -> 0
    deleted/retired -> 1
    draft/unpublished -> 2
  
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

