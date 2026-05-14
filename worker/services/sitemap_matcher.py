"""Sitemap-matcher service for Phase D.

Day 3 surface : `compute_inlinks_from_map` — given an in-memory
{page_url -> list[outgoing_urls]} collected by fetch_brand_pages, compute
the inbound link count per page and bulk-update internal_inlink_count.

Day 4 surface : `find_best_pages(question_text, client_brand_id, db, ...)`
— the semantic matcher used by materialize_content_items to auto-suggest
target_url. Combines :
  - **cosine similarity** between the query embedding and each page's
    stored embedding (numpy, in-memory, ~5ms on 5k vectors)
  - **authority boost** : 0.15 × log10(1 + internal_inlink_count). A page
    with 100 internal inlinks gets +0.30 multiplier on its cosine; a
    leaf page (0 inlinks) gets +0. This is the architectural-intent
    signal — hub pages are the brand's own canonical answers.
  - **gamme path bias** : +0.05 when the LEAD brand is a gamme (its
    `parent_id` is set) and the URL contains the gamme's slug. Caps at
    +0.10 if a URL matches multiple ways. Small enough to not override
    a clearly-better cosine match.

  final_score = cosine_raw × (1 + authority_boost) + gamme_boost

The matcher returns the top-K matches with a `gap` field (score[0] -
score[1]) so the caller can decide whether the top-1 is meaningfully
better than the runner-up. `SITEMAP_THRESHOLD` (default 0.55, env-
overridable) is the floor below which materialize falls back to the
legacy FAQPageMatcher web-search path.

URL normalization rules used by the inlink matcher :
  - lowercase the host
  - strip the leading 'www.' so 'www.brand.fr' and 'brand.fr' map equal
  - strip the URL fragment ('#anchor')
  - keep query string (a page with ?utm=... is functionally the same
    target as without, but matcher only ever sees clean URLs from the
    sitemap or from <link rel=canonical>; cleaning the QS would
    over-match in the rare case a sitemap entry includes one)
  - normalize an empty path to '/'

Self-links (a page linking to itself) are NOT counted — they reflect
nav/footer convenience, not architectural intent.
"""

from __future__ import annotations

import logging
import math
import os
import re
from collections import defaultdict
from urllib.parse import urlparse, urlunparse

import numpy as np
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Score floor below which materialize falls back to FAQPageMatcher.
# 0.55 is the plan default — to be tuned on real PF data in Day 4 smoke.
SITEMAP_THRESHOLD = float(os.environ.get("PHASE_D_SITEMAP_THRESHOLD", "0.55"))

# Scoring weights — exposed as module constants so we can A/B them later.
_AUTHORITY_WEIGHT = 0.15            # 0.15 × log10(1+inlinks) — page-authority boost
_GAMME_BOOST_PER_HIT = 0.05         # +0.05 per gamme-slug hit in URL
_GAMME_BOOST_CAP = 0.10             # never give more than +0.10 from gamme


def slugify_brand_name(name: str) -> str:
    """Normalize a brand name to a URL-slug candidate.

    'XERACALM AD' -> 'xeracalm-ad'
    'A-Derma'     -> 'a-derma'
    'Eau Thermale Avène' -> 'eau-thermale-avene'
    """
    if not name:
        return ""
    # Unicode-strip accents (NFKD decomposition + drop combining marks)
    import unicodedata
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    s = ascii_only.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def _normalize_url(url: str) -> str:
    """Canonical form used as a dict key in the inlink count map."""
    if not url:
        return ""
    try:
        p = urlparse(url.strip())
    except ValueError:
        return ""
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return ""
    path = p.path or "/"
    if path == "":
        path = "/"
    return urlunparse((p.scheme.lower() or "https", host, path, p.params, p.query, ""))


