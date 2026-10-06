"""AgentCore Platform v1.0"""

# RET-C2-003 — PostProcessNode (outer backbone, post_process slot)
#
# The output boundary. Everything a caller ever sees leaves through here, so
# this node owns the document's two stated invariants and the containment
# behaviour when either of them cannot be met:
#
#   1. No credential-shaped string reaches the surface.
#   2. Every monetary figure is an aggregate on the 1,000-unit grid.
#
# Node contract:
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes
#  - Return AgentStatus enum value strings — not enum objects

import logging
import re
from typing import Any, ClassVar, Dict, List, Tuple

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import TrustLevel
from framework.security.credential_detector import detect_credentials
from framework.utils.audit_logger import emit_trace_event
from src.services.failure_message import EMPTY_INPUT, INPUT_REJECTED, INVALID_VALUE, TOO_LONG
from src.services.service import EXTERNAL_ROUND_UNIT


logger = logging.getLogger(__name__)

# ── The identifier guards ────────────────────────────────────────────────────
# The grammar below reads ANY standalone three-letter uppercase word as a
# currency marker, and any 5+-digit run as a monetary figure. Both readings are
# wrong inside an identifier, and this template renders identifiers: the caller
# contract locks every rendered string to `[a-z0-9_]`, so a product or campaign
# code arrives as `sku_48210` or `spring_sale_2026` — a digit run with an
# UNDERSCORE in front of it and no letters of its own to protect it. The guard
# class is therefore read off this template's own render alphabet rather than
# assumed: `A-Za-z0-9_-`. Dropping `_` from it is exactly how `sku_48210`
# becomes `sku_48,000`.
#
# The LEADING guard additionally carries `.`, so no alternative can enter a
# number part-way through and treat the tail of a decimal fraction as a value
# of its own. `.` is deliberately absent from the TRAILING guard — with it, an
# amount that ends a sentence would escape the grid.
_IDENT_CHAR = r"A-Za-z0-9_\-"
_LEAD_GUARD = rf"(?<![{_IDENT_CHAR}.])"
_TRAIL_GUARD = rf"(?![{_IDENT_CHAR}])"

_CURRENCY_MARKER = r"(?:\b[A-Z]{3}|[¥￥$€£円₩])"

# Delimiter between a currency marker and its value: horizontal whitespace and
# at most ONE newline — never a paragraph break. A plain `\s*` spans blank
# lines, so a three-letter uppercase word ending a line would bind to the number
# that opens the next block and rewrite it ("Currency: JPY\n\n3. Cash Position"
# -> "0. Cash Position"), i.e. the gate would rewrite document structure. Every
# leak form (spaces, tabs, a single newline, signed, symmetric, comma-grouped)
# still matches.
_GATE_DELIM = r"[ \t]*(?:\n[ \t]*)?"

# A monetary amount may carry a DECIMAL part, and every value alternative
# absorbs it into the SAME token. Without that the fraction of "9999.99999" is
# a standalone 5+-digit run in its own right and gets rewritten into a number
# the document never contained; and in currency context the integer part snaps
# while the fraction dangles ("JPY 1234.56" -> "JPY 1,000.56").
#
# The `(?!\.\d)` arm is what makes the absorption stick. A plain `(?:\.\d+)?`
# lets the engine backtrack out of the fraction and re-match the integer part
# alone whenever the text right after the fraction fails the trailing guard, and
# the dangling-fraction bug returns. Either the fraction is taken whole, or the
# pattern asserts there is not one.
_VAL_FRACTION = r"(?:\.\d+|(?!\.\d))"

