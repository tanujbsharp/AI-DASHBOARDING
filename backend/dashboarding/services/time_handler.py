"""
Smart Time Period Handler for AI Reports Bot.
Intelligently interprets time references in user queries.

Aligned with usecase.md Section C.2, C.3, and Section D - Date Field Selection.

Rules:
- Month only (e.g., "November") → Assume current year
- No time specified → Entire lifetime (all data)
- Relative terms (e.g., "last month") → Calculate dynamically
- Use correct date field based on event type
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple
import re
from dataclasses import dataclass


@dataclass
class TimePeriod:
    """Represents a resolved time period."""
    start_timestamp: int
    end_timestamp: int
    description: str
    is_lifetime: bool = False
    # If true, end_timestamp is inclusive and range queries should use lte instead of lt.
    end_inclusive: bool = False
    
    def to_range_query(self, field: str = "created_on") -> Dict:
        """Convert to OpenSearch range query."""
        if self.is_lifetime:
            return {}  # No filter for lifetime
        end_key = "lte" if self.end_inclusive else "lt"
        return {
            "range": {
                field: {
                    "gte": self.start_timestamp,
                    end_key: self.end_timestamp
                }
            }
        }


class TimeHandler:
    """
    Intelligent time period parser.
    Aligned with usecase.md Sections C.2, C.3, and D.
    """
    
    MONTHS = {
        'january': 1, 'jan': 1,
        'february': 2, 'feb': 2,
        'march': 3, 'mar': 3,
        'april': 4, 'apr': 4,
        'may': 5,
        'june': 6, 'jun': 6,
        'july': 7, 'jul': 7,
        'august': 8, 'aug': 8,
        'september': 9, 'sep': 9, 'sept': 9,
        'october': 10, 'oct': 10,
        'november': 11, 'nov': 11,
        'december': 12, 'dec': 12,
    }
    
    # Date field hints - aligned with usecase.md Section D
    # Maps keywords to the appropriate date field
    FIELD_HINTS = {
        # COMPLETIONS → completed_date
        'completed_date': [
            'completion', 'completions', 'completed', 'finished', 'done', 
            'passed', 'finish', 'complete'
        ],
        # MODULE PUBLISHING → published_date
        'published_date': [
            'published', 'launch', 'launched', 'release', 'released',
            'go live', 'go-live', 'live', 'deploy', 'deployed'
        ],
        # MODULE CREATION → module_created_on
        'module_created_on': [
            'module created', 'module built', 'course created', 
            'new module', 'training created'
        ],
        # MODULE UPDATE → module_updated_on
        'module_updated_on': [
            'module updated', 'module modified', 'course updated'
        ],
        # INVITES/SENDS → invited_date (Section C.5: sent/delivered/pushed)
        'invited_date': [
            'invited', 'invitation', 'invite', 'sent', 'delivered', 
            'pushed', 'notification', 'notified'
        ],
        # USER CREATION → user_created_on
        'user_created_on': [
            'user created', 'account created', 'signup', 'signed up',
            'registered', 'registration'
        ],
        # HIRE DATE → hired_on
        'hired_on': [
            'hired', 'hire date', 'joined', 'joining', 'start date',
            'onboarded', 'onboarding'
        ],
        # RECORD CREATION/ASSIGNMENT → created_on (default)
        'created_on': [
            'assigned', 'enrolled', 'record created', 'allocated',
            'assignment', 'enrollment'
        ],
        # RECORD UPDATE → updated_on
        'updated_on': [
            'updated', 'modified', 'changed', 'last updated'
        ],
    }
    
    @classmethod
    def parse(cls, message: str) -> TimePeriod:
        """
        Parse time period from user message.
        
        Rules (aligned with usecase.md Section C.2 and C.3):
        1. If year + month mentioned → use that specific month
        2. If only month mentioned → assume current year
        3. If relative term (last week, this month) → calculate
        4. If no time mentioned → return lifetime (all data)
        """
        message_lower = message.lower()
        
        # Try explicit custom date ranges first (e.g., "from Oct 1 2025 to Dec 11 2025")
        explicit_range = cls._parse_explicit_date_range(message)
        if explicit_range:
            return explicit_range
        now = datetime.now(timezone.utc)
        
        # Check for "all time", "ever", "lifetime" - explicit lifetime request
        if any(term in message_lower for term in ['all time', 'ever', 'lifetime', 'entire', 'all data', 'total ever']):
            return cls._lifetime()
        
        # Pattern 1: Year with month (e.g., "November 2024", "2024 November")
        year_month = cls._parse_year_month(message_lower)
        if year_month:
            return year_month
        
        # Pattern 2: Only month mentioned (e.g., "in November", "for December")
        month_only = cls._parse_month_only(message_lower, now)
        if month_only:
            return month_only
        
        # Pattern 3: Only year mentioned (e.g., "in 2024", "for 2025")
        year_only = cls._parse_year_only(message_lower)
        if year_only:
            return year_only
        
        # Pattern 4: Relative time (e.g., "last month", "this week", "last 30 days")
        relative = cls._parse_relative_time(message_lower, now)
        if relative:
            return relative
        
        # Pattern 5: No time mentioned → return LIFETIME (all data)
        return cls._lifetime()
    
    @classmethod
    def detect_time_field_hint(cls, message: str) -> Optional[str]:
        """
        Infer which date field the user is referencing based on keywords.
        Implements usecase.md Section D - Picking the Right Date Field.
        
        Returns:
            The appropriate date field name, or None if not determinable
        """
        message_lower = message.lower()
        
        # Check each field and its keywords
        for field_name, keywords in cls.FIELD_HINTS.items():
            for keyword in keywords:
                if keyword in message_lower:
                    return field_name
        
        return None
    
    @classmethod
    def get_default_time_field(cls, message: str) -> str:
        """
        Get the default time field for a query based on context.
        Falls back to 'created_on' if no specific field detected.
        
        Args:
            message: User's query message
            
        Returns:
            The appropriate date field name
        """
        detected = cls.detect_time_field_hint(message)
        return detected if detected else 'created_on'
    
    @classmethod
    def _parse_explicit_date_range(cls, message: str) -> Optional[TimePeriod]:
        """Parse expressions like 'from Oct 1 2025 to Dec 11 2025' or 'since Oct 1 2025'."""
        now = datetime.now(timezone.utc)
        
        # First try "since [date]" (single date, means from that date until now)
        since_patterns = [
            # "since oct 1 2025", "since October 1, 2025"
            r'\bsince\s+([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\s*,?\s*(\d{4})\b',
            # "since 2025-10-01"
            r'\bsince\s+(\d{4})-(\d{1,2})-(\d{1,2})\b',
            # "after oct 1 2025"
            r'\bafter\s+([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\s*,?\s*(\d{4})\b',
        ]
        
        for pattern in since_patterns:
            match = re.search(pattern, message, flags=re.IGNORECASE)
            if match:
                groups = match.groups()
                start_dt = None
                
                if len(groups) == 3:
                    # Check if it's Month Day Year or Year Month Day format
                    if groups[0].isalpha():
                        # Month Day Year (e.g., "oct 1 2025")
                        month_str, day_str, year_str = groups
                        month_num = cls.MONTHS.get(month_str.lower())
                        if month_num:
                            try:
                                start_dt = datetime(int(year_str), month_num, int(day_str), tzinfo=timezone.utc)
                            except ValueError:
                                pass
                    else:
                        # Year Month Day (e.g., "2025-10-01")
                        year_str, month_str, day_str = groups
                        try:
                            start_dt = datetime(int(year_str), int(month_str), int(day_str), tzinfo=timezone.utc)
                        except ValueError:
                            pass
                
                if start_dt:
                    return TimePeriod(
                        start_timestamp=int(start_dt.timestamp()),
                        end_timestamp=int(now.timestamp()),
                        description=f"since {start_dt.strftime('%b %d, %Y')}",
                        is_lifetime=False
                    )
        
        # Handle "since <month> [year]" expressions (no specific day)
        month_since_match = re.search(r'\bsince\s+([A-Za-z]+)(?:\s+(20\d{2}))?\b', message, flags=re.IGNORECASE)
        if month_since_match:
            month_name = month_since_match.group(1)
            year_str = month_since_match.group(2)
            month_num = cls.MONTHS.get(month_name.lower())
            if month_num:
                year = int(year_str) if year_str else now.year
                start_dt = datetime(year, month_num, 1, tzinfo=timezone.utc)
                return TimePeriod(
                    start_timestamp=int(start_dt.timestamp()),
                    end_timestamp=int(now.timestamp()),
                    description=f"since {start_dt.strftime('%b %Y')}",
                    is_lifetime=False
                )
        
        # Then try full date ranges "from X to Y"
        range_patterns = [
            r'\b(from|between)\s+([A-Za-z0-9,\s/-]+?)\s+(?:to|until|through|-)\s+([A-Za-z0-9,\s/-]+)',
            r'\b([A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?\s*,?\s*\d{4})\s+(?:to|until|through|-)\s+([A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?\s*,?\s*\d{4})'
        ]
        
        for pattern in range_patterns:
            match = re.search(pattern, message, flags=re.IGNORECASE)
            if not match:
                continue
            
            groups = match.groups()
            if len(groups) >= 2:
                start_str = groups[-2]
                end_str = groups[-1]
            else:
                continue
            
            start_dt = cls._parse_explicit_date_string(start_str)
            end_dt = cls._parse_explicit_date_string(end_str)
            
            if start_dt and end_dt:
                if end_dt <= start_dt:
                    # Ensure end is after start by pushing end to end of day
                    end_dt = end_dt.replace(hour=23, minute=59, second=59)
                    if end_dt <= start_dt:
                        continue
                
                return TimePeriod(
                    start_timestamp=int(start_dt.timestamp()),
                    end_timestamp=int(end_dt.timestamp()),
                    description=f"{start_dt.strftime('%b %d %Y')} to {end_dt.strftime('%b %d %Y')}",
                    is_lifetime=False
                )
        return None
    
    @classmethod
    def _parse_explicit_date_string(cls, value: str) -> Optional[datetime]:
        """Parse a free-form date string into datetime."""
        value_clean = value.strip().replace(',', ' ')
        value_clean = re.sub(r'(\d{1,2})(st|nd|rd|th)', r'\1', value_clean, flags=re.IGNORECASE)
        value_clean = re.sub(r'\s+', ' ', value_clean)
        
        formats = [
            '%b %d %Y', '%B %d %Y', '%d %b %Y', '%d %B %Y',
            '%Y-%m-%d', '%m/%d/%Y'
        ]
        for fmt in formats:
            try:
                return datetime.strptime(value_clean, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None
    
    @classmethod
    def _lifetime(cls) -> TimePeriod:
        """Return a lifetime period (all data)."""
        return TimePeriod(
            start_timestamp=0,
            end_timestamp=int(datetime.now(timezone.utc).timestamp()),
            description="all time",
            is_lifetime=True
        )
    
    @classmethod
    def _parse_year_month(cls, message: str) -> Optional[TimePeriod]:
        """
        Parse "November 2024" or "2024 November" patterns.
        Aligned with usecase.md Section C.2 - Month/date normalization.
        """
        # Pattern: Month Year
        for month_name, month_num in cls.MONTHS.items():
            pattern = rf'\b{month_name}\s+(\d{{4}})\b'
            match = re.search(pattern, message)
            if match:
                year = int(match.group(1))
                return cls._month_period(month_num, year)
        
        # Pattern: Year Month
        for month_name, month_num in cls.MONTHS.items():
            pattern = rf'\b(\d{{4}})\s+{month_name}\b'
            match = re.search(pattern, message)
            if match:
                year = int(match.group(1))
                return cls._month_period(month_num, year)
        
        # Pattern: MM/YYYY or MM-YYYY (e.g., "11/2025", "2025-11")
        mm_yyyy_match = re.search(r'\b(\d{1,2})[/-](\d{4})\b', message)
        if mm_yyyy_match:
            month = int(mm_yyyy_match.group(1))
            year = int(mm_yyyy_match.group(2))
            if 1 <= month <= 12:
                return cls._month_period(month, year)
        
        # Pattern: YYYY-MM (e.g., "2025-11")
        yyyy_mm_match = re.search(r'\b(\d{4})[/-](\d{1,2})\b', message)
        if yyyy_mm_match:
            year = int(yyyy_mm_match.group(1))
            month = int(yyyy_mm_match.group(2))
            if 1 <= month <= 12:
                return cls._month_period(month, year)
        
        return None
    
    @classmethod
    def _parse_month_only(cls, message: str, now: datetime) -> Optional[TimePeriod]:
        """
        Parse month-only mentions.
        RULE (usecase.md C.2): If only month is mentioned, assume CURRENT YEAR.
        """
        for month_name, month_num in cls.MONTHS.items():
            # Look for standalone month mentions
            patterns = [
                rf'\bin\s+{month_name}\b',      # "in November"
                rf'\bfor\s+{month_name}\b',     # "for November"
                rf'\bduring\s+{month_name}\b',  # "during November"
                rf'\b{month_name}\b',           # just "November"
            ]
            for pattern in patterns:
                if re.search(pattern, message):
                    # Check if a year is NOT mentioned nearby
                    if not re.search(r'\b20\d{2}\b', message):
                        # No year mentioned - use current year
                        return cls._month_period(month_num, now.year)
        
        return None
    
    @classmethod
    def _parse_year_only(cls, message: str) -> Optional[TimePeriod]:
        """Parse year-only mentions (e.g., "in 2024")."""
        patterns = [
            r'\bin\s+(20\d{2})\b',
            r'\bfor\s+(20\d{2})\b',
            r'\bduring\s+(20\d{2})\b',
            r'\byear\s+(20\d{2})\b',
        ]
        for pattern in patterns:
            match = re.search(pattern, message)
            if match:
                year = int(match.group(1))
                return cls._year_period(year)
        
        # Also check for standalone year if it's a reasonable query
        year_match = re.search(r'\b(20\d{2})\b', message)
        if year_match:
            # Make sure no month is mentioned (would be handled by year_month)
            has_month = any(m in message for m in cls.MONTHS.keys())
            if not has_month:
                year = int(year_match.group(1))
                return cls._year_period(year)
        
        return None
    
    @classmethod
    def _parse_relative_time(cls, message: str, now: datetime) -> Optional[TimePeriod]:
        """
        Parse relative time expressions.
        Aligned with usecase.md Section C.3 - Relative time normalization.
        """
        
        # "last N days/weeks/months"
        last_n_match = re.search(r'\blast\s+(\d+)\s+(days?|weeks?|months?)\b', message)
        if last_n_match:
            n = int(last_n_match.group(1))
            unit = last_n_match.group(2).rstrip('s')
            
            if unit == 'day':
                # Interpret "last N days" as N calendar days ending today (inclusive),
                # so charts can reliably render exactly N daily buckets.
                # Example (now = Dec 16): last 30 days => start at Nov 17 00:00 UTC, end at Dec 17 00:00 UTC (exclusive).
                start = (now - timedelta(days=max(n - 1, 0))).replace(hour=0, minute=0, second=0, microsecond=0)
                end = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            elif unit == 'week':
                start = now - timedelta(weeks=n)
                end = now
            elif unit == 'month':
                # Interpret "last N months" as the previous N FULL calendar months (excluding current partial month).
                # Example (now = Dec 16): last 2 months => Oct 1 00:00 UTC through Nov 30 23:59:59 UTC.
                first_of_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                end = first_of_this_month - timedelta(seconds=1)  # end of previous day (inclusive)

                month = first_of_this_month.month - n
                year = first_of_this_month.year
                while month <= 0:
                    month += 12
                    year -= 1
                start = datetime(year, month, 1, tzinfo=timezone.utc)
            
            return TimePeriod(
                start_timestamp=int(start.timestamp()),
                end_timestamp=int(end.timestamp()),
                description=f"last {n} {unit}{'s' if n > 1 else ''}",
                end_inclusive=(unit == 'month')
            )
        
        # "last month" (previous calendar month)
        if 'last month' in message:
            first_of_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            # End of last month (inclusive)
            end = first_of_this_month - timedelta(seconds=1)
            if now.month == 1:
                start = datetime(now.year - 1, 12, 1, tzinfo=timezone.utc)
            else:
                start = datetime(now.year, now.month - 1, 1, tzinfo=timezone.utc)
            return TimePeriod(
                start_timestamp=int(start.timestamp()),
                end_timestamp=int(end.timestamp()),
                description=start.strftime("%B %Y"),
                end_inclusive=True
            )
        
        # "this month" (current calendar month)
        if 'this month' in message:
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            return TimePeriod(
                start_timestamp=int(start.timestamp()),
                end_timestamp=int(now.timestamp()),
                description=f"this month ({now.strftime('%B %Y')})"
            )
        
        # "last week"
        if 'last week' in message:
            start = now - timedelta(days=7)
            return TimePeriod(
                start_timestamp=int(start.timestamp()),
                end_timestamp=int(now.timestamp()),
                description="last 7 days"
            )
        
        # "this week"
        if 'this week' in message:
            start = now - timedelta(days=now.weekday())
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
            return TimePeriod(
                start_timestamp=int(start.timestamp()),
                end_timestamp=int(now.timestamp()),
                description="this week"
            )
        
        # "this year" / "YTD"
        if 'this year' in message or 'ytd' in message:
            start = datetime(now.year, 1, 1, tzinfo=timezone.utc)
            return TimePeriod(
                start_timestamp=int(start.timestamp()),
                end_timestamp=int(now.timestamp()),
                description=f"year {now.year}"
            )
        
        # "last year"
        if 'last year' in message:
            start = datetime(now.year - 1, 1, 1, tzinfo=timezone.utc)
            end = datetime(now.year, 1, 1, tzinfo=timezone.utc)
            return TimePeriod(
                start_timestamp=int(start.timestamp()),
                end_timestamp=int(end.timestamp()),
                description=f"year {now.year - 1}"
            )
        
        # "this quarter"
        if 'this quarter' in message:
            quarter = (now.month - 1) // 3
            quarter_start_month = quarter * 3 + 1
            start = datetime(now.year, quarter_start_month, 1, tzinfo=timezone.utc)
            return TimePeriod(
                start_timestamp=int(start.timestamp()),
                end_timestamp=int(now.timestamp()),
                description=f"Q{quarter + 1} {now.year}"
            )
        
        # "last quarter"
        if 'last quarter' in message:
            quarter = (now.month - 1) // 3
            if quarter == 0:
                # Previous quarter is Q4 of last year
                start = datetime(now.year - 1, 10, 1, tzinfo=timezone.utc)
                end = datetime(now.year, 1, 1, tzinfo=timezone.utc)
                desc = f"Q4 {now.year - 1}"
            else:
                prev_quarter_start = (quarter - 1) * 3 + 1
                start = datetime(now.year, prev_quarter_start, 1, tzinfo=timezone.utc)
                end = datetime(now.year, quarter * 3 + 1, 1, tzinfo=timezone.utc)
                desc = f"Q{quarter} {now.year}"
            return TimePeriod(
                start_timestamp=int(start.timestamp()),
                end_timestamp=int(end.timestamp()),
                description=desc
            )
        
        # "today"
        if 'today' in message:
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return TimePeriod(
                start_timestamp=int(start.timestamp()),
                end_timestamp=int(now.timestamp()),
                description="today"
            )
        
        # "yesterday"
        if 'yesterday' in message:
            yesterday = now - timedelta(days=1)
            start = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
            end = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return TimePeriod(
                start_timestamp=int(start.timestamp()),
                end_timestamp=int(end.timestamp()),
                description="yesterday"
            )
        
        return None
    
    @classmethod
    def _month_period(cls, month: int, year: int) -> TimePeriod:
        """Create a TimePeriod for a specific month."""
        start = datetime(year, month, 1, tzinfo=timezone.utc)
        if month == 12:
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
        
        return TimePeriod(
            start_timestamp=int(start.timestamp()),
            end_timestamp=int(end.timestamp()),
            description=start.strftime("%B %Y")
        )
    
    @classmethod
    def _year_period(cls, year: int) -> TimePeriod:
        """Create a TimePeriod for a full year."""
        start = datetime(year, 1, 1, tzinfo=timezone.utc)
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        
        return TimePeriod(
            start_timestamp=int(start.timestamp()),
            end_timestamp=int(end.timestamp()),
            description=f"year {year}"
        )
    
    @classmethod
    def get_current_context(cls) -> str:
        """
        Get current date context for the LLM.
        """
        now = datetime.now(timezone.utc)
        return f"""
CURRENT DATE: {now.strftime('%B %d, %Y')}
CURRENT YEAR: {now.year}
CURRENT MONTH: {now.strftime('%B')}

TIME INTERPRETATION RULES:
1. If user says just a month (e.g., "November") → Use {now.year}
2. If user says month + year (e.g., "November 2024") → Use that specific month
3. If no time mentioned → Query ALL data (entire lifetime)
4. For relative terms: "last month" = previous calendar month, "this month" = current month so far
5. "YTD" = year to date, from January 1st to now

DATE FIELD SELECTION (use the field that matches the event):
- COMPLETIONS → use completed_date
- MODULE PUBLISHING → use published_date
- INVITES/SENDS → use invited_date
- ASSIGNMENTS/ENROLLMENTS → use created_on
- MODULE CREATION → use module_created_on
"""

