"""HaloScan API client — adapted from net-checking/core/services/haloscan_service.py."""

import logging
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)

RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 1.6
TIMEOUT = 60


async def _request(method: str, endpoint: str, payload: dict | None = None) -> dict:
    url = f"{settings.haloscan_base_url}{endpoint}"
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "haloscan-api-key": settings.haloscan_api_key,
    }

    for attempt in range(RETRY_ATTEMPTS):
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                if method == "GET":
                    resp = await client.get(url, headers=headers)
                else:
                    resp = await client.post(url, json=payload or {}, headers=headers)

                if resp.status_code == 429:
                    wait = RETRY_BACKOFF ** (attempt + 1)
                    logger.warning(f"HaloScan rate limited, retrying in {wait:.1f}s")
                    import asyncio
                    await asyncio.sleep(wait)
                    continue

                resp.raise_for_status()
                return resp.json()

        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500 and attempt < RETRY_ATTEMPTS - 1:
                import asyncio
                await asyncio.sleep(RETRY_BACKOFF ** (attempt + 1))
                continue
            raise

    raise RuntimeError(f"HaloScan API failed after {RETRY_ATTEMPTS} attempts")


async def fetch_domain_positions(domain: str, limit: int = 500) -> list[dict]:
    """Fetch keyword positions for a domain. Returns URL + keyword + position + traffic.

    Auto-detects mode:
    - 'prefix' if domain contains a path (e.g. ducray.com/fr-fr)
    - 'root' otherwise (e.g. eau-thermale-avene.fr, fr.svr.com)
    """
    # "root" works for domains, subdomains AND path prefixes (e.g. ducray.com/fr-fr)
    mode = "root"

    data = await _request("POST", "/api/domains/positions", {
        "input": domain,
        "mode": mode,
        "lineCount": limit,
        "order_by": "traffic",
        "order": "desc",
        "page": 1,
    })
    return data.get("data", data) if isinstance(data, dict) else data


async def fetch_domain_top_pages(domain: str, limit: int = 100) -> list[dict]:
    """Fetch top pages for a domain."""
    data = await _request("POST", "/api/domains/topPages", {
        "input": domain,
        "mode": "auto",
        "lineCount": limit,
        "page": 1,
    })
    return data.get("data", data) if isinstance(data, dict) else data


async def fetch_site_competitors(domain: str, limit: int = 20) -> list[dict]:
    """Fetch SEO competitors for a domain.

    HaloScan requires `mode: "root"` and a ROOT domain (no path prefix). Passing a
    path-scoped domain like "ducray.com/fr-fr" returns SITE_NOT_FOUND. Passing a root
    domain without mode hangs until timeout.
    """
    root_domain = domain.split("/")[0] if domain else ""
    data = await _request("POST", "/api/domains/siteCompetitors", {
        "input": root_domain,
        "mode": "root",
        "lineCount": limit,
    })
    return data.get("results", data.get("data", [])) if isinstance(data, dict) else data


async def fetch_credits() -> dict:
    """Check remaining API credits."""
    return await _request("GET", "/api/user/credit")