_NUM_TOKEN_RE = re.compile(
    _LEAD_GUARD
    # marker THEN value: "JPY 9999", "JPY  -9999", "JPY\t9999", "¥9999".
    # The value alternatives accept the comma-grouped form FIRST: the regex is
    # leftmost-first, so without it "JPY 1,234" would match as marker + "1" and
    # the snap would mangle the number instead of rounding it.
    + rf"(?:(?P<pre>{_CURRENCY_MARKER}{_GATE_DELIM})"
    rf"(?P<val_after>[+-]?\d{{1,3}}(?:,\d{{3}})+{_VAL_FRACTION}|[+-]?\d{{1,4}}{_VAL_FRACTION})"
    # value THEN marker: "9999 JPY", "-9999\tJPY", "9999円", "+9999  $"
    rf"|(?P<val_before>[+-]?\d{{1,4}}{_VAL_FRACTION})(?P<post>{_GATE_DELIM}(?:[A-Z]{{3}}\b|[¥￥$€£円₩]))"
    # form-based, standalone at any magnitude: comma-grouped or 5+-digit runs
    rf"|(?P<val_form>[+-]?\d{{1,3}}(?:,\d{{3}})+{_VAL_FRACTION}|[+-]?\d{{5,}}{_VAL_FRACTION}))" + _TRAIL_GUARD
)

# ISO 4217 alphabetic codes. Consulted for ONE decision only: whether
# "<three uppercase letters>-<digits>" is a negative amount or an identifier.
# The two are lexically identical — "JPY-9999" (a signed amount, a real leak
# form) and "SKU-9999" (a product code) have the same shape — so no amount of
# guard-widening separates them; something has to know which three-letter words
# are currencies. ISO 4217 is a CLOSED, standardised vocabulary; the set of
# identifiers never could be, which is why the check runs this way round.
# Everywhere else the grammar still treats any standalone three-letter
# uppercase word as a marker, because there a false snap fails safe: a SEPARATED
# marker stays unrestricted, so "SKU 9999" still snaps.
_ISO_CURRENCY_CODES = frozenset(
    "AED AUD BRL CAD CHF CNY DKK EUR GBP HKD IDR ILS INR JPY KRW MXN MYR NOK NZD "
    "PHP PLN RUB SAR SEK SGD THB TRY TWD USD VND ZAR".split()
)

# A campaign brief shorter than this cannot be identified reliably inside the
# rendered plan, and a short common phrase would trigger on ordinary prose.
_VERBATIM_BRIEF_MIN_CHARS = 60

# Every state field that can carry released text to the caller. Cleared
# together on containment: AgentBaseGraph.get_output() reads formatted_output
# and falls back to result, and promotion_plan is what result was merged from.
_OUTPUT_BEARING_FIELDS: Tuple[str, ...] = ("result", "formatted_output", "promotion_plan")

# ── Caller-visible ERROR reason — a closed set ───────────────────────────────
#
# On every non-success invoke the caller receives ONE constant chosen here and
# nothing else: not an error_log line, not a field name, not an exception's
# text. Those stay on the internal channel (error_log — the state reducer
# appends to it, the audit trail reads it). A node- or framework-authored line
# can name a caller field or embed an exception's message and traceback, and
# reducing or redacting such text is not a closed set; not projecting it is.
_REASON_WORKFLOW_FAILED = "workflow_failed"  # a rejected caller contract, a trust denial or an inner-workflow error
_REASON_OUTPUT_WITHHELD = "output_withheld"  # this boundary refused the document
ERROR_REASONS = frozenset({_REASON_WORKFLOW_FAILED, _REASON_OUTPUT_WITHHELD})


def error_envelope(reason: str) -> Dict[str, str]:
    """The caller-visible ERROR envelope: a constant reason code and nothing else.

    Always carries its one key, so it is always truthy — a falsy value in a
    caller-facing slot would re-open AgentBaseGraph.get_output()'s
    ``formatted_output or result`` fallback.
    """
    if reason not in ERROR_REASONS:
        raise ValueError("error envelope reason must be one of ERROR_REASONS")
    return {"reason": reason}


