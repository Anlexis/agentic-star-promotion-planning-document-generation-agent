# RET-C2-003 — the caller-visible ERROR envelope carries closed-set labels only.
#
# On any non-success invoke the caller must receive values this template chose
# from a closed set — a constant reason code — and nothing read from error_log
# or from any other node- or framework-authored string. In this repository the
# live channel is RetC2003Agent.get_output(): the backbone routes every
# non-success status straight to finalize, and the override used to copy an
# allow-listed reduction of state["error_log"] into the invoke body. An entry
# there can be PreProcessNode's rejection line (which names a caller field),
# PostProcessNode's refusal line, the framework's trust-gate denial, or —
# through the framework's exception wrapper — an exception's message and its
# traceback with absolute source paths. A prefix allow-list is not a closed
# set; not projecting the channel is.
#
# Two surfaces are held here:
#   - RetC2003Agent.get_output(), parameterised over every non-success shape
#     the state can take at finalize, including the ones a compiled run cannot
#     be coaxed into (a foreign value in the reason slot, a surviving pre-gate
#     result / formatted_output / promotion_plan);
#   - PostProcessNode's containment delta, parameterised over every layer that
#     can withhold and over both entry points (execute() and the framework
#     pipeline): it records the closed-set reason, clears every output-bearing
#     field and re-emits nothing.
# The full path through the real ASGI /invoke is held in
# tests/proof_of_boundary/test_pb_invoke_http.py.
#
# The sentinel is deliberately NOT credential-shaped and carries no trace
# fragment: a redaction- or allow-list-based envelope passes it straight
# through, which is the defect these tests must fail on.

import json
from unittest.mock import MagicMock

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import RetC2003Agent
from src.nodes.post_process_node import (
    ERROR_REASONS,
    PostProcessNode,
    _REASON_OUTPUT_WITHHELD,
    _REASON_WORKFLOW_FAILED,
    error_envelope,
)

_ERROR = AgentStatus.ERROR.value
_SUCCESS = AgentStatus.SUCCESS.value

# Assembled at runtime (never a committed literal): a name, an email and a
# token-shaped fragment — what an echoed upstream body or a wrapped exception
# message can carry.
_FRAGMENTS = ("A. Tanaka", "a.tanaka@example.com", "sk-" + "live-xxx")
_SENTINEL = (
    "boom: upstream said {'customer':'"
    + _FRAGMENTS[0]
    + "','email':'"
    + _FRAGMENTS[1]
    + "','token':'"
    + _FRAGMENTS[2]
    + "'}"
)
_MARKERS = (_SENTINEL, "upstream said", *_FRAGMENTS)

# Entries of the shapes the internal channel really carries — this template's
# own lines and the framework's. Internal too, never projected.
_PRE_PROCESS_LINE = "PreProcessNode: input_context contains unsupported field(s): " + _FRAGMENTS[0]
_POST_PROCESS_LINE = "PostProcessNode: the generated plan contained a credential-shaped value and was withheld"
_TRUST_DENIAL = "[PreProcessNode] trust gate denied: required=verified_external, caller=anonymous"
_WRAPPED_EXCEPTION = (
    "[PromotionPlanGraphNode] " + _SENTINEL + '\nTraceback (most recent call last):\n  File "/abs/x.py", line 1'
)
_INTERNAL_ENTRIES = [_SENTINEL, _PRE_PROCESS_LINE, _POST_PROCESS_LINE, _TRUST_DENIAL, _WRAPPED_EXCEPTION]
_INTERNAL_MARKERS = (
    *_MARKERS,
    "PreProcessNode: ",
    "PostProcessNode: ",
    "[PreProcessNode]",
    "[PromotionPlanGraphNode]",
    "trust gate denied",
    "Traceback",
    'File "',
)

_BASE_ENVELOPE_KEYS = {"output", "status", "trace_id", "correlation_id", "node_history"}
_OUTPUT_BEARING_FIELDS = ("result", "formatted_output", "promotion_plan")
_BRIEF = "Seasonal sale campaign for loyalty members across the autumn outerwear range."
_DOCUMENT = "# Promotion Plan\n\n## Overview\n\nA seasonal_sale campaign for loyalty_members.\n"


