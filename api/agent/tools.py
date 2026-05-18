"""Agent tools - 10 read-only-ish functions the chatbot can call.

Architecture :
  Each tool is a plain Python function ``def tool_name(user, db, **params)``
  that returns a JSON-serializable dict. Every tool reuses the existing
  ``services.access`` scoping (list_user_clients, check_client_access) so
  cross-tenant data leaks are structurally impossible : a user can only see
  data for clients they already have access to in the UI.

  The Anthropic tool schemas live alongside each function in ``TOOLS``. The
  chat endpoint imports ``TOOL_SCHEMAS`` for messages.create() and calls
  ``run_tool(name, user, db, params)`` when the model issues a tool_use block.

Tool list (10) :
  1. get_active_context       active org + available clients (orientation)
  2. list_clients             scoped to active org
  3. list_recent_scans        for a client, paginated
  4. get_brand_visibility     latest visibility scores per LLM provider
  5. count_opportunities      content items grouped by status
  6. list_recent_content      recent FAQ + articles for a client
  7. get_content_detail       full detail of one item (verdict, sources)
  8. list_credits             current balances (scan + content)
  9. search_content_by_topic  full-text search across content items
  10. trigger_scan            enqueue a new scan (the only mutation - idempotent)

Future tools (not in v1) :
  - publish_content, reject_content : mutations beyond trigger_scan need
    explicit user-confirmation flow before being agent-callable.
  - refresh_ai_snapshot, generate_article : already cost-bearing operations,
    keep them behind the explicit UI button for now.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import func, or_, desc, case, Integer
from sqlalchemy.orm import Session

from models import (
    Client, ClientCredit, Scan, ScanContentItem,
    ScanLLMResult, Job, User,
)
from services.access import (
    check_client_access, list_user_clients, list_user_organizations,
    resolve_active_organization_id,
)

logger = logging.getLogger(__name__)

# Hard cap on rows returned by any list_* tool. Prevents the LLM from
# pulling a 10 000-item list into context and blowing the prompt budget.
MAX_ROWS = 25


# ── Helpers ────────────────────────────────────────────────────────────────

def _ensure_uuid(s: str | None) -> str | None:
    """Validate that a string is a well-formed UUID, return it normalized.
    Returns None for invalid / empty input so callers can branch cleanly
    instead of crashing on a malformed model-supplied param."""
    if not s:
        return None
    try:
        return str(uuid.UUID(str(s)))
    except (ValueError, AttributeError):
        return None


def _scoped_client(client_id: str, user: User, db: Session) -> Client | None:
    """Resolve a client ID into a Client instance the user can access.
    Returns None when access is denied so the tool can return a clean
    error dict rather than raising HTTPException (which would be wrapped
    in a 500 by the chat endpoint, less helpful for the LLM)."""
    cid = _ensure_uuid(client_id)
    if not cid:
        return None
    try:
        check_client_access(cid, user, db, method="GET")
    except Exception:
        return None
    return db.query(Client).filter(Client.id == cid).first()


def _client_brief(c: Client) -> dict:
    """Lightweight client representation - only the fields useful for an
    LLM to identify and reason about a workspace."""
    return {
        "id":    str(c.id),
        "name":  c.name,
        "brand": c.brand or None,
    }


# ── Tool implementations ───────────────────────────────────────────────────

def get_active_context(user: User, db: Session, _active_org_cookie: str | None = None) -> dict:
    """Where the user is in the app : active org + available clients.

    The chatbot should call this FIRST when a user opens the conversation
    so it knows what 'my clients' or 'my workspace' refers to. Without it
    the model would guess from the user's email and likely be wrong.
    """
    active_org_id = resolve_active_organization_id(user, db, _active_org_cookie)
    orgs = list_user_organizations(user, db)
    clients = list_user_clients(user, db, organization_id=active_org_id)
    return {
        "user_email":       user.email,
        "active_org_id":    active_org_id,
        "active_org_name":  next((o.name for o in orgs if str(o.id) == active_org_id), None),
        "available_orgs":   [{"id": str(o.id), "name": o.name} for o in orgs],
        "available_clients": [_client_brief(c) for c in clients[:MAX_ROWS]],
        "total_clients":    len(clients),
    }


def list_clients(user: User, db: Session, _active_org_cookie: str | None = None) -> dict:
    """List the clients (workspaces) the user can access in the active org."""
    active_org_id = resolve_active_organization_id(user, db, _active_org_cookie)
    clients = list_user_clients(user, db, organization_id=active_org_id)
    return {
        "clients":      [_client_brief(c) for c in clients[:MAX_ROWS]],
        "total":        len(clients),
        "truncated":    len(clients) > MAX_ROWS,
        "active_org_id": active_org_id,
    }


def list_recent_scans(
    user: User, db: Session,
    client_id: str, limit: int = 10,
) -> dict:
    """Recent scans for one client, newest first. status field reflects
    the worker job lifecycle (pending / running / completed / failed)."""
    client = _scoped_client(client_id, user, db)
    if not client:
        return {"error": "client_not_found_or_access_denied", "client_id": client_id}
    limit = max(1, min(limit, MAX_ROWS))
    scans = (
        db.query(Scan)
        .filter(Scan.client_id == client.id)
        .order_by(desc(Scan.created_at))
        .limit(limit)
        .all()
    )
    return {
        "client":  _client_brief(client),
        "scans": [
            {
                "id":             str(s.id),
                "name":           s.name or None,
                "status":         s.status,
                "domain":         s.domain,
                "created_at":     s.created_at.isoformat() + "Z" if s.created_at else None,
                "completed_at":   s.completed_at.isoformat() + "Z" if getattr(s, "completed_at", None) else None,
            }
            for s in scans
        ],
        "count":  len(scans),
    }


def get_brand_visibility(
    user: User, db: Session,
    client_id: str, scan_id: str | None = None,
) -> dict:
    """Aggregate brand visibility from the latest scan (or a specific scan).

    Returns counts of LLM responses where the brand appears in citations
    vs total responses, per LLM provider. The denominator is the number
    of (question, provider) pairs recorded in scan_llm_results.
    """
    client = _scoped_client(client_id, user, db)
    if not client:
        return {"error": "client_not_found_or_access_denied", "client_id": client_id}

    sid = _ensure_uuid(scan_id) if scan_id else None
    if sid:
        scan = db.query(Scan).filter(
            Scan.id == sid, Scan.client_id == client.id,
        ).first()
    else:
        scan = (
            db.query(Scan)
            .filter(Scan.client_id == client.id)
            .order_by(desc(Scan.created_at))
            .first()
        )
    if not scan:
        return {"error": "no_scan_found", "client": _client_brief(client)}

    # Aggregate per provider : count rows where target_cited=true (brand was
    # cited in the LLM's citations) vs total rows for the scan.
    cited_expr = case((ScanLLMResult.target_cited.is_(True), 1), else_=0)
    rows = (
        db.query(
            ScanLLMResult.provider,
            func.count(ScanLLMResult.id).label("total"),
            func.sum(cited_expr).label("cited"),
        )
        .filter(ScanLLMResult.scan_id == scan.id)
        .group_by(ScanLLMResult.provider)
        .all()
    )
    by_provider = [
        {
            "provider":   r.provider,
            "cited":      int(r.cited or 0),
            "total":      int(r.total or 0),
            "share_pct":  round(100.0 * (r.cited or 0) / max(1, r.total or 0), 1),
        }
        for r in rows
    ]
    return {
        "client":      _client_brief(client),
        "scan_id":     str(scan.id),
        "scan_name":   scan.name or None,
        "providers":   by_provider,
    }


def count_opportunities(
    user: User, db: Session,
    client_id: str, content_type: str | None = None,
) -> dict:
    """Number of ScanContentItem rows grouped by status, for one client.

    Optional content_type filter : 'faq' or 'netlinking_article'.
    Lets the LLM answer questions like 'how many drafts do I have ?'.
    """
    client = _scoped_client(client_id, user, db)
    if not client:
        return {"error": "client_not_found_or_access_denied", "client_id": client_id}

    q = (
        db.query(ScanContentItem.status, func.count(ScanContentItem.id))
        .join(Scan, Scan.id == ScanContentItem.scan_id)
        .filter(Scan.client_id == client.id)
    )
    if content_type:
        q = q.filter(ScanContentItem.content_type == content_type)
    q = q.group_by(ScanContentItem.status)
    rows = q.all()
    return {
        "client":        _client_brief(client),
        "content_type":  content_type or "all",
        "by_status":     {status: int(count) for status, count in rows},
        "total":         sum(int(count) for _, count in rows),
    }


def list_recent_content(
    user: User, db: Session,
    client_id: str,
    status: str | None = None,
    content_type: str | None = None,
    limit: int = 10,
) -> dict:
    """Recent content items (FAQ + articles) for one client, newest first."""
    client = _scoped_client(client_id, user, db)
    if not client:
        return {"error": "client_not_found_or_access_denied", "client_id": client_id}
    limit = max(1, min(limit, MAX_ROWS))

    q = (
        db.query(ScanContentItem)
        .join(Scan, Scan.id == ScanContentItem.scan_id)
        .filter(Scan.client_id == client.id)
    )
    if status:
        q = q.filter(ScanContentItem.status == status)
    if content_type:
        q = q.filter(ScanContentItem.content_type == content_type)
    items = q.order_by(desc(ScanContentItem.created_at)).limit(limit).all()

    return {
        "client":     _client_brief(client),
        "filters":    {"status": status, "content_type": content_type},
        "items": [
            {
                "id":             str(i.id),
                "content_type":   i.content_type,
                "status":         i.status,
                "topic_name":     i.topic_name or None,
                "target_question": (i.target_question or "")[:120] or None,
                "target_url":     i.target_url or None,
                "quality_score":  (i.content_metadata or {}).get("quality_score") if i.content_metadata else None,
                "created_at":     i.created_at.isoformat() + "Z" if i.created_at else None,
            }
            for i in items
        ],
        "count": len(items),
    }


def get_content_detail(user: User, db: Session, item_id: str) -> dict:
    """Full detail of one content item - quality verdict, sources, brand mentions.

    Used when the chatbot is asked to summarize / explain a specific draft.
    """
    iid = _ensure_uuid(item_id)
    if not iid:
        return {"error": "invalid_item_id", "item_id": item_id}

    item = db.query(ScanContentItem).filter(ScanContentItem.id == iid).first()
    if not item:
        return {"error": "item_not_found", "item_id": item_id}

    # Auth via the item's owning scan client
    scan = db.query(Scan).filter(Scan.id == item.scan_id).first()
    if not scan or not _scoped_client(str(scan.client_id), user, db):
        return {"error": "access_denied", "item_id": item_id}

    meta = item.content_metadata or {}
    sources_used = meta.get("sources_used") or []
    return {
        "id":               str(item.id),
        "content_type":     item.content_type,
        "status":           item.status,
        "topic":            item.topic_name or None,
        "persona":          item.persona_name or None,
        "target_question":  item.target_question or None,
        "target_url":       item.target_url or None,
        "metrics": {
            "quality_score":       meta.get("quality_score"),
            "validation_verdict":  meta.get("validation_verdict"),
            "ytg_soseo":           meta.get("ytg_soseo"),
            "ytg_dseo":            meta.get("ytg_dseo"),
            "target_soseo":        meta.get("target_soseo"),
            "target_dseo":         meta.get("target_dseo"),
            "fanout_coverage":     meta.get("fanout_coverage"),
            "sources_count":       len(sources_used),
            "duration_ms":         meta.get("duration_ms"),
        },
        "sources_by_type":  _sources_by_type(sources_used),
        "promoted_brand_ids":  [str(bid) for bid in (item.promoted_brand_ids or [])],
        "html_excerpt":     (item.content_text or item.content_html or "")[:600] or None,
        "validation_page_url":  f"/app/content/{item.id}",
    }


def _sources_by_type(sources_used: list[dict]) -> dict:
    """Group sources_used by type for a compact LLM-friendly summary."""
    by_type: dict[str, list[str]] = {}
    for s in sources_used:
        t = (s.get("type") or "other").lower()
        by_type.setdefault(t, []).append(s.get("domain") or s.get("url") or "")
    return {t: doms[:10] for t, doms in by_type.items()}


def list_credits(user: User, db: Session, client_id: str) -> dict:
    """Current credit balances for one client. The balance is the
    balance_after of the most recent ledger row per credit_type."""
    client = _scoped_client(client_id, user, db)
    if not client:
        return {"error": "client_not_found_or_access_denied", "client_id": client_id}

    balances: dict[str, int] = {}
    for credit_type in ("scan", "content"):
        last = (
            db.query(ClientCredit)
            .filter(
                ClientCredit.client_id == client.id,
                ClientCredit.credit_type == credit_type,
            )
            .order_by(desc(ClientCredit.created_at))
            .first()
        )
        balances[credit_type] = int(last.balance_after) if last else 0
    return {
        "client":   _client_brief(client),
        "balances": balances,
    }


def search_content_by_topic(
    user: User, db: Session,
    query: str,
    client_id: str | None = None,
    limit: int = 10,
) -> dict:
    """Find content items whose topic / question / target_url contains the query string.
    Searches across all clients the user can access if client_id is omitted.
    """
    if not query or len(query.strip()) < 2:
        return {"error": "query_too_short", "min_chars": 2}
    limit = max(1, min(limit, MAX_ROWS))

    if client_id:
        client = _scoped_client(client_id, user, db)
        if not client:
            return {"error": "client_not_found_or_access_denied", "client_id": client_id}
        client_ids = [client.id]
    else:
        clients = list_user_clients(user, db)
        client_ids = [c.id for c in clients]
        if not client_ids:
            return {"items": [], "count": 0}

    needle = f"%{query.strip().lower()}%"
    q = (
        db.query(ScanContentItem)
        .join(Scan, Scan.id == ScanContentItem.scan_id)
        .filter(Scan.client_id.in_(client_ids))
        .filter(
            or_(
                func.lower(func.coalesce(ScanContentItem.topic_name, "")).like(needle),
                func.lower(func.coalesce(ScanContentItem.target_question, "")).like(needle),
                func.lower(func.coalesce(ScanContentItem.target_url, "")).like(needle),
            )
        )
        .order_by(desc(ScanContentItem.created_at))
        .limit(limit)
    )
    items = q.all()
    return {
        "query":       query,
        "items": [
            {
                "id":             str(i.id),
                "content_type":   i.content_type,
                "status":         i.status,
                "topic_name":     i.topic_name or None,
                "target_question": (i.target_question or "")[:120] or None,
                "validation_page_url": f"/app/content/{i.id}",
            }
            for i in items
        ],
        "count":       len(items),
    }


def trigger_scan(
    user: User, db: Session,
    client_id: str, domain: str | None = None, name: str | None = None,
) -> dict:
    """Enqueue a new scan job for a client. Idempotent at the job level :
    the worker dedupes pending jobs for the same scan_id. We return the
    created scan_id so the chatbot can show a link to the live progress.

    Editor role required (uses the destructive-method gate in check_client_access).
    """
    cid = _ensure_uuid(client_id)
    if not cid:
        return {"error": "invalid_client_id", "client_id": client_id}
    try:
        check_client_access(cid, user, db, method="POST")
    except Exception:
        return {"error": "access_denied_editor_required", "client_id": client_id}

    client = db.query(Client).filter(Client.id == cid).first()
    if not client:
        return {"error": "client_not_found", "client_id": client_id}

    scan_domain = (domain or "").strip()
    if not scan_domain:
        return {"error": "domain_required", "client_id": client_id, "detail": "Pass an explicit domain - the client model has no default."}

    scan = Scan(
        client_id=client.id,
        name=name or f"Scan via assistant {scan_domain}",
        domain=scan_domain,
        status="pending",
        config={"trigger_source": "agent"},
    )
    db.add(scan)
    db.flush()  # need scan.id for the job payload

    job = Job(
        scan_id=scan.id,
        client_id=client.id,
        job_type="fetch_keywords",
        status="pending",
        payload={"scan_id": str(scan.id), "trigger_source": "agent"},
        attempts=0,
        max_attempts=3,
    )
    db.add(job)
    db.commit()

    return {
        "ok":      True,
        "scan_id": str(scan.id),
        "client":  _client_brief(client),
        "domain":  scan_domain,
        "status":  "pending",
        "scan_page_url": f"/app/scans/{scan.id}",
    }


# ── Tool registry + Anthropic schemas ──────────────────────────────────────

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_active_context",
        "description": (
            "Return the user's current location in the app : active organization, "
            "available organizations, and the list of clients (workspaces) they can "
            "access in the active org. Call this FIRST when starting a conversation "
            "so you know what 'my workspace' or 'my clients' refers to."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_clients",
        "description": "List the clients the user can access in the active organization.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_recent_scans",
        "description": (
            "List recent scans for one client, newest first. Each scan represents "
            "a snapshot of how LLMs answer the client's tracked questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "client_id": {"type": "string", "description": "UUID of the client"},
                "limit":     {"type": "integer", "description": "Max rows (1-25)", "default": 10},
            },
            "required": ["client_id"],
        },
    },
    {
        "name": "get_brand_visibility",
        "description": (
            "Aggregate brand visibility for one client : per-LLM-provider count of "
            "responses where the brand was cited vs total responses, plus share %. "
            "Uses the latest scan unless scan_id is given."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "client_id": {"type": "string"},
                "scan_id":   {"type": "string", "description": "Optional specific scan UUID. Latest if omitted."},
            },
            "required": ["client_id"],
        },
    },
    {
        "name": "count_opportunities",
        "description": (
            "Count of content items (FAQ + articles) grouped by status for one "
            "client. Status values : identified, draft, in_review, approved, "
            "published, rejected, generating."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "client_id":    {"type": "string"},
                "content_type": {"type": "string", "enum": ["faq", "netlinking_article"], "description": "Optional filter"},
            },
            "required": ["client_id"],
        },
    },
    {
        "name": "list_recent_content",
        "description": (
            "List recent content items (FAQ + articles) for one client, with their "
            "quality_score and status. Useful for 'show me my last 5 articles' "
            "queries. Each item includes a validation_page_url the user can open."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "client_id":    {"type": "string"},
                "status":       {"type": "string", "description": "Optional status filter"},
                "content_type": {"type": "string", "enum": ["faq", "netlinking_article"]},
                "limit":        {"type": "integer", "default": 10},
            },
            "required": ["client_id"],
        },
    },
    {
        "name": "get_content_detail",
        "description": (
            "Full detail of one content item : quality verdict, SOSEO/DSEO scores "
            "vs per-guide SERP targets, sources grouped by type (brand / scientific "
            "/ review / editorial), HTML excerpt, link to the validation page."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "description": "UUID of the content item"},
            },
            "required": ["item_id"],
        },
    },
    {
        "name": "list_credits",
        "description": "Current credit balances (scan + content) for one client.",
        "input_schema": {
            "type": "object",
            "properties": {"client_id": {"type": "string"}},
            "required": ["client_id"],
        },
    },
    {
        "name": "search_content_by_topic",
        "description": (
            "Substring search across content items (topic / target question / target "
            "url) for the user's accessible clients. Use when the user mentions a "
            "topic by name rather than by ID."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query":     {"type": "string", "description": "2+ char substring to search for"},
                "client_id": {"type": "string", "description": "Optional - omit to search across all accessible clients"},
                "limit":     {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "trigger_scan",
        "description": (
            "Enqueue a new scan for one client. Mutating but idempotent at the "
            "worker level. Returns scan_id + scan_page_url so the user can watch "
            "live progress. Requires editor role on the client."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "client_id": {"type": "string"},
                "domain":    {"type": "string", "description": "Optional - defaults to client.domain"},
                "name":      {"type": "string", "description": "Optional scan label"},
            },
            "required": ["client_id"],
        },
    },
]


TOOL_FUNCTIONS: dict[str, Any] = {
    "get_active_context":      get_active_context,
    "list_clients":            list_clients,
    "list_recent_scans":       list_recent_scans,
    "get_brand_visibility":    get_brand_visibility,
    "count_opportunities":     count_opportunities,
    "list_recent_content":     list_recent_content,
    "get_content_detail":      get_content_detail,
    "list_credits":            list_credits,
    "search_content_by_topic": search_content_by_topic,
    "trigger_scan":            trigger_scan,
}

# Tools that read the active_org cookie (so the dispatcher passes it in).
_TOOLS_NEEDING_ORG_COOKIE = {"get_active_context", "list_clients"}


def run_tool(
    name: str, user: User, db: Session,
    params: dict, active_org_cookie: str | None = None,
) -> dict:
    """Dispatch a tool call from the chat endpoint. Catches all exceptions
    and returns them as structured error dicts so the LLM can recover
    gracefully (vs the request hard-failing with a 500)."""
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return {"error": "unknown_tool", "tool": name, "available": sorted(TOOL_FUNCTIONS.keys())}
    try:
        kwargs = dict(params or {})
        if name in _TOOLS_NEEDING_ORG_COOKIE:
            kwargs["_active_org_cookie"] = active_org_cookie
        return fn(user, db, **kwargs)
    except TypeError as e:
        # Likely a missing required param or unexpected kwarg from the LLM.
        return {"error": "bad_params", "tool": name, "detail": str(e)}
    except Exception as e:
        logger.exception("agent.tools: %s raised", name)
        return {"error": "tool_failed", "tool": name, "detail": str(e)[:200]}
