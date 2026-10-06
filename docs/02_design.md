# Design Specification — RET-C2-003 Promotion Planning Document Generator

## Position in the framework

| Property | Value |
|---|---|
| Agent class | `RetC2003Agent` (`src/graph/graph.py`) |
| L1 Base (framework base class) | `AgentBaseGraph` — direct framework inheritance |
| Category | Cat 2 — domain-specific document-generation pipeline, nested pattern |
| Generation | Deterministic. No model call: the plan is derived from the campaign parameters by the rules in `src/services/service.py`. |

Three-layer separation:

- **State** — a flat `TypedDict` (`src/schemas/state.py`), never a Pydantic model: checkpoints are
  serialised with msgpack and object values corrupt silently.
- **Node** — every node extends `FunctionNode` and implements `execute(self, state) -> dict`,
  returning only the keys it changed.
- **Graph** — composition: an outer `AgentBaseGraph` whose `main` slot delegates to an inner
  `BaseGraph`.

---

## Architecture — the nested two-layer pattern

### Outer graph (`src/graph/graph.py`)

The backbone is fixed; `add_edges()` is not overridden.

```
START -> initialize -> pre_process -> main -> post_process -> finalize -> END
```

| Slot | Class | Responsibility |
|---|---|---|
| `initialize` | `InitializeNode` (framework default) | schema version, session id, trust level |
| `pre_process` | `PreProcessNode` | Owns the caller contract: brief checks, the injection screen, and the structured `input_context` validation |
| `main` | `PromotionPlanGraphNode(GraphNode)` | Delegates the domain workflow to `DomainWorkflowGraph`, or skips it when `pre_process` already declined the request |
| `post_process` | `PostProcessNode` | The output boundary: credential scan, verbatim redaction, the reporting grid, and containment — or, for a declined request, the fixed sentence saying what to correct |
| `finalize` | `FinalizeNode` (framework default) | response metadata, total time |

`PromotionPlanGraphNode` contract:

- `get_subgraph()` — lazily instantiate `DomainWorkflowGraph` with `_parent_config()`
- `extract_input(state)` — return the validated brief, and stash the validated caller contract on
  the context bridge for the imminent inner invoke
- `execute(state)` — skip the inner graph outright when `pre_process` recorded a reason code. A
  declined request has no validated input to act on, so running the workflow would only produce a
  second, vaguer reason for the same rejection and overwrite the specific one already settled
- `merge_output(state, sub_result)` — map `promotion_plan` and `status` into the outer delta, and
  carry the reason code across the boundary with the outer value winning: a reason settled before
  the inner run is the real one, and a plain read of `sub_result` would erase it
- `error_strategy = "propagate"` — an inner failure is a failure of the request

### Reporting a request that was not carried out

A request can fail to be carried out in two ways, and they are reported differently on purpose.

**A value the caller can correct completes the run.** An empty, too-short or oversized brief, and
any `input_context` field that fails the contract, are rejected exactly as before — no work is
done, nothing is produced, the audit event is emitted — but the run does not terminate.
`PreProcessNode` records a reason code in State, `main` skips the inner graph, and
`PostProcessNode` renders the fixed sentence for that code as the response body. `status` is
`success`, `output` carries the sentence, and the envelope has no `error` block. The alternative,
terminating, ends the calling surface's turn and surfaces only an exception type, leaving the
reason reachable solely from the audit trail — so the caller cannot correct a single value and
resend it on the same conversation. Because no plan was produced, none of the document's
structured sections is present in the body: a run that did not process the request must not return
something that reads like a result. The reason code itself is internal — it selects the sentence
and is never projected into the envelope.

**Everything else terminates**, and `RetC2003Agent.get_output()` closes the invoke envelope on it:
`output` is `null` and `error` is `{"reason": …}` with the reason drawn from a set this template
declares — `output_withheld` when `PostProcessNode` refused the document (recorded in State as
`error_reason`), `workflow_failed` for every other non-success outcome (an instruction-override
refusal, a trust denial, an inner-workflow error or a wrapped exception, a timeout — the backbone
routes all of them straight to `finalize`). An instruction-override refusal is deliberately on
this side: a refusal is not a value to correct, and reporting it like one would read as an
invitation to reword the brief until it gets through.

`error_log` is never projected on either path: it is the internal channel the state reducer
appends to and the audit trail reads, and it carries node- and framework-authored text — a line
naming a caller field, or a wrapped exception's message and traceback. Not publishing it is the
contract; reducing it to an allow-listed subset would not be. The success envelope is the
framework's base shape, untouched.

### Inner graph (`src/graph/domain_workflow_graph.py`)

Inherits `BaseGraph` directly, for a fully custom topology.

```
START -> input_parse -> plan_generate -> output_format -> END
```