def _strings(value):
    """Every string reachable in value: dict keys and values, list/tuple items,
    and the repr of anything else that is not a plain scalar."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from _strings(key)
            yield from _strings(child)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for child in value:
            yield from _strings(child)
    elif value is not None and not isinstance(value, (bool, int, float)):
        yield repr(value)


def _found(mapping, *markers) -> list:
    """The markers reachable anywhere inside mapping, walking nested values."""
    texts = list(_strings(mapping))
    return [marker for marker in markers if any(marker in text for text in texts)]


def _assert_internal_text_absent(mapping: dict) -> None:
    assert _found(mapping, *_INTERNAL_MARKERS) == [], mapping
    rendered = json.dumps(mapping, default=str)
    for marker in _INTERNAL_MARKERS:
        assert marker not in rendered


# ── RetC2003Agent.get_output(): the invoke envelope on every non-success state ─


def _finalize_state(**overrides) -> dict:
    """The outer state as finalize sees it after a non-success run: the
    internal channel carries every shape of entry the pipeline can put there."""
    state = {
        "status": _ERROR,
        "user_input": _BRIEF,
        "validated_input": None,
        "campaign_request": None,
        "error_log": list(_INTERNAL_ENTRIES),
        "trace_id": "tr",
        "correlation_id": "co",
        "node_history": ["InitializeNode", "PreProcessNode", "FinalizeNode"],
    }
    state.update(overrides)
    return state


_NON_SUCCESS_STATES = [
    pytest.param({}, _REASON_WORKFLOW_FAILED, id="caller-contract-rejected-at-pre-process"),
    pytest.param({"status": AgentStatus.TIMEOUT.value}, _REASON_WORKFLOW_FAILED, id="timeout-status"),
    pytest.param(
        {"error_reason": _REASON_OUTPUT_WITHHELD, "result": "", "formatted_output": "", "promotion_plan": ""},
        _REASON_OUTPUT_WITHHELD,
        id="post-process-withheld",
    ),
    pytest.param({"error_reason": _SENTINEL}, _REASON_WORKFLOW_FAILED, id="reason-outside-the-closed-set"),
    pytest.param({"error_reason": [_REASON_OUTPUT_WITHHELD]}, _REASON_WORKFLOW_FAILED, id="reason-not-a-string"),
    pytest.param({"result": _SENTINEL}, _REASON_WORKFLOW_FAILED, id="pre-gate-result-survives-in-state"),
    pytest.param({"formatted_output": _SENTINEL}, _REASON_WORKFLOW_FAILED, id="formatted-output-survives-in-state"),
    pytest.param({"promotion_plan": _SENTINEL}, _REASON_WORKFLOW_FAILED, id="promotion-plan-survives-in-state"),
    pytest.param(
        {"error_reason": _REASON_OUTPUT_WITHHELD, "result": _SENTINEL, "formatted_output": _SENTINEL},
        _REASON_OUTPUT_WITHHELD,
        id="withheld-but-fields-survive-in-state",
    ),
]


class TestInvokeErrorEnvelope:
    @pytest.mark.parametrize("overrides, reason", _NON_SUCCESS_STATES)
    def test_error_values_are_drawn_from_the_declared_constants(self, overrides, reason):
        out = RetC2003Agent().get_output(_finalize_state(**overrides))

        assert out["status"] != _SUCCESS
        assert set(out["error"]) == {"reason"}
        assert out["error"]["reason"] in ERROR_REASONS
        assert out["error"] == error_envelope(reason)
        assert out["error"], "the error envelope must stay truthy"
        assert out["output"] is None
        assert "error_log" not in out

    @pytest.mark.parametrize("overrides, reason", _NON_SUCCESS_STATES)
    def test_internal_text_appears_nowhere_in_the_invoke_envelope(self, overrides, reason):
        _assert_internal_text_absent(RetC2003Agent().get_output(_finalize_state(**overrides)))

    @pytest.mark.parametrize("overrides, reason", _NON_SUCCESS_STATES)
    def test_the_envelope_keys_are_a_fixed_set(self, overrides, reason):
        out = RetC2003Agent().get_output(_finalize_state(**overrides))
        assert set(out) == _BASE_ENVELOPE_KEYS | {"error"}

    def test_success_envelope_is_unchanged(self):
        out = RetC2003Agent().get_output(
            _finalize_state(
                status=_SUCCESS,
                result=_DOCUMENT,
                formatted_output=_DOCUMENT,
                promotion_plan=_DOCUMENT,
                error_log=[],
            )
        )

        assert out["status"] == _SUCCESS
        assert out["output"] == _DOCUMENT
        assert "error" not in out
        assert "error_log" not in out
        assert set(out) == _BASE_ENVELOPE_KEYS

    def test_a_stale_reason_cannot_turn_a_success_into_an_error(self):
        out = RetC2003Agent().get_output(
            _finalize_state(
                status=_SUCCESS,
                result=_DOCUMENT,
                formatted_output=_DOCUMENT,
                error_reason=_REASON_OUTPUT_WITHHELD,
                error_log=[],
            )
        )

        assert out["status"] == _SUCCESS
        assert out["output"] == _DOCUMENT
        assert "error" not in out

    def test_the_probe_finds_the_sentinel_where_it_lives(self):
        # Verify the verifier: the same walk DOES find every marker in a mapping
        # that carries it, so the "nowhere" assertions above are not vacuous.
        carrier = {"error_log": list(_INTERNAL_ENTRIES), "nested": {"deep": [{"k": _SENTINEL}]}}
        assert set(_found(carrier, *_INTERNAL_MARKERS)) == set(_INTERNAL_MARKERS)


# ── PostProcessNode: the containment delta on every layer that can withhold ──


@pytest.fixture
def audit_spy(monkeypatch):
    spy = MagicMock()
    monkeypatch.setattr("src.nodes.post_process_node.emit_trace_event", spy)
    return spy


def _gate_state(document: str) -> dict:
    return {
        "status": _SUCCESS,
        "user_input": _BRIEF,
        "validated_input": _BRIEF,
        "result": document,
        "promotion_plan": document,
        # The internal channel already carries the sentinel when the boundary runs.
        "error_log": [_SENTINEL],
        # For the framework pipeline entry point (node(state)).
        "caller_trust_level": TrustLevel.VERIFIED_EXTERNAL.value,
        "session_id": "closed-set-session",
        "trace_id": "closed-set-trace",
        "correlation_id": "closed-set-test",
        "node_history": [],
        "execution_time": {},
    }


# Runtime-assembled: the token part alone is not credential-shaped.
_CREDENTIALLED = (
    "## Overview\n\nPlan for the loyalty campaign.\n\n## Budget Allocation\n\nAuthorize with Bearer "
    + "abc123def456ghi789jkl"
    + ".\n"
)
_OFF_GRID = "## Overview\n\nPlan for the loyalty campaign.\n\n## Budget Allocation\n\nOnline media: JPY 1234567.\n"

# (document, which detector look fires). The post-snap layer is reached by
# letting the detector pass the document on its first look and fire on the
# re-scan after the numeric snap.
_WITHHOLD_PATHS = [
    pytest.param("", None, id="empty-plan"),
    pytest.param(_CREDENTIALLED, None, id="credential-before-the-snap"),
    pytest.param(_OFF_GRID, "post_snap", id="credential-after-the-snap"),
]
_ENTRY_POINTS = ["execute", "call"]


def _arm(monkeypatch, trigger) -> None:
    if trigger == "post_snap":
        looks: list = []

        def _second_look_only(text: str) -> bool:
            looks.append(text)
            return len(looks) >= 2

        monkeypatch.setattr("src.nodes.post_process_node.detect_credentials", _second_look_only)


def _drive(state: dict, entry: str) -> dict:
    node = PostProcessNode()
    return node.execute(state) if entry == "execute" else node(state)


class TestContainmentRecordsTheClosedSetReason:
    @pytest.mark.parametrize("document, trigger", _WITHHOLD_PATHS)
    @pytest.mark.parametrize("entry", _ENTRY_POINTS)
    def test_delta_carries_the_declared_reason_and_clears_every_field(
        self, audit_spy, monkeypatch, document, trigger, entry
    ):
        _arm(monkeypatch, trigger)
        result = _drive(_gate_state(document), entry)

        assert result["status"] == _ERROR
        assert result["error_reason"] == _REASON_OUTPUT_WITHHELD
        assert result["error_reason"] in ERROR_REASONS
        for field in _OUTPUT_BEARING_FIELDS:
            assert field in result, f"{field} was not cleared on containment"
            assert result[field] == ""
        # The fallback chain the base envelope reads resolves to nothing.
        assert not (result.get("formatted_output") or result.get("result"))

    @pytest.mark.parametrize("document, trigger", _WITHHOLD_PATHS)
    @pytest.mark.parametrize("entry", _ENTRY_POINTS)
    def test_delta_writes_its_own_line_and_re_emits_nothing(self, audit_spy, monkeypatch, document, trigger, entry):
        _arm(monkeypatch, trigger)
        result = _drive(_gate_state(document), entry)

        # One line, this node's own, naming the layer — never the document.
        assert len(result["error_log"]) == 1, result["error_log"]
        assert result["error_log"][0].startswith("PostProcessNode: ")
        # The entry already in error_log is not re-emitted (the state reducer
        # appends, so it would be duplicated) and appears nowhere in the delta;
        # neither does the refused document.
        assert _found(result, *_MARKERS) == [], result
        assert "loyalty campaign" not in json.dumps(result, default=str)

    @pytest.mark.parametrize("document, trigger", _WITHHOLD_PATHS)
    def test_the_audit_event_carries_a_cause_code_and_no_text(self, audit_spy, monkeypatch, document, trigger):
        _arm(monkeypatch, trigger)
        PostProcessNode().execute(_gate_state(document))

        events = [(call.args[0], call.args[1]) for call in audit_spy.call_args_list if len(call.args) > 1]
        assert events, "a withheld document must be audited"
        assert events[-1][0] == "promotion_plan_withheld"
        assert events[-1][1]["reason"]
        for _name, payload in events:
            assert _found(payload, *_MARKERS) == [], payload
            assert "loyalty campaign" not in json.dumps(payload, default=str)

    def test_a_clean_document_records_no_reason(self, audit_spy):
        result = PostProcessNode().execute(_gate_state(_DOCUMENT))

        assert result["status"] == _SUCCESS
        assert "error_reason" not in result
        assert result["formatted_output"] == result["result"]


# ── The envelope builder itself ───────────────────────────────────────────────


class TestErrorEnvelopeBuilder:
    def test_every_declared_reason_builds_a_truthy_single_key_envelope(self):
        for reason in ERROR_REASONS:
            envelope = error_envelope(reason)
            assert envelope
            assert envelope == {"reason": reason}

    def test_a_reason_outside_the_closed_set_is_refused_and_not_echoed(self):
        with pytest.raises(ValueError) as info:
            error_envelope(_SENTINEL)
        assert _SENTINEL not in str(info.value)
