"""
cost_tracker.py — cost cap for Person 3's own LLM call sites.

Scope note, stated plainly rather than implied: this only covers the LLM
calls Person 3's own code makes (content_agent.py's LLM idea generation,
translate.py's Arabic translation). It does NOT cover Person 2's agents
(margin.py, reviews.py, critic.py), which make their own separate LLM calls
and would need their own equivalent tracker — that's outside this file's
reach without editing their modules. Flagged here rather than silently
implying full-pipeline cost coverage.

Design: a simple per-run budget in USD, checked before each optional LLM
call. Uses rough published per-token pricing for the models actually used
here (Gemini 3.1 Flash Lite, Claude Sonnet). Token counts come from
response usage metadata where the SDK provides it; falls back to a
conservative word-count-based estimate when it doesn't (better to
overestimate than silently undercount).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# Rough published per-1M-token pricing (USD), input+output blended estimate.
# Update if pricing changes — this is a soft budget guard, not a billing system.
_PRICE_PER_1M_TOKENS = {
    "gemini-3.1-flash-lite": 0.30,
    "claude-sonnet-4-6": 6.00,
}
_DEFAULT_MODEL_PRICE = 1.00  # conservative fallback for an unrecognized model name


@dataclass
class CostTracker:
    budget_usd: float = 0.50   # default per-run cap; override via CONTENT_AGENT_COST_CAP_USD
    spent_usd: float = 0.0
    calls_made: int = 0
    calls_skipped_over_budget: int = 0
    log: list[str] = field(default_factory=list)

    def remaining(self) -> float:
        return max(0.0, self.budget_usd - self.spent_usd)

    def can_afford(self, estimated_cost_usd: float) -> bool:
        return self.spent_usd + estimated_cost_usd <= self.budget_usd

    def record(self, model: str, estimated_tokens: int) -> float:
        price_per_1m = _PRICE_PER_1M_TOKENS.get(model, _DEFAULT_MODEL_PRICE)
        cost = (estimated_tokens / 1_000_000) * price_per_1m
        self.spent_usd += cost
        self.calls_made += 1
        self.log.append(f"{model}: ~{estimated_tokens} tokens, ~${cost:.4f} (running total ${self.spent_usd:.4f})")
        return cost

    def record_skip(self, reason: str) -> None:
        self.calls_skipped_over_budget += 1
        self.log.append(f"SKIPPED (over budget): {reason}")

    def summary(self) -> str:
        return (f"Person-3 LLM cost this run: ${self.spent_usd:.4f} of ${self.budget_usd:.2f} budget, "
                f"{self.calls_made} call(s) made, {self.calls_skipped_over_budget} skipped over budget.")


def get_default_tracker() -> CostTracker:
    budget = float(os.environ.get("CONTENT_AGENT_COST_CAP_USD", "0.50"))
    return CostTracker(budget_usd=budget)


def estimate_tokens(text: str) -> int:
    """Rough estimate (~4 chars/token for English, worse for Arabic) —
    conservative on purpose, better to overcount than let a run blow past
    budget on an undercount."""
    return max(1, len(text) // 3)