def _contain(reason: str, new_errors: List[str]) -> Dict[str, Any]:
    """Fail-closed containment delta: nothing published, the closed-set reason recorded.

    Every output-bearing field is cleared — AgentBaseGraph.get_output() reads
    formatted_output and falls back to result, and promotion_plan is what
    result was merged from. ``new_errors`` are this node's own lines (they name
    the layer that refused, never the document) and go to error_log, the
    internal channel; entries already there are not re-emitted, because the
    state reducer appends and would duplicate them. RetC2003Agent.get_output()
    projects ``error_reason`` — and only that — to the caller.
    """
    contained: Dict[str, Any] = {
        "status": AgentStatus.ERROR.value,
        "error_reason": reason,
        "error_log": list(new_errors),
    }
    for field in _OUTPUT_BEARING_FIELDS:
        contained[field] = ""
    return contained


def _is_identifier_hyphen(match: "re.Match[str]") -> bool:
    """True when this match is "<letters>-<digits>", i.e. an identifier.

    Only the marker-then-value branch with an ALPHABETIC marker, an EMPTY
    delimiter and a signed value can be ambiguous; every other form is
    unambiguous and never reaches this test.
    """
    pre = match.group("pre") or ""
    token = match.group("val_after") or ""
    if not pre or not token.startswith(("-", "+")):
        return False
    marker = pre.strip()
    if not marker.isalpha():  # a currency SYMBOL is never an identifier prefix
        return False
    return pre == marker and marker.upper() not in _ISO_CURRENCY_CODES


def _enforce_precision(text: str) -> Tuple[str, int]:
    """Snap every monetary-form token onto the external reporting grid.

    Returns ``(sanitised_text, snap_count)``. A snap means a full-precision
    figure reached the external surface and the gate rounded it. The currency
    marker, the original delimiter whitespace and the explicit sign of the
    original token are all preserved on the replacement.
    """
    snaps = 0

    def _snap(match: "re.Match[str]") -> str:
        nonlocal snaps
        if _is_identifier_hyphen(match):
            return match.group(0)
        pre = match.group("pre") or ""
        post = match.group("post") or ""
        token = match.group("val_after") or match.group("val_before") or match.group("val_form")
        # float(), not int(): the token may carry a decimal fraction, and the
        # whole amount — not just its integer part — is what sits on the grid.
        value = float(token.replace(",", ""))  # float() understands a leading +/-
        if value % EXTERNAL_ROUND_UNIT == 0:
            return match.group(0)
        snaps += 1
        snapped = int(round(value / EXTERNAL_ROUND_UNIT) * EXTERNAL_ROUND_UNIT)
        plus = "+" if token.startswith("+") and snapped >= 0 else ""
        return f"{pre}{plus}{snapped:,d}{post}"

    return _NUM_TOKEN_RE.sub(_snap, text), snaps


def _redact_verbatim_brief(formatted: str, brief: str) -> Tuple[str, int]:
    """Replace any verbatim embedding of the whole campaign brief.

    The plan is a reduction of the brief; echoing the brief back wholesale is a
    leak of that contract regardless of what the brief contains. This layer is
    independent of the precision grid and carries its own audit event — a
    verbatim echo is order-independent, while the grid is not.
    """
    if not brief or len(brief) < _VERBATIM_BRIEF_MIN_CHARS or brief not in formatted:
        return formatted, 0
    count = formatted.count(brief)
    return formatted.replace(brief, "[REDACTED]"), count


# Reason code -> the sentence the caller reads. A code with no entry falls
# back to the generic one rather than leaking the code itself.
_DEGRADED_MESSAGES = {
    "EMPTY_INPUT": EMPTY_INPUT,
    "QUESTION_TOO_LONG": TOO_LONG,
    "INVALID_REQUEST": INVALID_VALUE,
}


