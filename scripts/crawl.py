#!/usr/bin/env python3
"""Crawl sources defined in root.yaml and emit a daily JSON digest.

Usage:
  python scripts/crawl.py                     # last 24h, write to data/digests/YYYY-MM-DD.json
  python scripts/crawl.py --since 7d          # last 7 days
  python scripts/crawl.py --date 2026-05-21   # backdated digest filename
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from fetchers import RSSFetcher, YouTubeAPIFetcher, YouTubeFetcher  # noqa: E402
from fetchers.base import FetchResult  # noqa: E402


def parse_since(spec: str) -> timedelta | None:
    """Return the window length, or None for 'all' (no lower bound)."""
    if spec.strip().lower() in {"all", "genesis", "*"}:
        return None
    m = re.fullmatch(r"(\d+)([hdw])", spec.strip())
    if not m:
        raise ValueError(f"invalid --since spec: {spec!r} (use e.g. 24h, 7d, 2w, all)")
    n = int(m.group(1))
    unit = m.group(2)
    return {"h": timedelta(hours=n), "d": timedelta(days=n), "w": timedelta(weeks=n)}[unit]


def load_sources(root_yaml: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    doc = yaml.safe_load(root_yaml.read_text())
    return doc, doc.get("sources", [])


def select_fetcher(source: dict[str, Any], yt, rss: RSSFetcher):
    url = (source.get("url") or "") + " " + (source.get("handle") or "")
    handle = source.get("handle") or ""
    if "youtube.com" in url or handle.startswith("@"):
        return yt
    if source.get("feed") or source.get("url"):
        return rss
    return None


def crawl(sources: list[dict[str, Any]], since: datetime, cache_path: Path,
          workers: int = 8, youtube_api_key: str | None = None) -> list[FetchResult]:
    if youtube_api_key:
        yt = YouTubeAPIFetcher(
            api_key=youtube_api_key,
            cache_path=cache_path.parent / "youtube_api.json",
        )
        print("using YouTube Data API v3 (full backfill enabled)", file=sys.stderr)
    else:
        yt = YouTubeFetcher(cache_path=cache_path)
        print("no YOUTUBE_API_KEY set; falling back to RSS (15 videos/channel cap)",
              file=sys.stderr)
    rss = RSSFetcher()
    results: list[FetchResult] = []

    def run_one(src: dict[str, Any]) -> FetchResult:
        sid = src.get("id", "<unknown>")
        try:
            fetcher = select_fetcher(src, yt, rss)
            if fetcher is None:
                return FetchResult(source_id=sid, error="no fetcher (missing url/handle)")
            return fetcher.fetch(src, since=since)
        except Exception as e:  # noqa: BLE001 - one bad source must not kill the run
            return FetchResult(source_id=sid, error=f"unhandled: {type(e).__name__}: {e}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for res in pool.map(run_one, sources):
            results.append(res)
            status = f"err: {res.error}" if res.error else f"{len(res.items)} items"
            print(f"  [{res.source_id}] {status}", file=sys.stderr)

    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=ROOT / "root.yaml")
    ap.add_argument("--since", default="24h", help="window e.g. 24h, 7d, 2w")
    ap.add_argument("--date", default=None, help="override digest date (YYYY-MM-DD)")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "data" / "digests")
    ap.add_argument("--cache", type=Path,
                    default=ROOT / "data" / "resolved" / "youtube_channel_ids.json")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--only", default=None,
                    help="comma-separated source ids to restrict the crawl")
    args = ap.parse_args()

    doc, sources = load_sources(args.root)
    if args.only:
        keep = {s.strip() for s in args.only.split(",") if s.strip()}
        sources = [s for s in sources if s["id"] in keep]
        if not sources:
            print(f"no sources matched --only={args.only}", file=sys.stderr)
            return 2

    now = datetime.now(timezone.utc)
    window = parse_since(args.since)
    if window is None:
        since = datetime(1970, 1, 1, tzinfo=timezone.utc)
    else:
        since = now - window
    date_str = args.date or now.strftime("%Y-%m-%d")

    print(f"crawling {len(sources)} sources since {since.isoformat()}", file=sys.stderr)
    results = crawl(
        sources, since, args.cache,
        workers=args.workers,
        youtube_api_key=os.environ.get("YOUTUBE_API_KEY") or None,
    )

    all_items = [item for r in results for item in r.items]
    all_items.sort(key=lambda i: i.published_at, reverse=True)

    digest = {
        "version": 1,
        "generated_at": now.isoformat(),
        "window": {"since": since.isoformat(), "until": now.isoformat(), "spec": args.since},
        "source_count": len(sources),
        "item_count": len(all_items),
        "categories": doc.get("categories", []),
        "sources_status": [
            {"source_id": r.source_id, "items": len(r.items), "error": r.error}
            for r in results
        ],
        "items": [i.to_dict() for i in all_items],
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out = args.out_dir / f"{date_str}.json"
    out.write_text(json.dumps(digest, indent=2, ensure_ascii=False))
    print(f"wrote {out} ({len(all_items)} items)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
