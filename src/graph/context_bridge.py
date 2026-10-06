"""AgentCore Platform v1.0 — caller-context bridge across the graph boundary."""

# Why this exists: GraphNode.execute() invokes the inner graph as
# `subgraph.invoke(user_input, session_id=..., ctx=...)` and does NOT forward
# the outer state's input_context, so an inner-node read of
# state["input_context"] would always see {} through the nested graph. The
# sanctioned subclass hooks bridge it:
#
#   PromotionPlanGraphNode.extract_input(state)   [runs BEFORE subgraph.invoke]
#       -> set_inner_request(state["campaign_request"], state["output_format"])
#   DomainWorkflowGraph._extra_initial_state()    [runs INSIDE subgraph.invoke]
#       -> returns {"campaign_request": ..., "output_format": ...}
#
# The output format travels the same way for the same reason. It is resolved
# from the OUTER agent's own config, so the value the agent was constructed with
# is the one the inner graph honours — rather than each layer reading the
# configuration file for itself and being free to disagree with the other.
#
# What crosses the bridge is the VALIDATED caller contract produced by
# PreProcessNode — never the raw request body — so the inner graph is only ever
# handed fields that already passed their type, shape and bound checks.
#
# The alternative, smuggling caller data inside validated_input, is not usable
# here: the framework masks that field at every node boundary, so a caller
# segment label can be rewritten between hops. This channel is not masked,
# which is also why PreProcessNode screens and bounds every field itself
# before anything enters it.
#
# A ContextVar keeps the hand-off correct per thread/task, so concurrent
# invocations in one process cannot see each other's context.

from contextvars import ContextVar
from typing import Optional

_CALLER_CAMPAIGN_REQUEST: ContextVar[Optional[str]] = ContextVar("ret_c2_003_caller_campaign_request", default=None)
_RESOLVED_OUTPUT_FORMAT: ContextVar[Optional[str]] = ContextVar("ret_c2_003_resolved_output_format", default=None)


def set_inner_request(campaign_request: Optional[str], output_format: Optional[str]) -> None:
    """Stash what the inner graph needs for the imminent invoke."""
    _CALLER_CAMPAIGN_REQUEST.set(campaign_request or None)
    _RESOLVED_OUTPUT_FORMAT.set(output_format or None)


def get_caller_campaign_request() -> Optional[str]:
    """Read (without consuming) the stashed contract JSON; None when unset."""
    return _CALLER_CAMPAIGN_REQUEST.get()


def get_resolved_output_format() -> Optional[str]:
    """Read (without consuming) the stashed output format; None when unset."""
    return _RESOLVED_OUTPUT_FORMAT.get()
