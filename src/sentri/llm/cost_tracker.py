"""Daily LLM cost tracking via the cache table.

Tracks spend per calendar day and enforces a configurable daily limit.
Uses the existing CacheRepository for persistence (no new tables).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sentri.db.cache_repo import CacheRepository

logger = logging.getLogger("sentri.llm.cost")

# Cache key prefix
_KEY_PREFIX = "llm_cost_"

# Rough per-token costs (USD) — conservative estimates
# Used only when the API response doesn't include actual cost.
ESTIMATED_COSTS = {
    "claude": {"input": 3.0 / 1_000_000, "output": 15.0 / 1_000_000},
    "openai": {"input": 2.5 / 1_000_000, "output": 10.0 / 1_000_000},
    "gemini": {"input": 0.075 / 1_000_000, "output": 0.30 / 1_000_000},
}


class CostTracker:
    """Tracks daily LLM spend and enforces a budget limit."""

    def __init__(self, cache_repo: CacheRepository, daily_limit: float = 5.0):
        self._cache = cache_repo
        self._daily_limit = daily_limit

    def _today_key(self) -> str:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return f"{_KEY_PREFIX}{today}"

    def get_today_spend(self) -> float:
        """Get total spend for today (UTC) in USD."""
        raw = self._cache.get(self._today_key())
        if raw is None:
            return 0.0
        try:
            data = json.loads(raw)
            return float(data.get("total_usd", 0.0))
        except (json.JSONDecodeError, ValueError):
            return 0.0

    def is_within_budget(self) -> bool:
        """Check if we're still within the daily budget."""
        return self.get_today_spend() < self._daily_limit

    def record_usage(
        self,
        provider: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        actual_cost: float | None = None,
    ) -> float:
        """Record a single LLM call's cost. Returns the estimated cost.

        If actual_cost is provided (from API response), uses that.
        Otherwise estimates from token counts.
        """
        if actual_cost is not None:
            cost = actual_cost
        else:
            rates = ESTIMATED_COSTS.get(provider.lower(), ESTIMATED_COSTS["openai"])
            cost = (input_tokens * rates["input"]) + (output_tokens * rates["output"])

        key = self._today_key()
        current = self.get_today_spend()
        new_total = current + cost

        data = json.dumps(
            {
                "total_usd": round(new_total, 6),
                "calls": self._get_call_count() + 1,
                "last_provider": provider,
            }
        )
        # TTL of 48h so yesterday's data is cleaned up naturally
        self._cache.set(key, data, ttl_seconds=172800)

        if new_total >= self._daily_limit:
            logger.warning(
                "Daily LLM budget exhausted: $%.4f / $%.2f",
                new_total,
                self._daily_limit,
            )

        return cost

    def _get_call_count(self) -> int:
        raw = self._cache.get(self._today_key())
        if raw is None:
            return 0
        try:
            return json.loads(raw).get("calls", 0)
        except (json.JSONDecodeError, ValueError):
            return 0

    def get_summary(self) -> dict:
        """Get a summary of today's LLM usage."""
        return {
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "total_usd": self.get_today_spend(),
            "daily_limit": self._daily_limit,
            "calls": self._get_call_count(),
            "within_budget": self.is_within_budget(),
        }
