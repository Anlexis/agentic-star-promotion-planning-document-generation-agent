"""AgentCore Platform v1.0"""

# RET-C2-003 — InputParseNode
# Inner domain node 1 (wired by DomainWorkflowGraph).
#
# Responsibility:
#   - Take the campaign parameters the caller supplied on the structured
#     channel (validated by PreProcessNode, delivered through the context
#     bridge) as authoritative
#   - Fill anything the caller did not supply by reading the free-text brief
#   - Reduce every value that will RENDER into the plan document to the inert
#     identifier alphabet, whichever of the two routes it arrived by
#   - Write the consolidated parameters to State as a partial dict

import json
import logging
import math
import re
from typing import Any, ClassVar, Dict, List, Optional

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from framework.utils.audit_logger import emit_trace_event

logger = logging.getLogger(__name__)

# Valid channel values (normalized to lower-case)
_VALID_CHANNELS = {"online", "offline", "both"}

# Channel aliases for common variations
_CHANNEL_ALIASES: Dict[str, str] = {
    "digital": "online",
    "web": "online",
    "internet": "online",
    "in-store": "offline",
    "instore": "offline",
    "physical": "offline",
    "store": "offline",
    "omnichannel": "both",
    "omni-channel": "both",
    "all": "both",
    "all channels": "both",
}

# Keywords for heuristic extraction of campaign parameters from free text
_CAMPAIGN_TYPE_KEYWORDS: List[str] = [
    "seasonal sale",
    "new product launch",
    "loyalty program",
    "flash sale",
    "clearance",
    "brand awareness",
    "customer acquisition",
    "retention",
    "holiday promotion",
    "back-to-school",
    "anniversary sale",
    "grand opening",
]

# The alphabet every rendered caller string is reduced to. The plan document is
# read and forwarded by humans, so a value that reaches it must be incapable of
# carrying markup, directives or layout — and that has to hold for values
# lifted out of the free-text brief just as much as for values the caller
# declared. This is also the alphabet the output gate's identifier guards are
# widened to; the two must stay in step.
_INERT_ALPHABET_RE = re.compile(r"[^a-z0-9_]+")
_INERT_MAX_LENGTH = 32

_MAX_DURATION_WEEKS = 104.0

# Free-text duration units expressed in weeks, for the degraded path.
_DURATION_UNIT_WEEKS: Dict[str, float] = {
    "day": 1.0 / 7.0,
    "week": 1.0,
    "month": 4.345,
    "quarter": 13.0,
    "year": 52.0,
}


def _to_inert(raw: Optional[str], fallback: str) -> str:
    """Reduce a caller-supplied string to the inert identifier alphabet."""
    if not raw:
        return fallback
    reduced = _INERT_ALPHABET_RE.sub("_", str(raw).strip().lower()).strip("_")
    reduced = reduced[:_INERT_MAX_LENGTH].strip("_")
    return reduced or fallback


def _normalize_channel(raw: Optional[str]) -> str:
    """Normalize a raw channel string to one of: online, offline, both."""
    normalized = (raw or "").strip().lower()
    if normalized in _VALID_CHANNELS:
        return normalized
    return _CHANNEL_ALIASES.get(normalized, "both")


