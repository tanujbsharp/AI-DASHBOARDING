"""
Response Validator for AI Reports Bot.
Ensures responses contain only verified data from query results.
Prevents hallucination of numbers and facts.
"""

import re
import logging
from typing import Dict, Any, List, Tuple, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of response validation."""
    is_valid: bool
    issues: List[str]
    numbers_found: List[int]
    numbers_verified: List[int]
    numbers_unverified: List[int]
    corrected_message: Optional[str] = None


class ResponseValidator:
    """
    Validates that LLM-generated responses only contain data from actual query results.
    
    Prevents:
    - Hallucinated numbers
    - Incorrect calculations
    - Made-up statistics
    """
    
    @staticmethod
    def _safe_int(value: Any) -> Optional[int]:
        """Convert value to int when possible without raising exceptions."""
        if value is None:
            return None
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            try:
                return int(float(stripped))
            except ValueError:
                return None
        return None
    
    @classmethod
    def validate_response(
        cls,
        response_message: str,
        query_results: Dict[str, Any],
        tolerance: float = 0.01
    ) -> ValidationResult:
        """
        Validate that a response message only contains numbers from query results.
        
        Args:
            response_message: The LLM-generated message
            query_results: Actual results from query execution
            tolerance: Tolerance for percentage calculations (default 1%)
            
        Returns:
            ValidationResult with validation status and details
        """
        # Extract all numbers from the response
        numbers_in_response = cls._extract_numbers(response_message)
        
        # Build set of valid numbers from query results
        valid_numbers = cls._build_valid_numbers_set(query_results)
        
        # Check each number
        verified = []
        unverified = []
        issues = []
        
        for num in numbers_in_response:
            if cls._is_valid_number(num, valid_numbers, tolerance):
                verified.append(num)
            else:
                unverified.append(num)
                issues.append(f"Number {num:,} not found in query results")
        
        # Additional validation for percentages
        percentage_issues = cls._validate_percentages(response_message, query_results)
        issues.extend(percentage_issues)
        
        is_valid = len(unverified) == 0 and len(percentage_issues) == 0
        
        return ValidationResult(
            is_valid=is_valid,
            issues=issues,
            numbers_found=numbers_in_response,
            numbers_verified=verified,
            numbers_unverified=unverified,
            corrected_message=None  # Could implement auto-correction
        )
    
    @classmethod
    def _extract_numbers(cls, text: str) -> List[int]:
        """
        Extract all numbers from text, handling comma-formatted numbers.
        
        CRITICAL: Exclude numbers that are:
        - Query parameters (top N, show N, first N, etc.)
        - Date components (day numbers in dates like "Nov 22")
        - Time periods (last N days, etc.)
        - Years
        - Small ordinals/conversational numbers
        """
        # Match numbers with optional commas (e.g., 1,234,567)
        pattern = r'\b(\d{1,3}(?:,\d{3})*|\d+)\b'
        matches = re.findall(pattern, text)
        
        numbers = []
        text_lower = text.lower()
        
        for match in matches:
            # Remove commas and convert to int
            try:
                num = int(match.replace(',', ''))
                
                # Filter out likely non-data numbers:
                
                # 1. Years (1900-2100) - these are likely years, not data
                if 1900 <= num <= 2100:
                    continue
                
                # 2. Time period numbers (e.g., "last 30 days", "past 7 days", "next 5 weeks")
                time_patterns = [
                    rf'\blast\s+{num}\s+(days?|weeks?|months?|years?)\b',
                    rf'\bpast\s+{num}\s+(days?|weeks?|months?|years?)\b',
                    rf'\bnext\s+{num}\s+(days?|weeks?|months?|years?)\b',
                    rf'\b{num}\s+(days?|weeks?|months?|years?)\s+ago\b',
                    rf'\b{num}\s+(days?|weeks?|months?|years?)\s+back\b',
                ]
                if any(re.search(pattern, text_lower) for pattern in time_patterns):
                    continue
                
                # 3. Query parameter numbers (top N, show N, first N, etc.)
                query_param_patterns = [
                    rf'\btop\s+{num}\b',
                    rf'\bfirst\s+{num}\b',
                    rf'\bshow\s+{num}\b',
                    rf'\bdisplay\s+{num}\b',
                    rf'\blist\s+{num}\b',
                    rf'\bget\s+{num}\b',
                    rf'\bfetch\s+{num}\b',
                    rf'\b{num}\s+results?\b',
                    rf'\b{num}\s+items?\b',
                    rf'\b{num}\s+records?\b',
                    rf'\b{num}\s+users?\b',
                    rf'\b{num}\s+modules?\b',
                ]
                if any(re.search(pattern, text_lower) for pattern in query_param_patterns):
                    continue
                
                # 4. Date components - day numbers in dates (e.g., "Nov 22, 2025", "22nd", "22/12/2025")
                # Check if number appears near month names or date patterns
                date_patterns = [
                    rf'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+{num}\b',
                    rf'\b{num}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b',
                    rf'\b{num}(?:st|nd|rd|th)?\s*[,/]\s*\d{{1,2}}',  # "22, 2025" or "22/12"
                    rf'\b{num}(?:st|nd|rd|th)?\s*[,/]\s*\d{{4}}',  # "22, 2025" or "22/2025"
                    rf'\b\d{{1,2}}[/-]{num}[/-]\d{{2,4}}',  # "12/22/2025" or "12-22-2025"
                ]
                if any(re.search(pattern, text_lower) for pattern in date_patterns):
                    continue
                
                # 5. Small numbers (1-31) that could be dates, ordinals, or query parameters
                # Only validate numbers > 31 as they're more likely to be actual data values
                if num <= 31:
                    continue
                
                # Only include numbers that are likely to be actual data:
                # - Numbers > 31 (unless they're years, time periods, query params, or dates)
                numbers.append(num)
            except ValueError:
                continue
        
        return numbers
    
    @classmethod
    def _build_valid_numbers_set(cls, query_results: Dict[str, Any]) -> set:
        """
        Build a set of all valid numbers that could appear in a response.
        
        Includes:
        - Total count
        - Aggregation values
        - Bucket counts
        - Calculated percentages/rates
        """
        valid = set()
        
        # Add total
        total = cls._safe_int(query_results.get('total', 0)) or 0
        valid.add(total)
        
        # Add count of returned results
        count = cls._safe_int(query_results.get('count', 0)) or 0
        valid.add(count)
        
        # Process aggregations
        aggregations = query_results.get('aggregations', {})
        cls._extract_numbers_from_aggregations(aggregations, valid, total)
        
        # Add potential calculated values
        cls._add_calculated_values(valid, total, aggregations)
        
        return valid
    
    @classmethod
    def _extract_numbers_from_aggregations(
        cls,
        aggregations: Dict[str, Any],
        valid: set,
        total: int
    ):
        """Recursively extract numbers from aggregation results."""
        for agg_name, agg_value in aggregations.items():
            if isinstance(agg_value, dict):
                # Cardinality/value
                if 'value' in agg_value:
                    numeric_value = cls._safe_int(agg_value.get('value'))
                    if numeric_value is not None:
                        valid.add(numeric_value)
                
                # Doc count
                if 'doc_count' in agg_value:
                    doc_value = cls._safe_int(agg_value.get('doc_count'))
                    if doc_value is not None:
                        valid.add(doc_value)
                
                # Buckets
                if 'buckets' in agg_value:
                    buckets = agg_value['buckets']
                    # Handle both list and dict formats for buckets
                    if isinstance(buckets, dict):
                        # If buckets is a dict, convert to list of bucket objects
                        bucket_list = list(buckets.values())
                    elif isinstance(buckets, list):
                        # If buckets is a list, use it directly
                        bucket_list = buckets
                    else:
                        bucket_list = []
                    
                    valid.add(len(bucket_list))  # Number of buckets (already int)
                    for bucket in bucket_list:
                        # Skip if bucket is not a dict (shouldn't happen, but be safe)
                        if not isinstance(bucket, dict):
                            continue
                        if 'doc_count' in bucket:
                            bucket_doc = cls._safe_int(bucket.get('doc_count'))
                            if bucket_doc is not None:
                                valid.add(bucket_doc)
                        # Nested aggregations in bucket
                        for key, val in bucket.items():
                            if isinstance(val, dict):
                                # Extract value (for metrics like completion_rate, assigned, etc.)
                                if 'value' in val:
                                    value = val.get('value')
                                    # Handle both int and float values (completion rates are floats)
                                    numeric = cls._safe_int(value)
                                    if numeric is not None:
                                        valid.add(numeric)
                                        # For percentages/rates, also add rounded versions
                                        if isinstance(value, float) and 0 <= value <= 100:
                                            valid.add(int(round(value)))
                                            valid.add(int(round(value * 10) / 10))  # One decimal place
                                # Recurse into nested aggregations (e.g., completed.completed_count.value)
                                nested_aggs = {k: v for k, v in val.items() 
                                             if isinstance(v, dict) and k != 'value'}
                                if nested_aggs:
                                    cls._extract_numbers_from_aggregations(nested_aggs, valid, total)
                
                # Stats
                for stat in ['min', 'max', 'avg', 'sum', 'count']:
                    if stat in agg_value and agg_value[stat] is not None:
                        numeric_stat = cls._safe_int(agg_value.get(stat))
                        if numeric_stat is not None:
                            valid.add(numeric_stat)
                
                # Recurse into nested aggregations
                nested = {k: v for k, v in agg_value.items() 
                         if isinstance(v, dict) and k not in ['buckets']}
                if nested:
                    cls._extract_numbers_from_aggregations(nested, valid, total)
            
            elif isinstance(agg_value, (int, float)):
                numeric_value = cls._safe_int(agg_value)
                if numeric_value is not None:
                    valid.add(numeric_value)
    
    @classmethod
    def _add_calculated_values(cls, valid: set, total: int, aggregations: Dict[str, Any]):
        """Add commonly calculated values (percentages, rates) to valid set."""
        if total is None or total == 0:
            return
        
        # For each numeric value, calculate what percentage of total it represents
        for num in list(valid):
            if isinstance(num, (int, float)) and num > 0 and num <= total:
                # Calculate percentage
                pct = round((num / total) * 100, 1)
                valid.add(int(pct))
                valid.add(int(round(pct)))
        
        # Calculate completion rates if we have completed count
        completed_count = None
        for agg_name, agg_value in aggregations.items():
            if 'completed' in agg_name.lower() or 'complete' in agg_name.lower():
                if isinstance(agg_value, dict):
                    if 'value' in agg_value:
                        completed_count = cls._safe_int(agg_value.get('value'))
                    elif 'doc_count' in agg_value:
                        completed_count = cls._safe_int(agg_value.get('doc_count'))
        
        if completed_count is not None and total > 0:
            rate = round((completed_count / total) * 100, 1)
            valid.add(int(rate))
            valid.add(int(round(rate)))
    
    @classmethod
    def _is_valid_number(cls, num: int, valid_numbers: set, tolerance: float) -> bool:
        """
        Check if a number is valid.
        
        Allows for:
        - Exact matches
        - Small rounding differences
        - Year numbers (2020-2030)
        """
        # Exact match
        if num in valid_numbers:
            return True
        
        # Year numbers are always valid
        if 2020 <= num <= 2030:
            return True
        
        # Small numbers (likely ordinals, percentages, or counts)
        if num <= 100 and num in valid_numbers:
            return True
        
        # Check with tolerance for rounded values
        for valid in valid_numbers:
            if valid > 0:
                diff = abs(num - valid) / valid
                if diff <= tolerance:
                    return True
        
        return False
    
    @classmethod
    def _validate_percentages(cls, message: str, query_results: Dict[str, Any]) -> List[str]:
        """Validate percentage calculations in the message."""
        issues = []
        
        # Find percentage patterns
        pct_pattern = r'(\d+(?:\.\d+)?)\s*%'
        percentages = re.findall(pct_pattern, message)
        
        if not percentages:
            return issues
        
        # Get total and relevant counts
        total = cls._safe_int(query_results.get('total', 0)) or 0
        if total == 0:
            return issues
        
        aggregations = query_results.get('aggregations', {})
        
        # Build list of valid percentages
        valid_percentages = set()
        
        for agg_name, agg_value in aggregations.items():
            if isinstance(agg_value, dict):
                count = None
                if 'value' in agg_value:
                    count = cls._safe_int(agg_value.get('value'))
                elif 'doc_count' in agg_value:
                    count = cls._safe_int(agg_value.get('doc_count'))
                    # Also check nested
                    for k, v in agg_value.items():
                        if isinstance(v, dict) and 'value' in v:
                            nested_count = cls._safe_int(v.get('value'))
                            if nested_count is not None and nested_count <= total:
                                pct = round((nested_count / total) * 100, 1)
                                valid_percentages.add(pct)
                                valid_percentages.add(round(pct))
                
                if count is not None and count <= total:
                    pct = round((count / total) * 100, 1)
                    valid_percentages.add(pct)
                    valid_percentages.add(round(pct))
        
        # Check each percentage in message
        for pct_str in percentages:
            try:
                pct = float(pct_str)
                # Check if this percentage is valid (with 1% tolerance)
                is_valid = any(
                    abs(pct - valid) <= 1.0
                    for valid in valid_percentages
                )
                if not is_valid and pct not in [0, 100]:  # 0% and 100% are always potentially valid
                    issues.append(f"Percentage {pct}% may not be accurate based on query results")
            except ValueError:
                continue
        
        return issues
    
    @classmethod
    def create_safe_response(
        cls,
        query_results: Dict[str, Any],
        response_type: str,
        time_context: Optional[str] = None
    ) -> str:
        """
        Create a guaranteed-safe response using only query result data.
        Use this as a fallback when LLM response fails validation.
        
        Args:
            query_results: Actual query results
            response_type: Type of response expected
            time_context: Time period description
            
        Returns:
            A safe, accurate response message
        """
        total = query_results.get('total', 0)
        aggregations = query_results.get('aggregations', {})
        results = query_results.get('results', [])
        
        # For chart/ranking queries, aggregations are the primary data
        is_chart_query = response_type in ['bar_chart', 'pie_chart', 'line_chart'] and len(results) == 0 and aggregations
        
        if is_chart_query:
            # Extract top result from aggregations
            for agg_name, agg_value in aggregations.items():
                if isinstance(agg_value, dict) and 'buckets' in agg_value:
                    buckets = agg_value.get('buckets', [])
                    # Handle both list and dict formats for buckets
                    if isinstance(buckets, dict):
                        # If buckets is a dict, convert to list of bucket objects
                        bucket_list = list(buckets.values())
                    elif isinstance(buckets, list):
                        # If buckets is a list, use it directly
                        bucket_list = buckets
                    else:
                        bucket_list = []
                    
                    if bucket_list and isinstance(bucket_list[0], dict):
                        top_bucket = bucket_list[0]
                        key = top_bucket.get('key', 'Unknown')
                        # Get count from nested cardinality or doc_count
                        count = top_bucket.get('doc_count', 0)
                        for nested_key, nested_value in top_bucket.items():
                            if nested_key not in ['key', 'doc_count'] and isinstance(nested_value, dict):
                                if 'value' in nested_value:
                                    count = nested_value['value']
                                    break
                        
                        # Check for user info in top_hits (check multiple possible names)
                        for top_hits_name in ['user_info', 'user_details', 'top_hits']:
                            if top_hits_name in top_bucket and isinstance(top_bucket[top_hits_name], dict):
                                hits = top_bucket[top_hits_name].get('hits', {}).get('hits', [])
                                if hits:
                                    source = hits[0].get('_source', {})
                                    first_name = source.get('first_name', '')
                                    last_name = source.get('last_name', '')
                                    email = source.get('email_addr', key)
                                    if first_name or last_name:
                                        key = f"{first_name} {last_name}".strip() or email
                                        break
                        
                        # Always show the key (email) even if no name found
                        if '@' in str(key):
                            # Key is already an email, use it as-is
                            pass
                        
                        # Detect if this is a user query (key is email or agg_name contains 'user')
                        is_user_query = '@' in str(key) or 'user' in agg_name.lower() or 'by_user' in agg_name.lower()
                        
                        # Ensure count is an integer (fallback to 0 if None)
                        count_int = cls._safe_int(count) or 0
                        
                        if is_user_query and 'unique_modules' in str(top_bucket):
                            return f"{key} has completed the most modules with {count_int:,} unique modules completed."
                        elif is_user_query:
                            return f"{key} has the highest count with {count_int:,}."
                        else:
                            return f"Top result: {key} with {count_int:,}."
        
        parts = [f"Found {total:,} records"]
        
        if time_context:
            parts[0] += f" {time_context}"
        
        # Add aggregation summaries
        for agg_name, agg_value in aggregations.items():
            if isinstance(agg_value, dict):
                if 'value' in agg_value:
                    value = cls._safe_int(agg_value.get('value')) or 0
                    label = agg_value.get('_label', cls._format_label(agg_name))
                    parts.append(f"{label}: {value:,}")
                elif 'buckets' in agg_value:
                    bucket_count = len(agg_value['buckets'])
                    label = agg_value.get('_label', cls._format_label(agg_name))
                    parts.append(f"{label}: {bucket_count} categories")
                elif 'doc_count' in agg_value:
                    count = cls._safe_int(agg_value.get('doc_count')) or 0
                    label = agg_value.get('_label', cls._format_label(agg_name))
                    parts.append(f"{label}: {count:,}")
        
        return ". ".join(parts) + "."
    
    @classmethod
    def _format_label(cls, agg_name: str) -> str:
        """Convert aggregation name to readable label."""
        return agg_name.replace('_', ' ').title()

