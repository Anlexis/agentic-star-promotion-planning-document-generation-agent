"""AgentCore Platform v1.0"""

# RET-C2-003 — DomainWorkflowGraph (inner BaseGraph)
#
# The inner graph of the nested architecture. It encapsulates the whole retail
# promotion-planning workflow:
#
#   START → input_parse → plan_generate → output_format → END
#
# Called by PromotionPlanGraphNode.get_subgraph() (graph.py). get_output()
# shapes the sub_result dict consumed by merge_output() there.
#
# Two things arrive from the outer layer and neither travels in State by
# itself:
#   format   — resolved by the outer agent from its own config, carried across
#              the boundary by the bridge and seeded into State as
#              `output_format`; the forwarded `output:` block is the fallback
#              when the inner graph is constructed directly
#   contract — the validated caller parameters, carried across the graph-node
#              boundary by src/graph/context_bridge.py and seeded into State as
#              `campaign_request`
# Both are seeded by _extra_initial_state(), because nodes take no constructor
# arguments and read everything from State.

from typing import Any, Dict

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus

from src.graph.context_bridge import get_caller_campaign_request, get_resolved_output_format
from src.nodes.input_parse import InputParseNode
from src.nodes.output_format import OutputFormatNode
from src.nodes.plan_generate import PlanGenerateNode
from src.schemas.state import State

_SUPPORTED_OUTPUT_FORMATS = frozenset({"markdown", "json"})


class DomainWorkflowGraph(BaseGraph):
    """Inner domain workflow graph for RET-C2-003.

    Inherits BaseGraph directly for a fully custom node topology. All nodes are
    FunctionNode subclasses returning partial-dict state updates; initialize and
    finalize are outer backbone concerns and are not registered here.
    """

    # ── Identity ────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        """Unique identifier for this inner graph."""
        return "ret_c2_003_promotion_plan_workflow"

    @property
    def state_schema(self) -> type:
        """TypedDict subclass shared across the inner and outer graph."""
        return State

    # ── Config validation ───────────────────────────────────────────────────

    def _validate_config(self) -> None:
        """Reject a forwarded config the inner graph cannot honour.

        Only `output.format` is read here, and it is checked against the closed
        set rather than accepted and silently ignored. RetC2003Agent runs the
        same check at start-up, which is where a misconfigured deployment should
        fail; this one covers the inner graph being constructed directly.
        """
        output = (self.config.get("configurable") or {}).get("output")
        if output is None:
            return
        if not isinstance(output, dict):
            raise ValueError(f"[{type(self).__name__}] 'output' must be a mapping, got: {type(output).__name__}")
        declared = output.get("format")
        if declared is not None and declared not in _SUPPORTED_OUTPUT_FORMATS:
            raise ValueError(
                f"[{type(self).__name__}] 'output.format' must be one of "
                f"{sorted(_SUPPORTED_OUTPUT_FORMATS)} — see config/config.yaml"
            )

    # ── Node registration ───────────────────────────────────────────────────

    def register_nodes(self) -> None:
        """Register the three domain nodes.

        No super() call — BaseGraph.register_nodes() is abstract. Every key
        registered here is referenced in add_edges().
        """
        self._nodes["input_parse"] = InputParseNode()
        self._nodes["plan_generate"] = PlanGenerateNode()
        self._nodes["output_format"] = OutputFormatNode()

    # ── Edge wiring ─────────────────────────────────────────────────────────

    def add_edges(self) -> None:
        """Wire the linear promotion-planning topology.

        Intentionally linear — no conditional branching between domain nodes,
        so add_conditional_edges() is not used.
        """
        self._sg.add_edge(START, "input_parse")
        self._sg.add_edge("input_parse", "plan_generate")
        self._sg.add_edge("plan_generate", "output_format")
        self._sg.add_edge("output_format", END)

    # ── Routing ─────────────────────────────────────────────────────────────

    def route(self, state: State) -> str:
        """Required by the BaseGraph ABC; unused on this linear topology.

        Annotated with this graph's OWN State rather than AgentState: LangGraph
        reads a path callable's annotation as its input schema and projects away
        every field the annotation does not carry, so an AgentState annotation
        would hide the domain fields from any future conditional branch.
        """
        if state.get("status") == AgentStatus.ERROR.value:
            return END
        return "output_format"

    # ── Initial state ───────────────────────────────────────────────────────

    def _extra_initial_state(self) -> Dict[str, Any]:
        """Seed the forwarded config and the bridged caller contract into State.

        Nodes are no-arg and read everything from State, so this is the only
        point at which either can reach them.
        """
        extra: Dict[str, Any] = {}

        # The outer agent resolved the format from its own config and stashed it;
        # the forwarded config block is the fallback for an inner graph
        # constructed directly, without an outer agent in front of it.
        resolved = get_resolved_output_format()
        if not resolved:
            output = (self.config.get("configurable") or {}).get("output") or {}
            resolved = output.get("format") if isinstance(output, dict) else None
        if isinstance(resolved, str) and resolved in _SUPPORTED_OUTPUT_FORMATS:
            extra["output_format"] = resolved

        campaign_request = get_caller_campaign_request()
        if campaign_request:
            extra["campaign_request"] = campaign_request

        return extra

    # ── Output shape ────────────────────────────────────────────────────────

    def get_output(self, state: AgentState) -> Dict[str, Any]:
        """Shape the dict returned to the outer graph as sub_result.

        Designed together with PromotionPlanGraphNode.merge_output():
            this get_output()   emits → promotion_plan, status, trace_id
            outer merge_output() reads → promotion_plan, status
        """
        return {
            # the reason must leave the subgraph or the outer graph cannot report it
            "error_code": state.get("error_code"),
            "promotion_plan": state.get("promotion_plan"),
            "status": state.get("status"),
            "trace_id": state.get("trace_id"),
            "node_history": state.get("node_history", []),
        }