| Node | Class | Responsibility |
|---|---|---|
| `input_parse` | `InputParseNode` | Consolidate the declared parameters with what the brief yields; reduce every rendered string to the inert alphabet |
| `plan_generate` | `PlanGenerateNode` | Ask `PromotionPlanService` for the five-section document |
| `output_format` | `OutputFormatNode` | Render as markdown or JSON |

All seven `BaseGraph` abstract methods are implemented. `route()` is annotated with this graph's own
`State`: LangGraph reads a path callable's annotation as its input schema and projects away every
field the annotation does not carry, so a base-class annotation would hide the domain fields from
any conditional branch added later.

### The context bridge (`src/graph/context_bridge.py`)

`GraphNode.execute()` invokes the inner graph as
`subgraph.invoke(user_input, session_id=..., ctx=...)` and does not forward `input_context`, so an
inner-node read of `state["input_context"]` always sees `{}`. Two things therefore cross the
boundary through `ContextVar`s, set by the outer node's `extract_input()` and read back by the inner
graph's `_extra_initial_state()`:

- the validated caller contract — the contract `PreProcessNode` produced, never the raw request body;
- the resolved output format, which the outer agent takes from **its own** `self.config`. Having each
  layer read `config/config.yaml` independently would let the agent and its subgraph disagree about
  the configuration, with nothing to report the disagreement. `_parent_config()` still forwards the
  `output:` block, as the fallback for an inner graph constructed directly.

A `ContextVar` keeps the hand-off correct per thread and task, so concurrent invocations in one
process cannot see each other's context.

---

## Configuration

`config/agent.yaml` is the static manifest the registry reads to discover the agent; it carries
identity keys only. Every runtime value lives in `config/config.yaml`:

| Key | Default | Consumed by |
|---|---|---|
| `max_retry` | 3 | the backbone's routing |
| `timeout_s` | 60 | reserved by the runtime configuration contract; the installed framework validates it but does not act on it |
| `output.format` | `markdown` | `OutputFormatNode`, seeded into State by the inner graph; overridable per request |

The outer agent is constructed with this file (`RetC2003Agent(config=load_runtime_config())`), and
its `_validate_config()` rejects an unsupported `output.format` at start-up rather than at the first
invoke — the inner graph is compiled lazily, so a misconfigured deployment would otherwise start
cleanly and fail per request. The format then reaches the inner graph through the bridge. An empty
`_parent_config()` would silently disconnect every declared setting: the inner graph would run on
defaults while `config.yaml` claimed otherwise, and nothing would fail.

---

## The caller contract

`POST /invoke` accepts the brief as `input` and, optionally, the same parameters declared
explicitly in `input_context`.

| Field | Accepted | Rejected |
|---|---|---|
| `campaign_type` | 1–32 chars of `[a-z0-9_]` | anything else — it renders into the document |
| `target_segment` | 1–32 chars of `[a-z0-9_]` | as above |
| `channel` | `online`, `offline`, `both` | any other value |
| `output_format` | `markdown`, `json` | any other value |
| `duration_weeks` | a finite number in 1–104 | NaN, ±Infinity, booleans, out of range |
| `budget_total_jpy` | a finite number in 1–10,000,000,000 | as above |

An unknown field is rejected rather than ignored. Ignoring is not stripping: an undeclared key
stays on the context channel, reaches the first node's result, and is scanned there by the
framework's output gate — and a silently dropped parameter plans the wrong campaign either way.

Numbers go through a finite-and-in-range parser rather than a plain comparison. NaN and the
infinities parse cleanly through `float()` and arrive intact through raw JSON, and every comparison
against NaN is `False`, so `low <= value <= high` passes them through.

Absent fields degrade to the heuristics applied to the free-text brief. That is the documented
baseline, not a second contract: whatever those heuristics yield is reduced to the same inert
alphabet before it can reach the document, and a budget FIGURE is only ever taken from the declared
route — a misread currency substring would otherwise drive the allocation arithmetic.

Which rejection lands where:

| Rejected because | Reason code | What the caller receives |
|---|---|---|
| The brief is empty or whitespace only | `EMPTY_INPUT` | completes — `status: success`, the sentence asking for the question to be sent |
| The brief is longer than the accepted maximum | `QUESTION_TOO_LONG` | completes — the sentence asking for a shorter request |
| The brief is not a string or is below the minimum length, or an `input_context` field fails its type, shape, enum or bound check, or the context itself is not an object | `INVALID_REQUEST` | completes — the sentence saying a value could not be accepted |
| The brief, or an `input_context` field NAME, carries instruction-control content | — | terminates — `output: null`, `error: {"reason": "workflow_failed"}` |

Each sentence names what to correct and nothing else. It never echoes the rejected value, names an
internal field path, or quotes a gate message: a rejected value is exactly the value that was not
safe to handle, and the field-level wording stays on the internal channel.

