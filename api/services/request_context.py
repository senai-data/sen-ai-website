"""Request-scoped context vars set by middleware.

Used by helpers that need to know HTTP-level facts (like the current
request method for RBAC role escalation) without having to thread the
`Request` object through every internal helper signature.

`contextvars.ContextVar` is task-local in asyncio, so each request runs
in its own context — there is no cross-request leakage.
"""

from contextvars import ContextVar

# H6: set by `request_method_middleware` in main.py on every request.
# `_check_scan_access` reads this to auto-escalate role requirements
# from "viewer" to "editor" on POST/PUT/PATCH/DELETE.
current_request_method: ContextVar[str] = ContextVar(
    "current_request_method", default="GET"
)


__all__ = ["current_request_method"]
