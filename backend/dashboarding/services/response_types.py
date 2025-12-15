"""
Response type determination for AI Reports Bot.
Defines structured response types and rules for selecting them.

Aligned with usecase.md Section B - Intent Classification.
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
import re


class ResponseType(Enum):
    """
    Types of responses the AI can generate.
    Aligned with usecase.md Section B intent classification.
    """
    # From usecase.md Section B:
    KPI_WIDGET = "kpi_widget"       # 1) DESCRIBE / KPI SUMMARY - Single big number
    MULTI_KPI = "multi_kpi"         # Multiple KPI widgets side by side
    TABLE = "table"                  # 2) LIST / REPORT (ROWS) - Tabular data display
    BAR_CHART = "bar_chart"          # 3) BREAKDOWN / GROUP BY - Bar chart visualization
    LINE_CHART = "line_chart"        # 4) TREND / TIME SERIES - Line chart over time
    PIE_CHART = "pie_chart"          # Proportional breakdown (pie/donut)
    COMPARISON = "comparison"        # 5) COMPARE - Side-by-side comparison (X vs Y)
    DRILLDOWN = "drilldown"          # 6) DRILLDOWN FOLLOW-UP - Refinement of previous query
    DATA_DICTIONARY = "data_dictionary"  # 7) DATA DICTIONARY - What fields exist
    TEXT_RESPONSE = "text_response"  # Simple text answer


@dataclass
class ResponseConfig:
    """Configuration for how to respond to a query."""
    response_type: ResponseType
    title: str
    description: str
    aggregations: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Dict[str, str]] = field(default_factory=dict)
    fields_to_show: List[str] = field(default_factory=list)
    visualization_config: Dict[str, Any] = field(default_factory=dict)
    size: int = 0  # 0 = aggregation only, >0 = return documents


class ResponseTypeDetector:
    """
    Determines the appropriate response type based on user query patterns.
    Ensures consistency - same type of query always gets same response format.
    
    Aligned with usecase.md Section B - Intent Classification.
    """
    
    # Query patterns mapped to response types
    PATTERNS = {
        # 7) DATA DICTIONARY - "What fields exist?"
        'data_dictionary': {
            'patterns': [
                r'\bwhat\s+(can|fields?|data|columns?|attributes?)\b.*(filter|query|available|exist)',
                r'\bwhat\s+fields?\b',
                r'\bwhat\s+can\s+(you|i)\s+(filter|query|search)\b',
                r'\blist\s+(all\s+)?fields?\b',
                r'\bshow\s+(me\s+)?schema\b',
                r'\bwhat\s+data\s+(is\s+)?available\b',
                r'\bwhat\s+can\s+you\s+tell\s+me\s+about\b',
                r'\bexplain\s+(the\s+)?schema\b',
                r'\bwhat\s+dimensions?\b',
                r'\bhelp\b.*\b(filter|query|understand)\b',
            ],
            'response_type': ResponseType.DATA_DICTIONARY,
            'priority': 20
        },
        
        # 6) DRILLDOWN FOLLOW-UP - Refinement queries
        'drilldown': {
            'patterns': [
                r'\bnow\s+only\s+for\b',
                r'\bsame\s+(as\s+)?but\b',
                r'\bsame\s+as\s+before\b',
                r'\bexclude\s+',
                r'\bonly\s+(show|for|include)\b',
                r'\bnarrow\s+(down|it)\b',
                r'\bfilter\s+(that|those|it)\s+(to|by)\b',
                r'\b(from|of)\s+those\b',
                r'\bof\s+these\b',
                r'\bamong\s+(those|these)\b',
                r'\bwithin\s+(those|these|that)\b',
                r'\bnow\s+for\s+(only|just)\b',
            ],
            'response_type': ResponseType.DRILLDOWN,
            'priority': 18
        },
        
        # 5) COMPARISON patterns - X vs Y
        # NOTE: "completion rate" queries that ask for "top X by completion rate" should be BAR_CHART, not COMPARISON
        'comparison': {
            'patterns': [
                r'\bvs\.?\s+',
                r'\bversus\b',
                r'\bcompared?\s+to\b',
                r'\bdelivered.+completed\b',
                r'\bassigned.+completed\b',
                r'\bstarted.+finished\b',
                # Only match completion rate if it's explicitly a comparison (X vs Y format)
                # NOT if it's "top X by completion rate" (that's a bar chart)
                r'\bcompletion\s+rate\s+(?:vs|versus|compared)',
                r'\bconversion\s+rate\b',
                r'\b(\w+)\s+vs\.?\s+(\w+)\b',
                r'\bthis\s+month\s+vs\.?\s+last\s+month\b',
                r'\bcompare\b',
            ],
            'response_type': ResponseType.COMPARISON,
            'priority': 10  # Lower than bar_chart (11) so "top X by completion rate" becomes bar chart
        },
        
        # 1) KPI Widget patterns - single value questions (DESCRIBE / KPI SUMMARY)
        'kpi_single': {
            'patterns': [
                r'\bhow many\s+(unique\s+|distinct\s+)?(users?|people|employees?|trainees?|learners?)\b',
                r'\btotal\s+(unique\s+|distinct\s+)?(users?|count|number)\b',
                r'\bhow many\s+(unique\s+|distinct\s+)?(modules?|trainings?|courses?)\b',
                r'\btotal\s+(unique\s+|distinct\s+)?(modules?|trainings?|courses?)\b',
                r'\bhow many\s+(completions?|completed)\b',
                r'\bwhat.+\btotal\b',
                r'\bcount\s+of\b',
                r'\bnumber\s+of\b',
                r'\bunique\s+(users?|modules?|trainings?|courses?|managers?|coaches?|trainers?)\b',
                r'\bavg\s+',
                r'\baverage\s+',
                r'\bwhat\s+is\s+the\s+(average|avg|mean)\b',
            ],
            'response_type': ResponseType.KPI_WIDGET,
            'priority': 14
        },
        
        # 2) TABLE patterns - LIST / REPORT (ROWS)
        'table': {
            'patterns': [
                r'\blist\b',
                r'\bshow\s+me\b',
                r'\bgive\s+me\b',
                r'\bdisplay\b',
                r'\btable\b',
                r'\breport\b',
                r'\bdetails?\b',
                r'\bexport\b',
                r'\beach\s+user\b',
                r'\bper\s+user\b',
                r'\bwho\s+(completed|has|have)\b',
                r'\bwhich\s+(all\s+)?(users?|people|modules?|trainings?)\b',
                r'\bwho\s+all\b',
                r'\bnames?\s+of\b',
                r'\bwho\s+are\s+these\b',
                r'\bwho\s+are\s+those\b',
                r'\bwho\s+are\s+the\b',
                r'\bwho\s+are\b',
                r'\bwho\s+is\b',
                r'\bwho\b',
                r'\bwho\s+hasn\'t\b',
                r'\bwho\s+haven\'t\b',
                r'\busers?\s+who\b',
            ],
            'response_type': ResponseType.TABLE,
            'priority': 12
        },
        
        # 4) LINE_CHART patterns - TREND / TIME SERIES
        'line_chart': {
            'patterns': [
                r'\btrend\b',
                r'\bover\s+time\b',
                r'\bmonth\s+by\s+month\b',
                r'\bweek\s+by\s+week\b',
                r'\bday\s+by\s+day\b',
                r'\bprogression\b',
                r'\bgrowth\b',
                r'\bhistory\b',
                r'\btime\s+series\b',
                r'\bweekly\s+',
                r'\bmonthly\s+',
                r'\bdaily\s+',
            ],
            'response_type': ResponseType.LINE_CHART,
            'priority': 10
        },
        
        # Pie chart patterns (subset of breakdown)
        'pie_chart': {
            'patterns': [
                r'\bpercentage\s+breakdown\b',
                r'\bshare\s+of\b',
                r'\bproportion\b',
                r'\bpie\s+chart\b',
                r'\bdistribution\s+of\b',
            ],
            'response_type': ResponseType.PIE_CHART,
            'priority': 9
        },
        
        # 3) BAR_CHART patterns - BREAKDOWN / GROUP BY
        'bar_chart': {
            'patterns': [
                # Completion rate queries that are rankings (not comparisons)
                r'\btop\s+\d*\s*(?:modules?|trainings?|courses?)\s+by\s+completion\s+rate',
                r'\btop\s+(?:modules?|trainings?|courses?)\s+by\s+completion\s+rate',
                r'completion\s+rate\s+by\s+(?:module|training|course)',
                r'\bby\s+completion\s+rate\b',
                r'\bby\s+(city|cities|country|countries|skill|skills|product|products|module|modules|department|team|region|location)\b',
                r'\bbreakdown\s+by\b',
                r'\bdistribution\b',
                r'\bper\s+(city|country|skill|product|module)\b',
                r'\bacross\s+(cities|countries|skills|products|modules|regions|locations)\b',
                r'\btop\s+\d+\b',
                r'\bbest\s+\d+\b',
                r'\bworst\s+\d+\b',
                r'\bbottom\s+\d+\b',
                r'\bhighest\s+\d+\b',
                r'\blowest\s+\d+\b',
                r'\bgroup\s+by\b',
            ],
            'response_type': ResponseType.BAR_CHART,
            'priority': 11  # Higher priority than comparison to catch "top X by completion rate"
        },
        
        # Multi-KPI patterns (SUMMARY)
        'multi_kpi': {
            'patterns': [
                r'\bsummary\b',
                r'\boverview\b',
                r'\bdashboard\b',
                r'\bstats\b',
                r'\bstatistics\b',
                r'\bmetrics\b',
                r'\bkpis?\b',
                r'\bhighlight\b',
            ],
            'response_type': ResponseType.MULTI_KPI,
            'priority': 7
        },
    }
    
    @classmethod
    def detect(cls, user_message: str) -> ResponseType:
        """
        Detect the most appropriate response type for a user message.
        Uses pattern matching with priority to ensure consistency.
        
        Aligned with usecase.md Section B intent classification.
        """
        message_lower = user_message.lower()
        
        matches = []
        for pattern_group, config in cls.PATTERNS.items():
            for pattern in config['patterns']:
                if re.search(pattern, message_lower):
                    matches.append({
                        'group': pattern_group,
                        'type': config['response_type'],
                        'priority': config['priority']
                    })
                    break  # Only count each group once
        
        if not matches:
            # Default to KPI widget for simple questions, table for complex
            word_count = len(message_lower.split())
            if word_count <= 8:
                return ResponseType.KPI_WIDGET
            return ResponseType.TABLE
        
        # Return highest priority match
        matches.sort(key=lambda x: x['priority'], reverse=True)
        return matches[0]['type']
    
    @classmethod
    def is_drilldown_query(cls, user_message: str) -> bool:
        """Check if the query is a drilldown/follow-up query."""
        return cls.detect(user_message) == ResponseType.DRILLDOWN
    
    @classmethod
    def is_data_dictionary_query(cls, user_message: str) -> bool:
        """Check if the query is asking about available fields/schema."""
        return cls.detect(user_message) == ResponseType.DATA_DICTIONARY
    
    @classmethod
    def get_response_config(cls, response_type: ResponseType, query_context: Dict[str, Any]) -> ResponseConfig:
        """
        Get the configuration for building a response of the given type.
        """
        configs = {
            ResponseType.KPI_WIDGET: cls._build_kpi_config,
            ResponseType.MULTI_KPI: cls._build_multi_kpi_config,
            ResponseType.TABLE: cls._build_table_config,
            ResponseType.BAR_CHART: cls._build_bar_chart_config,
            ResponseType.LINE_CHART: cls._build_line_chart_config,
            ResponseType.PIE_CHART: cls._build_pie_chart_config,
            ResponseType.COMPARISON: cls._build_comparison_config,
            ResponseType.DRILLDOWN: cls._build_drilldown_config,
            ResponseType.DATA_DICTIONARY: cls._build_data_dictionary_config,
            ResponseType.TEXT_RESPONSE: cls._build_text_config,
        }
        
        builder = configs.get(response_type, cls._build_kpi_config)
        return builder(query_context)
    
    @classmethod
    def _build_kpi_config(cls, ctx: Dict) -> ResponseConfig:
        """Build config for single KPI widget response."""
        return ResponseConfig(
            response_type=ResponseType.KPI_WIDGET,
            title=ctx.get('title', 'Total Count'),
            description=ctx.get('description', ''),
            aggregations={},
            size=0
        )
    
    @classmethod
    def _build_multi_kpi_config(cls, ctx: Dict) -> ResponseConfig:
        """Build config for multiple KPI widgets."""
        return ResponseConfig(
            response_type=ResponseType.MULTI_KPI,
            title=ctx.get('title', 'Summary'),
            description=ctx.get('description', ''),
            aggregations={},
            size=0
        )
    
    @classmethod
    def _build_table_config(cls, ctx: Dict) -> ResponseConfig:
        """Build config for table response."""
        return ResponseConfig(
            response_type=ResponseType.TABLE,
            title=ctx.get('title', 'Data Report'),
            description=ctx.get('description', ''),
            fields_to_show=ctx.get('fields', []),
            size=ctx.get('size', 100)
        )
    
    @classmethod
    def _build_bar_chart_config(cls, ctx: Dict) -> ResponseConfig:
        """Build config for bar chart response."""
        return ResponseConfig(
            response_type=ResponseType.BAR_CHART,
            title=ctx.get('title', 'Distribution'),
            description=ctx.get('description', ''),
            visualization_config={
                'type': 'bar',
                'x_label': ctx.get('x_label', 'Category'),
                'y_label': ctx.get('y_label', 'Count')
            },
            size=0
        )
    
    @classmethod
    def _build_line_chart_config(cls, ctx: Dict) -> ResponseConfig:
        """Build config for line chart response."""
        return ResponseConfig(
            response_type=ResponseType.LINE_CHART,
            title=ctx.get('title', 'Trend'),
            description=ctx.get('description', ''),
            visualization_config={
                'type': 'line',
                'x_label': ctx.get('x_label', 'Time'),
                'y_label': ctx.get('y_label', 'Value')
            },
            size=0
        )
    
    @classmethod
    def _build_pie_chart_config(cls, ctx: Dict) -> ResponseConfig:
        """Build config for pie chart response."""
        return ResponseConfig(
            response_type=ResponseType.PIE_CHART,
            title=ctx.get('title', 'Distribution'),
            description=ctx.get('description', ''),
            visualization_config={
                'type': 'pie'
            },
            size=0
        )
    
    @classmethod
    def _build_comparison_config(cls, ctx: Dict) -> ResponseConfig:
        """Build config for comparison response."""
        return ResponseConfig(
            response_type=ResponseType.COMPARISON,
            title=ctx.get('title', 'Comparison'),
            description=ctx.get('description', ''),
            aggregations={},
            visualization_config={
                'type': 'comparison_bar'
            },
            size=0
        )
    
    @classmethod
    def _build_drilldown_config(cls, ctx: Dict) -> ResponseConfig:
        """Build config for drilldown/follow-up response."""
        # Drilldown inherits the previous response type
        prev_type = ctx.get('previous_response_type', ResponseType.TABLE)
        return ResponseConfig(
            response_type=prev_type if isinstance(prev_type, ResponseType) else ResponseType.TABLE,
            title=ctx.get('title', 'Filtered Results'),
            description=ctx.get('description', 'Refined query based on previous results'),
            size=ctx.get('size', 50)
        )
    
    @classmethod
    def _build_data_dictionary_config(cls, ctx: Dict) -> ResponseConfig:
        """Build config for data dictionary response."""
        return ResponseConfig(
            response_type=ResponseType.DATA_DICTIONARY,
            title='Available Fields',
            description='Schema and field documentation',
            size=0
        )
    
    @classmethod
    def _build_text_config(cls, ctx: Dict) -> ResponseConfig:
        """Build config for text-only response."""
        return ResponseConfig(
            response_type=ResponseType.TEXT_RESPONSE,
            title='',
            description=ctx.get('description', ''),
            size=0
        )

