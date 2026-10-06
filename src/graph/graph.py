"""AgentCore Platform v1.0"""

# ─────────────────────────────────────────────────────────────────────────────
# RET-C2-003 — Promotion Planning Document Generator
#
# Nested two-layer architecture:
#
#   Outer backbone (fixed — do NOT override add_edges()):
#     START → initialize → pre_process → main → {route} → post_process → finalize → END
#
#   The `main` slot is PromotionPlanGraphNode(GraphNode), which delegates the
#   whole domain workflow to DomainWorkflowGraph (inner BaseGraph):
#     START → input_parse → plan_generate → output_format → END
#
# Directory layout:
#   src/graph/graph.py                 ← outer graph (this file)
#   src/graph/domain_workflow_graph.py ← inner graph (domain topology)
#   src/graph/context_bridge.py        ← caller contract across the boundary
# ─────────────────────────────────────────────────────────────────────────────

from pathlib import Path
from typing import Any, ClassVar, Dict

import yaml

from framework.errors import ConfigError
from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus

from src.graph.context_bridge import set_inner_request
from src.nodes.post_process_node import ERROR_REASONS, PostProcessNode, _REASON_WORKFLOW_FAILED, error_envelope
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State

# The representations OutputFormatNode can actually render. Declared here as
# well as in the inner graph because the two checks fire at different moments:
# this one at start-up, the inner one if the graph is constructed directly.
_SUPPORTED_OUTPUT_FORMATS = frozenset({"markdown", "json"})

# config/agent.yaml is the static registry manifest (flat, identity keys only);
# every runtime value lives in config/config.yaml.
_RUNTIME_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"


