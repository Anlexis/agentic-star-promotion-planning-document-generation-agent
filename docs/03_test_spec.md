# Test Specification — RET-C2-003 Promotion Planning Document Generator

Every test below ships in this repository. Run them with:

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v
```

The suite needs no platform connection; running the agent itself does.

---

## Layout

| Path | What it covers |
|---|---|
| `tests/unit/test_campaign_contract.py` | the structured caller contract: bounds, inert alphabet, closed sets, fail-closed numbers |
| `tests/unit/test_promotion_plan_service.py` | the budget arithmetic and the shape of the generated document |
| `tests/unit/test_output_gate.py` | the reporting grid and containment, at the node |
| `tests/unit/test_error_envelope_closed_set.py` | the caller-visible error envelope is a closed set: `get_output()` on every non-success state, and the containment delta on every layer that can withhold |
| `tests/unit/test_framework_compliance_tc06_tc07.py` | the framework's default gates cannot be replaced |
| `tests/proof_of_boundary/test_pb01_empty_input.py` | an unusable brief is refused |
| `tests/proof_of_boundary/test_pb02_prompt_injection.py` | instruction-control content is refused; ordinary retail prose is not |
| `tests/proof_of_boundary/test_pb03_malformed_input.py` | a non-string brief is refused, structurally |
| `tests/proof_of_boundary/test_import_isolation.py` | no imports past the documented packages |
| `tests/proof_of_boundary/test_pb_invoke_order.py` | the per-node invoke order, and the trust refusal |
| `tests/proof_of_boundary/test_pb7_hitl_interrupt_propagation.py` | interrupt propagation — skipped: not enabled for this template |
| `tests/proof_of_boundary/test_pb_invoke_http.py` | end-to-end through the real ASGI `/invoke` |

---

## Boundary cases

### PB-01 — Empty input

**Boundary**: `PreProcessNode`, called directly.

Empty string, a single space, a whitespace run, newlines only, and a brief below the minimum
length are each refused, with `validated_input` and `campaign_request` left unset, no
`promotion_plan` key written, and a descriptive line on the internal channel. The refusal is
asserted as a value the caller can correct: the node **completes** — `status` is `success` — while
carrying a non-empty reason code. Both halves are checked, because asserting only the status would
pass on a run that quietly produced a plan. An oversized brief is refused the same way rather than
truncated — a truncated brief plans a campaign the caller did not describe. A brief exactly at the
minimum length is accepted, because the bound is inclusive and worth probing rather than assuming.

### PB-02 — Prompt injection

**Boundary**: `PreProcessNode`, called directly, with no framework wrapper in front of it.

This is the one input path asserted to end on an **error** status rather than to complete carrying
a reason code: an instruction-override refusal is not a value the caller can correct.

Refused: chat-template control tokens (`<|im_start|>`, `[INST]`, `<<SYS>>`), directive phrases
aimed at the agent's own instructions, role reassignment, a system-prompt probe, and a directive
spliced apart with markup. A hostile field NAME in `input_context` is refused too, and a
`\u`-escaped payload is caught because the screen runs after parsing.

Not refused: four real merchandising briefs that share vocabulary with the attacks — "ignore last
season's underperforming SKUs", "act as a premium brand", "follow the prior campaign's rules of
thumb". This direction is asserted deliberately: a screen that blocks real briefs blocks real work.

No refusal echoes the payload back to the caller.

### PB-03 — Malformed input

**Boundary**: `PreProcessNode`, called directly.

An integer, a float, `None`, bytes, a list, a dict and a boolean are each refused with a message
naming the type received, `validated_input` and `campaign_request` left unset, and the same
completion-with-a-reason-code shape PB-01 asserts. `bool` is listed separately because it is an
`int` subclass in Python and reaches a naive check as `1`.

### PB-04 — Import isolation

An AST scan of `src/` asserting nothing imports the platform's internals.

### PB-06 — Invoke order and trust

Every concrete node under `src/nodes/` is driven through `__call__` and the order is pinned:
node_start, input gate, `execute()`, output gate, node_complete. A separate case drives an
under-trusted caller into a privileged node and asserts the refusal happens before `execute()` runs
— behaviourally, not by matching the framework's wording.

### PB-07 — Interrupt propagation

Skipped. No graph class in this template opts into cross-boundary interrupt propagation, so there
is no propagation behaviour to assert. The module is real and importable rather than a passing
stub, and the skip states its reason.

---

## The caller contract

`tests/unit/test_campaign_contract.py`.

"Refused" throughout this section means the shape PB-01 describes: the node completes — `status` is
`success` — carrying a reason code, with no contract handed on. Every case below asserts both
halves.

Each numeric field is driven through the same matrix: `"NaN"`, `"Infinity"`, `"-Infinity"`, raw
`float("nan")`, raw `float("inf")`, `float("-inf")`, `True`, `None`, an empty string, a word, a
list and a dict — plus one value below range and one above. All are refused. An in-range value is
accepted and carried through to the contract handed to the inner graph. A rejection names the field
and does not echo the value.

Both rendered string fields are driven through values that are not inert: uppercase, a space, a
hyphen, markup, over-length, empty, a non-string, and a fragment of markdown table syntax that
would reshape the document. All are refused; an inert value is accepted and carried.

`channel` and `output_format` are checked against their closed sets in both directions. An unknown
field is refused and named. A non-object context is refused. An absent context is accepted — that
is the degraded path, not an error.

---

## The budget arithmetic

`tests/unit/test_promotion_plan_service.py`.

For each channel mix, the per-category amounts are asserted against the declared total. The split
is checked to reconcile with the total it reports. Every rendered amount is asserted to be on the
1,000-unit grid, for totals from 1,000 up to the maximum accepted budget. Without a declared total,
no aggregate is claimed at all — the section falls back to percentages.

Rounding is half-to-even, pinned explicitly in both the service and the output gate: if the two
disagreed, a figure the service rendered would be re-rounded on its way out.

The document shape is asserted too: all five sections present, the schema note stating the
invariant, the timeline spanning the declared duration, and the channel breakdown following the
mix.

---

## The output boundary

`tests/unit/test_output_gate.py` and the containment cases in `tests/proof_of_boundary/test_pb_invoke_http.py`.

**Must snap** — fifteen representations, each one a form an earlier version of the grammar let
through: below 10,000; marker after the value; symbol instead of code; explicit sign; multi-space,
tab and newline delimiters; the comma-grouped form; a decimal amount; and a separated three-letter
marker.

**Must be byte-identical** — twenty-one forms, each one something a merely aggressive gate corrupts:
an already-on-grid amount, a decimal fraction, a percentage, a ratio, a decimal followed by a
letter, a section heading after a three-letter code across a blank line, this template's own
identifier alphabet (`sku_48210`, `spring_sale_2026`, `campaign_123456`, `women_25_40`), attached
identifiers (`SKU-9999`, `SKF-6205`, `STU-1234`), hyphenated words, an embedded acronym with a
year, percentages, a week count, a rendered table row, and two patterns that must survive intact
for a scan to see them (`SSN 123-45-6789`, `TAX 987-65-4321`).

**Containment** — a document carrying a credential is withheld, and the node's own delta is asserted
to contain no released text, no traceback and no source path, with the fallback chain
(`formatted_output or result`) resolving to nothing. Every path that can return a non-success
status is measured, not only the headline one.

**The error envelope is a closed set** (`tests/unit/test_error_envelope_closed_set.py`).
`RetC2003Agent.get_output()` on every non-success shape the state can take at finalize — a
non-success status with no reason recorded, a timeout, a withheld document, a foreign or
non-string value in the reason slot, a surviving pre-gate `result` / `formatted_output` /
`promotion_plan` — returns
`output: null`, `error: {"reason": …}` drawn from `ERROR_REASONS`, no `error_log` key and a fixed
key set; a runtime-assembled sentinel seeded into `error_log` alongside entries of the shapes the
channel really carries (a field-naming line, a trust denial, a wrapped exception with traceback)
appears nowhere in the returned mapping, walking nested values. The success envelope is unchanged
and a stale reason cannot turn it into an error. `PostProcessNode`'s containment delta, on every
layer that can withhold (empty plan, credential before the snap, credential after the snap) and
through both `execute()` and the framework pipeline, records `error_reason=output_withheld`,
clears every output-bearing field, writes exactly one line of its own and re-emits nothing; the
audit payload carries a cause code and no text.

**End-to-end containment** is exercised at two injection points, because they prove different
guards: at the generation step, where the framework's own gate stops the credential inside the
inner graph; and at the graph-node boundary, whose gates are deliberate no-ops, so nothing between
the inner graph and the caller inspects what crosses it except this template's output gate. The
body is closed at the envelope as well (`output: null` on any non-success status), so the
node-level clearing is held on its own by `tests/unit/test_output_gate.py` rather than by the
end-to-end case: a gate that withholds without clearing fails there, not end-to-end.

The envelope tests were verified against a mutant with the original `get_output()` restored — the
allow-listed `error_log` projection: 35 cases fail (7 end-to-end, 28 unit), the sentinel and the
framework's wording reaching the body, and the rest of the suite is unaffected.

---

## End-to-end

`tests/proof_of_boundary/test_pb_invoke_http.py` drives the FastAPI app in `src/api/server.py` —
the same adapter a deployment serves.

| Case | Expectation |
|---|---|
| No token / wrong token | 401 with a generic body that does not say which |
| `/health` | 200, no auth |
| Declared parameters | 200; the budget split is arithmetic on the declared total, and the declared duration drives the timeline |
| Brief only | 200; a plan is still produced, and no aggregate is claimed |
| `output_format: json` | 200; all five section keys present and parseable |
| A non-finite number | `status=success`, `output` is the fixed sentence for a value the caller can correct, no `error` key and no `error_log` key; no section of the document is present in it; neither the value nor the field name appears in the body |
| A control token in the brief | error status, empty output, the token absent from the response |
| A credential-shaped context value | 400 naming the field, never the value |
| Ordinary text on the same field | 200 |
| Too many keys / oversized context | 413 |
| Caller-contract rejection carrying a sentinel | `PreProcessNode`'s validator is made to author a runtime-assembled sentinel (a name, an email, a token-shaped fragment) into its rejection line. The rejected value is one the caller can correct, so the run completes: the body is `status=success`, `output` one of the four fixed sentences, no `error` and no `error_log`, a fixed key set, and the sentinel appears nowhere in it, walking nested values. Only the slot the reason arrives in moved; the containment obligation is checked identically |
| Exception in an outer node / in an inner node | The injection screen, then the plan service, is made to raise with the sentinel as its message; the framework wraps message and traceback into `error_log`; the body carries `workflow_failed` and neither the message, `Traceback`, `File "` nor the exception class |
| Output-boundary refusal | A document carrying the sentinel and a credential shape crosses the graph-node boundary; the body carries `error: {"reason": "output_withheld"}` and nothing from the document or the refusal line |
| Trust denial | No token configured, no middleware: the framework's denial line stays internal; the body carries `workflow_failed` and no "trust gate denied" |
| Verify the verifier | The replaced validator does put the sentinel into `PreProcessNode`'s own `error_log` — on the delta of a run that completed (`status=success`) — and the same walk finds it there |
