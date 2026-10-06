"""AgentCore Platform v1.0"""

# State must be a flat TypedDict — never a Pydantic BaseModel. LangGraph
# checkpoints use msgpack serialization; Pydantic objects cause silent
# corruption. Extend AgentState with agent-specific fields only, and never add
# credentials or secrets: State is checkpointed.
#
# RET-C2-003 — Promotion Planning Document Generator
# Shared across the outer AgentBaseGraph and the inner BaseGraph.
#
# Field ownership:
#   validated_input   ← PreProcessNode  (outer pre_process slot)
#   campaign_request  ← PreProcessNode, re-seeded inside the inner graph by
#                       DomainWorkflowGraph._extra_initial_state()
#   campaign_type     ← InputParseNode  (inner domain node 1)
#   target_segment    ← InputParseNode
#   channel           ← InputParseNode
#   duration_weeks    ← InputParseNode
#   budget_total_jpy  ← InputParseNode
#   parsed_campaign   ← InputParseNode
#   promotion_plan    ← PlanGenerateNode → OutputFormatNode
#   output_format     ← DomainWorkflowGraph._extra_initial_state(), overridden
#                       by a caller-declared value via InputParseNode
#   error_reason      ← PostProcessNode (outer post_process slot), on containment
#   trace_id          ← framework (AgentState base field)

from typing import Any, Dict, Optional

from framework.schemas.agent_state import AgentState


class State(AgentState):
    """RET-C2-003 agent state — flat TypedDict, no Pydantic.

    Extends AgentState with promotion-planning domain fields. Shared fields
    (user_input, status, session_id, node_history, error_log, result, hitl_*)
    are inherited.
    """

    # ── Input / validation layer ────────────────────────────────────────────
    # The campaign brief after the input checks in PreProcessNode.
    validated_input: Optional[str]

    # The validated structured caller contract, as a JSON string. A JSON string
    # rather than a dict so every State value stays msgpack-safe. Written by
    # PreProcessNode and carried into the inner graph by the context bridge.
    campaign_request: Optional[str]

    # ── Campaign parameters (consolidated by InputParseNode) ────────────────
    # Inert identifier, e.g. "seasonal_sale".
    campaign_type: Optional[str]

    # Inert identifier, e.g. "women_25_40".
    target_segment: Optional[str]

    # One of: "online", "offline", "both".
    channel: Optional[str]

    # Campaign length in weeks: finite, within the supported horizon, or None
    # when neither the caller nor the brief supplied a usable figure.
    duration_weeks: Optional[float]

    # Total campaign budget. Only ever taken from the declared caller contract,
    # where it has passed the finite-and-in-range check; None otherwise.
    budget_total_jpy: Optional[float]

    # Consolidated view of the above, handed to PlanGenerateNode. Primitive
    # values only — no nested objects.
    parsed_campaign: Optional[Dict[str, Any]]

    # ── Generated output ────────────────────────────────────────────────────
    # Written by PlanGenerateNode, replaced by OutputFormatNode with the
    # formatted document, and gated by PostProcessNode before release.
    promotion_plan: Optional[str]

    # "markdown" (default) or "json".
    output_format: Optional[str]

    # ── Containment ─────────────────────────────────────────────────────────
    # Closed-set reason recorded when PostProcessNode withholds the document
    # ("output_withheld"). RetC2003Agent.get_output() projects it — and only
    # it — to the caller as error.reason; error_log is never projected. Never
    # free text.
    error_reason: Optional[str]
    error_code: Optional[str]
