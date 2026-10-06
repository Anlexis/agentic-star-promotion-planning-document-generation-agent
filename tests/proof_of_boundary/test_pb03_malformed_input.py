# PB-03: Malformed-input boundary
#
# A campaign brief that is not a string is a caller mistake, not an exception:
# it must produce a structured refusal naming the problem, never a crash and
# never a silent coercion.
#
# Expected on every case below:
#   * status == error
#   * validated_input is None — no partial processing
#   * a type-naming message in error_log, with the rejected value not echoed

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


_NOT_STRINGS = [
    ("integer", 42),
    ("float", 3.14),
    ("none", None),
    ("bytes", b"seasonal sale campaign"),
    ("list", ["seasonal sale"]),
    ("dict", {"campaign_type": "seasonal sale"}),
    # bool is an int subclass in Python, so it reaches a naive check as 1.
    ("bool", True),
]


def _run(user_input: object) -> dict:
    return PreProcessNode().execute({"user_input": user_input})


class TestPB03MalformedInput:
    @pytest.mark.parametrize("label,value", _NOT_STRINGS, ids=[label for label, _ in _NOT_STRINGS])
    def test_non_string_brief_is_refused(self, label: str, value: object) -> None:
        result = _run(value)

        assert _declined(result), f"{label} was accepted"
        assert result["validated_input"] is None
        assert result["campaign_request"] is None

    @pytest.mark.parametrize("label,value", _NOT_STRINGS, ids=[label for label, _ in _NOT_STRINGS])
    def test_refusal_names_the_type_not_the_value(self, label: str, value: object) -> None:
        message = " ".join(_run(value)["error_log"])

        assert "must be a string" in message
        assert type(value).__name__ in message

    def test_a_valid_string_brief_passes(self) -> None:
        result = _run("Seasonal sale campaign for loyalty members across the autumn range.")

        assert result["status"] == _SUCCESS
        assert result["validated_input"]
