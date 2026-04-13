"""Google Ads Intelligence — dashboard data endpoints + sync trigger.

All endpoints require the google_ads app to be enabled for the client
(enforced by require_app("google_ads") dependency).
"""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from models import Job, OAuthConnection, SyncRun, get_db
from services.auth_service import get_current_user
from services.feature_gate import require_app

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Helpers ──────────────────────────────────────────────────────────

def _default_dates(date_from: str | None, date_to: str | None) -> tuple[str, str]:
    """Default to last 30 days if not specified."""
    if not date_to:
        date_to = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    if not date_from:
        date_from = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
    return date_from, date_to


# ── Campaigns ────────────────────────────────────────────────────────

@router.get("/{client_id}/campaigns/summary")
async def campaign_summary(
    client_id: str,
    date_from: str = Query(None),
    date_to: str = Query(None),
    customer_id: str = Query(None, description="Filter by brand account"),
    client=Depends(require_app("google_ads")),
    db: Session = Depends(get_db),
):
    """Aggregated campaign KPIs for the date range."""
    date_from, date_to = _default_dates(date_from, date_to)

    filters = "WHERE client_id = :client_id AND date >= :date_from AND date <= :date_to"
    params: dict = {"client_id": client_id, "date_from": date_from, "date_to": date_to}
    if customer_id:
        filters += " AND customer_id = :customer_id"
        params["customer_id"] = customer_id

    row = db.execute(text(f"""
        SELECT
            COALESCE(SUM(impressions), 0) as total_impressions,
            COALESCE(SUM(clicks), 0) as total_clicks,
            COALESCE(SUM(cost_micros), 0) as total_cost_micros,
            COALESCE(SUM(conversions), 0) as total_conversions,
            COALESCE(SUM(conversions_value), 0) as total_conversions_value,
            COALESCE(SUM(all_conversions), 0) as total_all_conversions,
            COUNT(DISTINCT campaign_id) as campaign_count,
            COUNT(DISTINCT customer_id) as account_count
        FROM gads_campaigns
        {filters}
    """), params).fetchone()

    total_cost = (row.total_cost_micros or 0) / 1_000_000
    total_conv = row.total_conversions or 0
    total_conv_value = row.total_conversions_value or 0

    return {
        "date_from": date_from,
        "date_to": date_to,
        "impressions": row.total_impressions,
        "clicks": row.total_clicks,
        "cost": round(total_cost, 2),
        "conversions": round(total_conv, 1),
        "conversions_value": round(total_conv_value, 2),
        "all_conversions": round(row.total_all_conversions, 1),
        "ctr": round(row.total_clicks / row.total_impressions * 100, 2) if row.total_impressions else 0,
        "avg_cpc": round(total_cost / row.total_clicks, 2) if row.total_clicks else 0,
        "cpa": round(total_cost / total_conv, 2) if total_conv else 0,
        "roas": round(total_conv_value / total_cost, 2) if total_cost else 0,
        "campaign_count": row.campaign_count,
        "account_count": row.account_count,
    }


@router.get("/{client_id}/campaigns/daily")
async def campaign_daily(
    client_id: str,
    date_from: str = Query(None),
    date_to: str = Query(None),
    customer_id: str = Query(None),
    client=Depends(require_app("google_ads")),
    db: Session = Depends(get_db),
):
    """Daily time series for trend charts."""
    date_from, date_to = _default_dates(date_from, date_to)

    filters = "WHERE client_id = :client_id AND date >= :date_from AND date <= :date_to"
    params: dict = {"client_id": client_id, "date_from": date_from, "date_to": date_to}
    if customer_id:
        filters += " AND customer_id = :customer_id"
        params["customer_id"] = customer_id

    rows = db.execute(text(f"""
        SELECT date,
            SUM(impressions) as impressions,
            SUM(clicks) as clicks,
            SUM(cost_micros) as cost_micros,
            SUM(conversions) as conversions,
            SUM(conversions_value) as conversions_value
        FROM gads_campaigns
        {filters}
        GROUP BY date ORDER BY date
    """), params).fetchall()

    return [
        {
            "date": str(r.date),
            "impressions": r.impressions,
            "clicks": r.clicks,
            "cost": round(r.cost_micros / 1_000_000, 2) if r.cost_micros else 0,
            "conversions": round(r.conversions, 1) if r.conversions else 0,
            "conversions_value": round(r.conversions_value, 2) if r.conversions_value else 0,
        }
        for r in rows
    ]


