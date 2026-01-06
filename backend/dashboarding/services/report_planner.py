"""
Heuristic planner that maps a natural-language reporting intent to the most
appropriate OpenSearch index and suggests a starter field set for the
interactive report builder.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Set, Tuple

from django.conf import settings

from .opensearch_client import OpenSearchClient

logger = logging.getLogger(__name__)


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _contains_phrase(text: str, tokens: Set[str], phrase: str) -> bool:
    phrase = phrase.lower().strip()
    if not phrase:
        return False
    if " " in phrase or len(phrase) > 6:
        return phrase in text
    return phrase in tokens


class ReportPlanner:
    """Lightweight intent router for the report builder."""

    DEFAULT_FIELDS: Dict[str, List[str]] = {
        "module_consumption_data": [
            "first_name",
            "last_name",
            "email_addr",
            "module_name",
            "completed_date",
            "completed_status",
            "complete_percentage",
        ],
        "user_profile_data": [
            "first_name",
            "last_name",
            "email_addr",
            "designation",
            "city",
            "country",
            "status",
            "manager_email_addr",
        ],
        "module_catalog_data": [
            "module_name",
            "module_type_name",
            "module_status",
            "published_date",
            "skill_name",
            "product_name",
            "estd_time",
        ],
        "daily_user_activity_data": [
            "first_name",
            "last_name",
            "email_addr",
            "module",
            "instant_answers",
            "learning_pathway",
            "lotd",
            "points",
            "completed_on",
        ],
        "monthly_user_activity_data": [
            "first_name",
            "last_name",
            "email_addr",
            "module",
            "instant_answers",
            "learning_pathway",
            "lotd",
            "points",
            "completed_on",
        ],
    }

    ACTIVITY_KEYWORDS = [
        "instant answer",
        "instant answers",
        "ia usage",
        "ia ",
        "questions asked",
        "queries asked",
        "query count",
        "learning pathway",
        "pathway",
        "lotd",
        "learning of the day",
        "points",
        "point total",
        "engagement",
        "usage",
        "per day",
        "daily trend",
    ]

    MODULE_CATALOG_KEYWORDS = [
        "catalog",
        "published",
        "go live",
        "launch",
        "module status",
        "module type",
        "module owner",
        "module description",
        "skill_name",
        "skill name",
        "product_name",
        "product name",
        "estd_time",
        "tags",
        "staff",
    ]

    USER_PROFILE_KEYWORDS = [
        "active users",
        "invited users",
        "deleted users",
        "designation",
        "city",
        "state",
        "country",
        "manager",
        "trainer",
        "coach",
        "user role",
        "user_role",
        "attribute",
        "attributes",
        "hired",
        "dob",
        "phone",
        "mobile",
        "directory",
        "profile",
        "user list",
        "employee",
        "people",
    ]

    PROFILE_NEGATIVE_KEYWORDS = [
        "module",
        "modules",
        "training",
        "course",
        "completed",
        "completion",
        "instant",
        "pathway",
        "lotd",
        "points",
    ]

    DAY_HINTS = [
        "today",
        "yesterday",
        "daily",
        "this week",
        "last week",
        "past 7 days",
        "last 7 days",
        "per day",
        "per-day",
        "each day",
        "24 hours",
        "24hrs",
        "weekly",
    ]

    MONTH_HINTS_FULL = [
        "this month",
        "last month",
        "monthly",
        "month",
        "quarter",
        "year",
        "annual",
        "ytd",
        "trend",
        "q1",
        "q2",
        "q3",
        "q4",
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
    ]

    MONTH_HINTS_SHORT = ["jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"]

    def __init__(self):
        self.os_client = OpenSearchClient()

    def plan(self, prompt: str) -> Dict[str, Any]:
        normalized_prompt = (prompt or "").strip()
        prompt_lower = normalized_prompt.lower()
        tokens = set(_tokenize(prompt_lower))

        if not normalized_prompt:
            index_id = "module_consumption_data"
            reasons = ["No prompt provided. Defaulted to module consumption data."]
            confidence = 0.4
        else:
            index_id, reasons, confidence = self._select_index(prompt_lower, tokens)

        schema = self._get_schema(index_id)
        recommended_fields = self._select_fields(index_id, schema)

        return {
            "prompt": normalized_prompt,
            "index_id": index_id,
            "index_name": schema.get("index_name") if isinstance(schema, dict) else None,
            "confidence": round(confidence, 2),
            "reasons": reasons,
            "recommended_fields": recommended_fields,
            "schema": schema,
        }

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _select_index(self, text: str, tokens: Set[str]) -> Tuple[str, List[str], float]:
        reasons: List[str] = []

        if self._contains_any(text, tokens, self.ACTIVITY_KEYWORDS):
            index_id, activity_reason = self._choose_activity_index(text, tokens)
            reasons.append(activity_reason)
            return index_id, reasons, 0.9

        if self._contains_any(text, tokens, self.MODULE_CATALOG_KEYWORDS):
            reasons.append("Detected catalog/publishing keywords.")
            return "module_catalog_data", reasons, 0.85

        if self._contains_any(text, tokens, self.USER_PROFILE_KEYWORDS) and not self._contains_any(
            text, tokens, self.PROFILE_NEGATIVE_KEYWORDS
        ):
            reasons.append("Detected user directory/profile keywords.")
            return "user_profile_data", reasons, 0.8

        reasons.append("Defaulted to module consumption data for completion-focused intent.")
        return "module_consumption_data", reasons, 0.55

    def _contains_any(self, text: str, tokens: Set[str], keywords: List[str]) -> bool:
        return any(_contains_phrase(text, tokens, kw) for kw in keywords)

    def _choose_activity_index(self, text: str, tokens: Set[str]) -> Tuple[str, str]:
        if self._has_day_hint(text):
            return "daily_user_activity_data", "Detected daily/weekly language or per-day metrics."
        if self._has_month_hint(text, tokens):
            return "monthly_user_activity_data", "Detected month/quarter/YTD language."
        return "monthly_user_activity_data", "Detected activity metrics (defaulted to monthly summaries)."

    def _has_day_hint(self, text: str) -> bool:
        lowered = text.lower()
        return any(hint in lowered for hint in self.DAY_HINTS)

    def _has_month_hint(self, text: str, tokens: Set[str]) -> bool:
        lowered = text.lower()
        full_match = any(hint in lowered for hint in self.MONTH_HINTS_FULL)
        short_match = any(hint in tokens for hint in self.MONTH_HINTS_SHORT)
        return full_match or short_match

    def _select_fields(self, index_id: str, schema: Dict[str, Any]) -> List[str]:
        schema_fields = {field.get("name") for field in schema.get("fields", [])} if isinstance(schema, dict) else set()
        defaults = self.DEFAULT_FIELDS.get(index_id, [])
        if not schema_fields:
            return defaults
        filtered = [field for field in defaults if field in schema_fields]
        if filtered:
            return filtered
        # Fall back to first few fields in schema
        return [field for field in schema_fields][:6]

    def _get_schema(self, index_id: str) -> Dict[str, Any]:
        schema = self.os_client.get_enriched_schema(index_id, include_samples=True)
        if isinstance(schema, dict) and "error" in schema:
            logger.warning("Failed to load schema for %s: %s", index_id, schema["error"])
            fallback = {
                "index_id": index_id,
                "index_name": settings.OPENSEARCH_INDEXES.get(index_id, index_id),
                "document_count": 0,
                "fields": [],
                "field_count": 0,
                "sample_documents": [],
                "schema_error": schema["error"],
            }
            return fallback
        return schema


