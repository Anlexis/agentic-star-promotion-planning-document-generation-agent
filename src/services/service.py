"""AgentCore Platform v1.0"""

# Service layer: the promotion-planning domain rules.
#
# Holds the section builders and the budget arithmetic so the nodes stay thin
# adapters between State and the domain. Contains no routing, no State access
# and no credentials.

from __future__ import annotations

from typing import Any, Dict, List, Tuple

# External reporting grid. Budget figures leave this agent as AGGREGATES
# rounded to the nearest 1,000 currency units: a promotion plan is circulated
# by e-mail and pasted into decks, and a to-the-yen split implies a precision
# the planning inputs do not have. The output gate in PostProcessNode enforces
# the same grid on the rendered document, so an amount that reaches the surface
# by any other route is snapped there too.
EXTERNAL_ROUND_UNIT = 1_000

# Allocation weights by channel mix. Each list sums to 1.0.
_ALLOCATION_WEIGHTS: Dict[str, List[Tuple[str, float]]] = {
    "online": [
        ("Online media", 0.75),
        ("Creative & production", 0.15),
        ("Contingency", 0.10),
    ],
    "offline": [
        ("In-store / offline events", 0.75),
        ("Creative & production", 0.15),
        ("Contingency", 0.10),
    ],
    "both": [
        ("Online media", 0.40),
        ("Offline / in-store events", 0.40),
        ("Creative & production", 0.10),
        ("Contingency", 0.10),
    ],
}

# Phase weights used to spread the campaign duration across the timeline.
_PHASE_WEIGHTS: List[Tuple[str, float, str]] = [
    ("Pre-launch", 0.20, "finalize creative, set up tracking, brief stakeholders"),
    ("Launch", 0.25, "activate primary channels, begin promotion push"),
    ("Sustain", 0.40, "monitor performance, optimize spend toward best channels"),
    ("Wrap-up", 0.15, "close out, capture results, post-campaign review"),
]

SCHEMA_NOTE = (
    "_Budget figures in this plan are aggregates rounded to the nearest 1,000 "
    "currency units; per-line-item amounts are not reported._"
)


def snap_to_grid(value: float) -> int:
    """Round a currency amount onto the external reporting grid."""
    return int(round(value / EXTERNAL_ROUND_UNIT) * EXTERNAL_ROUND_UNIT)


def normalize_channel(channel: str | None) -> str:
    """Return one of online / offline / both."""
    normalized = (channel or "").strip().lower()
    return normalized if normalized in _ALLOCATION_WEIGHTS else "both"


class PromotionPlanService:
    """Builds the five sections of a retail promotion plan.

    Every section is derived from the campaign parameters handed in; nothing is
    read from State, the environment or the network. When the caller supplied a
    numeric budget the Budget Allocation section carries computed per-category
    aggregates; without one it carries the percentage split alone, which is the
    documented degraded baseline rather than a different pipeline.
    """

    def build_plan(self, params: Dict[str, Any]) -> str:
        """Render the full markdown plan for the given campaign parameters."""
        channel = normalize_channel(params.get("channel"))
        sections = [
            self.build_overview(params, channel),
            self.build_timeline(params),
            self.build_channel_breakdown(channel),
            self.build_budget_allocation(params, channel),
            self.build_kpis(params),
        ]
        return "\n\n".join(sections).strip()

    # ── Sections ────────────────────────────────────────────────────────────

    def build_overview(self, params: Dict[str, Any], channel: str) -> str:
        return (
            "## Overview\n\n"
            f"This promotion plan covers a **{params.get('campaign_type', 'general promotion')}** "
            f"campaign targeting **{params.get('target_segment', 'general customers')}**. "
            f"The campaign runs over **{self.describe_duration(params)}** across "
            f"**{channel}** channel(s) within a budget of **{self.describe_budget(params)}**. "
            "The plan below breaks the engagement into a phased timeline, a channel-level "
            "activation breakdown, a budget allocation, and measurable KPIs.\n\n"
            f"{SCHEMA_NOTE}"
        )

    def build_timeline(self, params: Dict[str, Any]) -> str:
        weeks = params.get("duration_weeks")
        lines = ["## Timeline\n", f"Planned over the campaign duration ({self.describe_duration(params)}):\n"]
        if isinstance(weeks, (int, float)):
            for index, (phase, weight, activities) in enumerate(_PHASE_WEIGHTS, start=1):
                span = max(1, int(round(float(weeks) * weight)))
                lines.append(f"- **Phase {index} — {phase} (~{span} week{'s' if span != 1 else ''}):** {activities}")
        else:
            for index, (phase, _weight, activities) in enumerate(_PHASE_WEIGHTS, start=1):
                lines.append(f"- **Phase {index} — {phase}:** {activities}")
        return "\n".join(lines)

    def build_channel_breakdown(self, channel: str) -> str:
        lines = ["## Channel Breakdown\n"]
        if channel in ("online", "both"):
            lines.append("- **Online:** paid search, social media, email, display retargeting")
        if channel in ("offline", "both"):
            lines.append("- **Offline:** in-store signage, staff promotion, local print/flyer")
        return "\n".join(lines)

    def build_budget_allocation(self, params: Dict[str, Any], channel: str) -> str:
        weights = _ALLOCATION_WEIGHTS[channel]
        budget = params.get("budget_total_jpy")
        header = "## Budget Allocation\n\n" f"Total budget: {self.describe_budget(params)}\n\n"
        if isinstance(budget, (int, float)):
            rows = ["| Category | Share | Aggregate (JPY) |", "|---|---|---|"]
            for label, weight in weights:
                amount = snap_to_grid(float(budget) * weight)
                rows.append(f"| {label} | {int(weight * 100)}% | {amount:,d} |")
            rows.append(f"| **Total** | 100% | {snap_to_grid(float(budget)):,d} |")
            return header + "\n".join(rows)
        rows = ["| Category | Share |", "|---|---|"]
        for label, weight in weights:
            rows.append(f"| {label} | {int(weight * 100)}% |")
        return header + "\n".join(rows)

    def build_kpis(self, params: Dict[str, Any]) -> str:
        return (
            "## KPIs\n\n"
            f"Success metrics for the {params.get('campaign_type', 'general promotion')} campaign:\n\n"
            "| Metric | Target | Method | Timing |\n|---|---|---|---|\n"
            "| Sales lift | +10% | POS / order data | Post-campaign |\n"
            "| Conversion rate | 3% | Web/store analytics | Weekly |\n"
            "| Reach | Per channel plan | Channel reporting | Ongoing |\n"
            "| ROI | Positive | Spend vs. incremental revenue | Post-campaign |"
        )

    # ── Descriptions ────────────────────────────────────────────────────────

    def describe_duration(self, params: Dict[str, Any]) -> str:
        weeks = params.get("duration_weeks")
        if isinstance(weeks, (int, float)):
            rounded = int(round(float(weeks)))
            return f"{rounded} week{'s' if rounded != 1 else ''}"
        return str(params.get("duration") or "an unspecified duration")

    def describe_budget(self, params: Dict[str, Any]) -> str:
        budget = params.get("budget_total_jpy")
        if isinstance(budget, (int, float)):
            return f"JPY {snap_to_grid(float(budget)):,d}"
        return str(params.get("budget_range") or "an unspecified budget")
