"""H2: Rate limiting via slowapi.

In-memory backend (single API container, no Redis needed). Per-IP keying
based on the real client IP from `X-Forwarded-For` (nginx forwards it),
falling back to `request.client.host` if the header is absent.

Limits are deliberately generous for legit users (typo retries on login,
multi-tab usage) but tight enough to make brute-force / credit-burn /
OpenAI-drain attacks impractical.

## Important behavior note: auth runs BEFORE per-route decorators

FastAPI evaluates `Depends(get_current_user)` before the function body, and
the `@limiter.limit(...)` decorator wraps the function body. So on routes
that require auth, an unauthenticated request returns 401 WITHOUT consuming
a per-route rate-limit slot. The per-route limit only applies to requests
that pass auth — its purpose is to defend against an authenticated attacker
burning resources (credits, OpenAI calls), NOT against an unauthenticated
flood. The unauthenticated flood is bounded by SlowAPIMiddleware's
`default_limits` (currently 300/minute per IP).

For login/register, the route function IS the auth, so the per-route
decorator runs on every request — that's where brute-force protection lives.
"""

from fastapi import Request
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address


def get_client_ip(request: Request) -> str:
    """Extract the real client IP, trusting nginx's X-Forwarded-For.

    nginx is configured to set `X-Forwarded-For` (see nginx/nginx.conf).
    The header may contain a comma-separated chain (`client, proxy1, proxy2`);
    the first entry is the originating client.

    Falls back to the direct connection address if the header is missing
    (e.g. when hitting the API container directly bypassing nginx).
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return get_remote_address(request)


limiter = Limiter(
    key_func=get_client_ip,
    storage_uri="memory://",
    # Default limit applied to any route NOT explicitly decorated.
    # Generous on purpose: per-route decorators tighten where it matters.
    default_limits=["300/minute"],
)


__all__ = ["limiter", "get_client_ip", "RateLimitExceeded"]