@router.get("/{client_id}/campaigns/by-channel")
async def campaigns_by_channel(
    client_id: str,
    date_from: str = Query(None),
    date_to: str = Query(None),
    client=Depends(require_app("google_ads")),
    db: Session = Depends(get_db),
):
    """Spend breakdown by campaign channel type."""
    date_from, date_to = _default_dates(date_from, date_to)

    rows = db.execute(text("""
        SELECT channel_type,
            SUM(cost_micros) as cost_micros,
            SUM(clicks) as clicks,
            SUM(impressions) as impressions,
            SUM(conversions) as conversions
        FROM gads_campaigns
        WHERE client_id = :client_id AND date >= :date_from AND date <= :date_to
        GROUP BY channel_type ORDER BY cost_micros DESC
    """), {"client_id": client_id, "date_from": date_from, "date_to": date_to}).fetchall()

    return [
        {
            "channel_type": r.channel_type,
            "cost": round(r.cost_micros / 1_000_000, 2) if r.cost_micros else 0,
            "clicks": r.clicks,
            "impressions": r.impressions,
            "conversions": round(r.conversions, 1) if r.conversions else 0,
        }
        for r in rows
    ]


@router.get("/{client_id}/campaigns/top")
async def top_campaigns(
    client_id: str,
    date_from: str = Query(None),
    date_to: str = Query(None),
    customer_id: str = Query(None),
    sort_by: str = Query("cost_micros", description="Sort metric"),
    limit: int = Query(20),
    client=Depends(require_app("google_ads")),
    db: Session = Depends(get_db),
):
    """Top campaigns by spend, conversions, or clicks."""
    date_from, date_to = _default_dates(date_from, date_to)
    allowed_sorts = {"cost_micros", "clicks", "conversions", "impressions", "conversions_value"}
    if sort_by not in allowed_sorts:
        sort_by = "cost_micros"

    filters = "WHERE client_id = :client_id AND date >= :date_from AND date <= :date_to"
    params: dict = {"client_id": client_id, "date_from": date_from, "date_to": date_to, "limit": limit}
    if customer_id:
        filters += " AND customer_id = :customer_id"
        params["customer_id"] = customer_id

    rows = db.execute(text(f"""
        SELECT customer_id, campaign_id, campaign_name, channel_type,
            SUM(impressions) as impressions,
            SUM(clicks) as clicks,
            SUM(cost_micros) as cost_micros,
            SUM(conversions) as conversions,
            SUM(conversions_value) as conversions_value
        FROM gads_campaigns
        {filters}
        GROUP BY customer_id, campaign_id, campaign_name, channel_type
        ORDER BY {sort_by} DESC
        LIMIT :limit
    """), params).fetchall()

    return [
        {
            "customer_id": r.customer_id,
            "campaign_id": r.campaign_id,
            "campaign_name": r.campaign_name,
            "channel_type": r.channel_type,
            "impressions": r.impressions,
            "clicks": r.clicks,
            "cost": round(r.cost_micros / 1_000_000, 2) if r.cost_micros else 0,
            "conversions": round(r.conversions, 1) if r.conversions else 0,
            "conversions_value": round(r.conversions_value, 2) if r.conversions_value else 0,
            "cpa": round(r.cost_micros / 1_000_000 / r.conversions, 2) if r.conversions else None,
        }
        for r in rows
    ]


# ── Keywords ─────────────────────────────────────────────────────────