def _parse_json_brief(brief: str) -> Optional[Dict[str, Any]]:
    """Try to parse brief as JSON. Returns dict or None."""
    try:
        parsed = json.loads(brief)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _extract_from_structured(payload: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Extract campaign parameters from a JSON-shaped brief.

    Accepts both snake_case and camelCase keys. Missing keys return None.
    """

    def _get(source: Dict[str, Any], *keys: str) -> Optional[str]:
        for key in keys:
            value = source.get(key)
            if value is not None:
                return str(value)
        return None

    return {
        "campaign_type": _get(payload, "campaign_type", "campaignType", "type"),
        "target_segment": _get(payload, "target_segment", "targetSegment", "segment", "audience"),
        "budget_range": _get(payload, "budget_range", "budgetRange", "budget"),
        "duration": _get(payload, "duration", "campaign_duration", "campaignDuration"),
        "channel": _get(payload, "channel", "channels", "distribution_channel"),
    }


def _extract_from_text(brief: str) -> Dict[str, Optional[str]]:
    """Extract campaign parameters from a free-text brief using heuristics."""
    brief_lower = brief.lower()
    params: Dict[str, Optional[str]] = {
        "campaign_type": None,
        "target_segment": None,
        "budget_range": None,
        "duration": None,
        "channel": None,
    }

    for keyword in _CAMPAIGN_TYPE_KEYWORDS:
        if keyword in brief_lower:
            params["campaign_type"] = keyword
            break
    if params["campaign_type"] is None:
        match = re.search(r"(?:campaign|promotion)[:\s]+([a-z0-9 ]+?)(?:[,.\n]|$)", brief_lower)
        if match:
            params["campaign_type"] = match.group(1).strip()

    budget_match = re.search(
        r"(?:budget|cost)[:\s]*"
        r"([\$¥€£]?\s*\d[\d,\.]*\s*[KkMmBb]?(?:\s*[-–]\s*[\$¥€£]?\s*\d[\d,\.]*\s*[KkMmBb]?)?)"
        r"(?:\s*(?:JPY|USD|EUR|GBP|jpy|usd|eur|gbp))?",
        brief,
        re.IGNORECASE,
    )
    if budget_match:
        params["budget_range"] = budget_match.group(1).strip()

    duration_match = re.search(
        r"(?:duration|period|for|over|during)[:\s]*"
        r"(\d+\s*(?:day|week|month|quarter|year)s?(?:\s+[-–]\s*\d+\s*(?:day|week|month|year)s?)?)",
        brief,
        re.IGNORECASE,
    )
    if duration_match:
        params["duration"] = duration_match.group(1).strip()

    for alias, canonical in _CHANNEL_ALIASES.items():
        if alias in brief_lower:
            params["channel"] = canonical
            break
    if params["channel"] is None:
        for channel in _VALID_CHANNELS:
            if channel in brief_lower:
                params["channel"] = channel
                break

    segment_match = re.search(
        r"(?:target|segment|audience|customer|demographic)[:\s]+([^,.\n]+?)(?:[,.\n]|$)",
        brief,
        re.IGNORECASE,
    )
    if segment_match:
        params["target_segment"] = segment_match.group(1).strip()

    return params


def _duration_to_weeks(raw: Optional[str]) -> Optional[float]:
    """Convert a free-text duration ("4 weeks", "3 months") to weeks.

    Returns None when the text carries no usable figure or the result is not a
    finite value inside the supported horizon — the timeline then degrades to
    the unspanned phase list rather than inventing a span.
    """
    if not raw:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*(day|week|month|quarter|year)s?", raw, re.IGNORECASE)
    if not match:
        return None
    try:
        quantity = float(match.group(1))
    except ValueError:
        return None
    weeks = quantity * _DURATION_UNIT_WEEKS[match.group(2).lower()]
    if not math.isfinite(weeks) or not 0 < weeks <= _MAX_DURATION_WEEKS:
        return None
    return weeks


class InputParseNode(FunctionNode):
    """Consolidate the campaign parameters the plan is generated from.

    Inner domain node 1 for RET-C2-003, wired by DomainWorkflowGraph.

    Two routes reach this node and they are NOT equivalent:

      * `campaign_request` — the structured parameters the caller declared,
        already type-, shape- and bound-checked by PreProcessNode. Authoritative.
      * `validated_input` — the free-text (or JSON-shaped) brief. Heuristics
        fill whatever the structured route did not supply. This is the degraded
        baseline, not a second contract: every value it yields is reduced to the
        inert identifier alphabet before it can reach the document.

    Output state keys:
        campaign_type    (str)          — inert identifier
        target_segment   (str)          — inert identifier
        channel          (str)          — online | offline | both
        duration_weeks   (float | None) — finite, <= 104, or None
        budget_total_jpy (float | None) — finite, caller-declared only
        parsed_campaign  (dict)         — consolidated view handed to PlanGenerateNode
        status           (str)          — AgentStatus.SUCCESS or ERROR
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> Dict[str, Any]:
        validated_input = state.get("validated_input") or state.get("user_input", "")

        declared: Dict[str, Any] = {}
        raw_request = state.get("campaign_request")
        if isinstance(raw_request, str) and raw_request:
            try:
                loaded = json.loads(raw_request)
            except (json.JSONDecodeError, ValueError):
                loaded = None
            if isinstance(loaded, dict):
                declared = loaded

        if not declared and (not validated_input or not validated_input.strip()):
            emit_trace_event(
                "campaign_parse_failed",
                {"node": self.__class__.__name__, "reason": "no_campaign_input"},
                state,
            )
            logger.warning("InputParseNode: neither a campaign brief nor declared parameters were present")
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["InputParseNode: no campaign brief and no declared campaign parameters"],
                "parsed_campaign": None,
            }

        brief = (validated_input or "").strip()
        heuristic: Dict[str, Optional[str]] = {
            "campaign_type": None,
            "target_segment": None,
            "budget_range": None,
            "duration": None,
            "channel": None,
        }
        if brief:
            json_payload = _parse_json_brief(brief)
            heuristic = (
                _extract_from_structured(json_payload) if json_payload is not None else _extract_from_text(brief)
            )

        campaign_type = _to_inert(declared.get("campaign_type") or heuristic.get("campaign_type"), "general_promotion")
        target_segment = _to_inert(
            declared.get("target_segment") or heuristic.get("target_segment"), "general_customers"
        )
        channel = _normalize_channel(declared.get("channel") or heuristic.get("channel"))

        duration_weeks = declared.get("duration_weeks")
        if duration_weeks is None:
            duration_weeks = _duration_to_weeks(heuristic.get("duration"))

        # A budget FIGURE is only ever taken from the declared contract: the
        # heuristic reads a currency-shaped substring out of free text, and a
        # misread there would drive the allocation arithmetic. The heuristic
        # value is kept only as an inert label for the degraded path.
        budget_total_jpy = declared.get("budget_total_jpy")
        budget_label = _to_inert(heuristic.get("budget_range"), "unspecified")

        parsed_campaign: Dict[str, Any] = {
            "campaign_type": campaign_type,
            "target_segment": target_segment,
            "channel": channel,
            "duration_weeks": duration_weeks,
            "budget_total_jpy": budget_total_jpy,
            "budget_range": budget_label,
        }

        result: Dict[str, Any] = {
            "campaign_type": campaign_type,
            "target_segment": target_segment,
            "channel": channel,
            "duration_weeks": duration_weeks,
            "budget_total_jpy": budget_total_jpy,
            "parsed_campaign": parsed_campaign,
            "status": AgentStatus.SUCCESS.value,
        }
        # A caller-declared output format overrides the deployment default that
        # DomainWorkflowGraph seeded from config/config.yaml. Only the declared
        # route may set it: the value is already checked against a closed set.
        if declared.get("output_format"):
            result["output_format"] = declared["output_format"]

        emit_trace_event(
            "campaign_parsed",
            {
                "node": self.__class__.__name__,
                "channel": channel,
                "declared_fields": sorted(declared),
                "budget_declared": budget_total_jpy is not None,
                "duration_resolved": duration_weeks is not None,
            },
            state,
        )
        logger.info(
            "InputParseNode: parameters consolidated (channel=%s, declared=%d, budget_declared=%s)",
            channel,
            len(declared),
            budget_total_jpy is not None,
        )

        return result
