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
        """Extract all numbers from text, handling comma-formatted numbers."""
        # Match numbers with optional commas (e.g., 1,234,567)
        pattern = r'\b(\d{1,3}(?:,\d{3})*|\d+)\b'
        matches = re.findall(pattern, text)
        
        numbers = []
        for match in matches:
            # Remove commas and convert to int
            try:
                num = int(match.replace(',', ''))
                # Filter out likely non-data numbers:
                # - Years (1900-2100) - these are likely years, not data
                # - Small numbers (1-10) - these are likely ordinals, conversational, or not meaningful data
                # Only include numbers that are likely to be actual data:
                # - Numbers > 10 (unless they're years)
                # - Years outside normal range (< 1900 or > 2100)
                if 1900 <= num <= 2100:
                    # This is likely a year, skip it
                    continue
                elif num > 10:
                    # This is likely data (count, ID, etc.)
                    numbers.append(num)
                # else: num is 1-10, skip it (likely ordinal or conversational)
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
        total = query_results.get('total', 0)
        valid.add(total)
        
        # Add count of returned results
        count = query_results.get('count', 0)
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
                    valid.add(int(agg_value['value']))
                
                # Doc count
                if 'doc_count' in agg_value:
                    valid.add(int(agg_value['doc_count']))
                
                # Buckets
                if 'buckets' in agg_value:
                    buckets = agg_value['buckets']
                    valid.add(len(buckets))  # Number of buckets
                    for bucket in buckets:
                        if 'doc_count' in bucket:
                            valid.add(int(bucket['doc_count']))
                        # Nested aggregations in bucket
                        for key, val in bucket.items():
                            if isinstance(val, dict) and 'value' in val:
                                valid.add(int(val['value']))
                
                # Stats
                for stat in ['min', 'max', 'avg', 'sum', 'count']:
                    if stat in agg_value and agg_value[stat] is not None:
                        try:
                            valid.add(int(agg_value[stat]))
                        except (ValueError, TypeError):
                            pass
                
                # Recurse into nested aggregations
                nested = {k: v for k, v in agg_value.items() 
                         if isinstance(v, dict) and k not in ['buckets']}
                if nested:
                    cls._extract_numbers_from_aggregations(nested, valid, total)
            
            elif isinstance(agg_value, (int, float)):
                valid.add(int(agg_value))
    
    @classmethod
    def _add_calculated_values(cls, valid: set, total: int, aggregations: Dict[str, Any]):
        """Add commonly calculated values (percentages, rates) to valid set."""
        if total == 0:
            return
        
        # For each numeric value, calculate what percentage of total it represents
        for num in list(valid):
            if num > 0 and num <= total:
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
                        completed_count = int(agg_value['value'])
                    elif 'doc_count' in agg_value:
                        completed_count = int(agg_value['doc_count'])
        
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
        total = query_results.get('total', 0)
        if total == 0:
            return issues
        
        aggregations = query_results.get('aggregations', {})
        
        # Build list of valid percentages
        valid_percentages = set()
        
        for agg_name, agg_value in aggregations.items():
            if isinstance(agg_value, dict):
                count = None
                if 'value' in agg_value:
                    count = int(agg_value['value'])
                elif 'doc_count' in agg_value:
                    count = int(agg_value['doc_count'])
                    # Also check nested
                    for k, v in agg_value.items():
                        if isinstance(v, dict) and 'value' in v:
                            nested_count = int(v['value'])
                            if nested_count <= total:
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
                    if buckets:
                        top_bucket = buckets[0]
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
                        
                        if is_user_query and 'unique_modules' in str(top_bucket):
                            return f"{key} has completed the most modules with {int(count):,} unique modules completed."
                        elif is_user_query:
                            return f"{key} has the highest count with {int(count):,}."
                        else:
                            return f"Top result: {key} with {int(count):,}."
        
        parts = [f"Found {total:,} records"]
        
        if time_context:
            parts[0] += f" {time_context}"
        
        # Add aggregation summaries
        for agg_name, agg_value in aggregations.items():
            if isinstance(agg_value, dict):
                if 'value' in agg_value:
                    value = int(agg_value['value'])
                    label = agg_value.get('_label', cls._format_label(agg_name))
                    parts.append(f"{label}: {value:,}")
                elif 'buckets' in agg_value:
                    bucket_count = len(agg_value['buckets'])
                    label = agg_value.get('_label', cls._format_label(agg_name))
                    parts.append(f"{label}: {bucket_count} categories")
                elif 'doc_count' in agg_value:
                    count = int(agg_value['doc_count'])
                    label = agg_value.get('_label', cls._format_label(agg_name))
                    parts.append(f"{label}: {count:,}")
        
        return ". ".join(parts) + "."
    
    @classmethod
    def _format_label(cls, agg_name: str) -> str:
        """Convert aggregation name to readable label."""
        return agg_name.replace('_', ' ').title()