Two of those shapes never reach the node over HTTP: the standalone adapter's request model types
`input` as a string and `input_context` as an object, so a non-string brief or a non-object context
is a 422 from the transport before the agent runs. The node checks them anyway — a platform caller
invokes the agent directly, without that model in front of it.

The adapter also screens `input_context` for credential shapes before invoking, using the
framework's own detector so the refusal set matches the framework's block set exactly. Without that
screen a credential-shaped value produces an opaque failure inside the first node that the caller
cannot act on; with it, the caller gets a 400 naming the field. The value is never echoed, and a
field name is only echoed when it is itself inert.

---

## The output boundary

`PostProcessNode` runs on every path, and the first thing it does is look for a reason code. There
is no document to gate on a declined request, so it renders that code's fixed sentence into the
output-bearing slot, carries the code onward and completes. Nothing below applies to that path.

For a request that was actually carried out, the document has two stated invariants, both enforced
in `PostProcessNode.execute()`:

1. No credential-shaped string reaches the surface.
2. Every monetary figure is an aggregate on the 1,000-unit grid.

Order matters. The credential scan runs **before** the numeric snap and again after it. The snap
reads any standalone three-letter uppercase word as a currency marker, so an unscanned
`SSN 123-45-6789` would come out `SSN 0-45-6789` — destroying the pattern a scan would have caught,
and shipping the document. Verbatim redaction of the whole brief is order-independent and runs
alongside, with its own audit event.

The numeric grammar carries identifier guards over this template's own render alphabet
(`A-Za-z0-9_-`, plus `.` on the leading guard only). The alphabet is read off the renderer rather
than assumed: caller identifiers arrive as `sku_48210` or `spring_sale_2026`, which is a digit run
with an underscore in front of it and no letters of its own to protect it. For the one genuinely
ambiguous shape — `<three uppercase letters>-<digits>`, which `JPY-9999` and `SKU-9999` share — the
gate consults ISO 4217. A separated marker stays unrestricted, so `SKU 9999` still snaps: currency
codes are a closed vocabulary and identifiers never could be, which is why the check runs that way
round.

On violation the node does not raise. `AgentBaseGraph.get_output()` falls back to `state["result"]`
even on an error status, so a gate that raised would ship exactly the text it refused inside the
error envelope. Containment is to return an error status **and clear every output-bearing field**
(`result`, `formatted_output`, `promotion_plan`).

---

## Security layers

| Layer | Where | What |
|---|---|---|
| Trust | every node's `required_trust_level` | `VERIFIED_EXTERNAL` on all template nodes; the framework checks it before `execute()` |
| Entry-point auth | `src/api/server.py` | Bearer token when `INVOKE_AUTH_TOKEN` is set; an authenticated caller runs at `VERIFIED_EXTERNAL`. Middleware-established trust is never demoted. |
| Input | `PreProcessNode` | type, emptiness and length bounds; the injection screen; the structured contract |
| Injection screen | `PreProcessNode` | chat-template control tokens (`<\|…\|>`, `[INST]`, `<<SYS>>`) as a class, plus anchored directive phrases, over the brief and over every `input_context` field name. Screened on the raw text first and on a de-markup'd copy second: stripping first would delete a control token silently and forward the directive residue as plain text. A match terminates the run; it is not reported as a value to correct. |
| Output | `PostProcessNode` | as above |
| Audit | every node | the framework emits the lifecycle events; each node additionally emits a domain event via `emit_trace_event()` |
| Credentials | nowhere in State | State is checkpointed; secrets are provided by the platform |

The injection screen is the template's own, not a delegation. An assertion that "the framework
refused" holds only where the framework's gate is active; where it is absent or configured off, the
payload reaches the answer path and the request succeeds. The screen is anchored so ordinary retail
prose survives — a brief that says "ignore last season's underperforming SKUs" is a legitimate
instruction to a marketer, and refusing it would block real work.

---

## Directory layout

```
src/
  api/server.py                    the standalone HTTP adapter
  graph/graph.py                   outer graph, runtime config loading, output shape
  graph/domain_workflow_graph.py   inner graph, initial state seeding
  graph/context_bridge.py          the caller contract across the graph boundary
  nodes/pre_process_node.py        the input boundary
  nodes/input_parse.py             parameter consolidation
  nodes/plan_generate.py           document generation
  nodes/output_format.py           representation
  nodes/post_process_node.py       the output boundary
  schemas/state.py                 the shared State TypedDict
  services/service.py              the domain rules and the budget arithmetic
config/
  agent.yaml                       static manifest
  config.yaml                      runtime parameters
```

## References

- Test specification: `docs/03_test_spec.md`
- Runtime configuration: `config/config.yaml`
