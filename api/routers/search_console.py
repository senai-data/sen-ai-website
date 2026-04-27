"""Google Search Console — site management, sync trigger, sync history, dashboard data.

All endpoints require the search_console app to be enabled for the client
(enforced by require_app("search_console") dependency).
"""

import logging
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from models import Client, Job, OAuthConnection, SyncRun, get_db
from services.auth_service import get_current_user
from services.feature_gate import require_app
from services.token_manager import get_valid_access_token

logger = logging.getLogger(__name__)
router = APIRouter()

GSC_SITES_URL = "https://www.googleapis.com/webmasters/v3/sites"


# ── Sites ────────────────────────────────────────────────────────────

@router.get("/{client_id}/sites")
async def list_sites(
    client_id: str,
    user=Depends(get_current_user),
    client=Depends(require_app("search_console")),
    db: Session = Depends(get_db),
):
    """List GSC sites accessible by the connected account."""
    conn = (
        db.query(OAuthConnection)
        .filter(
            OAuthConnection.client_id == client_id,
            OAuthConnection.product == "search_console",
            OAuthConnection.status == "active",
        )
        .first()
    )
    if not conn:
        raise HTTPException(404, "No active Search Console connection found. Connect via Settings > Connections.")

    access_token = await get_valid_access_token(conn, db)

    async with httpx.AsyncClient() as http:
        resp = await http.get(
            GSC_SITES_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30.0,
        )
    if resp.status_code != 200:
        logger.error(f"GSC list_sites failed: {resp.status_code} {resp.text[:300]}")
        raise HTTPException(502, "Failed to fetch sites from Google Search Console")

    entries = resp.json().get("siteEntry", [])
    return [
        {"siteUrl": e.get("siteUrl"), "permissionLevel": e.get("permissionLevel")}
        for e in entries
    ]


class SiteSelectRequest(BaseModel):
    sites: list[str]


@router.post("/{client_id}/sites/select")
async def select_sites(
    client_id: str,
    req: SiteSelectRequest,
    user=Depends(get_current_user),
    client=Depends(require_app("search_console")),
    db: Session = Depends(get_db),
):
    """Save the user's GSC site selection into client.apps JSONB."""
    if not req.sites:
        raise HTTPException(400, "At least one site must be selected")

    client_obj = db.query(Client).filter(Client.id == client_id).first()
    if not client_obj:
        raise HTTPException(404, "Client not found")

    apps = dict(client_obj.apps or {})
    sc = apps.get("search_console", {})
    sc["enabled"] = True
    sc["sites"] = req.sites
    apps["search_console"] = sc

    client_obj.apps = apps
    flag_modified(client_obj, "apps")
    db.commit()

    return {"sites": req.sites, "status": "saved"}


# ── Sync management ──────────────────────────────────────────────────

@router.get("/{client_id}/syncs")
async def list_syncs(
    client_id: str,
    limit: int = Query(20),
    client=Depends(require_app("search_console")),
    db: Session = Depends(get_db),
):
    """List recent GSC sync runs."""
    rows = (
        db.query(SyncRun)
        .filter(
            SyncRun.client_id == client_id,
            SyncRun.sync_type == "search_console",
        )
        .order_by(SyncRun.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": str(r.id),
            "sync_type": r.sync_type,
            "status": r.status,
            "date_from": str(r.date_from) if r.date_from else None,
            "date_to": str(r.date_to) if r.date_to else None,
            "stats": r.stats,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            "error_message": r.error_message,
        }
        for r in rows
    ]


class GscSyncTriggerRequest(BaseModel):
    connection_id: str
    date_from: str | None = None
    date_to: str | None = None
    country: str | None = None


@router.post("/{client_id}/syncs")
async def trigger_sync(
    client_id: str,
    req: GscSyncTriggerRequest,
    user=Depends(get_current_user),
    client=Depends(require_app("search_console")),
    db: Session = Depends(get_db),
):
    """Manually trigger a GSC data sync (creates a job)."""
    # Verify connection belongs to client
    conn = db.query(OAuthConnection).filter(
        OAuthConnection.id == req.connection_id,
        OAuthConnection.client_id == client_id,
    ).first()
    if not conn:
        raise HTTPException(404, "Connection not found for this client")

    # Read selected sites from client.apps
    client_obj = db.query(Client).filter(Client.id == client_id).first()
    apps = client_obj.apps or {} if client_obj else {}
    domains = apps.get("search_console", {}).get("sites", [])
    if not domains:
        raise HTTPException(400, "No GSC sites selected. Use the site selector first.")

    job = Job(
        client_id=client_id,
        job_type="sync_gsc",
        payload={
            "client_id": client_id,
            "connection_id": req.connection_id,
            "domains": domains,
            "date_from": req.date_from,
            "date_to": req.date_to,
            "country": req.country,
        },
    )
    db.add(job)
    db.commit()

    return {"job_id": str(job.id), "status": "pending"}


