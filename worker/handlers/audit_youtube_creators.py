"""Handler : Sprint 10 YouTube creator mapping.

For each scan we mine `scan_llm_results.citations[]` to find every
YouTube URL the LLMs cite, enrich each unique video via YouTube's free
oEmbed endpoint to recover its channel, then aggregate per-channel so
the user gets a shortlist of creators to engage with.

Sources :
  - scan_llm_results.citations[]        : YouTube URLs LLMs returned
  - scan_llm_results.brand_mentions[]   : who was named in each response
  - scan_brand_classifications          : my_brand / competitor on this scan
  - YouTube oEmbed (no API key, public) : title + channel name + author_url

Classification per channel (mirror of Sprint 9.1) :
  competitor_only : ≥1 classified competitor cited, target not cited
  shared          : both cited
  target_only     : only target cited

Cost : zero LLM. ~0.2-0.4s per oEmbed call × ≤200 videos = <90s per scan
worst case. Polite throttle of 0.4s between calls.
"""
from __future__ import annotations

import logging
import math
import time

from sqlalchemy import text as _text
from sqlalchemy.orm import Session

from adapters.youtube_oembed import (
    canonical_video_url, channel_handle_from_url, extract_video_id,
    fetch_oembed, is_youtube_url,
)

logger = logging.getLogger(__name__)

MAX_VIDEOS_PER_RUN = 200
MAX_VIDEOS_PER_CHANNEL = 8
OEMBED_DELAY_SECONDS = 0.4


def _cited_youtube_videos(db: Session, scan_id: str, limit: int) -> list[dict]:
    """Mine every YouTube URL cited by an LLM during this scan.

    Returns one entry per CANONICAL video URL (tracking params stripped)
    with :
        url             : canonical https://www.youtube.com/watch?v=ID
        video_id        : extracted 11-char ID or None
        raw_urls        : list of original URL variants we collapsed
        citation_count  : how many LLM responses cited the video
        contextes       : citation contexts (up to 5)
        slr_ids         : the LLM response IDs (for brand_mention lookup)
        winning_questions : [{question_id, question, provider, slr_id}, ...]
    """
    sql = _text(
        """
        SELECT slr.id::text       AS slr_id,
               slr.question_id::text AS question_id,
               sq.question           AS question,
               slr.provider          AS provider,
               citation->>'url'       AS raw_url,
               lower(citation->>'domaine') AS domain,
               citation->>'contexte'  AS contexte,
               slr.brand_mentions     AS brand_mentions
          FROM scan_llm_results slr
          JOIN LATERAL jsonb_array_elements(slr.citations) AS citation ON true
          LEFT JOIN scan_questions sq ON sq.id = slr.question_id
         WHERE slr.scan_id = :scan_id
           AND citation->>'url' IS NOT NULL
           AND (lower(citation->>'domaine') LIKE '%youtube.com'
                OR lower(citation->>'domaine') = 'youtu.be'
                OR citation->>'url' ILIKE '%youtube.com%'
                OR citation->>'url' ILIKE '%youtu.be%')
        """
    )

    bucket: dict[str, dict] = {}
    for r in db.execute(sql, {"scan_id": scan_id}).fetchall():
        if not r.raw_url or not is_youtube_url(r.raw_url):
            continue
        canon = canonical_video_url(r.raw_url)
        if not canon:
            continue
        b = bucket.get(canon)
        if b is None:
            b = {
                "url": canon,
                "video_id": extract_video_id(canon),
                "raw_urls": [],
                "citation_count": 0,
                "contextes": [],
                "_seen_slr": set(),
                "_slr_brand_mentions": [],  # [(slr_id, brand_mentions), ...]
                "winning_questions": [],
                "_seen_q_keys": set(),
                "_seen_count_keys": set(),
            }
            bucket[canon] = b
        # N-runs (T1) : count once per (question, provider) - the same video
        # cited across N runs of one question is one signal, not N.
        _ckey = (r.question_id, r.provider)
        if _ckey not in b["_seen_count_keys"]:
            b["_seen_count_keys"].add(_ckey)
            b["citation_count"] += 1
        if r.raw_url not in b["raw_urls"]:
            b["raw_urls"].append(r.raw_url)
        contexte = (r.contexte or "").strip()
        if contexte and len(b["contextes"]) < 5 and contexte not in b["contextes"]:
            b["contextes"].append(contexte[:300])
        if r.slr_id and r.slr_id not in b["_seen_slr"]:
            b["_seen_slr"].add(r.slr_id)
            b["_slr_brand_mentions"].append((r.slr_id, r.brand_mentions or []))
        if r.question:
            qkey = (r.question_id, r.provider)
            if qkey not in b["_seen_q_keys"]:
                b["_seen_q_keys"].add(qkey)
                if len(b["winning_questions"]) < 10:
                    b["winning_questions"].append({
                        "question_id": r.question_id,
                        "question": r.question,
                        "provider": r.provider,
                        "slr_id": r.slr_id,
                    })

    for b in bucket.values():
        b.pop("_seen_slr", None)
        b.pop("_seen_q_keys", None)
        b.pop("_seen_count_keys", None)

    out = sorted(bucket.values(), key=lambda x: -x["citation_count"])
    return out[:limit]


