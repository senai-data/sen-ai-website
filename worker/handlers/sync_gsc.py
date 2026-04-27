"""Handler: sync Google Search Console data for a client.

Reads OAuth tokens from oauth_connections, calls the GSC Search Analytics API,
and upserts results into gsc_queries and gsc_pages tables. Two passes:
  1. dimensions [date, query] → gsc_queries
  2. dimensions [date, query, page] → gsc_pages

job_payload:
    client_id: str (required)
    connection_id: str (required — oauth_connections row for search_console)
    domains: list[str] (required — e.g. ["sc-domain:example.com"])
    date_from: str YYYY-MM-DD (optional, default 90 days ago)
    date_to: str YYYY-MM-DD (optional, default 3 days ago — GSC data delay)
    country: str (optional, ISO 3166-1 alpha-3 e.g. "FRA", null = no filter)
"""

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from adapters.gsc_client import GscApiClient
from adapters.token_manager import get_valid_access_token

logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 90
GSC_DATA_DELAY_DAYS = 3


def execute(job_payload: dict, scan_id: str | None, db: Session) -> dict:
    """Main entry point — called by the worker poll loop."""
    client_id = job_payload.get("client_id")
    connection_id = job_payload.get("connection_id")
    domains = job_payload.get("domains", [])

    if not client_id or not connection_id:
        raise ValueError("Missing client_id or connection_id in job payload")
    if not domains:
        raise ValueError("No domains specified in job payload")

    country = job_payload.get("country")
    date_to = job_payload.get("date_to") or (
        datetime.utcnow() - timedelta(days=GSC_DATA_DELAY_DAYS)
    ).strftime("%Y-%m-%d")
    date_from = job_payload.get("date_from") or (
        datetime.utcnow() - timedelta(days=DEFAULT_LOOKBACK_DAYS + GSC_DATA_DELAY_DAYS)
    ).strftime("%Y-%m-%d")

    # Load connection and decrypt tokens
    from models import OAuthConnection, SyncRun

    conn = db.query(OAuthConnection).filter(OAuthConnection.id == connection_id).first()
    if not conn:
        raise ValueError(f"OAuthConnection {connection_id} not found")
    if conn.status != "active":
        raise ValueError(f"OAuthConnection {connection_id} is {conn.status}")

    access_token = get_valid_access_token(conn, db)

    # Create sync run
    sync_run = SyncRun(
        client_id=client_id,
        connection_id=connection_id,
        sync_type="search_console",
        status="running",
        date_from=datetime.strptime(date_from, "%Y-%m-%d"),
        date_to=datetime.strptime(date_to, "%Y-%m-%d"),
        started_at=datetime.utcnow(),
    )
    db.add(sync_run)
    db.flush()
    sync_run_id = str(sync_run.id)

    gsc_client = GscApiClient(access_token=access_token)

    total_stats = {"rows_fetched": 0, "domains_synced": 0, "errors": []}

    try:
        for domain in domains:
            logger.info(f"Syncing GSC domain {domain} ({date_from} → {date_to})")

            try:
                # Pass 1: date + query → gsc_queries
                n_queries = asyncio.run(
                    _sync_queries(gsc_client, client_id, domain, date_from, date_to,
                                  country, sync_run_id, db)
                )
                total_stats["rows_fetched"] += n_queries

                # Pass 2: date + query + page → gsc_pages
                n_pages = asyncio.run(
                    _sync_pages(gsc_client, client_id, domain, date_from, date_to,
                                country, sync_run_id, db)
                )
                total_stats["rows_fetched"] += n_pages

                total_stats["domains_synced"] += 1
            except Exception as e:
                logger.exception(f"Error syncing domain {domain}: {e}")
                total_stats["errors"].append({"domain": domain, "error": str(e)})

        # Update sync run
        sync_run.status = "completed"
        sync_run.stats = total_stats
        sync_run.completed_at = datetime.utcnow()
        db.commit()

        logger.info(
            f"GSC sync completed: {total_stats['domains_synced']} domains, "
            f"{total_stats['rows_fetched']} rows, {len(total_stats['errors'])} errors"
        )
        return total_stats

    except Exception as e:
        sync_run.status = "failed"
        sync_run.error_message = str(e)
        sync_run.completed_at = datetime.utcnow()
        db.commit()
        raise


async def _sync_queries(
    client: GscApiClient, client_id: str, domain: str,
    date_from: str, date_to: str, country: str | None,
    sync_run_id: str, db: Session,
) -> int:
    """Fetch query-level data and upsert into gsc_queries."""
    rows = await client.query_analytics(
        domain, date_from, date_to,
        dimensions=["date", "query"],
        country=country,
    )
    if not rows:
        return 0

    for row in rows:
        db.execute(text("""
            INSERT INTO gsc_queries (
                client_id, domain, date, query,
                clicks, impressions, ctr, position, sync_run_id
            ) VALUES (
                :client_id, :domain, :date, :query,
                :clicks, :impressions, :ctr, :position, :sync_run_id
            )
            ON CONFLICT (client_id, domain, date, query)
            DO UPDATE SET
                clicks = EXCLUDED.clicks,
                impressions = EXCLUDED.impressions,
                ctr = EXCLUDED.ctr,
                position = EXCLUDED.position,
                sync_run_id = EXCLUDED.sync_run_id
        """), {
            "client_id": client_id,
            "domain": domain,
            "date": row["date"],
            "query": row["query"],
            "clicks": row["clicks"],
            "impressions": row["impressions"],
            "ctr": row.get("ctr"),
            "position": row.get("position"),
            "sync_run_id": sync_run_id,
        })

    db.commit()
    logger.info(f"GSC queries: upserted {len(rows)} rows for {domain}")
    return len(rows)


async def _sync_pages(
    client: GscApiClient, client_id: str, domain: str,
    date_from: str, date_to: str, country: str | None,
    sync_run_id: str, db: Session,
) -> int:
    """Fetch page-level data and upsert into gsc_pages."""
    rows = await client.query_analytics(
        domain, date_from, date_to,
        dimensions=["date", "query", "page"],
        country=country,
    )
    if not rows:
        return 0

    for row in rows:
        page_url = row.get("page", "")
        page_hash = hashlib.md5(page_url.encode()).hexdigest()

        db.execute(text("""
            INSERT INTO gsc_pages (
                client_id, domain, date, query, page, page_hash,
                clicks, impressions, ctr, position, sync_run_id
            ) VALUES (
                :client_id, :domain, :date, :query, :page, :page_hash,
                :clicks, :impressions, :ctr, :position, :sync_run_id
            )
            ON CONFLICT (client_id, domain, date, query, page_hash)
            DO UPDATE SET
                page = EXCLUDED.page,
                clicks = EXCLUDED.clicks,
                impressions = EXCLUDED.impressions,
                ctr = EXCLUDED.ctr,
                position = EXCLUDED.position,
                sync_run_id = EXCLUDED.sync_run_id
        """), {
            "client_id": client_id,
            "domain": domain,
            "date": row["date"],
            "query": row["query"],
            "page": page_url,
            "page_hash": page_hash,
            "clicks": row["clicks"],
            "impressions": row["impressions"],
            "ctr": row.get("ctr"),
            "position": row.get("position"),
            "sync_run_id": sync_run_id,
        })

    db.commit()
    logger.info(f"GSC pages: upserted {len(rows)} rows for {domain}")
    return len(rows)
