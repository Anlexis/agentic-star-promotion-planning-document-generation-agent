# Promotion Planning Document Generation Agent

AI agent for generating retail promotion planning documents, built with Agentic Star.

> **Category**: Cat 2 (domain-specific document-generation pipeline)
> **Industry**: Retail
> **Template ID**: RET-C2-003

## Overview

Turns a retail campaign brief into a structured promotion planning document.

Give it a brief — free text, or the same parameters declared explicitly — and it returns a
five-section plan: an overview, a phased timeline, a per-channel activation breakdown, a
budget allocation, and a KPI table. Planning a promotion this way takes minutes instead of
the days a merchandising team normally spends assembling the same document by hand.

Generation is deterministic: the plan is derived from the campaign parameters by rules in
`src/services/service.py`, so the same brief always produces the same document and the
budget split is arithmetic rather than prose. Budget figures are reported as aggregates
rounded to the nearest 1,000 currency units, and the output boundary enforces that grid on
the rendered document as well as computing it.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode. If the platform is unreachable or the SDK version does not match, start-up fails during
graph compile / platform preflight rather than starting in a partially working state. This is
intentional — a half-running agent is worse than one that refuses to start.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent configuration
docs/         design and operational documentation
```

See `docs/` for the design and the test specification.

## Calling it

`POST /invoke` takes the brief as `input`. Anything you can state precisely is better declared
than inferred, so the same parameters can be passed structurally in `input_context`:

```json
{
  "input": "Seasonal sale campaign for loyalty members across the autumn outerwear range.",
  "input_context": {
    "campaign_type": "seasonal_sale",
    "target_segment": "loyalty_members",
    "channel": "both",
    "duration_weeks": 6,
    "budget_total_jpy": 8000000,
    "output_format": "markdown"
  }
}
```

Every field is optional; anything you leave out is inferred from the brief instead. Declared
values are validated against explicit bounds — `campaign_type` and `target_segment` are limited
to 1–32 characters of `[a-z0-9_]` because they are rendered into the document, `channel` and
`output_format` to their listed values, and the two numbers must be finite and in range. A
declared budget is what makes the Budget Allocation section carry computed amounts rather than
percentages alone.

Set `INVOKE_AUTH_TOKEN` in the server environment to require a Bearer token on `/invoke` when
running the adapter standalone.

## Customising

1. Adjust `config/config.yaml` for your own environment and defaults.
2. Change the allocation weights, phase weights and KPI targets in `src/services/service.py` —
   that module holds the domain rules, and the nodes are thin adapters over it.
3. Review the node implementations under `src/nodes/` for the input and output boundaries.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
