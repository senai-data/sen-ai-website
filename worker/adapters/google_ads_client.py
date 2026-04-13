"""Generic multi-tenant Google Ads API client.

Ported from the Pierre Fabre CLI (gads_service.py) but fully parameterized:
no singletons, no global state. Each call takes explicit credentials.

Usage:
    client = GoogleAdsClient(
        access_token="ya29...",
        developer_token="xxx",
        login_customer_id="8562972049",
    )
    rows = await client.query_campaigns("4004422204", "2026-01-01", "2026-03-31")
"""

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Google Ads API version — overridable via worker config
DEFAULT_API_VERSION = "v19"


class GoogleAdsClient:
    """Async Google Ads API client with retry logic."""

    def __init__(
        self,
        access_token: str,
        developer_token: str,
        login_customer_id: str,
        api_version: str = DEFAULT_API_VERSION,
        max_retries: int = 6,
    ):
        self.access_token = access_token
        self.developer_token = developer_token
        self.login_customer_id = login_customer_id.replace("-", "")
        self.api_version = api_version
        self.max_retries = max_retries

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "developer-token": self.developer_token,
            "login-customer-id": self.login_customer_id,
            "Content-Type": "application/json",
        }

    def _search_url(self, customer_id: str) -> str:
        cid = customer_id.replace("-", "")
        return (
            f"https://googleads.googleapis.com/{self.api_version}"
            f"/customers/{cid}/googleAds:search"
        )

    async def _query(
        self,
        customer_id: str,
        gaql: str,
        client: httpx.AsyncClient,
    ) -> list[dict[str, Any]]:
        """Execute a GAQL query with pagination and retry."""
        url = self._search_url(customer_id)
        all_results = []
        page_token = None

        while True:
            payload: dict[str, Any] = {"query": gaql}
            if page_token:
                payload["pageToken"] = page_token

            data = await self._post_with_retry(client, url, payload)
            if not data:
                break

            results = data.get("results", [])
            all_results.extend(results)

            page_token = data.get("nextPageToken")
            if not page_token:
                break

        return all_results

    async def _post_with_retry(
        self,
        client: httpx.AsyncClient,
        url: str,
        payload: dict,
    ) -> dict[str, Any] | None:
        """POST with exponential backoff (mirrors CLI's _post_with_retry)."""
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
                logger.error(f"Google Ads HTTP {resp.status_code}: {resp.text[:500]}")
                return None
            except Exception as e:
                delay = min(30.0, base ** attempt)
                logger.warning(
                    f"[EXC RETRY {attempt}/{self.max_retries}] {e} — waiting {delay:.1f}s"
                )
                await asyncio.sleep(delay)

        logger.error("Google Ads query failed after all retries")
        return None

    # ── Query methods ─────────────────────────────────────────────────

    async def list_accessible_customers(self) -> list[dict[str, str]]:
        """List all Google Ads accounts accessible by the authenticated user."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://googleads.googleapis.com/{self.api_version}/customers:listAccessibleCustomers",
                headers=self._headers(),
                timeout=30.0,
            )
            if resp.status_code != 200:
                logger.error(f"listAccessibleCustomers failed: {resp.status_code} {resp.text[:300]}")
                return []
            data = resp.json()
            return [
                {"customer_id": name.split("/")[-1]}
                for name in data.get("resourceNames", [])
            ]

    async def query_campaigns(
        self, customer_id: str, start_date: str, end_date: str,
    ) -> list[dict[str, Any]]:
        """Campaign performance — daily."""
        gaql = f"""
            SELECT campaign.id, campaign.name, campaign_budget.amount_micros,
                   campaign.status, campaign.optimization_score,
                   campaign.advertising_channel_type,
                   metrics.clicks, metrics.impressions, metrics.ctr,
                   metrics.average_cpc, metrics.cost_micros, metrics.conversions,
                   metrics.conversions_value,
                   campaign.bidding_strategy_type, segments.date,
                   metrics.absolute_top_impression_percentage,
                   metrics.all_conversions, metrics.all_conversions_value,
                   metrics.average_cpm, metrics.top_impression_percentage
            FROM campaign
            WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'
        """
        async with httpx.AsyncClient() as client:
            return await self._query(customer_id, gaql, client)

    async def query_keywords(
        self, customer_id: str, start_date: str, end_date: str,
    ) -> list[dict[str, Any]]:
        """Keyword performance — daily, enabled keywords only."""
        gaql = f"""
            SELECT ad_group_criterion.keyword.text,
                   ad_group_criterion.keyword.match_type,
                   ad_group_criterion.status, ad_group_criterion.criterion_id,
                   ad_group.id, ad_group.name, campaign.id, campaign.name,
                   ad_group_criterion.quality_info.quality_score,
                   metrics.clicks, metrics.impressions, metrics.ctr,
                   metrics.average_cpc, metrics.cost_micros,
                   metrics.conversions, metrics.conversions_value,
                   metrics.search_impression_share,
                   metrics.search_absolute_top_impression_share,
                   metrics.search_top_impression_share,
                   metrics.search_click_share,
                   segments.date
            FROM keyword_view
            WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'
              AND ad_group_criterion.status = 'ENABLED'
        """
        async with httpx.AsyncClient() as client:
            return await self._query(customer_id, gaql, client)

    async def query_search_terms(
        self, customer_id: str, start_date: str, end_date: str,
    ) -> list[dict[str, Any]]:
        """Search term performance — daily."""
        gaql = f"""
            SELECT search_term_view.search_term, search_term_view.status,
                   segments.keyword.info.text, segments.keyword.info.match_type,
                   segments.search_term_match_type, segments.date,
                   campaign.id, campaign.name,
                   metrics.impressions, metrics.clicks, metrics.cost_micros,
                   metrics.conversions, metrics.conversions_value,
                   metrics.ctr, metrics.average_cpc
            FROM search_term_view
            WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'
        """
        async with httpx.AsyncClient() as client:
            return await self._query(customer_id, gaql, client)

    async def query_per_store(
        self, customer_id: str, start_date: str, end_date: str,
    ) -> list[dict[str, Any]]:
        """Per-store performance — daily (store visits, calls, directions, etc.)."""
        gaql = f"""
            SELECT
              per_store_view.place_id,
              per_store_view.business_name,
              per_store_view.address1,
              per_store_view.city,
              per_store_view.province,
              per_store_view.postal_code,
              per_store_view.country_code,
              campaign.id, campaign.name, campaign.advertising_channel_type,
              segments.date,
              metrics.eligible_impressions_from_location_asset_store_reach,
              metrics.all_conversions_from_location_asset_store_visits,
              metrics.all_conversions_from_location_asset_click_to_call,
              metrics.all_conversions_from_location_asset_directions,
              metrics.all_conversions_from_location_asset_website,
              metrics.all_conversions_from_location_asset_other_engagement,
              metrics.all_conversions_from_location_asset_menu,
              metrics.all_conversions_from_location_asset_order,
              metrics.view_through_conversions_from_location_asset_store_visits,
              metrics.view_through_conversions_from_location_asset_click_to_call,
              metrics.view_through_conversions_from_location_asset_directions,
              metrics.view_through_conversions_from_location_asset_website
            FROM per_store_view
            WHERE segments.date BETWEEN '{start_date}' AND '{end_date}'
        """
        async with httpx.AsyncClient() as client:
            return await self._query(customer_id, gaql, client)


# ── Response parsers ───────────────────────────────────────────────────
# Google Ads API returns deeply nested dicts. These helpers flatten them
# into simple dicts ready for DB insertion.

def parse_campaign_row(row: dict) -> dict:
    """Flatten a campaign query result row."""
    c = row.get("campaign", {})
    m = row.get("metrics", {})
    s = row.get("segments", {})
    b = row.get("campaignBudget", {})
    return {
        "campaign_id": int(c.get("id", 0)),
        "campaign_name": c.get("name"),
        "channel_type": c.get("advertisingChannelType"),
        "status": c.get("status"),
        "optimization_score": c.get("optimizationScore"),
        "bidding_strategy": c.get("biddingStrategyType"),
        "budget_micros": int(b.get("amountMicros", 0)) if b.get("amountMicros") else None,
        "date": s.get("date"),
        "impressions": int(m.get("impressions", 0)),
        "clicks": int(m.get("clicks", 0)),
        "cost_micros": int(m.get("costMicros", 0)),
        "conversions": float(m.get("conversions", 0)),
        "conversions_value": float(m.get("conversionsValue", 0)),
        "all_conversions": float(m.get("allConversions", 0)),
        "all_conversions_value": float(m.get("allConversionsValue", 0)),
        "ctr": float(m.get("ctr", 0)),
        "avg_cpc": float(m.get("averageCpc", 0)),
        "avg_cpm": float(m.get("averageCpm", 0)),
        "abs_top_impr_pct": float(m.get("absoluteTopImpressionPercentage", 0)),
        "top_impr_pct": float(m.get("topImpressionPercentage", 0)),
    }


def parse_keyword_row(row: dict) -> dict:
    """Flatten a keyword_view query result row."""
    kw = row.get("adGroupCriterion", {})
    kw_info = kw.get("keyword", {})
    qi = kw.get("qualityInfo", {})
    ag = row.get("adGroup", {})
    c = row.get("campaign", {})
    m = row.get("metrics", {})
    s = row.get("segments", {})
    return {
        "keyword_text": kw_info.get("text"),
        "match_type": kw_info.get("matchType"),
        "criterion_id": int(kw.get("criterionId", 0)) if kw.get("criterionId") else None,
        "ad_group_id": int(ag.get("id", 0)) if ag.get("id") else None,
        "ad_group_name": ag.get("name"),
        "campaign_id": int(c.get("id", 0)),
        "campaign_name": c.get("name"),
        "quality_score": qi.get("qualityScore"),
        "date": s.get("date"),
        "impressions": int(m.get("impressions", 0)),
        "clicks": int(m.get("clicks", 0)),
        "cost_micros": int(m.get("costMicros", 0)),
        "conversions": float(m.get("conversions", 0)),
        "conversions_value": float(m.get("conversionsValue", 0)),
        "ctr": float(m.get("ctr", 0)),
        "avg_cpc": float(m.get("averageCpc", 0)),
        "search_impr_share": _to_float(m.get("searchImpressionShare")),
        "search_abs_top_impr_share": _to_float(m.get("searchAbsoluteTopImpressionShare")),
        "search_top_impr_share": _to_float(m.get("searchTopImpressionShare")),
        "search_click_share": _to_float(m.get("searchClickShare")),
    }


def parse_search_term_row(row: dict) -> dict:
    """Flatten a search_term_view query result row."""
    stv = row.get("searchTermView", {})
    c = row.get("campaign", {})
    m = row.get("metrics", {})
    s = row.get("segments", {})
    kw = s.get("keyword", {}).get("info", {})
    return {
        "search_term": stv.get("searchTerm"),
        "campaign_id": int(c.get("id", 0)) if c.get("id") else None,
        "campaign_name": c.get("name"),
        "keyword_text": kw.get("text"),
        "keyword_match_type": kw.get("matchType"),
        "search_term_match_type": s.get("searchTermMatchType"),
        "date": s.get("date"),
        "impressions": int(m.get("impressions", 0)),
        "clicks": int(m.get("clicks", 0)),
        "cost_micros": int(m.get("costMicros", 0)),
        "conversions": float(m.get("conversions", 0)),
        "conversions_value": float(m.get("conversionsValue", 0)),
        "ctr": float(m.get("ctr", 0)),
        "avg_cpc": float(m.get("averageCpc", 0)),
    }


def parse_per_store_row(row: dict) -> dict:
    """Flatten a per_store_view query result row."""
    psv = row.get("perStoreView", {})
    c = row.get("campaign", {})
    m = row.get("metrics", {})
    s = row.get("segments", {})
    return {
        "place_id": psv.get("placeId"),
        "business_name": psv.get("businessName"),
        "address": psv.get("address1"),
        "city": psv.get("city"),
        "postal_code": psv.get("postalCode"),
        "campaign_id": int(c.get("id", 0)) if c.get("id") else None,
        "campaign_name": c.get("name"),
        "channel_type": c.get("advertisingChannelType"),
        "date": s.get("date"),
        "eligible_impressions": _to_float(m.get("eligibleImpressionsFromLocationAssetStoreReach")),
        "store_visits": _to_float(m.get("allConversionsFromLocationAssetStoreVisits")),
        "click_to_call": _to_float(m.get("allConversionsFromLocationAssetClickToCall")),
        "directions": _to_float(m.get("allConversionsFromLocationAssetDirections")),
        "website_clicks": _to_float(m.get("allConversionsFromLocationAssetWebsite")),
        "other_engagement": _to_float(m.get("allConversionsFromLocationAssetOtherEngagement")),
        "menu_clicks": _to_float(m.get("allConversionsFromLocationAssetMenu")),
        "orders": _to_float(m.get("allConversionsFromLocationAssetOrder")),
        "vtc_store_visits": _to_float(m.get("viewThroughConversionsFromLocationAssetStoreVisits")),
        "vtc_click_to_call": _to_float(m.get("viewThroughConversionsFromLocationAssetClickToCall")),
        "vtc_directions": _to_float(m.get("viewThroughConversionsFromLocationAssetDirections")),
        "vtc_website": _to_float(m.get("viewThroughConversionsFromLocationAssetWebsite")),
    }


def _to_float(v) -> float | None:
    """Safe float conversion — Google Ads returns some metrics as strings."""
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
