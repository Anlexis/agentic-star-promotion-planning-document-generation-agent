"""The projection arithmetic behind the Budget Allocation section."""

import pytest

from src.services.service import EXTERNAL_ROUND_UNIT, PromotionPlanService, normalize_channel, snap_to_grid

_SECTIONS = ("## Overview", "## Timeline", "## Channel Breakdown", "## Budget Allocation", "## KPIs")


@pytest.fixture()
def service() -> PromotionPlanService:
    return PromotionPlanService()


class TestBudgetArithmetic:
    @pytest.mark.parametrize(
        "channel,expected",
        [
            ("online", {"Online media": 6_000_000, "Creative & production": 1_200_000, "Contingency": 800_000}),
            ("offline", {"In-store / offline events": 6_000_000, "Contingency": 800_000}),
            ("both", {"Online media": 3_200_000, "Offline / in-store events": 3_200_000, "Contingency": 800_000}),
        ],
    )
    def test_shares_are_computed_from_the_declared_total(self, service, channel, expected) -> None:
        section = service.build_budget_allocation({"budget_total_jpy": 8_000_000}, channel)

        for label, amount in expected.items():
            assert f"| {label} |" in section
            assert f"{amount:,d}" in section

    def test_the_split_reconciles_to_the_total(self, service) -> None:
        """A plan whose parts do not add up to its own total is not usable."""
        total = 7_654_321
        section = service.build_budget_allocation({"budget_total_jpy": total}, "both")

        amounts = []
        for line in section.splitlines():
            cells = [cell.strip() for cell in line.split("|") if cell.strip()]
            if len(cells) == 3 and cells[2].replace(",", "").isdigit():
                amounts.append(int(cells[2].replace(",", "")))

        *categories, reported_total = amounts
        assert reported_total == snap_to_grid(total)
        # Each category is rounded independently, so the parts may differ from
        # the total by at most half a grid unit per category.
        assert abs(sum(categories) - reported_total) <= EXTERNAL_ROUND_UNIT * len(categories)

    @pytest.mark.parametrize("total", [1_000, 999, 1_500, 12_345_678, 10_000_000_000])
    def test_every_rendered_amount_is_on_the_grid(self, service, total) -> None:
        section = service.build_budget_allocation({"budget_total_jpy": total}, "both")

        for line in section.splitlines():
            for cell in line.split("|"):
                token = cell.strip().replace(",", "")
                if token.isdigit():
                    assert int(token) % EXTERNAL_ROUND_UNIT == 0, f"{cell!r} is off the grid"

    def test_without_a_declared_total_no_aggregate_is_claimed(self, service) -> None:
        section = service.build_budget_allocation({"budget_range": "unspecified"}, "both")

        assert "Aggregate (JPY)" not in section
        assert "| Online media | 40% |" in section

    @pytest.mark.parametrize(
        "value,expected",
        [
            (0, 0),
            (499, 0),
            (501, 1_000),
            (1_499, 1_000),
            (1_501, 2_000),
            # Exact halves round to even, which is what the output gate does too.
            # Pinned deliberately: the two must agree, or a figure the service
            # rendered would be re-rounded on its way out.
            (500, 0),
            (1_500, 2_000),
        ],
    )
    def test_snap_rounds_to_nearest_with_half_to_even(self, value, expected) -> None:
        assert snap_to_grid(value) == expected


class TestPlanShape:
    def test_all_five_sections_are_present(self, service) -> None:
        plan = service.build_plan({"campaign_type": "flash_sale", "channel": "both"})

        for heading in _SECTIONS:
            assert heading in plan

    def test_the_schema_note_states_the_invariant(self, service) -> None:
        plan = service.build_plan({"budget_total_jpy": 1_000_000, "channel": "online"})

        assert "rounded to the nearest 1,000" in plan

    def test_timeline_spans_the_declared_duration(self, service) -> None:
        plan = service.build_plan({"duration_weeks": 12, "channel": "online"})

        assert "12 weeks" in plan
        # 20/25/40/15 of twelve weeks.
        assert "(~2 weeks)" in plan and "(~3 weeks)" in plan and "(~5 weeks)" in plan

    def test_a_one_week_campaign_reads_in_the_singular(self, service) -> None:
        assert "1 week)" in service.build_plan({"duration_weeks": 1, "channel": "online"})

    @pytest.mark.parametrize(
        "channel,present,absent",
        [("online", "**Online:**", "**Offline:**"), ("offline", "**Offline:**", "**Online:**")],
    )
    def test_channel_breakdown_follows_the_mix(self, service, channel, present, absent) -> None:
        section = service.build_channel_breakdown(channel)

        assert present in section
        assert absent not in section

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("online", "online"),
            ("OFFLINE", "offline"),
            ("  Both  ", "both"),
            ("carrier_pigeon", "both"),
            (None, "both"),
            ("", "both"),
        ],
    )
    def test_channel_normalisation_defaults_to_the_full_mix(self, raw, expected) -> None:
        assert normalize_channel(raw) == expected