def compute_inlinks_from_map(
    client_brand_id: str,
    links_map: dict[str, list[str]],
    db: Session,
) -> dict:
    """Reset every row in this brand's index to 0, then bulk-set inlink
    counts from the in-memory map.

    Reset-then-set guarantees idempotency : if a page that used to be a hub
    has lost all its inbound links since the last crawl, its count drops
    back to 0. A pure increment loop would never decrease a stale count.

    `links_map` shape : `{source_page_url: [outgoing_url1, outgoing_url2, ...]}`.
    Outgoing URLs that don't match any of this brand's indexed pages are
    silently dropped (they're external links). Self-links are dropped too.

    Returns :
      {
        "pages_with_links": int,        # source pages that had ≥1 outgoing internal link
        "inlinks_total": int,           # sum of all increments
        "targets_with_inlinks": int,    # distinct target URLs that got ≥1 inlink
        "max_inlinks_on_one_page": int,
      }
    """
    from models import ClientBrandPage

    # Build the set of "our pages" — used as a filter on outgoing edges so
    # external links don't bloat the count.
    rows = (
        db.query(ClientBrandPage.id, ClientBrandPage.url)
        .filter(ClientBrandPage.client_brand_id == client_brand_id)
        .all()
    )
    if not rows:
        return {
            "pages_with_links": 0, "inlinks_total": 0,
            "targets_with_inlinks": 0, "max_inlinks_on_one_page": 0,
        }

    # Map normalized URL → row id, so we resolve outgoing edges back to
    # the actual row to update.
    norm_to_id: dict[str, str] = {}
    for row_id, raw_url in rows:
        nu = _normalize_url(raw_url)
        if nu:
            norm_to_id[nu] = str(row_id)

    inlink_counts: dict[str, int] = defaultdict(int)
    pages_with_links = 0
    inlinks_total = 0

    for source_url, outgoing in links_map.items():
        if not outgoing:
            continue
        source_norm = _normalize_url(source_url)
        had_any = False
        for target_url in outgoing:
            target_norm = _normalize_url(target_url)
            if not target_norm or target_norm == source_norm:
                continue
            target_id = norm_to_id.get(target_norm)
            if not target_id:
                continue
            inlink_counts[target_id] += 1
            inlinks_total += 1
            had_any = True
        if had_any:
            pages_with_links += 1

    # Reset all rows in this brand to 0, then bulk-update only the ones
    # that have inlinks. Two passes keep the SQL simple and idempotent.
    db.query(ClientBrandPage).filter(
        ClientBrandPage.client_brand_id == client_brand_id,
    ).update({ClientBrandPage.internal_inlink_count: 0})

    # Group updates by count value to keep query count bounded — at most
    # one query per distinct count.
    by_count: dict[int, list[str]] = defaultdict(list)
    for row_id, n in inlink_counts.items():
        by_count[n].append(row_id)
    for n, ids in by_count.items():
        db.query(ClientBrandPage).filter(
            ClientBrandPage.id.in_(ids),
        ).update({ClientBrandPage.internal_inlink_count: n}, synchronize_session=False)

    db.commit()

    max_inlinks = max(inlink_counts.values(), default=0)
    logger.info(
        f"compute_inlinks brand={client_brand_id}: pages_with_links={pages_with_links} "
        f"inlinks_total={inlinks_total} targets={len(inlink_counts)} max={max_inlinks}"
    )

    return {
        "pages_with_links": pages_with_links,
        "inlinks_total": inlinks_total,
        "targets_with_inlinks": len(inlink_counts),
        "max_inlinks_on_one_page": max_inlinks,
    }


# ─────────────────────────────────────────────────────────────────────────
# Day 4 : semantic matcher
# ─────────────────────────────────────────────────────────────────────────


def _l2_normalize_matrix(mat: np.ndarray) -> np.ndarray:
    """Row-wise L2 normalize. Rows with zero norm are left unchanged (zero
    cosine after the matmul, no divide-by-zero crash)."""
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