# ── Dashboard data (real-time GSC API — no local DB needed) ─────────

GSC_ANALYTICS_URL = "https://www.googleapis.com/webmasters/v3/sites"


def _default_gsc_dates(date_from: str | None, date_to: str | None) -> tuple[str, str]:
    """Default to last 28 days (GSC data has ~3 day lag)."""
    if not date_to:
        date_to = (datetime.utcnow() - timedelta(days=3)).strftime("%Y-%m-%d")
    if not date_from:
        date_from = (datetime.utcnow() - timedelta(days=31)).strftime("%Y-%m-%d")
    return date_from, date_to


async def _get_gsc_connection(client_id: str, db: Session):
    """Get active GSC OAuth connection + valid access token."""
    conn = (
        db.query(OAuthConnection)
        .filter(
            OAuthConnection.client_id == client_id,
            OAuthConnection.product == "search_console",
            OAuthConnection.status == "active",
        )
        .first()
    )
    if not conn:
        raise HTTPException(404, "No active Search Console connection")
    access_token = await get_valid_access_token(conn, db)
    return conn, access_token


def _get_domain(client_id: str, domain: str | None, db: Session) -> str | None:
    """Get the target GSC domain. Returns one domain (not all).

    If domain param is given, use it. Otherwise default to first selected site.
    Dashboard shows one site at a time for speed (each API call = 2-5s).
    """
    client_obj = db.query(Client).filter(Client.id == client_id).first()
    sites = (client_obj.apps or {}).get("search_console", {}).get("sites", []) if client_obj else []
    if domain and domain in sites:
        return domain
    return sites[0] if sites else None


def _gsc_query_sync(access_token: str, domain: str, payload: dict) -> list[dict]:
    """Call GSC Search Analytics API (sync httpx — called via run_in_executor)."""
    from urllib.parse import quote
    encoded = quote(domain, safe="")
    url = f"{GSC_ANALYTICS_URL}/{encoded}/searchAnalytics/query"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    all_rows = []
    with httpx.Client() as http:
        while True:
            resp = http.post(url, json=payload, headers=headers, timeout=60.0)
            if resp.status_code != 200:
                logger.error(f"GSC API {resp.status_code}: {resp.text[:300]}")
                break
            data = resp.json()
            rows = data.get("rows", [])
            dims = payload.get("dimensions", [])
            for row in rows:
                keys = row.get("keys", [])
                parsed = {}
                for i, dim in enumerate(dims):
                    if i < len(keys):
                        parsed[dim] = keys[i]
                parsed["clicks"] = int(row.get("clicks", 0))
                parsed["impressions"] = int(row.get("impressions", 0))
                parsed["ctr"] = row.get("ctr", 0)
                parsed["position"] = row.get("position")
                all_rows.append(parsed)
            if len(rows) < payload.get("rowLimit", 25000):
                break
            payload["startRow"] = payload.get("startRow", 0) + payload.get("rowLimit", 25000)
    return all_rows


import asyncio
from functools import partial