@router.get("/{client_id}/keywords/top")
async def top_keywords(
    client_id: str,
    date_from: str = Query(None),
    date_to: str = Query(None),
    customer_id: str = Query(None),
    sort_by: str = Query("cost_micros"),
    limit: int = Query(50),
    client=Depends(require_app("google_ads")),
    db: Session = Depends(get_db),
):
    """Top keywords by spend, conversions, or clicks."""
    date_from, date_to = _default_dates(date_from, date_to)
    allowed_sorts = {"cost_micros", "clicks", "conversions", "impressions"}
    if sort_by not in allowed_sorts:
        sort_by = "cost_micros"

    filters = "WHERE client_id = :client_id AND date >= :date_from AND date <= :date_to"
    params: dict = {"client_id": client_id, "date_from": date_from, "date_to": date_to, "limit": limit}
    if customer_id:
        filters += " AND customer_id = :customer_id"
        params["customer_id"] = customer_id

    rows = db.execute(text(f"""
        SELECT keyword_text, match_type, customer_id, campaign_name,
            SUM(impressions) as impressions,
            SUM(clicks) as clicks,
            SUM(cost_micros) as cost_micros,
            SUM(conversions) as conversions,
            AVG(quality_score) as avg_quality_score,
            AVG(search_impr_share) as avg_search_impr_share
        FROM gads_keywords
        {filters}
        GROUP BY keyword_text, match_type, customer_id, campaign_name
        ORDER BY {sort_by} DESC
        LIMIT :limit
    """), params).fetchall()

    return [
        {
            "keyword": r.keyword_text,
            "match_type": r.match_type,
            "customer_id": r.customer_id,
            "campaign_name": r.campaign_name,
            "impressions": r.impressions,
            "clicks": r.clicks,
            "cost": round(r.cost_micros / 1_000_000, 2) if r.cost_micros else 0,
            "conversions": round(r.conversions, 1) if r.conversions else 0,
            "quality_score": round(r.avg_quality_score, 1) if r.avg_quality_score else None,
            "search_impr_share": round(r.avg_search_impr_share * 100, 1) if r.avg_search_impr_share else None,
        }
        for r in rows
    ]


# ── Search Terms ─────────────────────────────────────────────────────

@router.get("/{client_id}/search-terms/top")
async def top_search_terms(
    client_id: str,
    date_from: str = Query(None),
    date_to: str = Query(None),
    customer_id: str = Query(None),
    sort_by: str = Query("cost_micros"),
    limit: int = Query(50),
    client=Depends(require_app("google_ads")),
    db: Session = Depends(get_db),
):
    """Top search terms by spend or conversions."""
    date_from, date_to = _default_dates(date_from, date_to)
    allowed_sorts = {"cost_micros", "clicks", "conversions", "impressions"}
    if sort_by not in allowed_sorts:
        sort_by = "cost_micros"

    filters = "WHERE client_id = :client_id AND date >= :date_from AND date <= :date_to"
    params: dict = {"client_id": client_id, "date_from": date_from, "date_to": date_to, "limit": limit}
    if customer_id:
        filters += " AND customer_id = :customer_id"
        params["customer_id"] = customer_id

    rows = db.execute(text(f"""
        SELECT search_term, customer_id,
            SUM(impressions) as impressions,
            SUM(clicks) as clicks,
            SUM(cost_micros) as cost_micros,
            SUM(conversions) as conversions
        FROM gads_search_terms
        {filters}
        GROUP BY search_term, customer_id
        ORDER BY {sort_by} DESC
        LIMIT :limit
    """), params).fetchall()

    return [
        {
            "search_term": r.search_term,
            "customer_id": r.customer_id,
            "impressions": r.impressions,
            "clicks": r.clicks,
            "cost": round(r.cost_micros / 1_000_000, 2) if r.cost_micros else 0,
            "conversions": round(r.conversions, 1) if r.conversions else 0,
        }
        for r in rows
    ]


# ── Store Performance ────────────────────────────────────────────────

