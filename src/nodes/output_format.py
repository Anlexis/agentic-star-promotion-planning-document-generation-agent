"""AgentCore Platform v1.0"""

# RET-C2-003 — OutputFormatNode
# Inner domain node 3 (wired by DomainWorkflowGraph).
#
# Responsibility:
#   - Read the rendered plan from State (written by PlanGenerateNode)
#   - Render it in the requested output format
#   - Write the formatted plan back to State, replacing the intermediate form
#
# The output format is resolved from State: a caller-declared value (checked
# against the closed set by PreProcessNode) wins over the deployment default
# that DomainWorkflowGraph seeds from config/config.yaml.

import json
import logging
import re
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from framework.utils.audit_logger import emit_trace_event

logger = logging.getLogger(__name__)

# Section headings produced by PromotionPlanService, used to split the document
# when the caller asked for the structured form.
_SECTION_PATTERNS: List[Tuple[str, str]] = [
    ("overview", r"##\s*(?:1\.)?\s*overview"),
    ("timeline", r"##\s*(?:2\.)?\s*timeline"),
    ("channel_breakdown", r"##\s*(?:3\.)?\s*channel\s+breakdown"),
    ("budget_allocation", r"##\s*(?:4\.)?\s*budget\s+allocation"),
    ("kpis", r"##\s*(?:5\.)?\s*kpis?(?:\s*/\s*key\s+performance\s+indicators?)?"),
]

_JSON_KEYS = [key for key, _ in _SECTION_PATTERNS]

_SUPPORTED_FORMATS = frozenset({"markdown", "json"})
_DEFAULT_FORMAT = "markdown"


def _extract_markdown_sections(plan_text: str) -> Dict[str, str]:
    """Split a markdown promotion plan into its named sections."""
    sections: Dict[str, str] = {key: "" for key in _JSON_KEYS}

    found_sections: List[Tuple[str, int]] = []
    for key, pattern in _SECTION_PATTERNS:
        match = re.search(pattern, plan_text, re.IGNORECASE)
        if match:
            found_sections.append((key, match.start()))
    found_sections.sort(key=lambda item: item[1])

    for index, (key, start) in enumerate(found_sections):
        heading_end = plan_text.index("\n", start) + 1 if "\n" in plan_text[start:] else len(plan_text)
        content_end = found_sections[index + 1][1] if index + 1 < len(found_sections) else len(plan_text)
        sections[key] = plan_text[heading_end:content_end].strip()

    return sections


def _to_markdown(plan_text: str) -> str:
    """Normalise the plan as a markdown document with a top-level heading."""
    normalized = plan_text.strip()
    if not normalized.startswith("#"):
        normalized = "# Promotion Plan\n\n" + normalized
    return normalized


def _to_json_string(plan_text: str) -> str:
    """Serialise the plan's five sections as a JSON object string."""
    sections = _extract_markdown_sections(plan_text)
    return json.dumps({key: sections[key] for key in _JSON_KEYS}, ensure_ascii=False, indent=2)


class OutputFormatNode(FunctionNode):
    """Render the promotion plan in the requested output format.

    Inner domain node 3 for RET-C2-003, wired by DomainWorkflowGraph.

    Input state keys:
        promotion_plan   (str | None)  — the plan as rendered by PlanGenerateNode
        output_format    (str | None)  — "markdown" or "json"

    Output state keys (partial dict):
        promotion_plan   (str)         — the formatted document
        status           (str)         — AgentStatus.SUCCESS or ERROR
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> Dict[str, Any]:
        raw_plan: Optional[str] = state.get("promotion_plan")

        if not raw_plan or not raw_plan.strip():
            logger.error("OutputFormatNode: promotion_plan is empty — cannot format")
            emit_trace_event(
                "promotion_plan_format_failed",
                {"node": self.__class__.__name__, "reason": "empty_plan"},
                state,
            )
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["OutputFormatNode: promotion_plan is missing or empty"],
                "promotion_plan": None,
            }

        requested = state.get("output_format")
        output_format = requested.strip().lower() if isinstance(requested, str) else ""
        if output_format not in _SUPPORTED_FORMATS:
            if output_format:
                logger.warning("OutputFormatNode: unsupported output_format — falling back to %s", _DEFAULT_FORMAT)
            output_format = _DEFAULT_FORMAT

        if output_format == "json":
            formatted_plan = _to_json_string(raw_plan)
        else:
            formatted_plan = _to_markdown(raw_plan)

        emit_trace_event(
            "promotion_plan_formatted",
            {
                "node": self.__class__.__name__,
                "output_format": output_format,
                "plan_chars": len(formatted_plan),
            },
            state,
        )
        logger.info("OutputFormatNode: plan formatted as %s (len=%d chars)", output_format, len(formatted_plan))

        return {
            "promotion_plan": formatted_plan,
            "status": AgentStatus.SUCCESS.value,
        }
