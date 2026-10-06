"""The output boundary: the stated invariants, enforced for every representation."""

import pytest

from framework.schemas.agent_status import AgentStatus

from src.nodes.post_process_node import _enforce_precision
from src.nodes.post_process_node import PostProcessNode

_ERROR = AgentStatus.ERROR.value
_SUCCESS = AgentStatus.SUCCESS.value

# Forms that MUST snap onto the 1,000-unit grid. Each one is a representation
# an earlier version of this grammar let through: magnitude below 10,000,
# marker after the value, a currency symbol rather than a code, an explicit
# sign, a multi-character or tab delimiter, and the comma-grouped form that
# leftmost matching would otherwise chop into "JPY 1".
_MUST_SNAP = [
    ("JPY 9999", "JPY 10,000"),
    ("9999 JPY", "10,000 JPY"),
    ("JPY-9999", "JPY-10,000"),
    ("JPY +9999", "JPY +10,000"),
    ("JPY  9999", "JPY  10,000"),
    ("JPY\t9999", "JPY\t10,000"),
    ("JPY\n9999", "JPY\n10,000"),
    ("¥9999", "¥10,000"),
    ("9999円", "10,000円"),
    ("￥9999", "￥10,000"),
    ("JPY 1,234", "JPY 1,000"),
    ("123456", "123,000"),
    ("1,234,567", "1,235,000"),
    ("JPY 1234.56", "JPY 1,000"),
    ("SKU 9999", "SKU 10,000"),
]

# Forms that MUST come through byte-identical. These are the ones a gate that
# is merely aggressive gets wrong, and each costs the reader something real:
# a corrupted part number, a rewritten ratio, a renumbered section heading.
_MUST_NOT_CHANGE = [
    "JPY 1,000",  # already on the grid
    "8.512345",  # a decimal fraction is not an amount
    "9999.99999%",  # nor is a percentage
    "ratio 0.123456",
    "JPY 1234.56m",  # the fraction cannot be backtracked out of
    "Currency: JPY\n\n3. Cash Position",  # the delimiter never spans a blank line
    "sku_48210",  # this template's own render alphabet
    "spring_sale_2026",
    "campaign_123456",
    "women_25_40",
    "SKU-9999",  # an identifier, not a negative amount
    "SKF-6205",
    "STU-1234",
    "back-to-school",
    "STAR 2026",
    "+10%",
    "3%",
    "~2 weeks",
    "| Online media | 40% | 2,000,000 |",
    "SSN 123-45-6789",  # must survive intact for a pattern scan to see it
    "TAX 987-65-4321",
]


class TestPrecisionGrid:
    @pytest.mark.parametrize("text,expected", _MUST_SNAP, ids=[t for t, _ in _MUST_SNAP])
    def test_off_grid_value_snaps(self, text: str, expected: str) -> None:
        got, snaps = _enforce_precision(text)

        assert got == expected
        assert snaps == 1

    @pytest.mark.parametrize("text", _MUST_NOT_CHANGE)
    def test_structural_token_is_untouched(self, text: str) -> None:
        got, snaps = _enforce_precision(text)

        assert got == text
        assert snaps == 0

    def test_no_magnitude_exemption(self) -> None:
        """If the invariant says every amount, small amounts are amounts too."""
        assert _enforce_precision("JPY 9,999")[0] == "JPY 10,000"
        assert _enforce_precision("JPY 1")[0] == "JPY 0"


def _run_gate(document: str, brief: str = "") -> dict:
    return PostProcessNode().execute({"result": document, "promotion_plan": document, "validated_input": brief})


class TestOutputGateContainment:
    _CREDENTIAL_DOC = (
        "## Overview\n\nPlan for the loyalty campaign.\n\n"
        "## Budget Allocation\n\nAuthorize with Bearer abc123def456ghi789jkl.\n"
    )

    def test_credentialled_document_is_withheld(self) -> None:
        result = _run_gate(self._CREDENTIAL_DOC)

        assert result["status"] == _ERROR

    def test_error_envelope_carries_no_released_text(self) -> None:
        """Containment, not merely refusal: the fallback path must find nothing.

        AgentBaseGraph.get_output() reads formatted_output and falls back to
        result even on error status, so a gate that raises — or returns ERROR
        without clearing — still ships the un-gated answer inside the error
        envelope.
        """
        result = _run_gate(self._CREDENTIAL_DOC)
        envelope = " ".join(str(value) for value in result.values())

        assert "Bearer abc123def456ghi789jkl" not in envelope
        assert "loyalty campaign" not in envelope
        assert "Traceback" not in envelope
        assert "src/nodes" not in envelope
        # The fallback chain itself must resolve to nothing.
        assert not (result.get("formatted_output") or result.get("result"))

    def test_every_output_bearing_field_is_cleared(self) -> None:
        result = _run_gate(self._CREDENTIAL_DOC)

        for field in ("result", "formatted_output", "promotion_plan"):
            assert field in result, f"{field} was not cleared on containment"
            assert not result[field]

    def test_empty_plan_is_contained_the_same_way(self) -> None:
        """Every path that returns non-success is measured, not just the headline one."""
        result = PostProcessNode().execute({"result": "", "promotion_plan": ""})

        assert result["status"] == _ERROR
        for field in ("result", "formatted_output", "promotion_plan"):
            assert not result[field]

    def test_clean_document_is_released_and_gated(self) -> None:
        document = "## Budget Allocation\n\n| Online media | 40% | JPY 1234 |\n"
        result = _run_gate(document)

        assert result["status"] == _SUCCESS
        assert "JPY 1,000" in result["result"]
        assert result["result"] == result["formatted_output"]

    def test_verbatim_brief_is_redacted_independently(self) -> None:
        brief = (
            "Seasonal sale campaign targeting loyalty members with a focus on autumn "
            "outerwear and accessories across the northern region stores."
        )
        result = _run_gate(f"## Overview\n\n{brief}\n\n## KPIs\n\nSales lift +10%.", brief)

        assert result["status"] == _SUCCESS
        assert brief not in result["result"]
        assert "[REDACTED]" in result["result"]