class PostProcessNode(FunctionNode):
    """The output gate for RET-C2-003.

    Outer backbone node (post_process slot). Surfaces the finished plan to the
    caller and enforces the document's stated invariants on the way out.

    Order matters. The credential PATTERN scan runs BEFORE the numeric snap and
    again after it: the snap treats any standalone three-letter uppercase word
    as a currency marker, so an unscanned "SSN 123-45-6789" would come out
    "SSN 0-45-6789" — the pattern a credential or identifier scan would have
    caught, destroyed, and the plan shipped. Verbatim redaction is
    order-independent and runs alongside.

    On violation the node does not raise. Raising leaves the un-gated inner
    answer in State, and AgentBaseGraph.get_output() falls back to
    state["result"] even on error status — so the ERROR envelope would carry
    exactly the text the gate refused. Containment is: return ERROR, and clear
    every output-bearing field. The caller-visible reason is the closed-set
    ``error_reason`` (``output_withheld``), which RetC2003Agent.get_output()
    projects; the line this node writes to error_log names the layer that
    refused and stays internal.

    Input state keys:  result, promotion_plan, validated_input, status
    Output state keys: result, formatted_output, promotion_plan, status,
                       error_reason, error_log
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def _withhold(self, state: AgentState, cause: str, message: str) -> Dict[str, Any]:
        """Audit the refusal (a cause code, never the document) and return _contain()."""
        emit_trace_event(
            "promotion_plan_withheld",
            {"node": self.__class__.__name__, "reason": cause},
            state,
        )
        logger.error("PostProcessNode: output withheld (%s)", cause)
        return _contain(_REASON_OUTPUT_WITHHELD, [f"PostProcessNode: {message}"])

    def execute(self, state: AgentState) -> Dict[str, Any]:
        # A run declined upstream has nothing to format. Render the reason as
        # the caller-facing body and carry the marker onward.
        marker = state.get("error_code")
        if marker:
            message = _DEGRADED_MESSAGES.get(marker, INPUT_REJECTED)
            emit_trace_event("post_process_degraded", {"reason": marker}, state)
            return {
                "status": AgentStatus.SUCCESS.value,
                "error_code": marker,
                "formatted_output": message,
                "result": message,
            }
        # `result` is set by PromotionPlanGraphNode.merge_output() from the inner
        # graph; `promotion_plan` is the formatted document behind it.
        document = state.get("result") or state.get("promotion_plan") or ""

        if not document:
            return self._withhold(
                state,
                "empty_plan",
                "no promotion plan was produced by the domain workflow",
            )

        # ── Layer 1: verbatim brief redaction (order-independent) ────────────
        brief = state.get("validated_input") or ""
        document, verbatim_redactions = _redact_verbatim_brief(document, brief if isinstance(brief, str) else "")
        if verbatim_redactions:
            emit_trace_event(
                "promotion_plan_verbatim_redacted",
                {"node": self.__class__.__name__, "redactions": verbatim_redactions},
                state,
            )

        # ── Layer 2: credential pattern scan, BEFORE the numeric snap ───────
        # The framework's own detector, not a local pattern set: a value the
        # framework catches and this node misses makes the framework's @final
        # output gate raise inside post-process, and the wrapper then returns a
        # bare ERROR partial that discards the clearing below. A detector gap is
        # a containment bypass, so the two block sets must be the same set.
        if detect_credentials(document):
            return self._withhold(
                state,
                "credential_pattern_pre_snap",
                "the generated plan contained a credential-shaped value and was withheld",
            )

        # ── Layer 3: the precision grid ─────────────────────────────────────
        document, snaps = _enforce_precision(document)

        # ── Layer 2 again: the snap rewrites digit runs, so re-scan ─────────
        if detect_credentials(document):
            return self._withhold(
                state,
                "credential_pattern_post_snap",
                "the generated plan contained a credential-shaped value and was withheld",
            )

        emit_trace_event(
            "promotion_plan_released",
            {
                "node": self.__class__.__name__,
                "plan_chars": len(document),
                "precision_snaps": snaps,
                "verbatim_redactions": verbatim_redactions,
            },
            state,
        )
        logger.info("PostProcessNode: plan released (len=%d chars, snaps=%d)", len(document), snaps)

        return {
            "result": document,
            "formatted_output": document,
            "status": AgentStatus.SUCCESS.value,
        }
