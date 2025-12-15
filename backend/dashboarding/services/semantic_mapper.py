"""
Semantic Field Mapper for intelligent query understanding.
Maps natural language terms to actual database field names.

Aligned with usecase.md Sections C (Normalization Rules) and D (Date Field Selection).
"""

from typing import Dict, List, Optional, Tuple, Any
import re


class SemanticFieldMapper:
    """
    Maps semantic user terms to actual database fields.
    Example: "scores" → "score", "grade" → "score", "trainings" → "module_name"
    
    Also extracts entities and concepts from natural language queries.
    Aligned with usecase.md Section C - Normalization Rules.
    """
    
    # Semantic aliases - maps user terms to actual field names
    # Aligned with usecase.md Section C.5 - Synonyms / adjacent term mapping
    FIELD_ALIASES = {
        # Score-related terms
        'score': ['score', 'scores', 'grade', 'grades', 'marks', 'result', 'results', 'performance'],
        'ratings': ['rating', 'ratings', 'feedback score', 'review'],
        'module_points': ['points', 'point', 'credits', 'points awarded'],
        
        # Progress terms - Section C.5: "progress" → complete_percentage
        'complete_percentage': ['progress', 'percentage', 'completion percentage', 'percent complete', 
                                '%', 'completion rate', 'completion %'],
        
        # Module/Training terms
        'module_name': ['module', 'modules', 'training', 'trainings', 'course', 'courses', 
                       'program', 'programs', 'lesson', 'lessons', 'curriculum'],
        'module_type_name': ['module type', 'course type', 'category', 'module category', 'training type'],
        'module_status': ['module status', 'published status', 'draft status', 'live modules'],
        'module_created_by': ['module creator', 'author', 'created by', 'module author'],
        'module_created_on': ['module creation date', 'module created on', 'module build date', 
                              'when module was created'],
        'module_updated_on': ['module updated on', 'module last updated', 'module modified date'],
        'module_desc': ['module description', 'description', 'course description'],
        'module_imp_name': ['implementation name', 'deployment name'],
        'pre_req_module_name': ['prerequisite module', 'pre-req module', 'prereq', 'prerequisite'],
        'prod_master_name': ['master product', 'product family', 'product group'],
        'product_name': ['product', 'products', 'brand', 'brands', 'device', 'devices', 'line', 'product category'],
        'skill_name': ['skill', 'skills', 'competency', 'competencies', 'capability', 'capabilities', 'expertise'],
        'sub_skill_name': ['sub skill', 'sub-skill', 'specialization', 'subspecialty'],
        'tags': ['tag', 'tags', 'labels', 'keywords'],
        'trainer_email_addr': ['trainer email', 'instructor email', 'trainer', 'instructor'],
        'coach_email_addr': ['coach email', 'mentor email', 'coach', 'mentor'],
        'staff': ['staff', 'owner', 'module owner', 'content owner'],
        'published_date': ['published date', 'launch date', 'go-live date', 'release date', 
                          'published on', 'when published', 'publish date'],
        'equivalence_module_name': ['equivalent module', 'equivalence module', 'cross-credit'],
        'estd_time': ['estimated time', 'duration', 'time to complete', 'est time', 'eta'],
        
        # Assignment/completion terms
        'assigned_status': ['assignment status', 'assigned', 'not assigned', 'unassigned', 'allocation'],
        'completed_status': ['completed', 'finished', 'done', 'complete', 'completion', 'passed',
                            'not completed', 'incomplete', 'pending', 'in progress'],
        'completed_date': ['completion date', 'completed on', 'finished on', 'when completed', 
                          'date completed', 'finish date'],
        'complete_type': ['completion type', 'type of completion', 'mode', 'learning type'],
        'complete_version': ['completion version', 'version completed'],
        'comments': ['comments', 'feedback', 'notes', 'remarks'],
        'invited_date': ['invited date', 'invite date', 'invitation date', 'when invited'],
        'invited_time': ['invite time', 'invited time', 'invitation time'],
        
        # User terms
        'uid': ['user id', 'uid', 'employee id', 'learner id'],
        'email_addr': ['user', 'users', 'employee', 'employees', 'person', 'people', 'learner', 
                      'learners', 'trainee', 'trainees', 'participant', 'participants', 
                      'staff member', 'member', 'members', 'email'],
        'first_name': ['first name', 'firstname', 'name', 'given name'],
        'last_name': ['last name', 'lastname', 'surname', 'family name'],
        'mobile_number': ['mobile', 'mobile number', 'phone', 'phone number', 'contact'],
        'user_role': ['role', 'user role', 'profile', 'persona', 'access level'],
        'user_status': ['user status', 'status of user', 'active user', 'invited user', 'account status'],
        'is_admin': ['admin', 'administrator', 'is admin', 'admin access'],
        'designation': ['designation', 'role', 'position', 'title', 'job title', 'job'],
        'language_name': ['language', 'preferred language', 'locale'],
        'dob': ['date of birth', 'birth date', 'birthday'],
        'hired_on': ['hired on', 'hire date', 'join date', 'joined on', 'joining date', 'start date'],
        'user_created_on': ['user created on', 'account creation date', 'account created', 'signup date'],
        'manager_email_addr': ['manager email', 'reporting manager email', 'manager', 'supervisor', 'boss'],
        
        # Location terms
        'city': ['city', 'cities', 'location', 'locations', 'place', 'places', 'office', 
                'offices', 'branch', 'branches', 'site'],
        'state': ['state', 'province', 'region state'],
        'country': ['country', 'countries', 'nation', 'nations', 'region', 'regions'],
        
        # Meta/system fields
        'created_by': ['created by', 'record created by', 'creator'],
        'created_on': ['date', 'time', 'when', 'created', 'assigned', 'enrolled', 'started', 
                      'assignment date', 'enrollment date'],
        'updated_on': ['updated on', 'record updated on', 'last updated', 'modified on'],
        'version': ['version', 'record version'],
        'id': ['record id', 'document id'],
    }
    
    USER_TERM_HINTS = [
        'user', 'users', 'person', 'people', 'employee', 'employees', 'learner', 'learners',
        'trainee', 'trainees', 'participant', 'participants', 'staff', 'team member', 'member'
    ]
    MODULE_TERM_HINTS = [
        'module', 'modules', 'training', 'trainings', 'course', 'courses', 'program', 'programs', 'lesson', 'lessons'
    ]
    COURSE_TERM_HINTS = ['course', 'courses', 'curriculum', 'curriculums', 'program', 'programs']
    DIRECTORY_KEYWORDS = [
        'list ', 'list all', 'list the', 'list of', 'show me', 'show us', 'show all', 'show the',
        'give me', 'give us', 'display', 'display the', 'display all', 'names of', 'who are', 'who is',
        'who all', 'which all', 'which are the', 'directory', 'roster', 'lookup'
    ]
    EXPLICIT_UNIQUE_KEYWORDS = ['unique', 'distinct', 'deduped', 'de-duped', 'deduplicated', 'de-duplicated']
    ACTIVITY_KEYWORDS = [
        'assigned', 'assignment', 'not assigned', 'unassigned', 'allocated',
        'completed', 'completion', 'complete ', 'complete%', 'complete %', 'complete_percentage',
        'pending', 'in progress', 'still have', 'still has', 'per module', 'per user',
        'per trainee', 'per learner', 'per employee',
        'completed date', 'completion date', 'complete date',
        'invited date', 'invited time', 'invite date', 'invite time',
        'complete percentage', 'completion %', 'comments', 'feedback'
    ]
    RELATIONSHIP_TERMS = ['per module', 'per user', 'each module', 'each user', 'user-module', 'module per user']
    SPECIAL_ENTITY_KEYWORDS = {
        'trainer_email_addr': ['trainer', 'trainers', 'instructor', 'instructors'],
        'coach_email_addr': ['coach', 'coaches', 'mentor', 'mentors'],
        'manager_email_addr': ['manager', 'managers', 'supervisor', 'supervisors', 'boss', 'reporting manager']
    }
    
    # Date field mapping - usecase.md Section D
    # Maps event types to the correct date field
    DATE_FIELD_MAPPING = {
        # Completions → completed_date
        'completions': 'completed_date',
        'completed': 'completed_date',
        'finished': 'completed_date',
        'done': 'completed_date',
        'completion': 'completed_date',
        'passed': 'completed_date',
        
        # Module publishing → published_date
        'published': 'published_date',
        'launched': 'published_date',
        'go live': 'published_date',
        'go-live': 'published_date',
        'released': 'published_date',
        'live modules': 'published_date',
        
        # Module creation → module_created_on
        'module created': 'module_created_on',
        'modules created': 'module_created_on',
        'new module': 'module_created_on',
        'module built': 'module_created_on',
        'course created': 'module_created_on',
        
        # Module updates → module_updated_on
        'module updated': 'module_updated_on',
        'module modified': 'module_updated_on',
        
        # Invites/Sends → invited_date (Section C.5: "sent"/"delivered"/"pushed")
        'invited': 'invited_date',
        'sent': 'invited_date',
        'delivered': 'invited_date',
        'pushed': 'invited_date',
        'invitation': 'invited_date',
        
        # Record creation/assignment → created_on
        'assigned': 'created_on',
        'enrolled': 'created_on',
        'record created': 'created_on',
        'allocated': 'created_on',
        
        # User-related dates
        'hired': 'hired_on',
        'joined': 'hired_on',
        'user created': 'user_created_on',
        'account created': 'user_created_on',
    }
    
    # Ambiguous terms that may need clarification (Section I)
    AMBIGUOUS_TERMS = {
        'sent': ['invited_date', 'assigned_status'],
        'delivered': ['invited_date', 'assigned_status'],
        'pushed': ['invited_date', 'assigned_status'],
        'score': ['complete_percentage', 'ratings'],  # If no score field exists
    }
    
    # Known cities in the database (lowercase)
    KNOWN_CITIES = [
        'bengaluru', 'mumbai', 'chennai', 'delhi', 'new delhi', 'pune', 
        'hyderabad', 'kolkata', 'ahmedabad', 'chandigarh', 'jaipur',
        'ajman', 'al qusais', 'dubai', 'abu dhabi', 'sharjah',
        'tokyo', 'osaka', 'berlin', 'frankfurt', 'singapore'
    ]
    
    # Known products in the database
    KNOWN_PRODUCTS = [
        'ideapad', 'thinkpad', 'oneplus', 'acer', 'lifestyle', 'think smart'
    ]
    
    # Known skills in the database
    KNOWN_SKILLS = [
        'skill one', 'product skill', 'technology', 'programming', 'sales', 'communication'
    ]
    
    # Concept mappings - understands what user is asking about conceptually
    CONCEPT_MAPPINGS = {
        'who_completed': {
            'description': 'Users who completed training',
            'filter': {'term': {'completed_status': 1}},
            'group_by': 'email_addr',
            'related_fields': ['first_name', 'last_name', 'email_addr']
        },
        'completion_rate': {
            'description': 'Ratio of completed to total assignments',
            'needs_aggregation': True,
            'calculation': 'completed_count / total_count * 100'
        },
        'top_performers': {
            'description': 'Users with highest scores or most completions',
            'sort_by': 'score',
            'sort_order': 'desc'
        },
        'training_delivered': {
            'description': 'Unique modules assigned to users',
            'group_by': 'module_name',
            'aggregation': 'cardinality'
        },
        'training_completed': {
            'description': 'Unique modules where users finished',
            'filter': {'term': {'completed_status': 1}},
            'group_by': 'module_name',
            'aggregation': 'cardinality'
        }
    }
    
    # Value mappings - maps user values to actual data values
    # Aligned with usecase.md Section C.4 - Status code normalization
    VALUE_MAPPINGS = {
        'city': {
            'bangalore': 'bengaluru',
            'bombay': 'mumbai',
            'madras': 'chennai',
            'calcutta': 'kolkata',
        },
        'country': {
            'india': ['republic of india', 'india'],
            'uae': 'united arab emirates',
            'germany': 'federal republic of germany',
        },
        # Section C.4: completed/finished/done → completed_status = 1
        'completed_status': {
            'completed': 1,
            'finished': 1,
            'done': 1,
            'passed': 1,
            'successful': 1,
            'not completed': 0,
            'incomplete': 0,
            'pending': 0,
            'in progress': 0,
            'failed': 0,
        },
        # Section C.4: published → module_status = 0
        'module_status': {
            'published': 0,
            'live': 0,
            'active': 0,
            'deleted': 1,
            'removed': 1,
            'retired': 1,
            'draft': 2,
            'unpublished': 2
        },
        # Section C.4: assigned → assigned_status = 0
        'assigned_status': {
            'assigned': 0,
            'allocated': 0,
            'in queue': 0,
            'not assigned': 1,
            'unassigned': 1
        },
        # Section C.4: active → user_status = 5
        'user_status': {
            'active': 5,
            'enabled': 5,
            'deleted': 4,
            'inactive': 4,
            'removed': 4,
            'invited': 1,
            'pending invite': 1
        }
    }
    
    STATUS_KEYWORDS = {
        'user_status': {
            5: ['active user', 'active users', 'enabled user', 'enabled users'],
            4: ['deleted user', 'deleted users', 'inactive user', 'inactive users', 'removed user', 'removed users'],
            1: ['invited user', 'invited users', 'pending invite', 'pending invitation']
        },
        'module_status': {
            0: ['published module', 'published modules', 'live module', 'live modules', 'launched module', 'launched modules'],
            1: ['deleted module', 'deleted modules', 'retired module', 'retired modules'],
            2: ['draft module', 'draft modules', 'unpublished module', 'unpublished modules']
        },
        'assigned_status': {
            0: ['assigned user', 'assigned users', 'users assigned', 'already assigned'],
            1: ['not assigned', 'unassigned', 'awaiting assignment', 'pending assignment']
        }
    }
    
    @classmethod
    def resolve_field(cls, user_term: str) -> Optional[str]:
        """
        Resolve a user term to an actual field name.
        Returns None if no mapping found.
        """
        term_lower = user_term.lower().strip()
        
        for field_name, aliases in cls.FIELD_ALIASES.items():
            if term_lower in [a.lower() for a in aliases]:
                return field_name
            # Also check if user term contains any alias
            for alias in aliases:
                if alias.lower() in term_lower or term_lower in alias.lower():
                    return field_name
        
        return None
    
    @classmethod
    def resolve_value(cls, field_name: str, user_value: str) -> any:
        """
        Resolve a user value to actual database value.
        Handles city name variations, status mappings, etc.
        """
        if field_name not in cls.VALUE_MAPPINGS:
            return user_value.lower()  # Default to lowercase
        
        mappings = cls.VALUE_MAPPINGS[field_name]
        value_lower = user_value.lower().strip()
        
        if value_lower in mappings:
            return mappings[value_lower]
        
        return user_value.lower()
    
    @classmethod
    def detect_date_field(cls, message: str) -> Optional[str]:
        """
        Detect the appropriate date field based on the event type mentioned.
        Implements usecase.md Section D - Picking the Right Date Field.
        
        Args:
            message: User's query message
            
        Returns:
            The appropriate date field name, or None if not determinable
        """
        message_lower = message.lower()
        
        # Check each event keyword and return the corresponding date field
        for keyword, date_field in cls.DATE_FIELD_MAPPING.items():
            if keyword in message_lower:
                return date_field
        
        return None
    
    @classmethod
    def check_ambiguity(cls, message: str) -> List[Tuple[str, List[str]]]:
        """
        Check if the message contains ambiguous terms that need clarification.
        Returns list of (term, possible_interpretations) tuples.
        
        Implements usecase.md Section I - Fail-Safes.
        """
        message_lower = message.lower()
        ambiguities = []
        
        for term, interpretations in cls.AMBIGUOUS_TERMS.items():
            if term in message_lower:
                # Check if context makes it clear
                if term in ['sent', 'delivered', 'pushed']:
                    # If message mentions "invite" or "invitation", it's clear
                    if any(w in message_lower for w in ['invite', 'invitation']):
                        continue
                    # If message mentions "assign" or "assignment", it's clear
                    if any(w in message_lower for w in ['assign', 'assignment']):
                        continue
                    # Otherwise, it's ambiguous
                    ambiguities.append((term, interpretations))
        
        return ambiguities
    
    @classmethod
    def extract_entities(cls, message: str) -> Dict[str, Any]:
        """
        Extract entities from a user message.
        Returns dict of field_type -> extracted_values
        
        Enhanced extraction with better city detection, product/skill matching,
        and more robust entity recognition.
        """
        entities = {}
        message_lower = message.lower()
        
        # Skip words that are definitely not entities
        skip_words = {
            'the', 'last', 'this', 'next', 'past', 'all', 'any', 'some',
            'january', 'february', 'march', 'april', 'may', 'june',
            'july', 'august', 'september', 'october', 'november', 'december',
            'jan', 'feb', 'mar', 'apr', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec',
            '2020', '2021', '2022', '2023', '2024', '2025', '2026',
            'week', 'month', 'year', 'day', 'time', 'period',
            'total', 'count', 'number', 'many', 'much',
            'training', 'trainings', 'module', 'modules', 'user', 'users'
        }
        
        # Extract city mentions - check against known cities first
        cities = []
        for city in cls.KNOWN_CITIES:
            if city in message_lower:
                cities.append(city)
        
        # Also check for city aliases
        city_aliases = {
            'bangalore': 'bengaluru',
            'bombay': 'mumbai',
            'madras': 'chennai',
            'calcutta': 'kolkata',
        }
        for alias, actual in city_aliases.items():
            if alias in message_lower and actual not in cities:
                cities.append(actual)
        
        # Pattern-based city extraction for unknown cities
        if not cities:
            city_patterns = [
                r'\bin\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b',  # "in Mumbai"
                r'\bfrom\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b',  # "from Chennai"
                r'\bfor\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b',  # "for Bangalore"
            ]
            for pattern in city_patterns:
                matches = re.findall(pattern, message)  # Use original case
                for match in matches:
                    match_lower = match.lower()
                    if match_lower not in skip_words:
                        resolved = cls.resolve_value('city', match_lower)
                        if resolved not in cities:
                            cities.append(resolved)
        
        if cities:
            entities['cities'] = cities
        
        # Extract product mentions
        products = []
        for product in cls.KNOWN_PRODUCTS:
            if product in message_lower:
                products.append(product)
        if products:
            entities['products'] = products
        
        # Extract skill mentions
        skills = []
        for skill in cls.KNOWN_SKILLS:
            if skill in message_lower:
                skills.append(skill)
        if skills:
            entities['skills'] = skills
        
        # Extract person names (for queries like "how many modules has tanuj completed")
        # Look for patterns like "has X completed", "did X complete", "for X", "by X"
        person_patterns = [
            r'\bhas\s+([a-z]+)\s+completed\b',
            r'\bdid\s+([a-z]+)\s+complete\b',
            r'\bfor\s+user\s+([a-z]+)\b',
            r'\bfor\s+([a-z]+)\b(?!\s+(city|country|module|skill|product|the|a|all|have|has|completed))',
            r'\bby\s+user\s+([a-z]+)\b',
            r'\bby\s+([a-z]+)\b(?!\s+(city|country|module|skill|product|have|has|completed))',
            r'\buser\s+([a-z]+)\b',
            r'\b([a-z]+)\'s\s+(completion|module|training|progress|completions|modules|trainings)\b',
            r'\bmodules?\s+(?:has\s+)?([a-z]+)\s+(?:completed|done|finished)\b',
            r'\bcompleted\s+by\s+([a-z]+)\b',
            r'\bshow\s+([a-z]+)\s+completed\b',
            r'\bshow\s+([a-z]+)\'s\b',
            # Exclude common verbs from this pattern - only match if it's clearly a name
            r'\b([a-z]+)\s+completed\s+modules?\b(?!\s+(?:have|has|did|do|will|would))',
            r'\b([a-z]+)\s+has\s+completed\b',
        ]
        
        # Words that are NOT person names
        # CRITICAL: Include common filler words, verbs, and question words to prevent false person name detection
        not_person_names = {
            # Articles and determiners
            'the', 'a', 'an', 'all', 'any', 'some', 'many', 'few', 'this', 'that', 'these', 'those',
            # Common nouns (entities)
            'user', 'users', 'module', 'modules', 'city', 'country', 'skill', 'product',
            # Completion-related terms
            'completed', 'complete', 'completion', 'completions', 'done', 'finished', 'published', 'created',
            # Question words
            'how', 'what', 'which', 'who', 'where', 'when', 'why',
            # Common verbs (including "were", "is", "are", etc.)
            'have', 'has', 'had', 'did', 'do', 'does', 'will', 'would', 'should', 'could', 'can', 'may', 'might', 'must',
            'is', 'are', 'was', 'were', 'be', 'been', 'being',
            'get', 'got', 'gets', 'getting',
            'make', 'made', 'makes', 'making',
            'go', 'went', 'goes', 'going', 'gone',
            'come', 'came', 'comes', 'coming',
            'see', 'saw', 'sees', 'seeing', 'seen',
            'know', 'knew', 'knows', 'knowing', 'known',
            'take', 'took', 'takes', 'taking', 'taken',
            'give', 'gave', 'gives', 'giving', 'given',
            'find', 'found', 'finds', 'finding',
            'say', 'said', 'says', 'saying',
            'tell', 'told', 'tells', 'telling',
            # Prepositions (including "in", "on", "at", etc.)
            'in', 'on', 'at', 'by', 'for', 'with', 'from', 'to', 'of', 'about', 'into', 'onto', 'upon',
            # Months
            'january', 'february', 'march', 'april', 'may', 'june', 'july', 
            'august', 'september', 'october', 'november', 'december',
            'jan', 'feb', 'mar', 'apr', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec',
            # Cities
            'bengaluru', 'mumbai', 'chennai', 'delhi', 'pune', 'hyderabad', 'kolkata',
            # Countries
            'india', 'japan', 'uae', 'singapore', 'dubai',
            # Metric terms
            'rate', 'rates', 'percentage', 'percent', 'top', 'most', 'best', 'worst',
        }
        
        for pattern in person_patterns:
            matches = re.findall(pattern, message_lower)
            for match in matches:
                name = match if isinstance(match, str) else match[0]
                name = name.strip().lower()
                # Filter out common verbs and stop words
                if name and len(name) > 2 and name not in not_person_names:
                    # Additional check: if name is a common verb, skip it
                    common_verbs = {'have', 'has', 'had', 'did', 'do', 'does', 'will', 'would', 'should', 'could', 'can', 'may', 'might', 'must'}
                    if name in common_verbs:
                        continue
                    if 'person_name' not in entities:
                        entities['person_name'] = name
                    break
            if 'person_name' in entities:
                break
        
        # Extract country mentions
        country_patterns = [
            ('india', ['republic of india', 'india']),
            ('uae', 'united arab emirates'),
            ('emirates', 'united arab emirates'),
            ('japan', 'japan'),
            ('germany', 'federal republic of germany'),
            ('singapore', 'singapore'),
        ]
        countries = []
        for pattern, value in country_patterns:
            if pattern in message_lower:
                if isinstance(value, list):
                    countries.extend(value)
                else:
                    countries.append(value)
        if countries:
            entities['countries'] = list(set(countries))
        
        # Extract number limits (top N)
        top_n_match = re.search(r'\b(top|best|worst|bottom|first|last|highest|lowest)\s+(\d+)\b', message_lower)
        if top_n_match:
            entities['limit'] = int(top_n_match.group(2))
            entities['limit_type'] = top_n_match.group(1)
        
        # Detect ranking/superlative queries ("most", "least", "highest", "lowest")
        # These need a breakdown, not just a count
        ranking_patterns = [
            (r'\bwhich\s+(module|training|course|user|city|skill)\s+(has|have|had|with)\s+(most|least|highest|lowest|maximum|minimum)', 'ranking'),
            (r'\b(most|least|highest|lowest|maximum|minimum)\s+(completions?|users?|modules?|trainings?)', 'ranking'),
            (r'\bwhat\s+(module|training|course)\s+(has|have|had)\s+(most|highest)', 'ranking'),
        ]
        for pattern, query_type in ranking_patterns:
            if re.search(pattern, message_lower):
                entities['is_ranking_query'] = True
                entities['wants_list'] = False  # Not a list, but a ranked breakdown
                # Detect what to rank by
                if 'module' in message_lower or 'training' in message_lower or 'course' in message_lower:
                    entities['rank_by'] = 'module_name'
                elif 'user' in message_lower or 'learner' in message_lower:
                    entities['rank_by'] = 'email_addr'
                elif 'city' in message_lower:
                    entities['rank_by'] = 'city'
                elif 'skill' in message_lower:
                    entities['rank_by'] = 'skill_name'
                break
        
        # Detect "more than X" / "at least X" / "greater than X" patterns
        # e.g., "users who have finished more than 5 modules"
        threshold_patterns = [
            r'(?:more|greater)\s+than\s+(\d+)',
            r'at\s+least\s+(\d+)',
            r'(\d+)\s+or\s+more',
            r'over\s+(\d+)',
            r'above\s+(\d+)',
        ]
        for pattern in threshold_patterns:
            match = re.search(pattern, message_lower)
            if match:
                threshold = int(match.group(1))
                entities['count_threshold'] = threshold
                entities['threshold_operator'] = 'gt'  # greater than
                break
        
        # Detect "less than X" / "fewer than X" patterns
        less_than_patterns = [
            r'(?:less|fewer)\s+than\s+(\d+)',
            r'at\s+most\s+(\d+)',
            r'(\d+)\s+or\s+less',
            r'under\s+(\d+)',
            r'below\s+(\d+)',
        ]
        for pattern in less_than_patterns:
            match = re.search(pattern, message_lower)
            if match:
                threshold = int(match.group(1))
                entities['count_threshold'] = threshold
                entities['threshold_operator'] = 'lt'  # less than
                break
        
        # Extract completion status intent
        completed_indicators = ['completed', 'completions', 'finished', 'done', 'complete', 'passed', 'successful']
        incomplete_indicators = ['not completed', 'incomplete', 'pending', 'in progress', 'not done', 'not finished', 'failed']
        
        # Check for "haven't completed" / "haven't done" patterns
        # Special case: "haven't completed any [module] in [time range]" 
        # This means: users with 0 completions in that time range (use completed_date)
        # vs "haven't completed [modules published in time range]" (use published_date)
        havent_completed_in_time_patterns = [
            r'haven\'t\s+completed\s+(?:any|a\s+single)\s+(?:module|training|course)',
            r'havent\s+completed\s+(?:any|a\s+single)\s+(?:module|training|course)',
            r'hasn\'t\s+completed\s+(?:any|a\s+single)\s+(?:module|training|course)',
            r'hasnt\s+completed\s+(?:any|a\s+single)\s+(?:module|training|course)',
            r'who\s+(?:hasn\'t|hasnt|haven\'t|havent)\s+completed\s+(?:any|a\s+single)?\s*(?:module|training|course)',
            r'who\s+(?:hasn\'t|hasnt|haven\'t|havent)\s+completed\s+any\s+module',
            r'which\s+(?:users?|people)\s+(?:hasn\'t|hasnt|haven\'t|havent)\s+completed\s+(?:any|a\s+single)?\s*(?:module|training|course)',
            r'(?:who|which\s+users?)\s+.*(?:hasn\'t|hasnt|haven\'t|havent).*completed.*(?:any|a\s+single)?\s*(?:module|training|course)',
            r'not\s+completed\s+(?:any|a\s+single)\s+(?:module|training|course)',
            r'haven\'t\s+done\s+(?:any|a\s+single)\s+(?:module|training|course)',
            r'havent\s+done\s+(?:any|a\s+single)\s+(?:module|training|course)',
            r'not\s+done\s+(?:any|a\s+single)\s+(?:module|training|course)',
            r'no\s+completions?\s+(?:in|during|for)',
            r'zero\s+completions?\s+(?:in|during|for)',
        ]
        has_havent_completed_in_time = any(re.search(pattern, message_lower) for pattern in havent_completed_in_time_patterns)
        
        # General "haven't completed" / "hasn't completed" patterns (use published_date)
        havent_completed_patterns = [
            r'(?:haven\'t|hasn\'t|havent|hasnt)\s+completed',
            r'who\s+(?:hasn\'t|hasnt|haven\'t|havent)\s+completed',
            r'which\s+(?:users?|people)\s+(?:hasn\'t|hasnt|haven\'t|havent)\s+completed',
            r'haven\'t\s+done',
            r'havent\s+done',
            r'haven\'t\s+finished',
            r'havent\s+finished',
            r'not\s+completed\s+a\s+single',
            r'\bno\s+completions\b',  # More specific: "no completions" not "all completions"
            r'never\s+completed',
        ]
        has_havent_completed = any(re.search(pattern, message_lower) for pattern in havent_completed_patterns)
        
        # Check for "did [module]" pattern - this implies completion
        # e.g., "which users did the situational leadership module"
        did_module_patterns = [
            r'\bdid\s+(?:the\s+)?(?:module|training|course)',
            r'\bdid\s+[a-z\s]+(?:module|training|course)',
            r'\busers?\s+did\s+',
            r'\bwho\s+did\s+',
            r'\bwhich\s+(?:users?|people)\s+did\s+'
        ]
        has_did_module = any(re.search(pattern, message_lower) for pattern in did_module_patterns)
        
        # Detect "latest" / "most recent" queries
        # e.g., "latest module completed", "most recent completion", "what is the latest module X has completed"
        latest_patterns = [
            r'\blatest\s+(?:module|training|course|completion)',
            r'\bmost\s+recent\s+(?:module|training|course|completion)',
            r'\bwhat\s+is\s+the\s+latest\s+(?:module|training|course)',
            r'\bwhat\s+is\s+the\s+most\s+recent\s+(?:module|training|course)',
            r'\blast\s+(?:module|training|course|completion)\s+(?:completed|done|finished)',
        ]
        has_latest = any(re.search(pattern, message_lower) for pattern in latest_patterns)
        if has_latest:
            entities['latest_query'] = True  # Flag for special query logic
            # Latest queries should only show completed modules
            entities['completion_filter'] = 1
        
        # Check incomplete first (more specific)
        if has_havent_completed_in_time or has_havent_completed or any(phrase in message_lower for phrase in incomplete_indicators):
            entities['completion_filter'] = 0
            
            # Special case: "haven't completed any module in [time range]"
            # This means: users with 0 completions in that time range
            # Use completed_date to check for completions in that period
            if has_havent_completed_in_time:
                entities['time_field_override'] = 'completed_date'  # Check completions in time range
                entities['zero_completions_in_range'] = True  # Flag for special query logic
            elif has_havent_completed and 'time_field_override' not in entities:
                # General "haven't completed" queries use published_date (when modules were published)
                entities['time_field_override'] = 'published_date'
        elif has_did_module or any(word in message_lower for word in completed_indicators):
            entities['completion_filter'] = 1

        # Check if user is asking about USERS (takes priority over module intent)
        user_focus = cls._detect_user_focus(message_lower)
        if user_focus:
            entities['user_focus'] = True
        
        # Detect if query needs unique results (not just counts, but unique entities in a list)
        # e.g., "which users are developer" = unique users, not all records
        # BUT: "total completions" = count all records, NOT unique modules!
        # Exclude "total completions" / "how many completions" from uniqueness detection
        total_completions_patterns = [
            r'total\s+(?:module\s+)?completions?',
            r'how\s+many\s+(?:module\s+)?completions?',
            r'number\s+of\s+(?:module\s+)?completions?',
            r'count\s+of\s+(?:module\s+)?completions?',
        ]
        is_total_completions = any(re.search(pattern, message_lower) for pattern in total_completions_patterns)
        
        if not is_total_completions:
            needs_unique, unique_field = cls._detect_uniqueness_intent(message_lower, user_focus)
        else:
            needs_unique, unique_field = (False, None)
        
        if needs_unique:
            entities['needs_unique'] = True
            # Determine what field to use for uniqueness
            if unique_field:
                entities['unique_field'] = unique_field
            elif user_focus or 'user' in message_lower or 'people' in message_lower or 'learner' in message_lower:
                entities['unique_field'] = 'uid'
            else:
                entities['unique_field'] = 'mid'
        
        # Mark "total completions" queries explicitly - they should count records, not unique modules
        if is_total_completions:
            entities['count_total_records'] = True  # Flag to count all records, not unique entities
        
        # Module action intent (created/published vs consumed)
        # Only set module_intent if NOT asking about users
        module_intent = cls._detect_module_intent(message_lower)
        if module_intent and not user_focus:
            entities['module_intent'] = module_intent
            # Check if user explicitly wants a list or just a count
            entities['wants_list'] = cls.wants_list_not_count(message_lower)
            if module_intent == 'created':
                entities['time_field_override'] = 'module_created_on'
            elif module_intent == 'published':
                entities['time_field_override'] = 'published_date'
                entities['module_status_filter'] = 0
            elif module_intent == 'consumed':
                entities['time_field_override'] = 'created_on'
        
        # Detect date field from context (Section D)
        detected_date_field = cls.detect_date_field(message)
        if detected_date_field and 'time_field_override' not in entities:
            entities['time_field_override'] = detected_date_field
        
        # Check for ambiguous terms (Section I)
        ambiguities = cls.check_ambiguity(message)
        if ambiguities:
            entities['ambiguous_terms'] = [term for term, _ in ambiguities]
            entities['ambiguity_details'] = ambiguities
        
        # Extract grouping intent
        group_patterns = [
            (r'\bby\s+(city|cities)\b', 'city'),
            (r'\bby\s+(country|countries)\b', 'country'),
            (r'\bby\s+(module|modules|training|trainings)\b', 'module_name'),
            (r'\bby\s+(skill|skills)\b', 'skill_name'),
            (r'\bby\s+(product|products)\b', 'product_name'),
            (r'\bby\s+(user|users|person|people)\b', 'email_addr'),
            (r'\bby\s+(manager|managers)\b', 'manager_email_addr'),
            (r'\bby\s+(trainer|trainers)\b', 'trainer_email_addr'),
            (r'\bby\s+(coach|coaches)\b', 'coach_email_addr'),
            (r'\bper\s+(city|country|module|skill|product)\b', None),  # Will be resolved
        ]
        for pattern, field in group_patterns:
            if re.search(pattern, message_lower):
                if field:
                    entities['group_by'] = field
                break
        
        # Status-based filters (user/module/assignment)
        status_filters = [
            ('user_status', 'user_status_filter'),
            ('module_status', 'module_status_filter'),
            ('assigned_status', 'assigned_status_filter')
        ]
        for field_name, entity_key in status_filters:
            status_value = cls._detect_status(message_lower, field_name)
            if status_value is not None:
                entities[entity_key] = status_value
        
        # Extract comparison intent
        if any(word in message_lower for word in ['vs', 'versus', 'compared to', 'comparison', 'compare']):
            entities['is_comparison'] = True
            # Try to identify what's being compared
            if 'delivered' in message_lower and 'completed' in message_lower:
                entities['comparison_type'] = 'delivered_vs_completed'
            elif 'assigned' in message_lower and 'completed' in message_lower:
                entities['comparison_type'] = 'assigned_vs_completed'
        
        # Check for completion rate queries - flag for special query handling
        if 'completion rate' in message_lower or 'completion %' in message_lower:
            entities['completion_rate_query'] = True
        
        # Check for "what modules were completed" / "which modules were completed" queries
        # These should return UNIQUE modules, not completion rows
        modules_completed_patterns = [
            r'what\s+modules?\s+(?:were|was)\s+completed',
            r'which\s+modules?\s+(?:were|was)\s+completed',
            r'what\s+modules?\s+.*completed\s+in',
            r'which\s+modules?\s+.*completed\s+in',
            r'modules?\s+(?:were|was)\s+completed',
        ]
        if any(re.search(pattern, message_lower) for pattern in modules_completed_patterns):
            entities['unique_modules_query'] = True  # Flag to use aggregation on mid
            entities['needs_unique'] = True
            entities['unique_field'] = 'mid'
        
        return entities
    
    @classmethod
    def get_concept(cls, message: str) -> Optional[Dict]:
        """
        Identify the conceptual query type from message.
        """
        message_lower = message.lower()
        
        # Check for comparison queries
        if 'vs' in message_lower or 'versus' in message_lower or 'compared to' in message_lower:
            if 'delivered' in message_lower and 'completed' in message_lower:
                return {
                    'type': 'comparison',
                    'left': cls.CONCEPT_MAPPINGS['training_delivered'],
                    'right': cls.CONCEPT_MAPPINGS['training_completed'],
                    'label': 'Modules Delivered vs Completed'
                }
            if 'assigned' in message_lower and 'completed' in message_lower:
                return {
                    'type': 'comparison',
                    'left': cls.CONCEPT_MAPPINGS['training_delivered'],
                    'right': cls.CONCEPT_MAPPINGS['training_completed'],
                    'label': 'Modules Assigned vs Completed'
                }
        
        # Check for completion rate queries
        if 'completion rate' in message_lower or 'completion %' in message_lower:
            return {
                'type': 'calculated_metric',
                'concept': cls.CONCEPT_MAPPINGS['completion_rate']
            }
        
        # Check for who completed queries
        if any(phrase in message_lower for phrase in ['who completed', 'who finished', 'users completed', 'people completed']):
            return {
                'type': 'user_list',
                'concept': cls.CONCEPT_MAPPINGS['who_completed']
            }
        
        # Check for top performers
        if any(phrase in message_lower for phrase in ['top score', 'best score', 'highest score', 'top performer']):
            return {
                'type': 'ranked_list',
                'concept': cls.CONCEPT_MAPPINGS['top_performers']
            }
        
        return None
    
    @classmethod
    def wants_list_not_count(cls, message_lower: str) -> bool:
        """
        Detect if user explicitly wants a LIST/TABLE of items, not just a count.
        
        Returns True for: "list modules", "show me modules", "give me the modules",
        and plural "which users/modules/courses" style questions when not tied to activity.
        Returns False for: "how many modules" or other aggregate-only questions.
        """
        if cls._wants_directory_list(message_lower):
            return True
        
        # Explicit plural "which" questions imply lists unless paired with activity keywords
        if not cls._contains_activity_terms(message_lower):
            if re.search(r'\bwhich\s+(users|people|employees|learners|modules|courses|trainings|programs)\b', message_lower):
                return True
        
        return False
    
    @classmethod
    def _contains_any(cls, message_lower: str, keywords: List[str]) -> bool:
        return any(keyword in message_lower for keyword in keywords)
    
    @classmethod
    def _contains_user_terms(cls, message_lower: str) -> bool:
        return cls._contains_any(message_lower, cls.USER_TERM_HINTS)
    
    @classmethod
    def _contains_module_terms(cls, message_lower: str) -> bool:
        return cls._contains_any(message_lower, cls.MODULE_TERM_HINTS)
    
    @classmethod
    def _mentions_course_terms(cls, message_lower: str) -> bool:
        return cls._contains_any(message_lower, cls.COURSE_TERM_HINTS)
    
    @classmethod
    def _contains_activity_terms(cls, message_lower: str) -> bool:
        if cls._contains_any(message_lower, cls.ACTIVITY_KEYWORDS):
            return True
        if cls._contains_any(message_lower, cls.RELATIONSHIP_TERMS):
            return True
        return False
    
    @classmethod
    def _has_explicit_unique_flag(cls, message_lower: str) -> bool:
        return cls._contains_any(message_lower, cls.EXPLICIT_UNIQUE_KEYWORDS)
    
    @classmethod
    def _wants_directory_list(cls, message_lower: str) -> bool:
        if cls._contains_any(message_lower, cls.DIRECTORY_KEYWORDS):
            return True
        # Sentences starting with "who are" or "what are the" typically imply lists
        if message_lower.strip().startswith('who ') or message_lower.strip().startswith('who are '):
            return True
        if message_lower.strip().startswith('what are the '):
            return True
        if re.search(r'\bwhich\s+(users|people|employees|learners|modules|courses|trainings|programs|coaches|trainers|managers)\b', message_lower):
            return True
        return False
    
    @classmethod
    def _should_return_all_records(cls, message_lower: str, explicit_unique: bool) -> bool:
        has_user = cls._contains_user_terms(message_lower)
        has_module = cls._contains_module_terms(message_lower)
        if has_user and has_module and not explicit_unique:
            return True
        if cls._contains_activity_terms(message_lower) and not explicit_unique:
            return True
        return False
    
    @classmethod
    def _detect_unique_field(cls, message_lower: str, user_focus: bool) -> Optional[str]:
        for field_name, keywords in cls.SPECIAL_ENTITY_KEYWORDS.items():
            if cls._contains_any(message_lower, keywords):
                return field_name
        
        if cls._mentions_course_terms(message_lower):
            return 'cmid'
        
        if cls._contains_module_terms(message_lower):
            return 'mid'
        
        if user_focus or cls._contains_user_terms(message_lower):
            return 'uid'
        
        return None
    
    @classmethod
    def _detect_user_focus(cls, message_lower: str) -> bool:
        """
        Detect if the user is asking about USERS (not modules).
        Examples: "which users completed", "who all users", "how many users"
        """
        user_focus_patterns = [
            # "how many" patterns for users
            'how many users', 'how many unique users', 'how many distinct users',
            'how many people', 'how many unique people',
            'how many learners', 'how many unique learners',
            'how many trainees', 'how many unique trainees',
            'how many employees', 'how many unique employees',
            # "which/who" patterns
            'which users', 'which all users', 'which user',
            'who all users', 'who are the users', 'who completed',
            'who finished', 'who passed', 'who failed',
            'list users', 'list the users', 'show users', 'show me users',
            'give me users', 'display users',
            'which people', 'which all people', 'who are the people',
            'which learners', 'which all learners', 'who are the learners',
            'which trainees', 'which all trainees',
            'which employees', 'which all employees',
            'users who', 'people who', 'learners who', 'trainees who',
            # Count patterns
            'unique users', 'total users', 'count of users',
            'number of users', 'unique learners', 'unique people',
        ]
        return any(pattern in message_lower for pattern in user_focus_patterns)
    
    @classmethod
    def _detect_uniqueness_intent(cls, message_lower: str, user_focus: bool) -> Tuple[bool, Optional[str]]:
        """
        Determine whether the query expects UNIQUE entities or ALL records.
        Returns (needs_unique, unique_field)
        """
        explicit_unique = cls._has_explicit_unique_flag(message_lower)
        directory_intent = cls._wants_directory_list(message_lower) or explicit_unique
        
        if not directory_intent:
            return False, None
        
        if cls._should_return_all_records(message_lower, explicit_unique):
            return False, None
        
        unique_field = cls._detect_unique_field(message_lower, user_focus)
        if not unique_field:
            return False, None
        
        return True, unique_field
    
    @classmethod
    def _detect_module_intent(cls, message_lower: str) -> Optional[str]:
        """Detect whether the user is talking about modules being created/published or consumed."""
        created_keywords = [
            'module created', 'modules created', 'new module', 'new modules', 'created modules', 'created module',
            'module production', 'module creation'
        ]
        published_keywords = [
            'module publish', 'modules published', 'module released', 'modules released',
            'module launch', 'launched module', 'launched modules', 'published modules', 'published module',
            'modules were published', 'module was published', 'go live'
        ]
        consumed_keywords = [
            'module consumed', 'modules consumed', 'consumed', 'taken',
            'completed modules', 'modules completed', 'module usage',
            'module used', 'modules used', 'how many were consumed',
            'consumption', 'taken by users', 'finished modules'
        ]
        
        for phrase in created_keywords:
            if phrase in message_lower:
                return 'created'
        for phrase in published_keywords:
            if phrase in message_lower:
                return 'published'
        # Fallback: if "published"/"release"/"launch" occurs anywhere with "module"
        publish_terms = ['publish', 'published', 'release', 'released', 'launch', 'launched', 'go live']
        if 'module' in message_lower and any(term in message_lower for term in publish_terms):
            return 'published'
        for phrase in consumed_keywords:
            if phrase in message_lower:
                return 'consumed'
        return None

    @classmethod
    def _detect_status(cls, message_lower: str, field_name: str) -> Optional[int]:
        """Detect status values based on descriptive keywords."""
        field_keywords = cls.STATUS_KEYWORDS.get(field_name)
        if not field_keywords:
            return None
        
        for value, keywords in field_keywords.items():
            if any(keyword in message_lower for keyword in keywords):
                return value
        return None
    
    @classmethod
    def build_field_context(cls, schema_fields: List[Dict]) -> str:
        """
        Build a context string that maps user terms to actual fields.
        This helps the LLM understand field mappings.
        """
        context_parts = [
            "SEMANTIC FIELD MAPPINGS (use these to interpret user queries):",
            ""
        ]
        
        # Group fields by category
        categories = {
            'User Information': ['uid', 'email_addr', 'first_name', 'last_name', 'user_role', 'user_status'],
            'Training/Module': ['module_name', 'module_status', 'skill_name', 'product_name', 'trainer_email_addr', 'coach_email_addr'],
            'Scores & Progress': ['score', 'ratings', 'complete_percentage', 'completed_status', 'assigned_status', 'complete_type'],
            'Location': ['city', 'state', 'country'],
            'Dates': ['created_on', 'module_created_on', 'published_date', 'completed_date', 'invited_date'],
        }
        
        for category, fields in categories.items():
            context_parts.append(f"\n{category}:")
            for field in fields:
                if field in cls.FIELD_ALIASES:
                    aliases = cls.FIELD_ALIASES[field][:5]  # First 5 aliases
                    context_parts.append(f"  • {field} (user may say: {', '.join(aliases)})")
        
        # Add value mappings
        context_parts.append("\n\nVALUE MAPPINGS (convert user values to database values):")
        context_parts.append("  • City: 'Bangalore' → 'bengaluru', 'Bombay' → 'mumbai', 'Madras' → 'chennai'")
        context_parts.append("  • Country: 'India' → 'republic of india' OR 'india' (use both in should clause)")
        context_parts.append("  • Status: 'completed'/'done'/'finished' → completed_status: 1")
        
        return '\n'.join(context_parts)
