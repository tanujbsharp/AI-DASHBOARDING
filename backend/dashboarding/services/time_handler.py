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
import pytz


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
    
    DEFAULT_TIMEZONE = pytz.timezone("Asia/Kolkata")
    
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
        
        # Pattern 1: Explicit single-day reference (e.g., "Dec 18 2025")
        single_day = cls._parse_single_day(message)
        if single_day:
            return single_day
        
        # Pattern 2: Year with month (e.g., "November 2024", "2024 November")
        year_month = cls._parse_year_month(message_lower)
        if year_month:
            return year_month
        
        # Pattern 3: Only month mentioned (e.g., "in November", "for December")
        month_only = cls._parse_month_only(message_lower, now)
        if month_only:
            return month_only
        
        # Pattern 4: Only year mentioned (e.g., "in 2024", "for 2025")
        year_only = cls._parse_year_only(message_lower)
        if year_only:
            return year_only
        
        # Pattern 5: Relative time (e.g., "last month", "this week", "last 30 days")
        relative = cls._parse_relative_time(message_lower, now)
        if relative:
            return relative
        
        # Pattern 6: No time mentioned → return LIFETIME (all data)
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
    def _parse_single_day(cls, message: str) -> Optional[TimePeriod]:
        """Parse explicit single-day references and return a 24-hour TimePeriod."""
        date_patterns = [
            r'\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2}(?:st|nd|rd|th)?(?:,\s*)?\s+\d{4}\b',
            r'\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{4}\b',
            r'\b\d{4}-\d{1,2}-\d{1,2}\b',
            r'\b\d{1,2}/\d{1,2}/\d{4}\b'
        ]
        
        for pattern in date_patterns:
            match = re.search(pattern, message, flags=re.IGNORECASE)
            if not match:
                continue
            
            candidate = match.group(0)
            parsed = cls._parse_explicit_date_string(candidate)
            if not parsed:
                continue
            
            local_start = cls.DEFAULT_TIMEZONE.localize(datetime(parsed.year, parsed.month, parsed.day, 0, 0, 0))
            local_end = local_start + timedelta(days=1)
            
            start_utc = local_start.astimezone(timezone.utc)
            end_utc = local_end.astimezone(timezone.utc)
            
            return TimePeriod(
                start_timestamp=int(start_utc.timestamp()),
                end_timestamp=int(end_utc.timestamp()),
                description=local_start.strftime("%b %d %Y")
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
        tz_local = cls.DEFAULT_TIMEZONE
        local_start = tz_local.localize(datetime(year, month, 1, 0, 0, 0))
        if month == 12:
            local_end = tz_local.localize(datetime(year + 1, 1, 1, 0, 0, 0))
        else:
            local_end = tz_local.localize(datetime(year, month + 1, 1, 0, 0, 0))
        
        start = local_start.astimezone(timezone.utc)
        end = local_end.astimezone(timezone.utc)
        description = local_start.strftime("%B %Y")
        
        return TimePeriod(
            start_timestamp=int(start.timestamp()),
            end_timestamp=int(end.timestamp()),
            description=description
        )
    
    @classmethod
    def _year_period(cls, year: int) -> TimePeriod:
        """Create a TimePeriod for a full year."""
        tz_local = cls.DEFAULT_TIMEZONE
        local_start = tz_local.localize(datetime(year, 1, 1, 0, 0, 0))
        local_end = tz_local.localize(datetime(year + 1, 1, 1, 0, 0, 0))
        start = local_start.astimezone(timezone.utc)
        end = local_end.astimezone(timezone.utc)
        description = local_start.strftime("year %Y")
        
        return TimePeriod(
            start_timestamp=int(start.timestamp()),
            end_timestamp=int(end.timestamp()),
            description=description
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


@dataclass
class ResolvedTimeframe:
    """Represents a resolved timeframe with dynamic date range."""
    gte: int  # epoch seconds
    lt: int   # epoch seconds (half-open interval)
    label: str
    type: str = "RELATIVE"


class TimeframeResolver:
    """
    Resolves timeframe keys into dynamic date ranges at runtime.
    Used for dashboard widgets to keep timeframes "live" (e.g., "last week" always means the most recent week).
    """
    
    SUPPORTED_TIMEFRAMES = {
        'LAST_WEEK',
        'THIS_WEEK',
        'LAST_MONTH',
        'THIS_MONTH',
        'LAST_3_MONTHS',
        'THIS_QUARTER',
        'LAST_QUARTER',
        'THIS_YEAR',
        'LAST_YEAR'
    }
    
    FLEX_TIMEFRAMES = {
        'last_n_months': re.compile(r'\blast\s+(\d+)\s+months?\b'),
        'last_n_weeks': re.compile(r'\blast\s+(\d+)\s+weeks?\b'),
        'last_n_days': re.compile(r'\blast\s+(\d+)\s+days?\b')
    }
    
    @classmethod
    def extract_timeframe_key(cls, message: str) -> Optional[str]:
        """
        Extract a timeframe key from a user message.
        Returns None if no supported relative timeframe is detected.
        """
        message_lower = message.lower()
        
        # Map patterns to timeframe keys
        patterns = {
            'LAST_WEEK': [
                r'\blast\s+week\b',
            ],
            'THIS_WEEK': [
                r'\bthis\s+week\b',
            ],
            'LAST_MONTH': [
                r'\blast\s+month\b',
            ],
            'THIS_MONTH': [
                r'\bthis\s+month\b',
            ],
            'LAST_3_MONTHS': [
                r'\blast\s+3\s+months?\b',
                r'\blast\s+three\s+months?\b',
            ],
            'THIS_QUARTER': [
                r'\bthis\s+quarter\b',
            ],
            'LAST_QUARTER': [
                r'\blast\s+quarter\b',
            ],
            'THIS_YEAR': [
                r'\bthis\s+year\b',
                r'\bytd\b',  # Year to date
            ],
            'LAST_YEAR': [
                r'\blast\s+year\b',
            ],
        }
        
        for timeframe_key, pattern_list in patterns.items():
            for pattern in pattern_list:
                if re.search(pattern, message_lower):
                    return timeframe_key
        
        # Flexible detection for "last N months/weeks/days"
        for key, regex in cls.FLEX_TIMEFRAMES.items():
            match = regex.search(message_lower)
            if match:
                return f"{key}:{match.group(1)}"
        
        return None
    
    @classmethod
    def resolve(
        cls,
        timeframe_key: str,
        timezone_str: str = "Asia/Kolkata",
        date_mode: str = "epoch_seconds"
    ) -> Optional[ResolvedTimeframe]:
        """
        Resolve a timeframe key into a dynamic date range.
        
        Args:
            timeframe_key: One of the SUPPORTED_TIMEFRAMES (e.g., "LAST_WEEK")
            timezone_str: Timezone string (default: "Asia/Kolkata")
            date_mode: "epoch_seconds", "epoch_millis", or "date" (for OpenSearch date math)
            
        Returns:
            ResolvedTimeframe with gte, lt (half-open interval), label, and type
            When date_mode is "date", gte and lt will be OpenSearch date math expressions (strings)
        """
        if timeframe_key not in cls.SUPPORTED_TIMEFRAMES:
            # Handle dynamic keys like "last_n_months:6"
            if timeframe_key and ':' in timeframe_key:
                base_key, value = timeframe_key.split(':', 1)
                if base_key in cls.FLEX_TIMEFRAMES and value.isdigit():
                    return cls._resolve_flexible_timeframe(base_key, int(value), timezone_str, date_mode)
            return None
        
        # If date_mode is "date", use OpenSearch date math expressions
        # This allows OpenSearch to calculate dates dynamically at query time
        if date_mode == "date":
            date_math = cls._get_date_math_expressions(timeframe_key)
            if date_math:
                return ResolvedTimeframe(
                    gte=date_math['gte'],  # String like "now-3M/M"
                    lt=date_math['lt'],    # String like "now" or "now/M"
                    label=date_math['label'],
                    type="RELATIVE"
                )
        
        # Otherwise, calculate epoch timestamps
        try:
            tz = pytz.timezone(timezone_str)
        except pytz.exceptions.UnknownTimeZoneError:
            tz = pytz.timezone("Asia/Kolkata")  # Fallback
        
        # Get current time in the specified timezone
        now_utc = datetime.now(timezone.utc)
        now_tz = now_utc.astimezone(tz)
        
        # Resolve the timeframe
        resolved = cls._resolve_timeframe(timeframe_key, now_tz, tz)
        if not resolved:
            return None
        
        # Convert to epoch based on date_mode
        if date_mode == "epoch_millis":
            gte = resolved['gte'] * 1000
            lt = resolved['lt'] * 1000
        else:
            gte = resolved['gte']
            lt = resolved['lt']
        
        return ResolvedTimeframe(
            gte=gte,
            lt=lt,
            label=resolved['label'],
            type="RELATIVE"
        )
    
    @classmethod
    def _get_date_math_expressions(cls, timeframe_key: str) -> Optional[Dict]:
        """
        Get OpenSearch date math expressions for relative timeframes.
        Returns expressions like "now-3M/M" for use in range queries.
        
        OpenSearch date math syntax:
        - now = current time
        - now-3M = 3 months ago
        - now/M = start of current month
        - now-1M/M = start of previous month
        - now/y = start of current year
        - now-1y/y = start of last year
        - now/w = start of current week (Monday)
        - now-1w/w = start of week 1 week ago
        """
        date_math_map = {
            'LAST_WEEK': {
                'gte': 'now-1w/w',  # Start of week 1 week ago (Monday 00:00)
                'lt': 'now/w',      # Start of current week (Monday 00:00)
                'label': 'Last week'
            },
            'THIS_WEEK': {
                'gte': 'now/w',     # Start of current week (Monday 00:00)
                'lt': 'now+1w/w',   # Start of next week (Monday 00:00)
                'label': 'This week'
            },
            'LAST_MONTH': {
                'gte': 'now-1M/M',  # Start of previous month (1st day 00:00)
                'lt': 'now/M',      # Start of current month (1st day 00:00)
                'label': 'Last month'
            },
            'THIS_MONTH': {
                'gte': 'now/M',     # Start of current month (1st day 00:00)
                'lt': 'now+1M/M',   # Start of next month (1st day 00:00)
                'label': 'This month'
            },
            'LAST_3_MONTHS': {
                'gte': 'now-3M/M',  # Start of month 3 months ago
                'lt': 'now',        # Current time
                'label': 'Last 3 months'
            },
            'THIS_YEAR': {
                'gte': 'now/y',      # Start of current year (Jan 1 00:00)
                'lt': 'now+1y/y',    # Start of next year (Jan 1 00:00)
                'label': 'This year'
            },
            'LAST_YEAR': {
                'gte': 'now-1y/y',   # Start of last year (Jan 1 00:00)
                'lt': 'now/y',       # Start of current year (Jan 1 00:00)
                'label': 'Last year'
            },
        }
        
        return date_math_map.get(timeframe_key)

    @classmethod
    def _resolve_flexible_timeframe(
        cls,
        key: str,
        value: int,
        timezone_str: str,
        date_mode: str
    ) -> Optional[ResolvedTimeframe]:
        if value <= 0:
            return None
        
        if key == 'last_n_months':
            gte = f"now-{value}M/M"
            lt = "now/M" if date_mode == "date" else "now"
            label = f"Last {value} month{'s' if value != 1 else ''}"
        elif key == 'last_n_weeks':
            gte = f"now-{value}w/w"
            lt = "now/w" if date_mode == "date" else "now"
            label = f"Last {value} week{'s' if value != 1 else ''}"
        elif key == 'last_n_days':
            gte = f"now-{value}d/d"
            lt = "now/d" if date_mode == "date" else "now"
            label = f"Last {value} day{'s' if value != 1 else ''}"
        else:
            return None
        
        return ResolvedTimeframe(
            gte=gte,
            lt=lt,
            label=label,
            type="RELATIVE"
        )
    
    @classmethod
    def _resolve_timeframe(
        cls,
        timeframe_key: str,
        now: datetime,
        tz: pytz.BaseTzInfo
    ) -> Optional[Dict]:
        """Internal method to resolve timeframe based on current time."""
        
        # Helper to get start of day in timezone
        def start_of_day(dt: datetime) -> datetime:
            return tz.localize(datetime(dt.year, dt.month, dt.day, 0, 0, 0))
        
        # Helper to get Monday of week
        def get_monday(dt: datetime) -> datetime:
            days_since_monday = dt.weekday()
            monday = dt - timedelta(days=days_since_monday)
            return start_of_day(monday)
        
        # Helper to get quarter start month
        def get_quarter_start_month(month: int) -> int:
            return ((month - 1) // 3) * 3 + 1
        
        if timeframe_key == 'LAST_WEEK':
            # Previous Mon 00:00 → this Mon 00:00
            this_monday = get_monday(now)
            last_monday = this_monday - timedelta(days=7)
            return {
                'gte': int(last_monday.timestamp()),
                'lt': int(this_monday.timestamp()),
                'label': 'Last week'
            }
        
        elif timeframe_key == 'THIS_WEEK':
            # This Mon 00:00 → next Mon 00:00
            this_monday = get_monday(now)
            next_monday = this_monday + timedelta(days=7)
            return {
                'gte': int(this_monday.timestamp()),
                'lt': int(next_monday.timestamp()),
                'label': 'This week'
            }
        
        elif timeframe_key == 'LAST_MONTH':
            # 1st of previous month 00:00 → 1st of current month 00:00
            first_of_this_month = start_of_day(now.replace(day=1))
            if now.month == 1:
                first_of_last_month = start_of_day(datetime(now.year - 1, 12, 1))
            else:
                first_of_last_month = start_of_day(datetime(now.year, now.month - 1, 1))
            return {
                'gte': int(first_of_last_month.timestamp()),
                'lt': int(first_of_this_month.timestamp()),
                'label': 'Last month'
            }
        
        elif timeframe_key == 'THIS_MONTH':
            # 1st of current month 00:00 → 1st of next month 00:00
            first_of_this_month = start_of_day(now.replace(day=1))
            if now.month == 12:
                first_of_next_month = start_of_day(datetime(now.year + 1, 1, 1))
            else:
                first_of_next_month = start_of_day(datetime(now.year, now.month + 1, 1))
            return {
                'gte': int(first_of_this_month.timestamp()),
                'lt': int(first_of_next_month.timestamp()),
                'label': 'This month'
            }
        
        elif timeframe_key == 'LAST_3_MONTHS':
            # Now minus 3 months → now
            three_months_ago = now - timedelta(days=90)  # Approximate
            # More precise: go back 3 calendar months
            month = now.month - 3
            year = now.year
            while month <= 0:
                month += 12
                year -= 1
            start = start_of_day(datetime(year, month, 1))
            return {
                'gte': int(start.timestamp()),
                'lt': int(now.timestamp()),
                'label': 'Last 3 months'
            }
        
        elif timeframe_key == 'THIS_QUARTER':
            # Quarter start 00:00 → next quarter start 00:00
            quarter_start_month = get_quarter_start_month(now.month)
            quarter_start = start_of_day(datetime(now.year, quarter_start_month, 1))
            # Next quarter start
            if quarter_start_month == 10:  # Q4
                next_quarter_start = start_of_day(datetime(now.year + 1, 1, 1))
            else:
                next_quarter_start = start_of_day(datetime(now.year, quarter_start_month + 3, 1))
            return {
                'gte': int(quarter_start.timestamp()),
                'lt': int(next_quarter_start.timestamp()),
                'label': f'Q{(quarter_start_month - 1) // 3 + 1} {now.year}'
            }
        
        elif timeframe_key == 'LAST_QUARTER':
            # Previous quarter start 00:00 → this quarter start 00:00
            current_quarter_start_month = get_quarter_start_month(now.month)
            current_quarter_start = start_of_day(datetime(now.year, current_quarter_start_month, 1))
            
            if current_quarter_start_month == 1:
                # Last quarter is Q4 of previous year
                last_quarter_start_month = 10
                last_quarter_year = now.year - 1
            else:
                last_quarter_start_month = current_quarter_start_month - 3
                last_quarter_year = now.year
            
            last_quarter_start = start_of_day(datetime(last_quarter_year, last_quarter_start_month, 1))
            return {
                'gte': int(last_quarter_start.timestamp()),
                'lt': int(current_quarter_start.timestamp()),
                'label': f'Q{(last_quarter_start_month - 1) // 3 + 1} {last_quarter_year}'
            }
        
        elif timeframe_key == 'THIS_YEAR':
            # Jan 1 00:00 → Jan 1 next year 00:00
            year_start = start_of_day(datetime(now.year, 1, 1))
            next_year_start = start_of_day(datetime(now.year + 1, 1, 1))
            return {
                'gte': int(year_start.timestamp()),
                'lt': int(next_year_start.timestamp()),
                'label': f'{now.year}'
            }
        
        elif timeframe_key == 'LAST_YEAR':
            # Jan 1 last year 00:00 → Jan 1 this year 00:00
            last_year_start = start_of_day(datetime(now.year - 1, 1, 1))
            this_year_start = start_of_day(datetime(now.year, 1, 1))
            return {
                'gte': int(last_year_start.timestamp()),
                'lt': int(this_year_start.timestamp()),
                'label': f'{now.year - 1}'
            }
        
        return None

