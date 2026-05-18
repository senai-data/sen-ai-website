"""Per-user daily message quota for the in-app chatbot.

In-memory counter keyed by (user_id, UTC date). Resets at midnight UTC.
Persistence is intentionally out of scope for v1 - the worst case after a
container restart is users get a fresh allotment, which we'll absorb
during the early-adopter phase. Move to Redis or a DB table when the
chatbot grows past a single backend pod.

Tier resolution :
  - For now, every user is on the 'free' tier (30 msgs/day). When Stripe
    subscription plans add an 'agent_premium' flag we'll branch on it.
"""

from __future__ import annotations
import logging
from datetime import datetime, timezone
from threading import Lock

from config import settings

logger = logging.getLogger(__name__)

# {(user_id, "YYYY-MM-DD"): count}
_counters: dict[tuple[str, str], int] = {}
_lock = Lock()


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _cap_for(user) -> int:
    """Daily message cap for a user. Free tier today, premium ready for
    when subscription plans land."""
    # Hook for future tier resolution. Read from user.subscription_plan or
    # user.organization.plan when implemented.
    is_premium = bool(getattr(user, "agent_premium", False))
    return settings.agent_daily_cap_premium if is_premium else settings.agent_daily_cap_free


def get_usage(user) -> dict:
    """Return current usage + cap + remaining for the calling user."""
    user_id = str(user.id)
    key = (user_id, _today_key())
    with _lock:
        used = _counters.get(key, 0)
    cap = _cap_for(user)
    return {
        "used":      used,
        "cap":       cap,
        "remaining": max(0, cap - used),
        "reset_at":  "00:00 UTC",
    }


def reserve(user) -> dict:
    """Atomically increment the user's counter and return the post-increment
    usage. Caller must check `remaining >= 0` BEFORE calling reserve - this
    function does not refuse, it just counts. The endpoint enforces the cap
    based on the returned value (allows the same call to be both counter
    and cap-check)."""
    user_id = str(user.id)
    key = (user_id, _today_key())
    with _lock:
        _counters[key] = _counters.get(key, 0) + 1
        used = _counters[key]
    cap = _cap_for(user)
    return {
        "used":      used,
        "cap":       cap,
        "remaining": max(0, cap - used),
        "exceeded":  used > cap,
    }


def reset_user(user) -> None:
    """Manually reset a user's counter for the day. Admin tooling."""
    user_id = str(user.id)
    key = (user_id, _today_key())
    with _lock:
        _counters.pop(key, None)
