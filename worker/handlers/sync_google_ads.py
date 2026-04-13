"""Handler: sync Google Ads data for a client.

Reads OAuth tokens from oauth_connections, calls the Google Ads API,
and upserts results into gads_* tables. Supports 4 sync types:
campaigns, keywords, search_terms, per_store (or 'all' for everything).

job_payload:
    client_id: str (required)
    connection_id: str (required — oauth_connections row for google_ads)
    sync_types: list[str] (optional, default ['campaigns', 'keywords', 'search_terms', 'per_store'])
    date_from: str YYYY-MM-DD (optional, default 30 days ago)
    date_to: str YYYY-MM-DD (optional, default yesterday)
    customer_ids: list[str] (optional, override connection.config.customer_ids)
"""

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from adapters.google_ads_client import (
    GoogleAdsClient,
    parse_campaign_row,
    parse_keyword_row,
    parse_per_store_row,
    parse_search_term_row,
)
from adapters.token_manager import decrypt_token
from config import settings

logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 30
DEFAULT_SYNC_TYPES = ["campaigns", "keywords", "search_terms", "per_store"]


def execute(job_payload: dict, scan_id: str | None, db: Session) -> dict:
    """Main entry point — called by the worker poll loop."""
    client_id = job_payload.get("client_id")
    connection_id = job_payload.get("connection_id")
    if not client_id or not connection_id:
        raise ValueError("Missing client_id or connection_id in job payload")

    sync_types = job_payload.get("sync_types", DEFAULT_SYNC_TYPES)
    date_to = job_payload.get("date_to") or (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    date_from = job_payload.get("date_from") or (
        datetime.utcnow() - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    ).strftime("%Y-%m-%d")

    # Load connection and decrypt tokens
    from models import OAuthConnection
    conn = db.query(OAuthConnection).filter(OAuthConnection.id == connection_id).first()
    if not conn:
        raise ValueError(f"OAuthConnection {connection_id} not found")
    if conn.status != "active":
        raise ValueError(f"OAuthConnection {connection_id} is {conn.status}")

    access_token = decrypt_token(conn.access_token_encrypted)
    config = conn.config or {}
    developer_token = config.get("developer_token") or settings.google_ads_developer_token
    login_customer_id = config.get("login_customer_id", "")
    customer_ids = job_payload.get("customer_ids") or config.get("customer_ids", [])

    if not developer_token:
        raise ValueError("No developer_token configured (neither in connection.config nor worker .env)")
    if not customer_ids:
        raise ValueError("No customer_ids configured — set in connection.config or job payload")

    # Create sync run
    from models import SyncRun
    sync_run = SyncRun(
        client_id=client_id,
        connection_id=connection_id,
        sync_type=",".join(sync_types),
        status="running",
        date_from=datetime.strptime(date_from, "%Y-%m-%d"),
        date_to=datetime.strptime(date_to, "%Y-%m-%d"),
        started_at=datetime.utcnow(),
    )
    db.add(sync_run)
    db.flush()
    sync_run_id = str(sync_run.id)

    gads_client = GoogleAdsClient(
        access_token=access_token,
        developer_token=developer_token,
        login_customer_id=login_customer_id,
        api_version=settings.google_ads_api_version,
    )

    total_stats = {"rows_fetched": 0, "accounts_synced": 0, "errors": []}

    try:
        for cid in customer_ids:
            cid_clean = cid.replace("-", "")
            logger.info(f"Syncing customer {cid_clean} ({date_from} → {date_to})")

            try:
                if "campaigns" in sync_types:
                    n = asyncio.run(_sync_campaigns(gads_client, client_id, cid_clean, date_from, date_to, sync_run_id, db))
                    total_stats["rows_fetched"] += n

                if "keywords" in sync_types:
                    n = asyncio.run(_sync_keywords(gads_client, client_id, cid_clean, date_from, date_to, sync_run_id, db))
                    total_stats["rows_fetched"] += n

                if "search_terms" in sync_types:
                    n = asyncio.run(_sync_search_terms(gads_client, client_id, cid_clean, date_from, date_to, sync_run_id, db))
                    total_stats["rows_fetched"] += n

                if "per_store" in sync_types:
                    n = asyncio.run(_sync_per_store(gads_client, client_id, cid_clean, date_from, date_to, sync_run_id, db))
                    total_stats["rows_fetched"] += n

                total_stats["accounts_synced"] += 1
            except Exception as e:
                logger.exception(f"Error syncing customer {cid_clean}: {e}")
                total_stats["errors"].append({"customer_id": cid_clean, "error": str(e)})

        # Update sync run
        sync_run.status = "completed"
        sync_run.stats = total_stats
        sync_run.completed_at = datetime.utcnow()
        db.commit()

        logger.info(
            f"Sync completed: {total_stats['accounts_synced']} accounts, "
            f"{total_stats['rows_fetched']} rows, {len(total_stats['errors'])} errors"
        )
        return total_stats

    except Exception as e:
        sync_run.status = "failed"
        sync_run.error_message = str(e)
        sync_run.completed_at = datetime.utcnow()
        db.commit()
        raise


async def _sync_campaigns(
    client: GoogleAdsClient, client_id: str, customer_id: str,
    date_from: str, date_to: str, sync_run_id: str, db: Session,
) -> int:
    """Fetch campaigns and upsert into gads_campaigns."""
    rows = await client.query_campaigns(customer_id, date_from, date_to)
    if not rows:
        return 0

    for row in rows:
        parsed = parse_campaign_row(row)
        db.execute(text("""
            INSERT INTO gads_campaigns (
                client_id, customer_id, campaign_id, campaign_name, channel_type,
                status, date, impressions, clicks, cost_micros, conversions,
                conversions_value, all_conversions, all_conversions_value,
                ctr, avg_cpc, avg_cpm, abs_top_impr_pct, top_impr_pct,
                optimization_score, bidding_strategy, budget_micros,
                raw_data, sync_run_id
            ) VALUES (
                :client_id, :customer_id, :campaign_id, :campaign_name, :channel_type,
                :status, :date, :impressions, :clicks, :cost_micros, :conversions,
                :conversions_value, :all_conversions, :all_conversions_value,
                :ctr, :avg_cpc, :avg_cpm, :abs_top_impr_pct, :top_impr_pct,
                :optimization_score, :bidding_strategy, :budget_micros,
                :raw_data, :sync_run_id
            )
            ON CONFLICT (client_id, customer_id, campaign_id, date)
            DO UPDATE SET
                campaign_name = EXCLUDED.campaign_name,
                channel_type = EXCLUDED.channel_type,
                status = EXCLUDED.status,
                impressions = EXCLUDED.impressions,
                clicks = EXCLUDED.clicks,
                cost_micros = EXCLUDED.cost_micros,
                conversions = EXCLUDED.conversions,
                conversions_value = EXCLUDED.conversions_value,
                all_conversions = EXCLUDED.all_conversions,
                all_conversions_value = EXCLUDED.all_conversions_value,
                ctr = EXCLUDED.ctr,
                avg_cpc = EXCLUDED.avg_cpc,
                avg_cpm = EXCLUDED.avg_cpm,
                abs_top_impr_pct = EXCLUDED.abs_top_impr_pct,
                top_impr_pct = EXCLUDED.top_impr_pct,
                optimization_score = EXCLUDED.optimization_score,
                bidding_strategy = EXCLUDED.bidding_strategy,
                budget_micros = EXCLUDED.budget_micros,
                raw_data = EXCLUDED.raw_data,
                sync_run_id = EXCLUDED.sync_run_id
        """), {
            "client_id": client_id,
            "customer_id": customer_id,
            "sync_run_id": sync_run_id,
            "raw_data": "{}",
            **parsed,
        })

    db.commit()
    logger.info(f"Campaigns: upserted {len(rows)} rows for customer {customer_id}")
    return len(rows)


async def _sync_keywords(
    client: GoogleAdsClient, client_id: str, customer_id: str,
    date_from: str, date_to: str, sync_run_id: str, db: Session,
) -> int:
    """Fetch keywords and upsert into gads_keywords."""
    rows = await client.query_keywords(customer_id, date_from, date_to)
    if not rows:
        return 0

    for row in rows:
        parsed = parse_keyword_row(row)
        db.execute(text("""
            INSERT INTO gads_keywords (
                client_id, customer_id, campaign_id, campaign_name,
                ad_group_id, ad_group_name, keyword_text, match_type,
                criterion_id, date, impressions, clicks, cost_micros,
                conversions, conversions_value, ctr, avg_cpc, quality_score,
                search_impr_share, search_abs_top_impr_share,
                search_top_impr_share, search_click_share,
                raw_data, sync_run_id
            ) VALUES (
                :client_id, :customer_id, :campaign_id, :campaign_name,
                :ad_group_id, :ad_group_name, :keyword_text, :match_type,
                :criterion_id, :date, :impressions, :clicks, :cost_micros,
                :conversions, :conversions_value, :ctr, :avg_cpc, :quality_score,
                :search_impr_share, :search_abs_top_impr_share,
                :search_top_impr_share, :search_click_share,
                :raw_data, :sync_run_id
            )
            ON CONFLICT (client_id, customer_id, COALESCE(criterion_id, 0), date)
            DO UPDATE SET
                campaign_name = EXCLUDED.campaign_name,
                ad_group_name = EXCLUDED.ad_group_name,
                keyword_text = EXCLUDED.keyword_text,
                match_type = EXCLUDED.match_type,
                impressions = EXCLUDED.impressions,
                clicks = EXCLUDED.clicks,
                cost_micros = EXCLUDED.cost_micros,
                conversions = EXCLUDED.conversions,
                conversions_value = EXCLUDED.conversions_value,
                ctr = EXCLUDED.ctr,
                avg_cpc = EXCLUDED.avg_cpc,
                quality_score = EXCLUDED.quality_score,
                search_impr_share = EXCLUDED.search_impr_share,
                search_abs_top_impr_share = EXCLUDED.search_abs_top_impr_share,
                search_top_impr_share = EXCLUDED.search_top_impr_share,
                search_click_share = EXCLUDED.search_click_share,
                sync_run_id = EXCLUDED.sync_run_id
        """), {
            "client_id": client_id,
            "customer_id": customer_id,
            "sync_run_id": sync_run_id,
            "raw_data": "{}",
            **parsed,
        })

    db.commit()
    logger.info(f"Keywords: upserted {len(rows)} rows for customer {customer_id}")
    return len(rows)


async def _sync_search_terms(
    client: GoogleAdsClient, client_id: str, customer_id: str,
    date_from: str, date_to: str, sync_run_id: str, db: Session,
) -> int:
    """Fetch search terms and insert into gads_search_terms."""
    rows = await client.query_search_terms(customer_id, date_from, date_to)
    if not rows:
        return 0

    # Search terms don't have a natural unique key (same term can appear
    # across campaigns), so we delete + re-insert for the date range.
    db.execute(text("""
        DELETE FROM gads_search_terms
        WHERE client_id = :client_id AND customer_id = :customer_id
          AND date >= :date_from AND date <= :date_to
    """), {"client_id": client_id, "customer_id": customer_id,
           "date_from": date_from, "date_to": date_to})

    for row in rows:
        parsed = parse_search_term_row(row)
        db.execute(text("""
            INSERT INTO gads_search_terms (
                client_id, customer_id, campaign_id, campaign_name,
                search_term, keyword_text, keyword_match_type,
                search_term_match_type, date, impressions, clicks,
                cost_micros, conversions, conversions_value, ctr, avg_cpc,
                raw_data, sync_run_id
            ) VALUES (
                :client_id, :customer_id, :campaign_id, :campaign_name,
                :search_term, :keyword_text, :keyword_match_type,
                :search_term_match_type, :date, :impressions, :clicks,
                :cost_micros, :conversions, :conversions_value, :ctr, :avg_cpc,
                :raw_data, :sync_run_id
            )
        """), {
            "client_id": client_id,
            "customer_id": customer_id,
            "sync_run_id": sync_run_id,
            "raw_data": "{}",
            **parsed,
        })

    db.commit()
    logger.info(f"Search terms: inserted {len(rows)} rows for customer {customer_id}")
    return len(rows)


async def _sync_per_store(
    client: GoogleAdsClient, client_id: str, customer_id: str,
    date_from: str, date_to: str, sync_run_id: str, db: Session,
) -> int:
    """Fetch per-store performance and upsert into gads_store_performance."""
    rows = await client.query_per_store(customer_id, date_from, date_to)
    if not rows:
        return 0

    for row in rows:
        parsed = parse_per_store_row(row)
        if not parsed.get("place_id"):
            continue
        db.execute(text("""
            INSERT INTO gads_store_performance (
                client_id, customer_id, campaign_id, campaign_name, channel_type,
                place_id, business_name, address, city, postal_code, date,
                eligible_impressions, store_visits, click_to_call, directions,
                website_clicks, other_engagement, orders, menu_clicks,
                vtc_store_visits, vtc_click_to_call, vtc_directions, vtc_website,
                raw_data, sync_run_id
            ) VALUES (
                :client_id, :customer_id, :campaign_id, :campaign_name, :channel_type,
                :place_id, :business_name, :address, :city, :postal_code, :date,
                :eligible_impressions, :store_visits, :click_to_call, :directions,
                :website_clicks, :other_engagement, :orders, :menu_clicks,
                :vtc_store_visits, :vtc_click_to_call, :vtc_directions, :vtc_website,
                :raw_data, :sync_run_id
            )
            ON CONFLICT (client_id, customer_id, place_id, COALESCE(campaign_id, 0), date)
            DO UPDATE SET
                campaign_name = EXCLUDED.campaign_name,
                channel_type = EXCLUDED.channel_type,
                business_name = EXCLUDED.business_name,
                address = EXCLUDED.address,
                city = EXCLUDED.city,
                postal_code = EXCLUDED.postal_code,
                eligible_impressions = EXCLUDED.eligible_impressions,
                store_visits = EXCLUDED.store_visits,
                click_to_call = EXCLUDED.click_to_call,
                directions = EXCLUDED.directions,
                website_clicks = EXCLUDED.website_clicks,
                other_engagement = EXCLUDED.other_engagement,
                orders = EXCLUDED.orders,
                menu_clicks = EXCLUDED.menu_clicks,
                vtc_store_visits = EXCLUDED.vtc_store_visits,
                vtc_click_to_call = EXCLUDED.vtc_click_to_call,
                vtc_directions = EXCLUDED.vtc_directions,
                vtc_website = EXCLUDED.vtc_website,
                sync_run_id = EXCLUDED.sync_run_id
        """), {
            "client_id": client_id,
            "customer_id": customer_id,
            "sync_run_id": sync_run_id,
            "raw_data": "{}",
            **parsed,
        })

    db.commit()
    logger.info(f"Per-store: upserted {len(rows)} rows for customer {customer_id}")
    return len(rows)
