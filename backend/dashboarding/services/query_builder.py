"""Query builder for constructing natural language prompts from UI selections."""

from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta


class QueryBuilder:
    """Builds natural language prompts from UI selections."""
    
    # Predefined mega filters with their natural language equivalents
    MEGA_FILTERS = {
        'last_7_days': {
            'label': 'Last 7 Days',
            'description': 'completed in the last 7 days',
            'date_field': 'completion_date',
            'range': lambda: (datetime.now() - timedelta(days=7), datetime.now())
        },
        'last_30_days': {
            'label': 'Last 30 Days',
            'description': 'completed in the last 30 days',
            'date_field': 'completion_date',
            'range': lambda: (datetime.now() - timedelta(days=30), datetime.now())
        },
        'last_month': {
            'label': 'Last Month',
            'description': 'completed last month',
            'date_field': 'completion_date',
            'range': lambda: _get_last_month_range()
        },
        'this_month': {
            'label': 'This Month',
            'description': 'completed this month',
            'date_field': 'completion_date',
            'range': lambda: _get_this_month_range()
        },
        'this_year': {
            'label': 'This Year',
            'description': 'completed this year',
            'date_field': 'completion_date',
            'range': lambda: _get_this_year_range()
        },
        'all_time': {
            'label': 'All Time',
            'description': '',
            'date_field': None,
            'range': None
        }
    }
    
    @classmethod
    def get_mega_filters(cls) -> List[Dict[str, str]]:
        """Return list of available mega filters."""
        return [
            {'id': key, 'label': value['label'], 'description': value['description']}
            for key, value in cls.MEGA_FILTERS.items()
        ]
    
    @classmethod
    def build_prompt(
        cls,
        index_name: str,
        mega_filter: Optional[str],
        additional_filters: List[Dict[str, Any]],
        selected_columns: List[str],
        sort_field: Optional[str] = None,
        sort_order: str = 'desc'
    ) -> str:
        """
        Build a natural language prompt from UI selections.
        
        Args:
            index_name: The target index name
            mega_filter: ID of the selected mega filter
            additional_filters: List of additional filter conditions
            selected_columns: List of columns to include in results
            sort_field: Field to sort by
            sort_order: Sort order (asc/desc)
            
        Returns:
            Natural language prompt for Claude
        """
        prompt_parts = [f"Generate an ElasticSearch query for the index {index_name}."]
        
        # Add mega filter context
        if mega_filter and mega_filter in cls.MEGA_FILTERS:
            mega_info = cls.MEGA_FILTERS[mega_filter]
            if mega_info['description']:
                prompt_parts.append(f"I want records {mega_info['description']}.")
        
        # Add additional filters
        if additional_filters:
            filter_descriptions = []
            for f in additional_filters:
                field = f.get('field', '')
                operator = f.get('operator', 'equals')
                value = f.get('value', '')
                
                if operator == 'equals':
                    filter_descriptions.append(f'{field} = "{value}"')
                elif operator == 'not_equals':
                    filter_descriptions.append(f'{field} != "{value}"')
                elif operator == 'contains':
                    filter_descriptions.append(f'{field} contains "{value}"')
                elif operator == 'greater_than':
                    filter_descriptions.append(f'{field} > {value}')
                elif operator == 'less_than':
                    filter_descriptions.append(f'{field} < {value}')
                elif operator == 'between':
                    min_val = f.get('min_value', '')
                    max_val = f.get('max_value', '')
                    filter_descriptions.append(f'{field} between {min_val} and {max_val}')
                elif operator == 'in':
                    values = f.get('values', [])
                    filter_descriptions.append(f'{field} in [{", ".join(values)}]')
            
            if filter_descriptions:
                prompt_parts.append("Apply filters:")
                for desc in filter_descriptions:
                    prompt_parts.append(f"  - {desc}")
        
        # Add column selection
        if selected_columns:
            prompt_parts.append("Columns required in this sequence:")
            for col in selected_columns:
                prompt_parts.append(f"  - {col}")
        
        # Add sorting
        if sort_field:
            prompt_parts.append(f"Sort by {sort_field} {sort_order}.")
        
        prompt_parts.append("Return only the query in JSON format.")
        
        return '\n'.join(prompt_parts)
    
    @classmethod
    def generate_report_title(
        cls,
        mega_filter: Optional[str],
        additional_filters: List[Dict[str, Any]]
    ) -> str:
        """Generate a human-readable report title from selections."""
        title_parts = []
        
        if mega_filter and mega_filter in cls.MEGA_FILTERS:
            mega_info = cls.MEGA_FILTERS[mega_filter]
            title_parts.append(mega_info['label'])
        
        # Add filter summaries
        for f in additional_filters[:2]:  # Limit to first 2 filters for title
            field = f.get('field', '').replace('_', ' ').title()
            value = f.get('value', '')
            if value:
                title_parts.append(f"{field}: {value}")
        
        if additional_filters and len(additional_filters) > 2:
            title_parts.append(f"+{len(additional_filters) - 2} more filters")
        
        return ' | '.join(title_parts) if title_parts else 'Custom Report'


def _get_last_month_range():
    """Get date range for last month."""
    today = datetime.now()
    first_of_this_month = today.replace(day=1)
    last_of_prev_month = first_of_this_month - timedelta(days=1)
    first_of_prev_month = last_of_prev_month.replace(day=1)
    return (first_of_prev_month, last_of_prev_month)


def _get_this_month_range():
    """Get date range for this month."""
    today = datetime.now()
    first_of_month = today.replace(day=1)
    return (first_of_month, today)


def _get_this_year_range():
    """Get date range for this year."""
    today = datetime.now()
    first_of_year = today.replace(month=1, day=1)
    return (first_of_year, today)

