"""
Response Generation Prompt Builder for AI Reports Bot.
Generates prompts that instruct the LLM to create human-readable responses
using ONLY actual data from query execution.
"""

from typing import Dict, Any, Optional, List
from ..response_types import ResponseType


class ResponsePromptBuilder:
    """
    Builds prompts for generating human-readable responses.
    
    Key principle: ALL numbers in the response MUST come from the provided query results.
    The LLM should NEVER invent or guess numbers.
    """
    
    SYSTEM_PROMPT = """You are a friendly, conversational AI assistant that helps users understand their data. Be natural and helpful, like you're chatting with a colleague.

CRITICAL RULES:
1. ONLY use numbers from the PROVIDED QUERY RESULTS - never invent numbers
2. If a number isn't in the results, say "data not available" - NEVER guess
3. Be conversational and friendly - use "I found", "Here's what I see", "Looks like", etc.
4. Keep it simple and clear - avoid overly technical jargon
5. Always specify what the numbers represent (e.g., "unique users" vs "records")
6. Include time context if a date filter was applied

TONE: Friendly, helpful, conversational. Like you're explaining data to a friend, not writing a formal report.

Your response should be a JSON object:
{{
  "message": "Your friendly, conversational summary here",
  "highlights": ["Key insight 1", "Key insight 2"],
  "warnings": ["Any data quality notes"]
}}"""

    RESPONSE_TEMPLATES = {
        ResponseType.KPI_WIDGET: """
Generate a response for a KEY METRIC display.

QUERY RESULTS:
{query_results}

REQUIREMENTS:
- Lead with the main number prominently
- Explain what the number represents
- Add context (time period, filters applied)
- Format large numbers with commas

EXAMPLE FORMAT:
"Found 1,234 unique users who completed training in November 2024."
""",

        ResponseType.MULTI_KPI: """
Generate a response for MULTIPLE KEY METRICS.

QUERY RESULTS:
{query_results}

REQUIREMENTS:
- Summarize each metric clearly
- Show relationships between metrics if relevant (e.g., completion rate)
- For rates: calculate from the raw numbers provided

EXAMPLE FORMAT:
"Summary for November 2024: 1,234 total users assigned, 567 completed (45.9% completion rate), across 89 unique modules."
""",

        ResponseType.TABLE: """
Generate a response introducing TABLE DATA.

QUERY RESULTS:
Total records: {total}
Showing: {count} records

REQUIREMENTS:
- State how many records were found
- Mention key columns being shown
- Note if results were limited/paginated

EXAMPLE FORMAT:
"Showing 50 of 1,234 training records. The table includes user name, email, module, and completion status."
""",

        ResponseType.BAR_CHART: """
Generate a response for GROUPED/CHART DATA.

QUERY RESULTS:
{query_results}

REQUIREMENTS:
- Mention total categories found
- Highlight top performers if relevant
- Note the grouping dimension

EXAMPLE FORMAT:
"Training completions by city: Bengaluru leads with 456 completions, followed by Mumbai (234) and Chennai (123). Data covers 15 cities total."
""",

        ResponseType.COMPARISON: """
Generate a response for a COMPARISON (X vs Y).

QUERY RESULTS:
{query_results}

REQUIREMENTS:
- State both values being compared
- Calculate and state the percentage/rate
- Provide context for the comparison

CALCULATION RULES:
- Completion rate = (completed / total) * 100
- Round percentages to 1 decimal place

EXAMPLE FORMAT:
"Comparison: 89 modules were assigned, 45 were completed (50.6% completion rate) in November 2024."
""",

        ResponseType.LINE_CHART: """
Generate a response for TREND DATA.

QUERY RESULTS:
{query_results}

REQUIREMENTS:
- Describe the overall trend (increasing, decreasing, stable)
- Highlight notable peaks or dips
- Mention the time range covered

EXAMPLE FORMAT:
"Training completions show an upward trend over the last 6 months, from 123 in June to 456 in November (270% increase). Peak month was October with 489 completions."
""",

        ResponseType.PIE_CHART: """
Generate a response for DISTRIBUTION DATA.

QUERY RESULTS:
{query_results}

REQUIREMENTS:
- State total and breakdown
- Mention top categories with percentages
- Note if "Other" category exists

EXAMPLE FORMAT:
"Skill distribution: Technical skills dominate at 45% (567 records), followed by Sales at 30% (378) and Soft Skills at 25% (312)."
""",
    }
    
    @classmethod
    def build_prompt(
        cls,
        response_type: ResponseType,
        query_results: Dict[str, Any],
        original_query: str,
        time_context: Optional[str] = None,
        filters_applied: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Build a prompt for generating the human-readable response.
        
        Args:
            response_type: The type of response to generate
            query_results: Actual results from query execution
            original_query: The user's original question
            time_context: Description of time period if applicable
            filters_applied: Any filters that were applied
            
        Returns:
            Complete prompt for response generation
        """
        # Get the template for this response type
        template = cls.RESPONSE_TEMPLATES.get(
            response_type,
            cls.RESPONSE_TEMPLATES[ResponseType.KPI_WIDGET]
        )
        
        # Format query results for the prompt
        results_str = cls._format_results_for_prompt(query_results)
        
        # Build the full prompt
        parts = [
            f"USER'S QUESTION: {original_query}",
            "",
            template.format(
                query_results=results_str,
                total=query_results.get('total', 0),
                count=query_results.get('count', 0)
            ),
        ]
        
        # Add time context
        if time_context:
            parts.append(f"\nTIME PERIOD: {time_context}")
        
        # Add filter context
        if filters_applied:
            parts.append("\nFILTERS APPLIED:")
            for filter_name, filter_value in filters_applied.items():
                parts.append(f"  - {filter_name}: {filter_value}")
        
        parts.append("\nGenerate your response JSON now. Remember: ONLY use numbers from the query results above.")
        
        return '\n'.join(parts)
    
    @classmethod
    def _format_results_for_prompt(cls, query_results: Dict[str, Any]) -> str:
        """Format query results into a clear string for the LLM."""
        parts = []
        
        # Total count - THIS IS THE AUTHORITATIVE COUNT
        total = query_results.get('total', 0)
        count = query_results.get('count', 0)
        parts.append(f"Total matching records: {total:,}")
        if count != total:
            parts.append(f"Returned records: {count:,}")
        
        # Sample results (for table views)
        results = query_results.get('results', [])
        if results:
            parts.append(f"\nActual results returned: {len(results)} records")
            if len(results) > 0:
                # Show first record structure
                first = results[0]
                fields = [k for k in first.keys() if not k.startswith('_')]
                parts.append(f"Fields: {', '.join(fields[:10])}")
        
        # Aggregations (but note: for count threshold queries, use total/count above, not bucket count!)
        aggregations = query_results.get('aggregations', {})
        if aggregations:
            parts.append("\nAggregation Results (for reference only - use 'total' above for count):")
            for agg_name, agg_value in aggregations.items():
                formatted = cls._format_aggregation(agg_name, agg_value)
                parts.append(f"  {formatted}")
        
        return '\n'.join(parts)
    
    @classmethod
    def _format_aggregation(cls, name: str, value: Any) -> str:
        """Format a single aggregation result."""
        if isinstance(value, dict):
            # Cardinality or value count
            if 'value' in value:
                return f"{name}: {int(value['value']):,}"
            
            # Terms aggregation (buckets)
            if 'buckets' in value:
                buckets = value['buckets']
                bucket_count = len(buckets)
                if bucket_count == 0:
                    return f"{name}: 0 categories"
                
                # Show top 5 buckets
                top_buckets = buckets[:5]
                bucket_strs = [f"{b.get('key', '?')}: {b.get('doc_count', 0):,}" for b in top_buckets]
                result = f"{name}: {bucket_count} categories"
                if bucket_strs:
                    result += f" (top: {', '.join(bucket_strs)})"
                return result
            
            # Nested filter aggregation
            if 'doc_count' in value:
                doc_count = value['doc_count']
                # Check for nested aggs
                nested = {k: v for k, v in value.items() if k != 'doc_count' and isinstance(v, dict)}
                if nested:
                    nested_str = ', '.join(
                        f"{k}: {v.get('value', v.get('doc_count', '?'))}"
                        for k, v in nested.items()
                    )
                    return f"{name}: {doc_count:,} records ({nested_str})"
                return f"{name}: {doc_count:,} records"
            
            # Stats aggregation
            if 'min' in value and 'max' in value:
                return f"{name}: min={value['min']}, max={value['max']}, avg={value.get('avg', 'N/A')}"
        
        return f"{name}: {value}"
    
    @classmethod
    def get_validation_prompt(cls, proposed_response: str, query_results: Dict[str, Any]) -> str:
        """
        Build a prompt to validate that a response doesn't contain hallucinated numbers.
        
        Args:
            proposed_response: The response to validate
            query_results: The actual query results
            
        Returns:
            Prompt for validation
        """
        results_str = cls._format_results_for_prompt(query_results)
        
        return f"""Validate this response against the actual data.

PROPOSED RESPONSE:
{proposed_response}

ACTUAL QUERY RESULTS:
{results_str}

Check:
1. Are all numbers in the response found in the query results?
2. Are calculations (percentages, rates) correct?
3. Is the time period accurate?

Respond with JSON:
{{
  "is_valid": true/false,
  "issues": ["List any problems found"],
  "corrected_response": "If invalid, provide corrected version"
}}"""

