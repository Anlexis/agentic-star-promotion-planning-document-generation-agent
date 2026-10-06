"""The runtime config in config/config.yaml reaches the code that reads it."""

import pytest

from framework.errors import ConfigError
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.context_bridge import set_inner_request
from src.graph.domain_workflow_graph import DomainWorkflowGraph
from src.graph.graph import RetC2003Agent, load_runtime_config

_BRIEF = "Seasonal sale campaign for loyalty members across the autumn outerwear range."


def _ctx() -> InvocationContext:
    return InvocationContext(session_id="config-test", caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)


def _invoke(config: dict, input_context: dict | None = None) -> dict:
    set_inner_request(None, None)  # the bridge is per-task; start from a clean one
    agent = RetC2003Agent(config=config)
    agent.compile()
    return agent.invoke(_BRIEF, ctx=_ctx(), input_context=input_context or {})


class TestDeclaredValuesAreLive:
    """A declared value that no code reads is worse than no declaration at all."""

    def test_the_shipped_file_parses_and_carries_the_documented_keys(self) -> None:
        runtime = load_runtime_config()

        assert runtime["max_retry"] == 3
        assert runtime["output"]["format"] == "markdown"

    def test_a_missing_file_degrades_to_defaults(self, monkeypatch) -> None:
        import src.graph.graph as graph_module

        monkeypatch.setattr(graph_module, "_RUNTIME_CONFIG_PATH", graph_module.Path("/nonexistent/config.yaml"))
        assert load_runtime_config() == {}

    def test_max_retry_reaches_the_backbone(self) -> None:
        agent = RetC2003Agent(config={"max_retry": 7})

        assert agent.config["max_retry"] == 7

    def test_declared_format_reaches_the_inner_graph(self) -> None:
        """End-to-end: the declaration alone changes the representation."""
        markdown = _invoke({"output": {"format": "markdown"}})["output"]
        rendered_json = _invoke({"output": {"format": "json"}})["output"]

        assert markdown.lstrip().startswith("#")
        assert not rendered_json.lstrip().startswith("#")
        assert '"budget_allocation"' in rendered_json

    def test_a_caller_declaration_overrides_the_deployment_default(self) -> None:
        output = _invoke({"output": {"format": "json"}}, {"output_format": "markdown"})["output"]

        assert output.lstrip().startswith("#")


class TestConfigIsValidatedNotIgnored:
    @pytest.mark.parametrize("declared", ["yaml", "html", "MARKDOWN", ""])
    def test_unsupported_format_fails_at_start_up(self, declared: str) -> None:
        """Not at the first invoke: a misconfigured deployment should not start."""
        with pytest.raises(ConfigError, match="output.format"):
            RetC2003Agent(config={"output": {"format": declared}}).compile()

    def test_non_mapping_output_block_fails_at_start_up(self) -> None:
        with pytest.raises(ConfigError, match="output"):
            RetC2003Agent(config={"output": "markdown"}).compile()

    def test_the_inner_graph_checks_its_own_forwarded_config(self) -> None:
        """Covers the inner graph being constructed directly, not through the agent."""
        with pytest.raises(ValueError, match="output.format"):
            DomainWorkflowGraph(config={"configurable": {"output": {"format": "yaml"}}}).compile()

    def test_an_absent_output_block_is_allowed(self) -> None:
        RetC2003Agent(config={"max_retry": 3}).compile()