@router.get("/{client_id}/stores/summary")
async def store_summary(
    client_id: str,
    date_from: str = Query(None),
    date_to: str = Query(None),
    customer_id: str = Query(None),
    sort_by: str = Query("store_visits"),
    limit: int = Query(50),
    client=Depends(require_app("google_ads")),
    db: Session = Depends(get_db),
):
    """Per-store aggregated metrics."""
    date_from, date_to = _default_dates(date_from, date_to)
    allowed_sorts = {"store_visits", "click_to_call", "directions", "website_clicks", "eligible_impressions"}
    if sort_by not in allowed_sorts:
        sort_by = "store_visits"

    filters = "WHERE client_id = :client_id AND date >= :date_from AND date <= :date_to"
    params: dict = {"client_id": client_id, "date_from": date_from, "date_to": date_to, "limit": limit}
    if customer_id:
        filters += " AND customer_id = :customer_id"
        params["customer_id"] = customer_id

    rows = db.execute(text(f"""
        SELECT place_id, business_name, city, postal_code,
            SUM(store_visits) as store_visits,
            SUM(click_to_call) as click_to_call,
            SUM(directions) as directions,
            SUM(website_clicks) as website_clicks,
            SUM(eligible_impressions) as eligible_impressions,
            SUM(other_engagement) as other_engagement
        FROM gads_store_performance
        {filters}
        GROUP BY place_id, business_name, city, postal_code
        ORDER BY {sort_by} DESC
        LIMIT :limit
    """), params).fetchall()

    return [
        {
            "place_id": r.place_id,
            "business_name": r.business_name,
            "city": r.city,
            "postal_code": r.postal_code,
            "store_visits": round(r.store_visits, 1) if r.store_visits else 0,
            "click_to_call": round(r.click_to_call, 1) if r.click_to_call else 0,
            "directions": round(r.directions, 1) if r.directions else 0,
            "website_clicks": round(r.website_clicks, 1) if r.website_clicks else 0,
            "eligible_impressions": round(r.eligible_impressions, 0) if r.eligible_impressions else 0,
        }
        for r in rows
    ]


@router.get("/{client_id}/stores/{place_id}/daily")
async def store_daily(
    client_id: str,
    place_id: str,
    date_from: str = Query(None),
    date_to: str = Query(None),
    client=Depends(require_app("google_ads")),
    db: Session = Depends(get_db),
):
    """Daily time series for a specific store."""
    date_from, date_to = _default_dates(date_from, date_to)

    rows = db.execute(text("""
        SELECT date,
            SUM(store_visits) as store_visits,
            SUM(click_to_call) as click_to_call,
            SUM(directions) as directions,
            SUM(website_clicks) as website_clicks,
            SUM(eligible_impressions) as eligible_impressions
        FROM gads_store_performance
        WHERE client_id = :client_id AND place_id = :place_id
          AND date >= :date_from AND date <= :date_to
        GROUP BY date ORDER BY date
    """), {"client_id": client_id, "place_id": place_id,
           "date_from": date_from, "date_to": date_to}).fetchall()

    return [
        {
            "date": str(r.date),
            "store_visits": round(r.store_visits, 1) if r.store_visits else 0,
            "click_to_call": round(r.click_to_call, 1) if r.click_to_call else 0,
            "directions": round(r.directions, 1) if r.directions else 0,
            "website_clicks": round(r.website_clicks, 1) if r.website_clicks else 0,
        }
        for r in rows
    ]


# ── Sync management ──────────────────────────────────────────────────

@router.get("/{client_id}/syncs")
async def list_syncs(
    client_id: str,
    limit: int = Query(20),
    client=Depends(require_app("google_ads")),
    db: Session = Depends(get_db),
):
    """List recent sync runs."""
    rows = (
        db.query(SyncRun)
        .filter(SyncRun.client_id == client_id)
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


class SyncTriggerRequest(BaseModel):
    connection_id: str
    sync_types: list[str] | None = None
    date_from: str | None = None
    date_to: str | None = None
    customer_ids: list[str] | None = None


@router.post("/{client_id}/syncs")
async def trigger_sync(
    client_id: str,
    req: SyncTriggerRequest,
    user=Depends(get_current_user),
    client=Depends(require_app("google_ads")),
    db: Session = Depends(get_db),
):
    """Manually trigger a Google Ads data sync (creates a job)."""
    # Verify connection belongs to client
    conn = db.query(OAuthConnection).filter(
        OAuthConnection.id == req.connection_id,
        OAuthConnection.client_id == client_id,
    ).first()
    if not conn:
        raise HTTPException(404, "Connection not found for this client")

    job = Job(
        client_id=client_id,
        job_type="sync_google_ads",
        payload={
            "client_id": client_id,
            "connection_id": req.connection_id,
            "sync_types": req.sync_types or ["campaigns", "keywords", "search_terms", "per_store"],
            "date_from": req.date_from,
            "date_to": req.date_to,
            "customer_ids": req.customer_ids,
        },
    )
    db.add(job)
    db.commit()

    return {"job_id": str(job.id), "status": "pending"}
