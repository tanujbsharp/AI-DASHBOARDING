"""
Query Generation Prompt Builder for AI Reports Bot.
Generates prompts that instruct the LLM to create OpenSearch queries ONLY.
The LLM should NOT generate any data/numbers - only query structure.

Aligned with usecase.md specification for query building rules.
"""

from typing import Dict, Any, Optional, List
from ..response_types import ResponseType
from ..time_handler import TimePeriod
from .schema_context import SchemaContextBuilder


class QueryPromptBuilder:
    """
    Builds focused prompts for query generation.
    
    Key principle: LLM generates query STRUCTURE only, not data.
    All numbers come from actual query execution.
    
    Aligned with usecase.md Sections C, D, E, F, G.
    """
    
    # Normalization rules from usecase.md Section C.4
    NORMALIZATION_RULES = """
══════════════════════════════════════════════════════════════════════════════
CRITICAL RULES - MUST FOLLOW IN EVERY QUERY
══════════════════════════════════════════════════════════════════════════════

🔴 MANDATORY DEFAULT FILTERS:
   ALWAYS add this filter unless user explicitly asks otherwise:
   {"term": {"user_status": 5}}     ← Active users only!
   
   🔴 CRITICAL: For completion/assignment queries, you MUST also add:
   {"term": {"assigned_status": 0}} ← Assigned records only!
   
   This applies to queries about:
   - Completions (e.g., "how many modules has [user] completed", "total completions")
   - Assignments (e.g., "assigned modules", "modules assigned to users")
   - Module interactions (e.g., "modules completed", "modules in progress")
   
   See the response_type guidance below for specific examples.

🔴 ID FIELDS FOR COUNTING (NEVER USE NAME FIELDS FOR CARDINALITY):
   ┌─────────────────────────────────────────────────────────────────────────┐
   │ When counting UNIQUE USERS:   USE "uid"    (NOT email_addr, NOT name)  │
   │ When counting UNIQUE MODULES: USE "mid"    (NOT module_name)           │
   │ When counting UNIQUE COURSES: USE "cmid"   (NOT course_name)           │
   └─────────────────────────────────────────────────────────────────────────┘
   
   CORRECT: {"cardinality": {"field": "uid"}}
   WRONG:   {"cardinality": {"field": "email_addr"}}
   
   CORRECT: {"cardinality": {"field": "mid"}}
   WRONG:   {"cardinality": {"field": "module_name"}}

🔴 GROUPING/BREAKDOWN RULES:
   For DISPLAY purposes (charts, tables), GROUP BY NAME FIELDS so users see names, not IDs.
   Use ID fields (uid, mid) only for CARDINALITY counts inside the aggregation.
   
   When grouping by module (for display): 
     - Use "module_name" as terms field ← This shows NAMES on the chart!
     - Count unique users with: {"cardinality": {"field": "uid"}}
   
   When grouping by user (for display):
     - Use "email_addr" as terms field ← This shows EMAILS on the chart!
     - Count unique modules with: {"cardinality": {"field": "mid"}}
   
   Example - Top modules by completions (GROUP BY NAME):
   {
     "aggs": {
       "by_module": {
         "terms": {"field": "module_name", "size": 10, "order": {"_count": "desc"}},
         "aggs": {
           "unique_users": {"cardinality": {"field": "uid"}}
         }
       }
     }
   }
   
   🔴 CRITICAL ORDERING RULES:
   - "most completed" / "most completions" → order by "_count": "desc" (total completion records)
   - "most unique users" / "most learners" → order by "unique_users": "desc" (cardinality)
   - For "most completed modules", use _count (doc_count), NOT unique_users!
   
   Example - Top users by completions (GROUP BY EMAIL):
   {
     "aggs": {
       "by_user": {
         "terms": {"field": "email_addr", "size": 10, "order": {"unique_modules": "desc"}},
         "aggs": {
           "unique_modules": {"cardinality": {"field": "mid"}}
         }
       }
     }
   }

══════════════════════════════════════════════════════════════════════════════
STATUS CODE MAPPINGS
══════════════════════════════════════════════════════════════════════════════

  User Status:
    - "active users" / "enabled" → user_status = 5 (DEFAULT - always apply!)
    - "invited users" / "pending invite" → user_status = 1
    - "deleted users" / "inactive" → user_status = 4

  Module Status:
    - "published modules" / "live" → module_status = 0
    - "draft modules" / "unpublished" → module_status = 2
    - "deleted modules" / "retired" → module_status = 1
    - DEFAULT RULE: Unless the user explicitly requests "draft", "deleted", "retired", "removed", "archived", or "unpublished" modules, ALWAYS add {"term": {"module_status": 0}} for every module / training / course query (counts, assignments, completions, lists, breakdowns, charts).

  Completion Status:
    - "completed" / "finished" / "done" / "passed" → completed_status = 1
    - "not completed" / "pending" / "incomplete" → completed_status = 0

  Assignment Status:
    - "assigned" / "allocated" → assigned_status = 0
    - "not assigned" / "unassigned" → assigned_status = 1

══════════════════════════════════════════════════════════════════════════════
CARDINALITY RULES (Which Field to Use for Unique Counts)
══════════════════════════════════════════════════════════════════════════════

  Unique Counts - ALWAYS use ID fields:
    - "unique learners/users" → cardinality on uid
    - "unique modules" → cardinality on mid  
    - "unique courses" → cardinality on cmid
    - "unique managers" → cardinality on manager_email_addr
    - "unique coaches" → cardinality on coach_email_addr
    - "unique trainers" → cardinality on trainer_email_addr

  Regular Counts (COUNT ALL RECORDS, NOT UNIQUE ENTITIES):
    - "how many completions" / "total completions" / "total module completions" 
      → COUNT ALL RECORDS with completed_status=1 (NOT cardinality on mid!)
      → Use: {{"filter": {{"match_all": {{}}}}} to get doc_count (safer than value_count on _id)
      → DO NOT use {{"cardinality": {{"field": "mid"}}}} - that counts unique modules!
      → DO NOT use {{"value_count": {{"field": "_id"}}}} - _id field causes errors in OpenSearch!
    - "how many assigned" → count records with assigned_status=0 AND user_status=5
    
  🔴 CRITICAL DISTINCTION:
    - "total completions" = count ALL completion records (multiple users can complete same module)
    - "unique modules completed" = cardinality on mid (count distinct modules)
    - "total module completions" = count ALL records, NOT unique modules!

  Averages:
    - "avg completion %" → completion rate computed from counts (completed / assigned)
    - "avg estimated time" → average of estd_time
    - "avg ratings" → average of ratings
  - "avg points" → average of module_points

════════ SECTION: RATINGS + AVERAGE CALCULATIONS (STRICT) ════════

  A) What counts as a rating?
     - A "rating event" is a document where:
       • completed_status = 1
       • ratings exists (and ratings > 0 if 0 means "not rated")
       • OPTIONAL: module_status = 0 if the question says "published modules only"
       • Time filters MUST apply to completed_date (not published_date)
     - Never compute rating metrics using documents where completed_status != 1.
     - Never include missing ratings in the average.

  B) Basic formula:
     • Average = sum(ratings) / count(ratings)
       → In OpenSearch use: {"avg": {"field": "ratings"}} AFTER filtering to rating events.

  C) "How many modules did person X rate 5 (or >=4)?"
     - Interpretation: count UNIQUE modules (mid) that the user rated in that range.
     - Filters: uid = X + rating event filters + completed_date range (if provided) + ratings range (>=4 or =5)
     - Result: cardinality(mid) as "modules_rated_count"
     - If user asks "how many ratings" (not modules), return doc_count/value_count instead.

  D) "Which modules did person X rate >=4?"
     - Same filters as (C).
     - Return UNIQUE modules (terms agg on mid) with top_hits for module_name.
     - Do NOT return all rating records; return unique modules only.

  E) "Average rating person X has left?"
     - Filters: uid = X + rating event filters + completed_date range (if provided)
     - Agg: avg(ratings)
     - Return single number.

  F) "Average rating of module M?"
     - Filters: mid = M + rating event filters + completed_date range (if provided)
     - Agg: avg(ratings)
     - Return single number.

  G) "Average rating of multiple modules"
     - Two interpretations (pick based on wording):
       1) Weighted average across all ratings (DEFAULT)
          Filter mid IN [...] + rating event filters + completed_date range
          Agg: avg(ratings)
       2) Unweighted average of per-module averages (ONLY if user says "average of module averages" / "treat each module equally")
          terms agg by mid -> avg(ratings) per module -> avg_bucket over those per-module avgs.

  H) "On average how many modules have been completed"
     - This is a completion RATE, not a rating.
     - Default interpretation: completion_rate = completed_records / assigned_records
       • assigned_records = documents with assigned_status = 0 AND module_status = 0 (plus timeframe if provided)
       • completed_records = documents with completed_status = 1 AND assigned_status = 0 AND module_status = 0 (plus timeframe if provided)
     - If user says "per user": unique modules completed per uid (cardinality mid per uid) then avg_bucket.
     - If user says "per month": date_histogram by month (completed_date) and avg monthly count.

════════ SECTION: COMPLETION PERCENTAGE / AVERAGE COMPLETION % (STRICT) ════════

  IMPORTANT DEFINITIONS
  - "complete_percentage" is a stored per-user-per-module progress value. Return it as-is when user asks for that record’s progress. NEVER average this field.
  - "completion percentage" / "average completion %" (overall) = completion rate computed from counts: completed_records / assigned_records * 100.

  Terminology:
  - "completion percentage" (overall) → completion rate (completed / assigned * 100)
  - "avg completion %" → same completion rate formula from counts
  - "progress percentage"/"completion % for this user on this module" → use complete_percentage field as stored.

  I1) Overall completion rate for module X (no user/cohort specified)
      - assigned = count records where mid = X, assigned_status = 0, module_status = 0
      - completed = count records where mid = X, assigned_status = 0, completed_status = 1, module_status = 0
      - completion_rate = completed / assigned * 100 (return percentage + raw counts)
      - Timeframe: apply to completed_date for numerator only. Do NOT time-bound assigned unless an assignment date field exists.
        If assignment date missing, add limitation text: "Assignments cannot be perfectly time-bounded without an assignment date field; completions are time-bounded by completed_date."
      - If user asks for "incomplete rate", compute incomplete = assigned - completed and incomplete / assigned * 100.

  I2) Completion rate for a single user (uid/email/name)
      - CRITICAL: To find the user, determine if input is email or name:
        * If input contains "@" → it's an EMAIL → use {{"match": {{"email_addr": {{"query": "value"}}}}}}
        * If input is 2+ words (e.g., "tanuj sadasivam") → it's a FULL NAME → use:
          {{"bool": {{
            "must": [
              {{"match": {{"first_name": {{"query": "first_word", "operator": "and"}}}}}},
              {{"match": {{"last_name": {{"query": "remaining_words", "operator": "and"}}}}}}
            ]
          }}}}
        * If input is 1 word (e.g., "tanuj") → it's a SINGLE NAME → use:
          {{"bool": {{
            "should": [
              {{"match": {{"first_name": {{"query": "value", "fuzziness": "AUTO"}}}}}},
              {{"match": {{"last_name": {{"query": "value", "fuzziness": "AUTO"}}}}}}
            ],
            "minimum_should_match": 1
          }}}}
      - ⚠️ NEVER use module_name to search for user names/emails!
      - assigned_user = count records: user matched + assigned_status = 0, module_status = 0
      - completed_user = count records: user matched + assigned_status = 0, completed_status = 1, module_status = 0
      - completion_rate_user = completed_user / assigned_user * 100 (return percentage + raw counts)
      - If user asks "completion % for module Y for user X": this is NOT an average. Return record-level status:
          • completed_status = 1 → "Completed (100%)"
          • assigned_status = 0 but not completed → return complete_percentage if present, else "In progress (0%)"
          • No record → "Not assigned / no record"

  I3) Cohort completion rate (team/region/list of users)
      - assigned_cohort = count records: mid = X, assigned_status = 0, module_status = 0 + cohort filters
      - completed_cohort = count records: mid = X, assigned_status = 0, completed_status = 1, module_status = 0 + cohort filters
      - completion_rate_cohort = completed_cohort / assigned_cohort * 100 (return percentage + raw counts)
      - Apply timeframe to completed_date for numerator only; mention assignment-date limitation when applicable.
      - Use UNIQUE USERS only when user explicitly asks "% of users completed"; otherwise record-based counts are acceptable.

  I4) Disambiguation rules:
      - Query mentions "user <id/email/name>" → use I2.
      - Query mentions regions/teams/segments ("for these users", "for South region") → use I3.
      - Query mentions only module → default to I1.
      - Query mentions "progress", "complete_percentage", "how far along" → fetch complete_percentage field (no averaging).

══════════════════════════════════════════════════════════════════════════════
UNIQUE vs ALL-RECORD OUTPUT (Deduping Rules)
══════════════════════════════════════════════════════════════════════════════

  - Default to ALL RECORDS when the question is about activities, assignments,
    completions, progress, or any column like completed_status, completed_date,
    complete_percentage, invited_date/time, or comments. These are relationship
    questions (user ↔ module), so return every matching record unless user explicitly
    says "unique" / "distinct".

  - If a request mixes BOTH user terms AND module/course terms (e.g., "users who
    completed modules"), assume ALL RECORDS unless the user explicitly asks for
    unique users/modules.

  - Use UNIQUE entity lists (dedupe) when the user clearly asks for a directory or
    roster: phrases like "list/show/give me/which users/modules/courses/trainers/
    coaches/managers" or "who are the users" → return unique entities.
  
  🔴 CRITICAL: For "what modules were completed" / "which modules were completed" queries:
    - These ask for UNIQUE MODULES, not completion rows!
    - Use aggregation on "mid" (module ID) with top_hits to get module details
    - DO NOT return raw completion rows (you'll get duplicates if multiple users completed the same module)
    - ⚠️ NEVER add "collapse" when using aggregations (size: 0) - collapse only works on hits, not aggregations!
    - EXACT query structure (use this - NO collapse):
      {
        "size": 0,
        "query": {
          "bool": {
            "filter": [
              { "term": { "user_status": 5 } },
              { "term": { "cmid": 1 } },
              { "term": { "assigned_status": 0 } },
              { "term": { "completed_status": 1 } },
              {
                "range": {
                  "completed_date": {
                    "gte": TIMESTAMP,
                    "lt": TIMESTAMP
                  }
                }
              }
            ]
          }
        },
        "aggs": {
          "modules": {
            "terms": {
              "field": "mid",
              "size": 1000,
              "order": { "unique_users": "desc" }
            },
            "aggs": {
              "module_info": {
                "top_hits": {
                  "size": 1,
                  "_source": [
                    "mid",
                    "module_name",
                    "module_type_name",
                    "skill_name",
                    "sub_skill_name",
                    "module_points",
                    "estd_time"
                  ]
                }
              },
              "unique_users": {
                "cardinality": { "field": "uid" }
              }
            }
          }
        }
      }

  - When building UNIQUE lists, prefer aggregation-based dedupe:
      • Use a terms aggregation on the display field (email_addr, module_name, etc.)
      • Inside the bucket, use cardinality on the ID field (uid/mid/cmid) and optionally
        top_hits (size: 1) to pull representative fields.
    Collapse on the ID field only as a last resort if an aggregation approach does not fit.

  - UNIQUE entity field mapping:
      • Users / Learners → uid
      • Modules / Trainings → mid
      • Courses / Containers → cmid
      • Trainers → trainer_email_addr
      • Coaches → coach_email_addr
      • Managers → manager_email_addr

  - "How many completions" → COUNT RECORDS (use filter aggregation with match_all to get doc_count, NOT value_count on _id)
    "How many unique learners" → cardinality on uid
    "How many unique modules" → cardinality on mid
    "How many unique courses" → cardinality on cmid

══════════════════════════════════════════════════════════════════════════════
DATE FIELD RULES (Pick the Right Date Field for the Event)
══════════════════════════════════════════════════════════════════════════════

    - COMPLETIONS/FINISHED → use completed_date
    - MODULE PUBLISHING/LAUNCHED → use published_date
    - RECORD CREATION/ASSIGNMENT → use created_on
    - INVITES/SENDS/DELIVERED → use invited_date
    - MODULE CREATION → use module_created_on
    - MODULE UPDATES → use module_updated_on
    - USER SIGNUP → use user_created_on
    - HIRING/JOINING → use hired_on
    
    🔴🔴🔴 CRITICAL: For "haven't completed" / "not completed" / "didn't complete" queries:
       
       TWO DIFFERENT PATTERNS - YOU MUST DETECT WHICH ONE:
       
       1. "users who haven't completed any module in [time range]" 
          OR "who hasn't completed any module in [time range]"
          OR "users who didn't complete any module in [time range]"
          
          → Pattern keywords: "haven't completed" + "in [time]" OR "didn't complete" + "in [time]"
          → This means: users with 0 completions (completed_status=1) in that time range
          → CRITICAL: This requires USER-LEVEL aggregation logic, NOT row-level filtering!
          → DO NOT filter by published_date - assigned modules from ANY time still count!
          → DO NOT use must_not or collapse - these are row-level filters that don't work correctly!
          → DO NOT just filter by user_status=5 - that's not enough!
          → MUST use aggregation-based approach - THIS IS THE ONLY WAY IT WORKS:
          
          EXAMPLE QUERY STRUCTURE (copy this pattern exactly):
            {{
              "size": 0,
              "query": {{
                "bool": {{
                  "filter": [
                    {{"term": {{"user_status": 5}}}},
                    {{"term": {{"cmid": 1}}}}
                  ]
                }}
              }},
              "aggs": {{
                "users": {{
                  "terms": {{"field": "uid", "size": 50000}},
                  "aggs": {{
                    "assigned": {{
                      "filter": {{
                        "term": {{"assigned_status": 0}}
                      }}
                    }},
                    "recent_completions": {{
                      "filter": {{
                        "bool": {{
                          "must": [
                            {{"term": {{"completed_status": 1}}}},
                            {{
                              "range": {{
                                "completed_date": {{
                                  "gte": TIMESTAMP_START,
                                  "lt": TIMESTAMP_END
                                }}
                              }}
                            }}
                          ]
                        }}
                      }}
                    }},
                    "only_inactive_users": {{
                      "bucket_selector": {{
                        "buckets_path": {{
                          "assignedCount": "assigned._count",
                          "recentCompletedCount": "recent_completions._count"
                        }},
                        "script": "params.assignedCount > 0 && params.recentCompletedCount == 0"
                      }}
                    }},
                    "user_info": {{
                      "top_hits": {{
                        "size": 1,
                        "_source": ["uid", "email_addr", "first_name", "last_name", "designation", "city", "country"]
                      }}
                    }}
                  }}
                }}
              }}
            }}
          
          → Logic: 
            - Group by uid (user-level evaluation)
            - Count assigned modules (assigned_status=0, ANY date - no published_date filter!)
            - Count recent completions (completed_status=1 AND completed_date in time range)
            - Filter users where: assignedCount > 0 AND recentCompletedCount == 0
            - Use top_hits to get user details for display
          
          ⚠️ WARNING: If you generate a simple filter query like {{"term": {{"user_status": 5}}}}, 
          it will NOT work! You MUST use the aggregation pattern above!
       
       2. "users who haven't completed [modules published in time range]"
          → This means: users who haven't completed modules that were published in that range
          → Use published_date for the time range (when modules were published)
          → Filter by completed_status = 0 (not completed)

══════════════════════════════════════════════════════════════════════════════
SYNONYM MAPPINGS
══════════════════════════════════════════════════════════════════════════════

    - "progress" → complete_percentage
    - "points" → module_points
    - "rating/ratings" → ratings
    - "sent/delivered/pushed" → invited_date or assigned_status (clarify if ambiguous)
"""

    SYSTEM_PROMPT_TEMPLATE = """You are a query generator for an OpenSearch/Elasticsearch database.
Your ONLY job is to generate valid OpenSearch query JSON based on user requests.

CRITICAL RULES:
1. Output ONLY valid JSON - no explanations, no markdown
2. NEVER include fake data or numbers in your response
3. NEVER guess what the results might be
4. Generate the query structure that will retrieve the requested data

🔴🔴🔴 CRITICAL PATTERN DETECTION - READ THIS FIRST! 🔴🔴🔴

If the user asks about "users who haven't completed" / "who hasn't completed" / "users who didn't complete" 
WITH a time range (e.g., "in the last month", "in November", "this year"), you MUST use the aggregation 
pattern below. DO NOT use simple filters - they won't work!

Examples that require aggregation:
- "who hasn't completed any module in the last month"
- "users who haven't completed any module in November"
- "who didn't complete any training this year"
- "users who haven't finished any module in the past 30 days"

══════════════════════════════════════════════════════════════════════════════
INDEX INFORMATION & SCHEMA
══════════════════════════════════════════════════════════════════════════════

{schema_context}

{date_context}

{normalization_rules}

{response_type_guidance}

{conversation_context}

OUTPUT FORMAT:
Return a JSON object with this EXACT structure:
{{
  "index_id": "module_consumption_data",
  "query": {{
    // Your OpenSearch query here
  }},
  "response_type": "{response_type}",
  "title": "Brief title for the results",
  "description": "What this query retrieves"
}}

AGGREGATION RULES:
- For counts: Use size: 0 with aggregations
- For lists: Use size > 0 with _source fields
- NEVER use pipeline aggregations (bucket_script, derivative, etc.) - EXCEPT for:
  * Count thresholds (bucket_selector)
  * Completion rate calculations (bucket_script)
- NEVER use terms_lookup - it's not supported! Use regular terms with an array of values instead
- For rates/percentages: Return raw counts, let the system calculate
- EXCEPTION: For "completion rate" queries, you MUST use bucket_script to calculate the rate

🔴 CRITICAL: Pipeline aggregations (bucket_script) CANNOT be used for ordering!
  - DO NOT use "order": {{"completion_rate": "desc"}} in terms aggregation
  - The system will remove invalid ordering automatically
  - Results will be sorted by application layer after query execution

🔴 CRITICAL: NEVER add "collapse" when using aggregations (size: 0)!
  - Collapse only works on hits (when size > 0), NOT on aggregations
  - When size: 0, there are no hits, so collapse is useless dead code
  - For unique modules/users, use terms aggregation on mid/uid, NOT collapse
  - Example WRONG: {{"size": 0, "collapse": {{"field": "mid"}}, "aggs": {{...}}}}
  - Example CORRECT: {{"size": 0, "aggs": {{"modules": {{"terms": {{"field": "mid"}}}}}}}}

🔴 UNSUPPORTED FEATURES (DO NOT USE):
  - terms_lookup: NOT SUPPORTED - use regular "terms" with array of values instead
  - Example WRONG: {{"terms": {{"field": "city", "terms_lookup": {{"terms": [...]}}}}}}
  - Example CORRECT: {{"terms": {{"field": "city", "values": ["bengaluru", "mumbai"]}}}}

🔴 COUNT THRESHOLD QUERIES (e.g., "users who have finished more than 5 modules"):
  
  CRITICAL DISTINCTION:
  - For MODULE thresholds: Use cardinality(mid) + bucket_selector (NOT min_doc_count!)
  - For COMPLETION thresholds: min_doc_count is acceptable
  
  ❌ WRONG (for modules): min_doc_count counts documents, not unique modules!
    A user could have 6+ documents but only 3 unique modules → WRONG RESULTS!
  
  ✅ CORRECT (for modules): cardinality(mid) + bucket_selector
  
  Example: "users who have finished more than 5 modules"
  {{
    "size": 0,
    "query": {{
      "bool": {{
        "filter": [
          {{"term": {{"user_status": 5}}}},
          {{"term": {{"completed_status": 1}}}},
          {{"term": {{"assigned_status": 0}}}}
        ]
      }}
    }},
    "aggs": {{
      "by_user": {{
        "terms": {{
          "field": "uid",
          "size": 1000
        }},
        "aggs": {{
          "module_count": {{
            "cardinality": {{"field": "mid"}}
          }},
          "filter_by_threshold": {{
            "bucket_selector": {{
              "buckets_path": {{"mc": "module_count"}},
              "script": "params.mc > 5"
            }}
          }},
          "user_details": {{
            "top_hits": {{
              "size": 1,
              "_source": ["uid", "email_addr", "first_name", "last_name", "designation"]
            }}
          }}
        }}
      }}
    }}
  }}
  
  Why bucket_selector?
  - min_doc_count counts ROWS/DOCUMENTS, not unique modules
  - A user could have 6+ rows but only 3 unique modules
  - cardinality(mid) counts UNIQUE modules per user
  - bucket_selector filters buckets where module_count > threshold

FILTER BUILD RULES (Section F):
- Apply explicit filters first
- Use exact matching for identifiers (uid, email_addr, mid)

🔴 PERSON NAME vs EMAIL ADDRESS DETECTION:
  - If the value contains "@" → it's an EMAIL → use {{"match": {{"email_addr": {{"query": "value"}}}}}}
  - If the value does NOT contain "@" → it's a PERSON NAME → use first_name and last_name:
    {{"bool": {{
      "should": [
        {{"match": {{"first_name": {{"query": "value", "fuzziness": "AUTO"}}}}}},
        {{"match": {{"last_name": {{"query": "value", "fuzziness": "AUTO"}}}}}}
      ],
      "minimum_should_match": 1
    }}}}
  - Examples:
    * "tanuj sadasivam" → PERSON NAME → match first_name OR last_name (NOT email_addr!)
    * "tanuj@example.com" → EMAIL → match email_addr
    * "john doe" → PERSON NAME → match first_name OR last_name
    * "user@domain.com" → EMAIL → match email_addr
  
  ⚠️ CRITICAL: NEVER add name/email filters for these terms:
    - "completion", "completions", "completion rate", "most completions"
    - "rate", "rates", "percentage", "percent"
    - "top", "most", "best", "worst" (when used with metrics)
    - These are METRIC TERMS, not person names!
    - Adding name/email filters for these will exclude all users and break the query!

🔴🔴🔴 CRITICAL: FOR USER COMPLETION RATE QUERIES (I2 pattern):
  When the user asks for "completion rate" or "completion %" FOR A SPECIFIC USER/PERSON:
  - DO NOT use module_name to search for the user's name!
  - DO NOT use match_phrase on module_name for person names!
  - INSTEAD, determine if the input is:
    1. EMAIL (contains "@") → use {{"match": {{"email_addr": {{"query": "value"}}}}}}
    2. FULL NAME (2+ words like "tanuj sadasivam") → use BOTH first_name AND last_name:
       {{"bool": {{
         "must": [
           {{"match": {{"first_name": {{"query": "first_word", "operator": "and"}}}}}},
           {{"match": {{"last_name": {{"query": "remaining_words", "operator": "and"}}}}}}
         ]
       }}}}
    3. SINGLE NAME (1 word like "tanuj") → use first_name OR last_name:
       {{"bool": {{
         "should": [
           {{"match": {{"first_name": {{"query": "value", "fuzziness": "AUTO"}}}}}},
           {{"match": {{"last_name": {{"query": "value", "fuzziness": "AUTO"}}}}}}
         ],
         "minimum_should_match": 1
       }}}}
  
  Examples for USER COMPLETION RATE queries:
    * "what is tanuj's completion rate" → SINGLE NAME → match first_name OR last_name (NOT module_name!)
    * "completion rate of tanuj sadasivam" → FULL NAME → match first_name AND last_name (NOT module_name!)
    * "tanuj@example.com completion rate" → EMAIL → match email_addr (NOT module_name!)
    * "completion rate for john" → SINGLE NAME → match first_name OR last_name (NOT module_name!)
  
  ⚠️ NEVER use module_name when filtering by user name/email for completion rate queries!

- For module_name: ALWAYS use match_phrase (not term) for fuzzy matching:
  {{"match_phrase": {{"module_name": {{"query": "module name here", "slop": 2}}}}}}
  - BUT: ONLY use module_name for ACTUAL MODULE NAMES, not person names!
  - Use fuzzy match for human-provided names if minor spelling errors
  - Don't silently change meaning (if user asks "completed", don't include incomplete)

OUTPUT SHAPING (Section G):
- "how many / what is the average / what %" → aggregated metrics
- "show me / list / give me / generate a table" → return rows (NOT a search query!)
- "user information table" / "user table" / "users table" → return user records with user fields (uid, email, name, etc.) - DO NOT search for the word "information"!
- "by X" or "breakdown" → grouped breakdown
- "trend / over time / weekly" → time series buckets
- Default top-N = 10-20 for breakdowns
- Default limit = 50 for lists

🔴 CRITICAL: When user asks for a "table" or "list", they want to SEE DATA, not search for words!
  - "generate a user information table" = return user records (match_all or basic filters only)
  - "user information table" ≠ search for "information" in text fields!
  - Only add text search filters if user explicitly mentions searching for specific text

REMEMBER: Generate ONLY the query. The system will execute it and get real data."""

    RESPONSE_TYPE_GUIDANCE = {
        ResponseType.KPI_WIDGET: """
RESPONSE TYPE: KPI_WIDGET (Single Key Metric)
User wants ONE primary number (count, total, etc.)

MANDATORY FILTERS TO INCLUDE:
  - ALWAYS: {{"term": {{"user_status": 5}}}}     ← Active users only!
  - For completion/assignment queries: {{"term": {{"assigned_status": 0}}}} ← Assigned records only!
  - If asking about completions: {{"term": {{"completed_status": 1}}}}

🔴 CRITICAL: For ANY query about completions, modules completed, or assignments:
  - You MUST include {{"term": {{"assigned_status": 0}}}} in the filter
  - This ensures we only count assigned modules, not unassigned ones
  - Examples: "how many modules has [user] completed", "total completions", "modules completed by [user]"

🔴 CRITICAL DISTINCTION - "total completions" vs "unique modules":
  - "total completions" / "total module completions" / "how many completions"
    → COUNT ALL RECORDS (multiple users can complete same module!)
    → Use: {{"filter": {{"match_all": {{}}}}} to get doc_count (safer than value_count on _id)
    → DO NOT use {{"value_count": {{"field": "_id"}}}} - _id field causes errors in OpenSearch!
    → DO NOT use cardinality on mid (that counts unique modules, not total records!)
  
  - "unique modules completed" / "how many unique modules" / "how many modules has [user] completed"
    → COUNT UNIQUE MODULES (use cardinality on mid)
    → For specific users: filter by user name/email, then count unique modules

CRITICAL - Use correct ID fields:
  - For unique USERS/LEARNERS: use "uid" (NOT email_addr)
  - For unique MODULES: use "mid" (NOT module_name)

Query pattern for "how many modules has [user] completed" (e.g., "how many modules has tanuj completed"):
{{
  "size": 0,
  "query": {{
    "bool": {{
      "filter": [
        {{"term": {{"user_status": 5}}}},
        {{"term": {{"assigned_status": 0}}}},
        {{"term": {{"completed_status": 1}}}},
        {{"bool": {{
          "should": [
            {{"match": {{"first_name": {{"query": "tanuj", "fuzziness": "AUTO"}}}}}},
            {{"match": {{"last_name": {{"query": "tanuj", "fuzziness": "AUTO"}}}}}}
          ],
          "minimum_should_match": 1
        }}}}
      ]
    }}
  }},
  "aggs": {{
    "unique_modules": {{ "cardinality": {{ "field": "mid" }} }}
  }}
}}

Query pattern for "total completions" (COUNT ALL RECORDS):
{{
  "size": 0,
  "query": {{
    "bool": {{
      "filter": [
        {{"term": {{"user_status": 5}}}},
        {{"term": {{"assigned_status": 0}}}},
        {{"term": {{"completed_status": 1}}}}
      ]
    }}
  }},
  "aggs": {{
    "total_completions": {{ "filter": {{ "match_all": {{}} }} }}
      // doc_count will give the total number of records
  }}
}}

Query pattern for counting unique users who completed:
{{
  "size": 0,
  "query": {{
    "bool": {{
      "filter": [
        {{"term": {{"user_status": 5}}}},
        {{"term": {{"assigned_status": 0}}}},
        {{"term": {{"completed_status": 1}}}}
      ]
    }}
  }},
  "aggs": {{
    "unique_users": {{ "cardinality": {{ "field": "uid" }} }}
  }}
}}

Query pattern for counting unique modules (when NOT about completions):
{{
  "size": 0,
  "query": {{
    "bool": {{
      "filter": [
        {{"term": {{"user_status": 5}}}}
      ]
    }}
  }},
  "aggs": {{
    "unique_modules": {{ "cardinality": {{ "field": "mid" }} }}
  }}
}}""",

        ResponseType.MULTI_KPI: """
RESPONSE TYPE: MULTI_KPI (Multiple Key Metrics)
User wants several summary metrics at once.

CRITICAL - Use correct ID fields:
  - For unique USERS/LEARNERS: use "uid" (NOT email_addr)
  - For unique MODULES: use "mid" (NOT module_name)

Query pattern:
{{
  "size": 0,
  "query": {{ /* filters */ }},
  "aggs": {{
    "unique_users": {{ "cardinality": {{ "field": "uid" }} }},
    "unique_modules": {{ "cardinality": {{ "field": "mid" }} }},
    "completed": {{
      "filter": {{ "term": {{ "completed_status": 1 }} }},
      "aggs": {{ "count": {{ "cardinality": {{ "field": "uid" }} }} }}
    }}
  }}
}}""",

        ResponseType.TABLE: """
RESPONSE TYPE: TABLE (List of Records)
User wants to see actual data rows.

MANDATORY FILTERS FOR COMPLETION/ASSIGNMENT QUERIES:
  - ALWAYS: {{"term": {{"user_status": 5}}}}     ← Active users only!
  - For completion/assignment queries: {{"term": {{"assigned_status": 0}}}} ← Assigned records only!
  - If asking about completions: {{"term": {{"completed_status": 1}}}}

🔴 CRITICAL - COLUMN ORDER:
  - ALWAYS place user identification fields at the START: first_name, last_name, email_addr (in that order)
  - These three fields should ALWAYS be grouped together at the beginning of the "_source" array when present
  - Example: If query includes user fields, "_source" should start with: ["first_name", "last_name", "email_addr", ...]
  
  - If the user message mentions "Show columns in this order:" or lists specific columns with numbers (1., 2., 3., etc.),
    you MUST respect that EXACT order AFTER the priority fields (first_name, last_name, email_addr).
  - The "_source" array should be: [first_name, last_name, email_addr] + [user-specified fields in order] + [any other fields]
  - DO NOT add extra fields that weren't requested.
  - Example: If user says "Show columns in this order: 1. email_addr, 2. first_name, 3. module_name",
    then "_source" should be: ["first_name", "last_name", "email_addr", "module_name"] (priority fields first, then user order).

CRITICAL - When to use COLLAPSE for unique results:

USE COLLAPSE when asking about ENTITY ATTRIBUTES (one record per entity):
  - "which users are developer" → {{"collapse": {{"field": "uid"}}}}
  - "list all users" → {{"collapse": {{"field": "uid"}}}}
  - "which modules were published" → {{"collapse": {{"field": "mid"}}}}
  - "users with role admin" → {{"collapse": {{"field": "uid"}}}}

DO NOT USE COLLAPSE when asking about ACTIVITIES/RELATIONSHIPS (show all records):
  - "which users have modules in progress" → NO collapse (show each module)
  - "users with pending assignments" → NO collapse (show each assignment)
  - "which users completed modules" → NO collapse (show each completion)
  - "modules assigned to users" → NO collapse (show each assignment)

Key rule: If query mentions "have modules", "have assignments", "in progress", "completed modules",
or any activity/relationship, DO NOT use collapse. Show all matching records.

Query pattern (with collapse for unique users):
{{
  "size": 50,
  "collapse": {{"field": "uid"}},
  "_source": ["first_name", "last_name", "email_addr", "module_name", "city", "completed_status"],
  "query": {{
    "bool": {{
      "filter": [
        {{"term": {{"user_status": 5}}}}
      ]
    }}
  }},
  "sort": [{{ "created_on": "desc" }}]
}}

Query pattern (without collapse for activities/completions):
{{
  "size": 50,
  "_source": ["first_name", "last_name", "email_addr", "module_name", "city", "completed_status"],
  "query": {{
    "bool": {{
      "filter": [
        {{"term": {{"user_status": 5}}}},
        {{"term": {{"assigned_status": 0}}}},
        {{"term": {{"completed_status": 1}}}}
      ]
    }}
  }},
  "sort": [{{ "created_on": "desc" }}]
}}""",

        ResponseType.BAR_CHART: """
RESPONSE TYPE: BAR_CHART (Grouped Data / Rankings)
User wants data broken down by category or ranked (e.g., "which module has most completions", "graph of most completed modules", "which user has completed the most modules")

🔴 CRITICAL: When user asks for rankings, "which X has most/least", "top X", or a "graph" or "chart", you MUST:
  1. Set response_type to "bar_chart" (or appropriate chart type)
  2. Generate a query with size: 0 and aggregations
  3. Use terms aggregation on the grouping field (module_name, email_addr, city, etc.)
  4. Include a cardinality aggregation to count unique entities
  5. Order by the count (descending for "most", ascending for "least")
  6. Use top_hits to get display fields (names, emails) for the results

MANDATORY FILTERS:
  - ALWAYS: {{"term": {{"user_status": 5}}}}     ← Active users only!
  - ALWAYS: {{"term": {{"assigned_status": 0}}}} ← Assigned records only!
  - If about completions: {{"term": {{"completed_status": 1}}}}

CRITICAL - For grouping (USE NAME FIELDS SO CHART SHOWS NAMES):
  - Group by MODULE: use "module_name" field ← Shows names on chart!
  - Group by USER: use "email_addr" field ← Shows emails on chart!
  - Count unique users with: {{"cardinality": {{"field": "uid"}}}}
  - Count unique modules with: {{"cardinality": {{"field": "mid"}}}}

Query pattern for "which module has most completions" OR "graph of most completed modules":
{{
  "size": 0,
  "query": {{
    "bool": {{
      "filter": [
        {{"term": {{"user_status": 5}}}},
        {{"term": {{"assigned_status": 0}}}},
        {{"term": {{"completed_status": 1}}}}
      ]
    }}
  }},
  "aggs": {{
    "by_module": {{
      "terms": {{ 
        "field": "module_name", 
        "size": 10, 
        "order": {{"_count": "desc"}} 
      }},
      "aggs": {{
        "unique_users": {{ 
          "cardinality": {{ "field": "uid" }} 
        }}
      }}
    }}
  }}
}}

🔴 CRITICAL: For "most completed" queries, ALWAYS use "order": {{"_count": "desc"}} 
  - "_count" = total completion records (doc_count) = what "most completed" means
  - "unique_users" = distinct users who completed = different metric!
  - Example: Module A has 100 completions from 10 users → _count=100, unique_users=10
  - Example: Module B has 50 completions from 50 users → _count=50, unique_users=50
  - For "most completed", Module A wins (100 > 50), even though Module B has more unique users

🔴 CRITICAL FOR CHARTS:
- ALWAYS use size: 0 when user asks for a "graph" or "chart"
- ALWAYS include aggregations (aggs) - charts need aggregations, not hits!
- Group by NAME fields (module_name, city, etc.) so chart shows readable labels
- Count with cardinality on ID fields (uid, mid) inside the aggregation
- Order by the count field (desc for "most", asc for "least")

Query pattern for "which user has completed the most modules" OR "top users by module completions":
{{
  "size": 0,
  "query": {{
    "bool": {{
      "filter": [
        {{"term": {{"user_status": 5}}}},
        {{"term": {{"assigned_status": 0}}}},
        {{"term": {{"completed_status": 1}}}}
      ]
    }}
  }},
  "aggs": {{
    "by_user": {{
      "terms": {{ 
        "field": "email_addr", 
        "size": 10, 
        "order": {{"unique_modules": "desc"}} 
      }},
      "aggs": {{
        "unique_modules": {{ 
          "cardinality": {{ "field": "mid" }} 
        }},
        "user_info": {{
          "top_hits": {{
            "size": 1,
            "_source": ["uid", "email_addr", "first_name", "last_name", "designation"]
          }}
        }}
      }}
    }}
  }}
}}

Query pattern for completions by city:
{{
  "size": 0,
  "query": {{
    "bool": {{
      "filter": [
        {{"term": {{"user_status": 5}}}},
        {{"term": {{"assigned_status": 0}}}},
        {{"term": {{"completed_status": 1}}}}
      ]
    }}
  }},
  "aggs": {{
    "by_city": {{
      "terms": {{ "field": "city", "size": 20 }},
      "aggs": {{
        "unique_users": {{ "cardinality": {{ "field": "uid" }} }}
      }}
    }}
  }}
}}

🔴 CRITICAL: For "completion rate" queries (e.g., "top modules by completion rate"):
  Completion rate = (completed users / assigned users) * 100
  
  ⚠️ NEVER add name/email filters for "completion", "completions", or "completion rate"!
  These are METRIC TERMS, not person names. Adding filters will exclude all users!
  
  Query structure (EXACT format - use this structure):
  {{
    "size": 0,
    "query": {{
      "bool": {{
        "filter": [
          {{"term": {{"user_status": 5}}}},
          {{"term": {{"cmid": 1}}}}
        ]
      }}
    }},
    "aggs": {{
      "by_module": {{
        "terms": {{ 
          "field": "mid", 
          "size": 50
          // DO NOT add order here - use bucket_sort instead!
        }},
        "aggs": {{
          "module_name": {{
            "terms": {{ "field": "module_name", "size": 1 }}
          }},
          "assigned_users": {{
            "filter": {{ "term": {{ "assigned_status": 0 }} }},
            "aggs": {{
              "value": {{ "cardinality": {{ "field": "uid" }} }}
            }}
          }},
          "completed_users": {{
            "filter": {{
              "bool": {{
                "must": [
                  {{"term": {{"assigned_status": 0}}}},
                  {{"term": {{"completed_status": 1}}}}
                ]
              }}
            }},
            "aggs": {{
              "value": {{ "cardinality": {{ "field": "uid" }} }}
            }}
          }},
          "completion_rate": {{
            "bucket_script": {{
              "buckets_path": {{
                "completed": "completed_users>value",
                "assigned": "assigned_users>value"
              }},
              "script": "params.assigned > 0 ? params.completed / params.assigned : 0"
            }}
          }},
          "sort_by_completion_rate": {{
            "bucket_sort": {{
              "sort": [
                {{ "completion_rate": {{ "order": "desc" }} }}
              ],
              "size": 10
            }}
          }}
        }}
      }}
    }}
  }}
  
  ⚠️ CRITICAL RULES:
  1. Aggregation names: "assigned_users" and "completed_users" (NOT "assigned" and "completed")
  2. Inside each filter aggregation, use "value" as the cardinality aggregation name
  3. buckets_path: "completed_users>value" and "assigned_users>value" (NOT "completed>completed_users")
  4. DO NOT add "order" in terms aggregation for completion_rate (pipeline aggregation)
  5. USE bucket_sort to sort by completion_rate after calculating it
  6. Group by "mid" (module ID), not "module_name" (more stable)
  7. NEVER add name/email filters for "completion", "completions", or "completion rate"

  Module-specific completion rate (single module KPI):
  {{
    "size": 0,
    "query": {{
      "bool": {{
        "filter": [
          {{"term": {{"user_status": 5}}}},
          {{"term": {{"assigned_status": 0}}}},
          {{"term": {{"module_status": 0}}}},
          {{"term": {{"cmid": 1}}}},
          {{
            "match_phrase": {{
              "module_name": {{
                "query": "MODULE_NAME_HERE",
                "slop": 2
              }}
            }}
          }}
        ]
      }}
    }},
    "aggs": {{
      "scope": {{
        "filters": {{
          "filters": {{
            "all": {{"match_all": {{}}}}
          }}
        }},
        "aggs": {{
          "assigned": {{
            "value_count": {{"field": "uid"}}
          }},
          "completed": {{
            "filter": {{"term": {{"completed_status": 1}}}},
            "aggs": {{
              "completed_count": {{
                "value_count": {{"field": "uid"}}
              }}
            }}
          }},
          "completion_rate": {{
            "bucket_script": {{
              "buckets_path": {{
                "completed": "completed>completed_count",
                "assigned": "assigned"
              }},
              "script": "params.assigned > 0 ? (params.completed / params.assigned) * 100 : 0"
            }}
          }}
        }}
      }}
    }}
  }}
  
  ⚠️ CRITICAL: 
  - Put assigned_status: 0 in the main query filter (defines denominator)
  - DO NOT put completed_status: 1 in the main query filter (only in aggregation)
  - DO NOT put ratings filters in the main query filter
  - Use "scope" filters aggregation (multi-bucket) with named "all" bucket (match_all)
  - bucket_script MUST be inside a filters aggregation (multi-bucket), not a single-bucket filter aggregation
  - Use "assigned" value_count on uid for assigned count (direct aggregation, not inside filter)
  - Use "completed" filter aggregation with completed_status: 1, containing "completed_count" value_count on uid
  - bucket_script references: completed: "completed>completed_count" and assigned: "assigned"
  - aggregations.scope.buckets.all.assigned.value = assigned count (denominator)
  - aggregations.scope.buckets.all.completed.completed_count.value = completed count (numerator)
  - aggregations.scope.buckets.all.completion_rate.value = completion rate %

  🔸 Time range handling for module completion rate:
     - ONLY apply completed_date range inside completed filter aggregation (numerator).
     - Never time-bound the main query or assigned bucket unless an assignment date field exists.
     - If no timeframe is provided, omit the range clause entirely.

  User-specific completion rate (single learner KPI):
  
  🔴 CRITICAL: Determine if user input is EMAIL or NAME:
  
  If EMAIL (contains "@"):
    {{"term": {{"email_addr": "user@example.com"}}}}
  
  If FULL NAME (2+ words like "tanuj sadasivam"):
    {{"bool": {{
      "must": [
        {{"match": {{"first_name": {{"query": "tanuj", "operator": "and"}}}}}},
        {{"match": {{"last_name": {{"query": "sadasivam", "operator": "and"}}}}}}
      ]
    }}}}
  
  If SINGLE NAME (1 word like "tanuj"):
    {{"bool": {{
      "should": [
        {{"match": {{"first_name": {{"query": "tanuj", "fuzziness": "AUTO"}}}}}},
        {{"match": {{"last_name": {{"query": "tanuj", "fuzziness": "AUTO"}}}}}}
      ],
      "minimum_should_match": 1
    }}}}
  
  ⚠️ NEVER use module_name for user names/emails!
  
  Example query structure:
  {{
    "size": 0,
    "query": {{
      "bool": {{
        "filter": [
          {{"term": {{"user_status": 5}}}},
          {{"term": {{"assigned_status": 0}}}},
          {{"term": {{"module_status": 0}}}},
          {{"term": {{"cmid": 1}}}},
          // Use ONE of the patterns above based on user input (email, full name, or single name)
          // Example for single name "tanuj":
          {{"bool": {{
            "should": [
              {{"match": {{"first_name": {{"query": "tanuj", "fuzziness": "AUTO"}}}}}},
              {{"match": {{"last_name": {{"query": "tanuj", "fuzziness": "AUTO"}}}}}}
            ],
            "minimum_should_match": 1
          }}}}
        ]
      }}
    }},
    "aggs": {{
      "completion_scope": {{
        "filters": {{
          "filters": {{
            "all_assigned": {{"match_all": {{}}}}
          }}
        }},
        "aggs": {{
          "total_assigned": {{
            "value_count": {{"field": "uid"}}
          }},
          "completion_stats": {{
            "filter": {{"term": {{"completed_status": 1}}}},
            "aggs": {{
              "completed_count": {{
                "value_count": {{"field": "uid"}}
              }}
            }}
          }},
          "completion_rate": {{
            "bucket_script": {{
              "buckets_path": {{
                "completed": "completion_stats>completed_count",
                "total": "total_assigned"
              }},
              "script": "params.total > 0 ? (params.completed * 100.0 / params.total) : 0"
            }}
          }}
        }}
      }}
    }}
  }}
  
  ⚠️ CRITICAL: 
  - Put assigned_status: 0 in the main query filter (defines denominator)
  - DO NOT put completed_status: 1 in the main query filter (only in aggregation)
  - DO NOT put ratings filters in the main query filter
  - Use "completion_scope" filters aggregation (multi-bucket) with named "all_assigned" bucket (match_all)
  - bucket_script MUST be inside a filters aggregation (multi-bucket), not a single-bucket filter aggregation
  - Use "total_assigned" value_count on uid for assigned count
  - Use "completion_stats" filter aggregation with completed_status: 1, containing "completed_count" value_count on uid
  - bucket_script references: completed: "completion_stats>completed_count" and total: "total_assigned"

  🔸 Time range handling for user completion rate:
     - Add completed_date range ONLY inside completed filter aggregation (numerator).
     - Keep the main query and assigned bucket counting all assigned records (no assignment date filter available).
     - When timeframe exists ("last quarter", "in 2024"), convert it to {{"range": {{"completed_date": {{"gte": START_TS, "lt": END_TS}}}}}.
  
  Per-user completion rate (completion rate for each user):
  {{
    "size": 0,
    "query": {{
      "bool": {{
        "filter": [
          {{"term": {{"user_status": 5}}}},
          {{"term": {{"assigned_status": 0}}}},
          {{"term": {{"module_status": 0}}}},
          {{"term": {{"cmid": 1}}}}
        ]
      }}
    }},
    "aggs": {{
      "by_user": {{
        "terms": {{"field": "uid", "size": 10000}},
        "aggs": {{
          "completion_scope": {{
            "filters": {{
              "filters": {{
                "all_assigned": {{"match_all": {{}}}}
              }}
            }},
            "aggs": {{
              "total_assigned": {{
                "value_count": {{"field": "uid"}}
              }},
              "completion_stats": {{
                "filter": {{"term": {{"completed_status": 1}}}},
                "aggs": {{
                  "completed_count": {{
                    "value_count": {{"field": "uid"}}
                  }}
                }}
              }},
              "rate": {{
                "bucket_script": {{
                  "buckets_path": {{
                    "completed": "completion_stats>completed_count",
                    "total": "total_assigned"
                  }},
                  "script": "params.total > 0 ? (params.completed * 100.0 / params.total) : 0"
                }}
              }}
            }}
          }}
        }}
      }}
    }}
  }}
  
  ⚠️ CRITICAL: 
  - Put assigned_status: 0 in the main query filter (defines denominator for all users)
  - DO NOT put completed_status: 1 in the main query filter (only in aggregation)
  - DO NOT put ratings filters in the main query filter
  - Use "completion_scope" filters aggregation (multi-bucket) with named "all_assigned" bucket (match_all) inside each user bucket
  - bucket_script MUST be inside a filters aggregation (multi-bucket), not a single-bucket filter aggregation
  - Use "total_assigned" value_count on uid for assigned count
  - Use "completion_stats" filter aggregation with completed_status: 1, containing "completed_count" value_count on uid
  - Each user bucket: completion_scope.buckets.all_assigned.total_assigned.value = assigned count, completion_scope.buckets.all_assigned.completion_stats.completed_count.value = completed count
""",

        ResponseType.COMPARISON: """
RESPONSE TYPE: COMPARISON (X vs Y)
User wants to compare two metrics.

CRITICAL - Use correct ID fields:
  - For unique USERS: use "uid" (NOT email_addr)
  - For unique MODULES: use "mid" (NOT module_name)

Query pattern (comparing modules delivered vs completed):
{{
  "size": 0,
  "query": {{ /* filters */ }},
  "aggs": {{
    "total_modules": {{ "cardinality": {{ "field": "mid" }} }},
    "completed_modules": {{
      "filter": {{ "term": {{ "completed_status": 1 }} }},
      "aggs": {{
        "count": {{ "cardinality": {{ "field": "mid" }} }}
      }}
    }}
  }}
}}

IMPORTANT: Return both raw counts. System will calculate percentage.""",

        ResponseType.LINE_CHART: """
RESPONSE TYPE: LINE_CHART (Trend Over Time)
User wants to see changes over time.

CRITICAL - Use correct ID fields:
  - For unique USERS: use "uid" (NOT email_addr)
  - For unique MODULES: use "mid" (NOT module_name)

Query pattern:
{{
  "size": 0,
  "query": {{ /* filters */ }},
  "aggs": {{
    "over_time": {{
      "date_histogram": {{
        "field": "created_on",
        "fixed_interval": "1d",
        "format": "yyyy-MM-dd",
        "keyed": false
      }},
      "aggs": {{
        "unique_users": {{ "cardinality": {{ "field": "uid" }} }}
      }}
    }}
  }}
}}

🔴 CRITICAL DATE_HISTOGRAM RULES:
  - For daily data: Use "fixed_interval": "1d" (NOT "calendar_interval")
  - ALWAYS include "format": "yyyy-MM-dd" to get readable dates in key_as_string
  - ALWAYS include "min_doc_count": 0 to show ALL days, even with 0 completions
  - For monthly data: Use "calendar_interval": "month" with "format": "yyyy-MM"
  - NEVER use size limit on date_histogram - it will limit the number of buckets!
  - For "last N days" queries, ensure the date range covers all N days AND include min_doc_count: 0
  - Example for "last 30 days":
    {{
      "date_histogram": {{
        "field": "completed_date",
        "fixed_interval": "1d",
        "format": "yyyy-MM-dd",
        "min_doc_count": 0,
        "extended_bounds": {{
          "min": START_TIMESTAMP,
          "max": END_TIMESTAMP
        }}
      }}
    }}""",

        ResponseType.PIE_CHART: """
RESPONSE TYPE: PIE_CHART (Proportional Breakdown)
User wants to see distribution/share.

Query pattern:
{{
  "size": 0,
  "query": {{ /* filters */ }},
  "aggs": {{
    "distribution": {{
      "terms": {{ "field": "skill_name", "size": 10 }}
    }}
  }}
}}""",

        ResponseType.DRILLDOWN: """
RESPONSE TYPE: DRILLDOWN (Follow-up Refinement)
User is refining or narrowing a previous query.

Reuse the filters from the previous query and ADD the new constraints.
Common patterns:
- "now only for X" → add filter for X
- "same but for Y" → change one filter
- "exclude Z" → add must_not for Z

Query pattern:
{{
  "size": 50,
  "query": {{
    "bool": {{
      "must": [
        /* previous filters */,
        /* new constraint filter */
      ]
    }}
  }},
  "_source": ["relevant", "fields"]
}}""",

        ResponseType.DATA_DICTIONARY: """
RESPONSE TYPE: DATA_DICTIONARY (Schema Information)
User is asking about available fields or what can be queried.

DO NOT generate an OpenSearch query. Instead, return:
{{
  "index_id": "module_consumption_data",
  "query": null,
  "response_type": "data_dictionary",
  "title": "Available Fields",
  "description": "Information about queryable fields and filters"
}}

The system will provide schema documentation directly.""",
    }
    
    @classmethod
    def build_system_prompt(
        cls,
        schema: Dict[str, Any],
        response_type: ResponseType,
        conversation_context: str = ""
    ) -> str:
        """
        Build the system prompt for query generation.
        
        Args:
            schema: Index schema information
            response_type: Determined response type for the query
            conversation_context: Context from previous conversation turns
            
        Returns:
            Complete system prompt for the LLM
        """
        # Build schema context
        schema_context = SchemaContextBuilder.build_context(schema, include_samples=True)
        
        # Get date context
        date_context = SchemaContextBuilder.get_date_context()
        
        # Get response type guidance
        response_guidance = cls.RESPONSE_TYPE_GUIDANCE.get(
            response_type,
            cls.RESPONSE_TYPE_GUIDANCE[ResponseType.KPI_WIDGET]
        )
        
        return cls.SYSTEM_PROMPT_TEMPLATE.format(
            schema_context=schema_context,
            date_context=date_context,
            normalization_rules=cls.NORMALIZATION_RULES,
            response_type_guidance=response_guidance,
            response_type=response_type.value,
            conversation_context=f"CONVERSATION CONTEXT:\n{conversation_context}" if conversation_context else ""
        )
    
    @classmethod
    def build_user_prompt(
        cls,
        user_message: str,
        time_period: Optional[TimePeriod] = None,
        time_field: str = 'created_on',
        active_filters: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Build the user prompt. Bedrock will handle all entity extraction and query building
        based on the comprehensive rules in the system prompt.
        
        Args:
            user_message: The user's natural language query
            time_period: Parsed time period from TimeHandler
            time_field: Suggested date field to use (Bedrock should determine the correct one based on context)
            active_filters: Active filters from conversation context
            
        Returns:
            User prompt for the LLM
        """
        parts = [f"USER QUERY: {user_message}"]
        
        # Detect "haven't completed" pattern and add specific reminder
        message_lower = user_message.lower()
        has_havent_completed = any(phrase in message_lower for phrase in [
            "haven't completed", "havent completed", "hasn't completed", "hasnt completed",
            "didn't complete", "didnt complete", "hasn't finished", "hasnt finished"
        ])
        has_time_range = time_period and not time_period.is_lifetime
        
        if has_havent_completed and has_time_range:
            # Check if this is a relative timeframe for date math
            from dashboarding.services.time_handler import TimeframeResolver
            timeframe_key = TimeframeResolver.extract_timeframe_key(user_message)
            date_math = None
            if timeframe_key:
                date_math = TimeframeResolver._get_date_math_expressions(timeframe_key)
            
            time_range_str = ""
            if date_math:
                time_range_str = f'Use OpenSearch date math: {{"gte": "{date_math["gte"]}", "lt": "{date_math["lt"]}"}}'
            else:
                time_range_str = f'Use epoch timestamps: {{"gte": {time_period.start_timestamp}, "lt": {time_period.end_timestamp}}}'
            
            parts.append(f"""
🔴🔴🔴 CRITICAL: This is a "haven't completed in time range" query!
You MUST use the aggregation pattern from the system prompt (pattern #1 in DATE FIELD RULES).
DO NOT use simple filters - they won't work!
Required structure:
- size: 0
- query.filter: only user_status=5 and cmid=1
- aggs.users: terms aggregation on uid
- aggs.users.aggs.assigned: filter for assigned_status=0
- aggs.users.aggs.recent_completions: filter for completed_status=1 AND completed_date in time range
- aggs.users.aggs.only_inactive_users: bucket_selector with script "params.assignedCount > 0 && params.recentCompletedCount == 0"
- aggs.users.aggs.user_info: top_hits to get user details

Time range: {time_range_str}
Use completed_date for the time range in recent_completions filter.""")
        
        # Add time period context if detected
        if time_period and not time_period.is_lifetime:
            if not (has_havent_completed and has_time_range):  # Don't duplicate if already added above
                # Check if this is a relative timeframe that should use date math
                from dashboarding.services.time_handler import TimeframeResolver
                timeframe_key = TimeframeResolver.extract_timeframe_key(user_message)
                
                if timeframe_key:
                    # Use date math expressions for relative timeframes
                    date_math = TimeframeResolver._get_date_math_expressions(timeframe_key)
                    if date_math:
                        parts.append(f"""
TIME FILTER DETECTED: {time_period.description} (relative timeframe)
Suggested date field: {time_field} (but determine the correct date field based on the query context - see DATE FIELD RULES)
Use OpenSearch date math expressions for dynamic date calculation:
{{"range": {{"<DATE_FIELD>": {{"gte": "{date_math['gte']}", "lt": "{date_math['lt']}"}}}}}}
This will calculate the date range dynamically at query time (e.g., "now-3M/M" means start of month 3 months ago).""")
                    else:
                        # Fallback to epoch if date math not available
                        parts.append(f"""
TIME FILTER DETECTED: {time_period.description}
Suggested date field: {time_field} (but determine the correct date field based on the query context - see DATE FIELD RULES)
Use this date range in your query:
{{"range": {{"<DATE_FIELD>": {{"gte": {time_period.start_timestamp}, "lt": {time_period.end_timestamp}}}}}}}""")
                else:
                    # Absolute date - use epoch timestamps
                    parts.append(f"""
TIME FILTER DETECTED: {time_period.description}
Suggested date field: {time_field} (but determine the correct date field based on the query context - see DATE FIELD RULES)
Use this date range in your query:
{{"range": {{"<DATE_FIELD>": {{"gte": {time_period.start_timestamp}, "lt": {time_period.end_timestamp}}}}}}}""")
        elif time_period and time_period.is_lifetime:
            parts.append("\nTIME: No specific time mentioned - query ALL data (no date filter)")
        
        # Add active filters from conversation
        if active_filters:
            parts.append("\nACTIVE FILTERS FROM PREVIOUS QUERIES:")
            for filter_type, filter_info in active_filters.items():
                parts.append(f"  - {filter_type}: {filter_info}")
        
        parts.append("\nGenerate the OpenSearch query JSON now. Follow all the rules in the system prompt.")
        
        return '\n'.join(parts)
    
    @classmethod
    def get_follow_up_context(
        cls,
        previous_query: Dict[str, Any],
        previous_result_total: int,
        previous_message: str
    ) -> str:
        """
        Build context for follow-up queries.
        Implements usecase.md Section H - Conversation Memory.
        
        Args:
            previous_query: The query from the previous turn
            previous_result_total: How many results the previous query returned
            previous_message: What the user asked previously
            
        Returns:
            Context string for follow-up handling
        """
        return f"""
PREVIOUS QUERY CONTEXT (Section H - Conversation Memory):
User asked: "{previous_message}"
That query returned {previous_result_total} records.

Previous query structure:
{previous_query}

FOLLOW-UP RULES:
1. Reuse filters from the previous query unless user explicitly overrides
2. If user says "now only for X", add X as additional filter
3. If user says "same but for Y", replace the relevant filter with Y
4. If user says "exclude Z", add a must_not clause for Z
5. Keep the time range unless user specifies a new one
6. Keep the breakdown dimension unless user changes it"""

    @classmethod
    def get_clarification_prompt(cls, ambiguous_terms: List[tuple]) -> str:
        """
        Build a clarification question for ambiguous queries.
        Implements usecase.md Section I - Fail-Safes.
        
        Args:
            ambiguous_terms: List of (term, possible_interpretations) tuples
            
        Returns:
            Clarification question to ask the user
        """
        questions = []
        for term, interpretations in ambiguous_terms:
            if term in ['sent', 'delivered', 'pushed']:
                questions.append(
                    f'When you say "{term}", do you mean:\n'
                    '  a) When invitations were sent (invited_date)\n'
                    '  b) Assignment status (assigned_status)'
                )
            elif term == 'score':
                questions.append(
                    'When you say "score", do you mean:\n'
                    '  a) Completion percentage (complete_percentage)\n'
                    '  b) Module ratings (ratings)'
                )
        
        if questions:
            return "I need a quick clarification:\n" + "\n".join(questions)
        return ""