def _scan_brand_context(
    db: Session, scan_id: str
) -> tuple[set[str], set[str], dict[str, str]]:
    """Return (target_names_lower, competitor_names_lower, name_map).

    Mirror of build_pr_outreach._scan_brand_domains but slimmer - we
    don't filter domains here (YouTube is the host scope), and we hard
    cap brand_mentions to classified competitors only (Sprint 9.1 fix
    pattern : brand_mentions carries ingredients, drugs, publications
    and self-mentions that aren't actual competitors).
    """
    rows = db.execute(_text(
        """
        SELECT cb.name, cb.canonical_name, cb.aliases, sbc.classification
          FROM scan_brand_classifications sbc
          JOIN client_brands cb ON cb.id = sbc.brand_id
         WHERE sbc.scan_id = :scan_id
           AND sbc.classification IN ('my_brand', 'competitor')
        """
    ), {"scan_id": scan_id}).fetchall()

    target: set[str] = set()
    competitor: set[str] = set()
    name_map: dict[str, str] = {}
    for r in rows:
        canonical_label = (r.canonical_name or r.name or "").strip()
        names = [r.name, r.canonical_name, *(r.aliases or [])]
        for n in names:
            if not n:
                continue
            lower = n.strip().lower()
            name_map[lower] = canonical_label or n.strip()
            if r.classification == "my_brand":
                target.add(lower)
            else:
                competitor.add(lower)
    return target, competitor, name_map


def _classify_brand_mentions(
    bm_list: list[dict],
    target_lower: set[str],
    competitor_lower: set[str],
    name_map: dict[str, str],
) -> tuple[bool, set[str]]:
    """Return (target_cited, competitor_canonical_names_set) for one
    LLM response's brand_mentions array."""
    target_cited = False
    comps: set[str] = set()
    for bm in bm_list or []:
        name = (bm.get("brand_name") or "").strip()
        if not name:
            continue
        nlower = name.lower()
        is_target_flag = bool(bm.get("est_marque_cible"))
        if is_target_flag or nlower in target_lower:
            target_cited = True
            continue
        if nlower not in competitor_lower:
            continue  # Sprint 9.1 noise gate
        comps.add(name_map.get(nlower, name))
    return target_cited, comps


def _leverage_score(
    citation_count: int,
    competitor_count: int,
    target_cited: bool,
    video_count: int,
) -> int:
    """Composite 0-100. Same shape as S9 leverage to keep cross-tab
    comparisons honest.

      40 pts engagement : log10(citation_count + 1) × 25 capped at 40
      30 pts breadth    : competitor_count × 12 capped at 30
      10 pts novelty    : ≥1 competitor AND target NOT cited
      20 pts catalogue  : log10(video_count + 1) × 24 capped at 20
                          (rewards channels with multiple cited videos)
    """
    cc = max(0, int(citation_count or 0))
    engagement = min(40, int(round(math.log10(cc + 1) * 25)))

    cb = max(0, int(competitor_count or 0))
    breadth = min(30, cb * 12)

    novelty = 10 if (cb > 0 and not target_cited) else 0

    vc = max(0, int(video_count or 0))
    catalogue = min(20, int(round(math.log10(vc + 1) * 24)))

    return max(0, min(100, engagement + breadth + novelty + catalogue))