async def _gsc_query(access_token: str, domain: str, payload: dict) -> list[dict]:
    """Async wrapper — runs sync httpx in a thread to avoid blocking the event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(_gsc_query_sync, access_token, domain, payload))


@router.get("/{client_id}/analytics/summary")
async def analytics_summary(
    client_id: str,
    date_from: str = Query(None),
    date_to: str = Query(None),
    domain: str = Query(None),
    client=Depends(require_app("search_console")),
    db: Session = Depends(get_db),
):
    """Real-time KPIs from GSC API (single site for speed)."""
    date_from, date_to = _default_gsc_dates(date_from, date_to)
    _, access_token = await _get_gsc_connection(client_id, db)
    site = _get_domain(client_id, domain, db)
    empty = {"date_from": date_from, "date_to": date_to, "domain": site, "clicks": 0, "impressions": 0,
             "ctr": 0, "avg_position": None, "unique_queries": 0, "days_with_data": 0}
    if not site:
        return empty

    # Single call with no dimensions → aggregated totals
    totals = await _gsc_query(access_token, site, {
        "startDate": date_from, "endDate": date_to,
        "dimensions": [], "rowLimit": 1, "startRow": 0,
    })
    if not totals:
        return empty
    t = totals[0]

    # Daily call for day count
    daily = await _gsc_query(access_token, site, {
        "startDate": date_from, "endDate": date_to,
        "dimensions": ["date"], "rowLimit": 25000, "startRow": 0,
    })

    return {
        "date_from": date_from,
        "date_to": date_to,
        "domain": site,
        "clicks": t["clicks"],
        "impressions": t["impressions"],
        "ctr": round(t["ctr"] * 100, 2) if t.get("ctr") else 0,
        "avg_position": round(t["position"], 1) if t.get("position") else None,
        "unique_queries": 0,
        "days_with_data": len(daily),
    }


@router.get("/{client_id}/analytics/daily")
async def analytics_daily(
    client_id: str,
    date_from: str = Query(None),
    date_to: str = Query(None),
    domain: str = Query(None),
    client=Depends(require_app("search_console")),
    db: Session = Depends(get_db),
):
    """Real-time daily trend from GSC API."""
    date_from, date_to = _default_gsc_dates(date_from, date_to)
    _, access_token = await _get_gsc_connection(client_id, db)
    site = _get_domain(client_id, domain, db)
    if not site:
        return []

    rows = await _gsc_query(access_token, site, {
        "startDate": date_from, "endDate": date_to,
        "dimensions": ["date"], "rowLimit": 25000, "startRow": 0,
    })

    return [
        {
            "date": r.get("date", ""),
            "clicks": r["clicks"],
            "impressions": r["impressions"],
            "ctr": round(r["ctr"] * 100, 2) if r.get("ctr") else 0,
            "position": round(r["position"], 1) if r.get("position") else None,
        }
        for r in sorted(rows, key=lambda x: x.get("date", ""))
    ]


@router.get("/{client_id}/analytics/queries")
async def analytics_top_queries(
    client_id: str,
    date_from: str = Query(None),
    date_to: str = Query(None),
    domain: str = Query(None),
    limit: int = Query(100),
    client=Depends(require_app("search_console")),
    db: Session = Depends(get_db),
):
    """Real-time top queries from GSC API (sorted by clicks desc)."""
    date_from, date_to = _default_gsc_dates(date_from, date_to)
    _, access_token = await _get_gsc_connection(client_id, db)
    site = _get_domain(client_id, domain, db)
    if not site:
        return []

    rows = await _gsc_query(access_token, site, {
        "startDate": date_from, "endDate": date_to,
        "dimensions": ["query"], "rowLimit": min(limit, 25000), "startRow": 0,
    })

    # GSC returns rows sorted by clicks desc by default
    return [
        {
            "query": r.get("query", ""),
            "clicks": r["clicks"],
            "impressions": r["impressions"],
            "ctr": round(r["ctr"] * 100, 2) if r.get("ctr") else 0,
            "position": round(r["position"], 1) if r.get("position") else None,
        }
        for r in rows[:limit]
    ]


@router.get("/{client_id}/analytics/pages")
async def analytics_top_pages(
    client_id: str,
    date_from: str = Query(None),
    date_to: str = Query(None),
    domain: str = Query(None),
    limit: int = Query(100),
    client=Depends(require_app("search_console")),
    db: Session = Depends(get_db),
):
    """Real-time top pages from GSC API (sorted by clicks desc)."""
    date_from, date_to = _default_gsc_dates(date_from, date_to)
    _, access_token = await _get_gsc_connection(client_id, db)
    site = _get_domain(client_id, domain, db)
    if not site:
        return []

    rows = await _gsc_query(access_token, site, {
        "startDate": date_from, "endDate": date_to,
        "dimensions": ["page"], "rowLimit": min(limit, 25000), "startRow": 0,
    })

    return [
        {
            "page": r.get("page", ""),
            "clicks": r["clicks"],
            "impressions": r["impressions"],
            "ctr": round(r["ctr"] * 100, 2) if r.get("ctr") else 0,
            "position": round(r["position"], 1) if r.get("position") else None,
        }
        for r in rows[:limit]
    ]
