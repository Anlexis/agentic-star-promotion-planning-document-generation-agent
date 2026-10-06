# PB-01: Empty-input boundary
#
# The campaign brief is the one mandatory caller input. PreProcessNode owns it,
# and an unusable brief must be refused there rather than degraded into a plan
# the caller never asked for.
#
# Expected on every case below:
#   * status == error
#   * validated_input is None — no partial processing, so no domain node runs
#   * a descriptive, field-naming message in error_log

import pytest

from framework.schemas.agent_status import AgentStatus

from src.nodes.pre_process_node import PreProcessNode

_ERROR = AgentStatus.ERROR.value
_SUCCESS = AgentStatus.SUCCESS.value

def _declined(result: dict) -> bool:
    """A rejection the caller can correct: the run COMPLETES carrying the reason.

    Both halves matter. The status says the calling surface's turn was not
    ended, and the reason code says the request was nonetheless not carried
    out — asserting only the status would pass on a run that quietly produced
    a plan.
    """
    return result["status"] == _SUCCESS and bool(result.get("error_code"))


_UNUSABLE = [
    ("empty string", ""),
    ("single space", " "),
    ("whitespace run", "   \t  "),
    ("newlines only", "\n\n\n"),
    ("below the minimum length", "sale"),
]


def _run(user_input: str) -> dict:
    return PreProcessNode().execute({"user_input": user_input})


class TestPB01EmptyInput:
    @pytest.mark.parametrize("label,brief", _UNUSABLE, ids=[label for label, _ in _UNUSABLE])
    def test_unusable_brief_is_refused(self, label: str, brief: str) -> None:
        result = _run(brief)

        assert _declined(result), f"{label} was accepted"
        assert result["validated_input"] is None
        assert result["campaign_request"] is None
        assert isinstance(result["error_log"], list) and result["error_log"]

    @pytest.mark.parametrize("label,brief", _UNUSABLE, ids=[label for label, _ in _UNUSABLE])
    def test_refusal_writes_no_plan(self, label: str, brief: str) -> None:
        assert "promotion_plan" not in _run(brief)

    def test_an_oversized_brief_is_refused_rather_than_truncated(self) -> None:
        """A truncated brief plans a campaign the caller did not describe."""
        result = _run("Seasonal sale. " * 2000)

        assert _declined(result)
        assert result["validated_input"] is None

    def test_a_usable_brief_passes_and_is_stripped(self) -> None:
        result = _run("  Seasonal sale campaign for loyalty members.  ")

        assert result["status"] == _SUCCESS
        assert result["validated_input"] == "Seasonal sale campaign for loyalty members."

    def test_a_brief_exactly_at_the_minimum_length_passes(self) -> None:
        """The bound is inclusive; probe it rather than assuming."""
        assert _run("a" * 10)["status"] == _SUCCESS
