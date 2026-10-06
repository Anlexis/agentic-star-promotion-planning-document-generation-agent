"""AgentCore Platform v1.0"""

# Standalone HTTP entry point for the agent.
# Entry points are adapters only — no business logic here.
# For platform-level routing, the platform gateway calls agent.invoke() directly.

import json
import os
import re
import secrets
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.context import bound_secrets
from framework.security.credential_detector import detect_credentials_in_value
from shared.secrets import factory as secrets_factory

from src.graph.graph import RetC2003Agent, load_runtime_config

app = FastAPI(title="Agent")

# Runtime parameters (config/config.yaml: max_retry, timeout_s, output block)
# are passed to the constructor so the backbone consumes them — an agent built
# without config would silently run on framework defaults while config.yaml
# claimed otherwise.
agent = RetC2003Agent(config=load_runtime_config())
agent.compile()
# namespace/agent_name match the manifest (config/agent.yaml).
agent.provision_secrets(secrets_factory(namespace="ret", agent_name="PromotionPlanningDocumentGeneratorAgent"))

# Caller-supplied campaign parameters travel in `input_context` alongside the
# brief text. The adapter enforces coarse transport-level caps here; the
# per-field contract (types, shapes, bounds, screens) is owned by PreProcessNode.
_MAX_CONTEXT_KEYS = 16
_MAX_CONTEXT_BYTES = 256 * 1024

# A field name is caller data too, so it is only echoed back when it is itself
# inert and trips no credential pattern of its own.
_SAFE_FIELD_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")


class InvokeRequest(BaseModel):
    input: str
    session_id: str = ""
    input_context: Dict[str, Any] = {}


def _credentialled_field(context: Dict[str, Any]) -> Optional[str]:
    """Return a caller-safe label for the first field carrying a credential shape.

    Why the adapter screens at all: InitializeNode — the FIRST node in the
    backbone — returns input_context verbatim in its result, and the framework's
    output gate scans every value of every result. A credential-shaped string
    anywhere in input_context therefore makes node 1 return an error with
    a traceback, before any of this template's code runs, and the caller gets an
    opaque failure it cannot act on. The request cannot succeed either way, so
    the screen converts that into an actionable refusal.

    The scan uses the framework's own detector, so the refusal set is the
    framework's block set by construction rather than a local approximation of
    it. Iterating top-level fields is exactly equivalent to scanning the whole
    mapping, because detect_credentials_in_value(dict) is defined as the union
    over its values; that identity is what lets the refusal name a field
    without widening or narrowing the set. Note the detector scans VALUES only,
    never keys — this screen is deliberately consistent with that rather than
    diverging from it.
    """
    for index, (name, value) in enumerate(context.items(), start=1):
        if not detect_credentials_in_value(value):
            continue
        safe_name = (
            str(name)
            if _SAFE_FIELD_NAME_RE.match(str(name)) and not detect_credentials_in_value(str(name))
            else f"field #{index}"
        )
        return safe_name
    return None


@app.post("/invoke")
async def invoke(req: InvokeRequest, request: Request) -> Dict[str, Any]:
    trust = getattr(request.state, "trust_level", TrustLevel.ANONYMOUS)
    # Standalone caller auth: when INVOKE_AUTH_TOKEN is set on the server
    # environment, callers that no upstream middleware vouched for (still
    # ANONYMOUS) must present it as a Bearer token and run at
    # VERIFIED_EXTERNAL. Middleware-established trust is never demoted.
    # This adapter is the entry-point auth boundary — a deployment-level caller
    # credential, not an agent secret, so ctx.secrets does not apply: no
    # InvocationContext exists before auth.
    expected = os.environ.get("INVOKE_AUTH_TOKEN")
    if expected and trust is TrustLevel.ANONYMOUS:
        supplied = request.headers.get("authorization", "")
        # Compare bytes: compare_digest raises TypeError on non-ASCII str input
        # (headers decode as latin-1), which would 500 instead of a clean 401.
        if not secrets.compare_digest(supplied.encode(), f"Bearer {expected}".encode()):
            # Generic body on purpose — do not leak whether the token was
            # absent, malformed, or wrong.
            raise HTTPException(status_code=401, detail="Token is invalid or expired.")
        trust = TrustLevel.VERIFIED_EXTERNAL

    if len(req.input_context) > _MAX_CONTEXT_KEYS:
        raise HTTPException(status_code=413, detail="input_context has too many keys.")
    if len(json.dumps(req.input_context, default=str)) > _MAX_CONTEXT_BYTES:
        raise HTTPException(status_code=413, detail="input_context is too large.")

    offending = _credentialled_field(req.input_context)
    if offending is not None:
        # 400, not 422: pydantic owns 422 and returns a list of error objects
        # there, so reusing it makes client-side handling ambiguous.
        # The field is named; the value never is.
        raise HTTPException(
            status_code=400,
            detail=f"input_context.{offending} looks like a credential and was not accepted.",
        )

    with bound_secrets(agent._secrets_provider):
        ctx = InvocationContext(
            session_id=req.session_id or str(uuid4()),
            caller_trust_level=trust,
            caller_id=getattr(request.state, "caller_id", ""),
        )
        result: Dict[str, Any] = agent.invoke(req.input, ctx=ctx, input_context=req.input_context)
        return result


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "agent": "PromotionPlanningDocumentGeneratorAgent"}
