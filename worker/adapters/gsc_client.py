"""Async Google Search Console API client.

Mirrors the pattern of google_ads_client.py: explicit credentials,
retry with exponential backoff, pagination.

Usage:
    client = GscApiClient(access_token="ya29...")
    sites = await client.list_sites()
    rows = await client.query_analytics(
        "sc-domain:example.com", "2026-01-01", "2026-03-31",
        dimensions=["date", "query"],
    )
"""

import asyncio
import logging
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://www.googleapis.com/webmasters/v3"
DEFAULT_ROW_LIMIT = 8000


class GscApiClient:
    """Async Google Search Console API client with retry logic."""

    def __init__(self, access_token: str, max_retries: int = 6):
        self.access_token = access_token
        self.max_retries = max_retries

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    # ── Retry helpers ─────────────────────────────────────────────────

    async def _post_with_retry(
        self,
        client: httpx.AsyncClient,
        url: str,
        payload: dict,
    ) -> dict[str, Any] | None:
        """POST with exponential backoff (same pattern as GoogleAdsClient)."""
        base = 1.5
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = await client.post(
                    url, json=payload, headers=self._headers(), timeout=120.0,
                )
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code in (429, 500, 502, 503, 504):
                    retry_after = resp.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else min(30.0, base ** attempt)
                    logger.warning(
                        f"[RETRY {attempt}/{self.max_retries}] HTTP {resp.status_code} "
                        f"— waiting {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error(f"GSC HTTP {resp.status_code}: {resp.text[:500]}")
                return None
            except Exception as e:
                delay = min(30.0, base ** attempt)
                logger.warning(
                    f"[EXC RETRY {attempt}/{self.max_retries}] {e} — waiting {delay:.1f}s"
                )
                await asyncio.sleep(delay)

        logger.error("GSC query failed after all retries")
        return None

    async def _get_with_retry(
        self,
        client: httpx.AsyncClient,
        url: str,
    ) -> dict[str, Any] | None:
        """GET with exponential backoff."""
        base = 1.5
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = await client.get(
                    url, headers=self._headers(), timeout=30.0,
                )
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code in (429, 500, 502, 503, 504):
                    retry_after = resp.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else min(30.0, base ** attempt)
                    logger.warning(
                        f"[RETRY {attempt}/{self.max_retries}] HTTP {resp.status_code} "
                        f"— waiting {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error(f"GSC HTTP {resp.status_code}: {resp.text[:500]}")
                return None
            except Exception as e:
                delay = min(30.0, base ** attempt)
                logger.warning(
                    f"[EXC RETRY {attempt}/{self.max_retries}] {e} — waiting {delay:.1f}s"
                )
                await asyncio.sleep(delay)

        logger.error("GSC GET failed after all retries")
        return None

    # ── API methods ───────────────────────────────────────────────────

    async def list_sites(self) -> list[dict[str, str]]:
        """List all GSC sites accessible by the authenticated user.

        Returns:
            [{"siteUrl": "sc-domain:example.com", "permissionLevel": "siteOwner"}, ...]
        """
        url = f"{BASE_URL}/sites"
        async with httpx.AsyncClient() as client:
            data = await self._get_with_retry(client, url)
            if not data:
                return []
            return data.get("siteEntry", [])

    async def query_analytics(
        self,
        domain: str,
        start_date: str,
        end_date: str,
        dimensions: list[str] | None = None,
        country: str | None = None,
        row_limit: int = DEFAULT_ROW_LIMIT,
    ) -> list[dict[str, Any]]:
        """Query GSC Search Analytics API with pagination.

        Args:
            domain: Site URL (e.g. "sc-domain:example.com")
            start_date: "YYYY-MM-DD"
            end_date: "YYYY-MM-DD"
            dimensions: ["date", "query"] or ["date", "query", "page"]
            country: ISO 3166-1 alpha-3 (e.g. "FRA"), None = no filter
            row_limit: Rows per page (max 25000, default 8000)

        Returns:
            List of dicts with keys matching dimensions + clicks/impressions/ctr/position
        """
        if dimensions is None:
            dimensions = ["date", "query"]

        encoded_domain = quote(domain, safe="")
        url = f"{BASE_URL}/sites/{encoded_domain}/searchAnalytics/query"

        payload: dict[str, Any] = {
            "startDate": start_date,
            "endDate": end_date,
            "dimensions": dimensions,
            "rowLimit": row_limit,
            "startRow": 0,
        }

        if country:
            payload["dimensionFilterGroups"] = [{
                "filters": [{
                    "dimension": "country",
                    "operator": "equals",
                    "expression": country,
                }]
            }]

        all_rows: list[dict[str, Any]] = []

        async with httpx.AsyncClient() as client:
            while True:
                data = await self._post_with_retry(client, url, payload)
                if not data:
                    break

                rows = data.get("rows", [])
                for row in rows:
                    parsed = _parse_analytics_row(row, dimensions)
                    all_rows.append(parsed)

                # Pagination: if we got exactly row_limit rows, there may be more
                if len(rows) < row_limit:
                    break

                payload["startRow"] = payload["startRow"] + row_limit

        logger.info(
            f"GSC query_analytics({domain}, {start_date}→{end_date}, "
            f"dims={dimensions}): {len(all_rows)} rows"
        )
        return all_rows


def _parse_analytics_row(row: dict, dimensions: list[str]) -> dict[str, Any]:
    """Flatten a GSC Search Analytics API response row.

    API returns:
        {"keys": ["2026-01-15", "seo audit"], "clicks": 5, "impressions": 120, ...}

    We map keys[i] → dimensions[i] and add the metrics.
    """
    keys = row.get("keys", [])
    result: dict[str, Any] = {}

    for i, dim in enumerate(dimensions):
        if i < len(keys):
            result[dim] = keys[i]

    result["clicks"] = int(row.get("clicks", 0))
    result["impressions"] = int(row.get("impressions", 0))
    result["ctr"] = row.get("ctr")
    result["position"] = row.get("position")

    return result