def find_best_pages(
    question_text: str,
    client_brand_id: str,
    db: Session,
    *,
    openai_api_key: str,
    top_k: int = 3,
    exclude_urls: list[str] | None = None,
    gamme_slug: str | None = None,
) -> list[dict]:
    """Return the top-K best-matching pages for a query, scored against the
    brand's embedded sitemap corpus.

    Each match is a dict :
        {
            "url": str,
            "title": str | None,
            "score": float,             # final score (cosine × authority × gamme)
            "cosine_raw": float,        # unscaled cosine in [-1, 1] (typ. [0, 1])
            "authority_boost": float,   # the multiplicative add-on (0..~0.4)
            "gamme_boost": float,       # the additive bonus (0, 0.05, or 0.10)
            "gap": float,               # score[i] - score[i+1]; 0 on the last row
            "inlink_count": int,        # raw count for debugging/UI
            "last_embedded_at": str | None,
            "source": "sitemap_index",
        }

    Returns [] when :
      - The brand has no embedded pages (Day 3 hasn't run)
      - The query can't be embedded (no API key, OpenAI error)
      - All pages were filtered by exclude_urls

    Cost : 1 OpenAI embeddings call per invocation (~$0.0000005). System-
    triggered, no credit-debit needed. The daily cap in
    services.embeddings.DAILY_COST_CAP_USD still applies — if the brand's
    client is already over the cap for the day, the embed call fails and
    we return [] (caller falls back to FAQPageMatcher).
    """
    from models import ClientBrandPage
    from services.embeddings import EMBEDDING_MODEL, embed_batch

    rows = (
        db.query(ClientBrandPage)
        .filter(
            ClientBrandPage.client_brand_id == client_brand_id,
            ClientBrandPage.status == "embedded",
            ClientBrandPage.embedding.isnot(None),
        )
        .all()
    )
    if not rows:
        logger.info(
            f"find_best_pages: no embedded pages for brand {client_brand_id} "
            f"— matcher returns empty"
        )
        return []

    if exclude_urls:
        excl = {u.strip() for u in exclude_urls if u}
        rows = [r for r in rows if r.url not in excl]
        if not rows:
            logger.info(
                f"find_best_pages: all candidates excluded by exclude_urls "
                f"for brand {client_brand_id} (had {len(excl)} exclusions)"
            )
            return []

    if not openai_api_key:
        logger.warning("find_best_pages: OPENAI_API_KEY missing — returning []")
        return []

    try:
        embed_result = embed_batch([question_text], openai_api_key=openai_api_key)
    except Exception as exc:
        logger.exception(
            f"find_best_pages: query embedding failed ({type(exc).__name__}: "
            f"{exc}) — returning [] so caller falls back"
        )
        return []
    query_vec = np.array(embed_result["embeddings"][0], dtype=np.float32)
    query_norm = float(np.linalg.norm(query_vec))
    if query_norm == 0.0:
        logger.warning("find_best_pages: query embedding has zero norm — returning []")
        return []
    query_vec /= query_norm

    # Build the page matrix. JSONB → list[float] → np.array.
    # Rows whose embedding model differs from the current one go through
    # the same cosine — model drift is the caller's concern (a future re-
    # embed will refresh stale vectors).
    try:
        page_mat = np.array([r.embedding for r in rows], dtype=np.float32)
    except (TypeError, ValueError) as exc:
        logger.exception(
            f"find_best_pages: page_mat assembly failed — embedding shape "
            f"mismatch? ({exc})"
        )
        return []
    if page_mat.ndim != 2 or page_mat.shape[1] == 0:
        logger.warning(
            f"find_best_pages: bad page_mat shape {page_mat.shape} for "
            f"brand {client_brand_id}"
        )
        return []

    page_mat = _l2_normalize_matrix(page_mat)
    cosine_raw = page_mat @ query_vec               # shape: (N,)

    inlinks = np.array(
        [int(r.internal_inlink_count or 0) for r in rows], dtype=np.float32,
    )
    authority_boost = _AUTHORITY_WEIGHT * np.log10(1.0 + inlinks)

    # Gamme boost : path-substring match on the slug. Cheap O(N) string scan.
    if gamme_slug:
        gs = gamme_slug.lower()
        gamme_boost = np.array([
            min(_GAMME_BOOST_CAP, _GAMME_BOOST_PER_HIT) if gs and gs in (r.url or "").lower() else 0.0
            for r in rows
        ], dtype=np.float32)
    else:
        gamme_boost = np.zeros(len(rows), dtype=np.float32)

    scores = cosine_raw * (1.0 + authority_boost) + gamme_boost

    # Top-K + 1 (we need the runner-up to compute gap on top_k-th row)
    k = max(1, int(top_k))
    order = np.argsort(-scores)
    top_idx = order[: k + 1].tolist()
    if not top_idx:
        return []

    matches: list[dict] = []
    for rank, idx in enumerate(top_idx[:k]):
        r = rows[idx]
        # Gap = this score - next score. The last returned match's gap is
        # vs the (k+1)-th overall (or 0 if N <= k).
        if rank + 1 < len(top_idx):
            gap = float(scores[idx] - scores[top_idx[rank + 1]])
        else:
            gap = 0.0
        matches.append({
            "url": r.url,
            "title": r.title,
            "score": float(scores[idx]),
            "cosine_raw": float(cosine_raw[idx]),
            "authority_boost": float(authority_boost[idx]),
            "gamme_boost": float(gamme_boost[idx]),
            "gap": gap,
            "inlink_count": int(inlinks[idx]),
            "last_embedded_at": r.last_embedded_at.isoformat() if r.last_embedded_at else None,
            "source": "sitemap_index",
        })

    logger.info(
        f"find_best_pages brand={client_brand_id} corpus={len(rows)} "
        f"top1_score={matches[0]['score']:.3f} gap={matches[0]['gap']:.3f} "
        f"top1_url={matches[0]['url'][:80]}"
    )
    return matches
