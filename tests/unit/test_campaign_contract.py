"""The structured caller contract: every field bounded, inert, and fail-closed."""

import json

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


_BRIEF = "Seasonal sale campaign for loyalty members. Budget 5M JPY over 4 weeks, both channels."

# Values that must be rejected by EVERY numeric field. NaN and the infinities
# are the reason the check exists rather than a plain range comparison: they
# parse cleanly through float() and arrive intact through raw JSON, and every
# comparison against NaN is False — so a naive `low <= v <= high` passes them
# and the agent silently plans against a number that is not one.
_NON_FINITE = [
    "NaN",
    "Infinity",
    "-Infinity",
    float("nan"),
    float("inf"),
    float("-inf"),
    True,
    None,
    "",
    "eight",
    [4],
    {"weeks": 4},
]

_NUMERIC_FIELDS = {
    "duration_weeks": (0, 105, 4),  # (below range, above range, in range)
    "budget_total_jpy": (0, 10_000_000_001, 5_000_000),
}


def _run(input_context: dict) -> dict:
    return PreProcessNode().execute({"user_input": _BRIEF, "input_context": input_context})


class TestNumericFieldsFailClosed:
    @pytest.mark.parametrize("field", sorted(_NUMERIC_FIELDS))
    @pytest.mark.parametrize("value", _NON_FINITE, ids=[repr(v) for v in _NON_FINITE])
    def test_non_finite_rejected(self, field: str, value: object) -> None:
        result = _run({field: value})

        assert _declined(result), f"{field}={value!r} was accepted"
        assert result.get("campaign_request") is None

    @pytest.mark.parametrize("field", sorted(_NUMERIC_FIELDS))
    def test_out_of_range_rejected(self, field: str) -> None:
        below, above, _ = _NUMERIC_FIELDS[field]

        assert _declined(_run({field: below})), f"{field}={below} was accepted"
        assert _declined(_run({field: above})), f"{field}={above} was accepted"

    @pytest.mark.parametrize("field", sorted(_NUMERIC_FIELDS))
    def test_in_range_accepted_and_carried(self, field: str) -> None:
        _, _, good = _NUMERIC_FIELDS[field]
        result = _run({field: good})

        assert result["status"] == _SUCCESS
        assert json.loads(result["campaign_request"])[field] == good

    @pytest.mark.parametrize("field", sorted(_NUMERIC_FIELDS))
    def test_rejection_names_the_field_without_echoing_the_value(self, field: str) -> None:
        secret_shaped = "999999999999999999"
        message = " ".join(_run({field: secret_shaped}).get("error_log", []))

        assert field in message
        assert secret_shaped not in message


class TestRenderedStringsAreInert:
    @pytest.mark.parametrize("field", ["campaign_type", "target_segment"])
    @pytest.mark.parametrize(
        "value",
        [
            "Seasonal Sale",  # uppercase and a space
            "women 25-40",  # a hyphen
            "<b>bold</b>",  # markup
            "a" * 33,  # over length
            "",  # empty
            42,  # not a string
            "**Total** | 999,999",  # markdown that would reshape the document
        ],
    )
    def test_non_inert_value_rejected(self, field: str, value: object) -> None:
        assert _declined(_run({field: value})), f"{field}={value!r} was accepted"

    @pytest.mark.parametrize("field", ["campaign_type", "target_segment"])
    def test_inert_value_accepted(self, field: str) -> None:
        result = _run({field: "spring_clearance_2026"})

        assert result["status"] == _SUCCESS
        assert json.loads(result["campaign_request"])[field] == "spring_clearance_2026"


class TestClosedSets:
    @pytest.mark.parametrize("value", ["online", "offline", "both"])
    def test_channel_accepted(self, value: str) -> None:
        assert _run({"channel": value})["status"] == _SUCCESS

    @pytest.mark.parametrize("value", ["ONLINE", "in-store", "web", "", 1, None])
    def test_channel_rejected(self, value: object) -> None:
        assert _declined(_run({"channel": value}))

    @pytest.mark.parametrize("value", ["markdown", "json"])
    def test_output_format_accepted(self, value: str) -> None:
        assert _run({"output_format": value})["status"] == _SUCCESS

    @pytest.mark.parametrize("value", ["yaml", "MARKDOWN", "html", 0])
    def test_output_format_rejected(self, value: object) -> None:
        assert _declined(_run({"output_format": value}))


class TestStructuralLimits:
    def test_unknown_field_is_rejected_not_ignored(self) -> None:
        """Ignoring an undeclared key leaves it on a channel the framework scans."""
        result = _run({"campaign_type": "flash_sale", "shadow_budget": 1})

        assert _declined(result)
        assert "shadow_budget" in " ".join(result["error_log"])

    def test_non_object_context_is_rejected(self) -> None:
        assert _declined(_run(["campaign_type"]))  # type: ignore[arg-type]

    def test_absent_context_degrades_to_the_brief(self) -> None:
        """No structured data is a supported call, not an error."""
        result = PreProcessNode().execute({"user_input": _BRIEF})

        assert result["status"] == _SUCCESS
        assert result["campaign_request"] is None
        assert result["validated_input"] == _BRIEF


class TestRuntimeSuppliedContextField:
    """The execution environment attaches its own field to the context channel.

    A conversation history is put there by the runtime that serves the agent,
    not by the caller, and it arrives on every invocation made that way. A key
    set that only knows the caller contract turns each of those into an
    out-of-contract refusal, and the caller cannot remove a field it never
    added — the agent becomes unreachable on that route while every test that
    supplies its own context keeps passing.

    Accepting it is sound precisely because no constraint is attached to it:
    the node reads nothing from it and copies nothing out of it, so there is no
    promise the caller could be misled about. That is what separates it from an
    unknown CALLER field, which stays refused.
    """

    def test_runtime_field_is_accepted_and_does_not_leak(self) -> None:
        history = [{"role": "user", "content": "earlier turn about winter stock"}]
        result = _run({"campaign_type": "flash_sale", "conversation_history": history})

        assert result["status"] == _SUCCESS
        assert not result.get("error_code"), result
        carried = json.loads(result["campaign_request"])
        assert carried == {"campaign_type": "flash_sale"}
        assert "conversation_history" not in carried
        assert "winter stock" not in json.dumps(carried, ensure_ascii=False)
        assert "winter stock" not in result["validated_input"]

    def test_an_unknown_caller_field_is_still_refused(self) -> None:
        """The control: widening the set for the runtime must not open it to callers."""
        result = _run({"campaign_type": "flash_sale", "priority": "high"})

        assert _declined(result)
        assert "priority" in " ".join(result["error_log"])