def execute(job_payload: dict, scan_id: str, db: Session) -> dict:
    """Audit YouTube channels cited by LLMs in this scan.

    job_payload :
      - reset (bool) : drop existing rows before re-running
      - limit (int)  : cap distinct videos enriched (default MAX_VIDEOS_PER_RUN)
    """
    from datetime import datetime
    from models import Scan, ScanYouTubeCreator

    scan = db.query(Scan).filter(Scan.id == scan_id).first()
    if not scan:
        raise RuntimeError("Scan not found")

    reset = bool(job_payload.get("reset"))
    limit = int(job_payload.get("limit") or MAX_VIDEOS_PER_RUN)

    if reset:
        db.query(ScanYouTubeCreator).filter(ScanYouTubeCreator.scan_id == scan_id).delete()
        db.commit()

    videos = _cited_youtube_videos(db, scan_id, limit)
    if not videos:
        logger.info(f"audit_youtube_creators: no YouTube citations in scan {scan_id}")
        return {"videos": 0, "channels": 0, "oembed_ok": 0, "oembed_fail": 0}

    target_lower, competitor_lower, name_map = _scan_brand_context(db, scan_id)

    # Enrich each unique video via oEmbed. Result is grouped by channel
    # author_url ; videos that fail oEmbed (private/deleted/age-gated)
    # get a synthetic channel "(unknown channel)" so they remain visible.
    UNKNOWN_CHANNEL_URL = "(unknown channel)"
    channels: dict[str, dict] = {}
    oembed_ok = 0
    oembed_fail = 0

    for idx, v in enumerate(videos):
        meta = fetch_oembed(v["url"])
        if meta["status"] == 200 and meta["author_url"]:
            oembed_ok += 1
            channel_key = meta["author_url"]
            channel_name = meta["author_name"]
        else:
            oembed_fail += 1
            channel_key = UNKNOWN_CHANNEL_URL
            channel_name = None

        c = channels.get(channel_key)
        if c is None:
            c = {
                "channel_url": channel_key,
                "channel_name": channel_name,
                "channel_handle": channel_handle_from_url(channel_key),
                "citation_count": 0,
                "videos": [],
                "_competitor_set": set(),
                "_target_cited": False,
                "_winning_q": [],
                "_seen_q_keys": set(),
            }
            channels[channel_key] = c
        # If a later video on the same channel returns a name and we
        # didn't have one (first row was a fail), upgrade in place.
        if not c["channel_name"] and channel_name:
            c["channel_name"] = channel_name
        if not c["channel_handle"]:
            c["channel_handle"] = channel_handle_from_url(channel_key)

        # Tally brand_mentions across every LLM response that cited
        # this video. Filter through the Sprint 9.1 competitor gate.
        video_competitors: set[str] = set()
        video_target = False
        for slr_id, bm_list in v["_slr_brand_mentions"]:
            target_hit, comps = _classify_brand_mentions(
                bm_list, target_lower, competitor_lower, name_map
            )
            if target_hit:
                video_target = True
            video_competitors |= comps

        c["citation_count"] += v["citation_count"]
        c["_competitor_set"] |= video_competitors
        if video_target:
            c["_target_cited"] = True
        # Per-channel winning_questions dedupe.
        for q in v["winning_questions"]:
            qkey = (q.get("question_id"), q.get("provider"))
            if qkey in c["_seen_q_keys"]:
                continue
            c["_seen_q_keys"].add(qkey)
            if len(c["_winning_q"]) < 20:
                c["_winning_q"].append(q)

        c["videos"].append({
            "video_id": v["video_id"],
            "url": v["url"],
            "title": meta.get("title"),
            "thumbnail_url": meta.get("thumbnail_url"),
            "citation_count": v["citation_count"],
            "contexte": v["contextes"][0] if v["contextes"] else "",
            "contextes": v["contextes"],
            "competitor_brands": sorted(video_competitors),
            "target_cited": video_target,
            "winning_questions": v["winning_questions"][:5],
            "oembed_status": meta.get("status"),
            "oembed_error": meta.get("error"),
        })

        if (idx + 1) % 20 == 0:
            logger.info(
                f"youtube oembed progress {idx + 1}/{len(videos)} "
                f"(ok={oembed_ok}, fail={oembed_fail})"
            )
        time.sleep(OEMBED_DELAY_SECONDS)

    # Materialize per-channel rows. Drop channels that have neither a
    # classified competitor mention nor a target mention - they're noise
    # (random shorts the LLM dropped without a brand context). Same drop
    # as Sprint 9.1 materialize step.
    inserted = 0
    by_class: dict[str, int] = {"competitor_only": 0, "shared": 0, "target_only": 0}
    for channel_key, c in channels.items():
        competitors_sorted = sorted(c["_competitor_set"])
        target_cited = c["_target_cited"]
        if not competitors_sorted and not target_cited:
            continue

        # Sort videos competitor-cited first, then by citation_count.
        c["videos"].sort(key=lambda vd: (
            0 if (vd["competitor_brands"] and not vd["target_cited"]) else
            1 if (vd["competitor_brands"] and vd["target_cited"]) else 2,
            -vd["citation_count"],
        ))
        top_videos = c["videos"][:MAX_VIDEOS_PER_CHANNEL]

        classification = (
            "competitor_only" if (competitors_sorted and not target_cited) else
            "shared" if (competitors_sorted and target_cited) else
            "target_only"
        )
        by_class[classification] = by_class.get(classification, 0) + 1

        leverage = _leverage_score(
            c["citation_count"], len(competitors_sorted),
            target_cited, len(c["videos"]),
        )

        db.add(ScanYouTubeCreator(
            scan_id=scan_id,
            channel_url=channel_key,
            channel_name=c["channel_name"],
            channel_handle=c["channel_handle"],
            citation_count=c["citation_count"],
            video_count=len(c["videos"]),
            competitor_brands=competitors_sorted,
            target_cited=target_cited,
            classification=classification,
            top_videos=top_videos,
            winning_questions=c["_winning_q"],
            leverage_score=leverage,
            fetched_at=datetime.utcnow(),
        ))
        inserted += 1
        if inserted % 25 == 0:
            db.commit()

    db.commit()
    logger.info(
        f"audit_youtube_creators: scan {scan_id} → {inserted} channels "
        f"({by_class['competitor_only']} competitor_only, "
        f"{by_class['shared']} shared, {by_class['target_only']} target_only) "
        f"from {len(videos)} videos (oembed ok={oembed_ok}, fail={oembed_fail})"
    )
    return {
        "channels": inserted,
        "videos": len(videos),
        "oembed_ok": oembed_ok,
        "oembed_fail": oembed_fail,
        "by_classification": by_class,
    }
