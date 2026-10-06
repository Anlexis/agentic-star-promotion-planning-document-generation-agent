"""AgentCore Platform v1.0"""

# RET-C2-003 — PlanGenerateNode
# Inner domain node 2 (wired by DomainWorkflowGraph).
#
# Responsibility:
#   - Read the consolidated campaign parameters written by InputParseNode
#   - Ask PromotionPlanService for the five-section plan document
#   - Write the rendered plan to State
#
# Generation is deterministic: the plan is derived from the campaign parameters
# by the domain rules in src/services/service.py, so the same brief always
# yields the same document and the budget aggregates are arithmetic rather than
# prose. This is what config/agent.yaml declares as generation_mode.

import logging
from typing import Any, ClassVar, Dict

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from framework.utils.audit_logger import emit_trace_event

from src.services.service import PromotionPlanService

logger = logging.getLogger(__name__)


class PlanGenerateNode(FunctionNode):
    """Render the promotion plan from the consolidated campaign parameters.

    Inner domain node 2 for RET-C2-003.

    Input state keys:
        parsed_campaign  (dict | None) — consolidated parameters
        campaign_type / target_segment / channel / duration_weeks /
        budget_total_jpy               — individual fallbacks

    Output state keys (partial dict):
        promotion_plan   (str) — the rendered plan (markdown)
        status           (str) — AgentStatus.SUCCESS or ERROR
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> Dict[str, Any]:
        parsed_campaign: Dict[str, Any] = state.get("parsed_campaign") or {}
        params: Dict[str, Any] = {
            "campaign_type": parsed_campaign.get("campaign_type") or state.get("campaign_type") or "general_promotion",
            "target_segment": (
                parsed_campaign.get("target_segment") or state.get("target_segment") or "general_customers"
            ),
            "channel": parsed_campaign.get("channel") or state.get("channel") or "both",
            "duration_weeks": parsed_campaign.get("duration_weeks", state.get("duration_weeks")),
            "budget_total_jpy": parsed_campaign.get("budget_total_jpy", state.get("budget_total_jpy")),
            "budget_range": parsed_campaign.get("budget_range"),
        }

        promotion_plan = PromotionPlanService().build_plan(params)

        if not promotion_plan:
            logger.error("PlanGenerateNode: synthesis produced an empty plan")
            emit_trace_event(
                "promotion_plan_generation_failed",
                {"node": self.__class__.__name__, "reason": "empty_plan"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["PlanGenerateNode: synthesis produced an empty promotion plan"],
                "promotion_plan": None,
            }

        emit_trace_event(
            "promotion_plan_generated",
            {
                "node": self.__class__.__name__,
                "plan_chars": len(promotion_plan),
                "channel": params["channel"],
                "budget_aggregates_rendered": params["budget_total_jpy"] is not None,
            },
            state,
        )
        logger.info("PlanGenerateNode: plan rendered (len=%d chars)", len(promotion_plan))

        return {
            "promotion_plan": promotion_plan,
            "status": AgentStatus.SUCCESS.value,
        }