def load_runtime_config() -> Dict[str, Any]:
    """Load config/config.yaml (max_retry, timeout_s, output block).

    Shared by the standalone entry point — which passes it to the agent
    constructor so the backbone's retry setting actually applies — and by
    PromotionPlanGraphNode._parent_config(), which forwards the `output:` block
    to the inner graph. A missing or unreadable file degrades to {}, and the
    inner nodes then fall back to their documented defaults.
    """
    try:
        loaded = yaml.safe_load(_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


class PromotionPlanGraphNode(GraphNode):
    """GraphNode assigned to the `main` slot; wraps DomainWorkflowGraph.

    No constructor arguments (nodes are no-arg; constructor args raise
    TypeError at graph build). Configuration reaches the subgraph through
    _parent_config(), and the validated caller contract through the context
    bridge — GraphNode.execute() calls
    ``subgraph.invoke(user_input, session_id=..., ctx=...)`` and does not
    forward input_context, so state["input_context"] is always {} inside the
    inner graph.
    """

    # "propagate": re-raise inner graph exceptions as SubgraphError (fail fast).
    error_strategy: ClassVar[str] = "propagate"

    # Human review interrupts are handled inside the inner graph only.
    propagate_hitl: ClassVar[bool] = False

    def get_subgraph(self) -> Any:
        """Instantiate and return the inner domain workflow graph.

        Imported inside the method to avoid a circular import at module load.
        """
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        return DomainWorkflowGraph(config=self._parent_config())

    def execute(self, state: AgentState) -> dict[str, Any]:
        """Skip the inner graph when the request was already found unacceptable.

        A request declined by pre_process has no validated input to act on, so
        running the inner graph would only produce a second, vaguer reason for
        the same rejection - and overwrite the specific one already settled.
        """
        marker = state.get("error_code")
        if marker:
            return {"status": AgentStatus.SUCCESS.value, "error_code": marker}
        result: dict[str, Any] = super().execute(state)
        return result

    def extract_input(self, state: AgentState) -> str:
        """Return the string input for inner_graph.invoke(), and bridge the contract.

        Runs immediately before the inner invoke, which is what makes it the
        correct place to stash what the inner graph needs: the ContextVars are
        read back by DomainWorkflowGraph._extra_initial_state() inside that same
        invoke. What crosses is the contract PreProcessNode validated — never the
        raw request body — plus the output format this agent resolved from its
        own config.
        """
        campaign_request = state.get("campaign_request")
        output_format = state.get("output_format")
        set_inner_request(
            campaign_request if isinstance(campaign_request, str) else None,
            output_format if isinstance(output_format, str) else None,
        )
        brief = state.get("validated_input") or state.get("user_input") or ""
        return brief if isinstance(brief, str) else ""

    def merge_output(self, state: AgentState, sub_result: Dict[str, Any]) -> Dict[str, Any]:
        """Map the inner graph's output into the outer state delta.

        Returns ONLY changed keys. Designed together with
        DomainWorkflowGraph.get_output():

            inner get_output() emits  → promotion_plan, status, trace_id
            this merge_output() reads → promotion_plan, status
        """
        return {
            # Outer reason wins: a reason settled before the inner run is the real
            # one, and a plain sub_result.get() would erase it.
            "error_code": state.get("error_code") or sub_result.get("error_code", ""),
            "result": sub_result.get("promotion_plan"),
            "promotion_plan": sub_result.get("promotion_plan"),
            "status": sub_result.get("status"),
        }

    def _parent_config(self) -> Dict[str, Any]:
        """Forward the runtime config the inner graph reads.

        Loads config/config.yaml and forwards the `output:` section — never an
        empty {} while the file declares one. An empty _parent_config() silently
        disconnects every declared setting: the inner graph then runs on
        framework defaults while config.yaml claims otherwise, and nothing
        fails. The backbone keys (max_retry, timeout_s) are consumed by the
        OUTER graph via its constructor config and are deliberately not
        forwarded — no inner node reads them.
        """
        runtime = load_runtime_config()
        configurable: Dict[str, Any] = {}
        if isinstance(runtime.get("output"), dict):
            configurable["output"] = runtime["output"]
        return {"configurable": configurable}


class RetC2003Agent(AgentBaseGraph):
    """Outer graph for RET-C2-003 Promotion Planning Document Generator.

    Inherits AgentBaseGraph directly (L1 Base). Domain logic is encapsulated in
    PromotionPlanGraphNode (main slot), which delegates to DomainWorkflowGraph.

    Backbone: initialize → pre_process → main → post_process → finalize (fixed).
    register_nodes() is the only override; add_edges() is not overridden —
    backbone wiring belongs to the framework.
    """

    @property
    def name(self) -> str:
        """Agent identifier registered with the platform registry."""
        return "ret_c2_003"

    @property
    def state_schema(self) -> type:
        return State

    def _validate_config(self) -> None:
        """Reject a runtime config this agent cannot honour, at start-up.

        The backbone keys are checked by the base implementation. `output.format`
        is checked here rather than left to the inner graph alone: the inner
        graph is compiled lazily on the first invoke, so a deployment with an
        unsupported format would otherwise start cleanly and fail per request.
        """
        super()._validate_config()
        output = self.config.get("output")
        if output is None:
            return
        if not isinstance(output, dict):
            raise ConfigError(f"[{type(self).__name__}] 'output' must be a mapping, got: {type(output).__name__}")
        declared = output.get("format")
        if declared is not None and declared not in _SUPPORTED_OUTPUT_FORMATS:
            raise ConfigError(
                f"[{type(self).__name__}] 'output.format' must be one of "
                f"{sorted(_SUPPORTED_OUTPUT_FORMATS)} — see config/config.yaml"
            )

    def register_nodes(self) -> None:
        """Fill all five backbone slots.

        super().register_nodes() MUST be called first — it injects the
        framework's default initialize and finalize nodes.
        """
        super().register_nodes()  # fills: initialize, finalize

        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = PromotionPlanGraphNode()
        self._nodes["post_process"] = PostProcessNode()

    def _extra_initial_state(self) -> Dict[str, Any]:
        """Seed the deployment's output format from THIS agent's own config.

        The value then travels to the inner graph through the context bridge, so
        the configuration the agent was constructed with is the configuration the
        inner graph honours. Reading the file independently in both layers would
        let them disagree, and nothing would report the disagreement.
        """
        output = self.config.get("output")
        declared = output.get("format") if isinstance(output, dict) else None
        return {"output_format": declared} if isinstance(declared, str) else {}

    def get_output(self, state: AgentState) -> Dict[str, Any]:
        """Close the invoke envelope on every non-success outcome.

        The base shape is ``{output, status, trace_id, correlation_id,
        node_history}`` with ``output = formatted_output or result`` and no
        status check. On the backbone every non-success status — a caller
        contract rejected at pre_process, a trust denial, an inner-workflow
        error or exception, a timeout — routes straight to finalize, so this
        override is the caller's last hop.

        On a non-success status the caller receives closed-set labels only:
        ``output`` is None (the base fallback is not consulted) and ``error``
        is ``error_envelope()`` — one constant reason code. ``output_withheld``
        is PostProcessNode's own reason, recorded in state as ``error_reason``
        when it refused the document; any other value in that slot is not
        trusted, and every other non-success outcome is ``workflow_failed``.
        ``error_log`` is never projected: it is the internal channel (state
        reducer, audit trail) and carries node- and framework-authored text —
        a line naming a caller field, or the framework's exception wrapper
        entry with the exception's message and a traceback with source paths.
        Reducing that text to an allow-listed subset is not a closed set.

        The success envelope is the base shape, untouched.
        """
        output: Dict[str, Any] = super().get_output(state)
        # A run that completed WITHOUT carrying out the request carries the
        # sentence saying what to correct, which PostProcessNode put in the
        # output-bearing slot. It is the base envelope that must reach the
        # caller, never the closed-set error envelope below: nothing failed,
        # and `error` would tell the caller the opposite. Checked ahead of the
        # status test so this stays true if the success branch ever starts
        # adding fields a declined run did not produce.
        if state.get("error_code"):
            return output
        if state.get("status") == AgentStatus.SUCCESS.value:
            return output
        recorded = state.get("error_reason")
        reason = recorded if isinstance(recorded, str) and recorded in ERROR_REASONS else _REASON_WORKFLOW_FAILED
        output["output"] = None
        output["error"] = error_envelope(reason)
        return output


# Alias kept for entry points that import the module-level graph class by name.
Graph = RetC2003Agent
