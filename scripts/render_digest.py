#!/usr/bin/env python3
"""Render a JSON digest as Markdown grouped by category and source.

Usage:
  python scripts/render_digest.py                      # latest digest -> digests/YYYY-MM-DD.md
  python scripts/render_digest.py --date 2026-05-21
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATE_STEM_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
HASHTAG_RE = re.compile(r"(?<!\w)#[\w-]+")
HTML_TAG_RE = re.compile(r"<[^>]+>")
MISSING_PLAYLIST_ERROR_RE = re.compile(
    r"playlistitems failed: playlistitems http 404:.*playlist.*not be found"
)
TIMESTAMP_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
URL_RE = re.compile(r"https?://\S+|www\.\S+")
TIER_LABELS = {
    1: "Tier 1 — Core Systems",
    2: "Tier 2 — Infrastructure & Protocols",
    3: "Tier 3 — Big Data & Analytics",
    4: "Tier 4 — Career & Interview",
}


def clean_summary(text: str | None) -> str:
    if not text:
        return ""

    cleaned_lines: list[str] = []
    for line in html.unescape(text).splitlines():
        line = HTML_TAG_RE.sub(" ", line).strip()
        if not line:
            continue
        without_urls = URL_RE.sub("", line).strip()
        without_tags = HASHTAG_RE.sub("", without_urls).strip()
        if not without_tags:
            continue
        cleaned_lines.append(without_tags)

    cleaned = " ".join(cleaned_lines)
    cleaned = TIMESTAMP_RE.split(cleaned, maxsplit=1)[0]
    cleaned = " ".join(cleaned.split())
    return cleaned if len(cleaned) >= 40 else ""


def clean_error(text: str | None) -> str:
    if not text:
        return ""
    text = HTML_TAG_RE.sub(" ", html.unescape(text))
    return " ".join(text.split())


def is_reportable_error(status: dict) -> bool:
    error = clean_error(status.get("error"))
    if not error:
        return False
    return MISSING_PLAYLIST_ERROR_RE.search(error.lower()) is None


def truncate(text: str | None, n: int = 280) -> str:
    text = clean_summary(text)
    if not text:
        return ""
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def render(digest: dict) -> str:
    cat_labels = {c["id"]: c["label"] for c in digest.get("categories", [])}
    items = digest.get("items", [])

    by_cat: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        by_cat[it.get("category") or "uncategorized"].append(it)

    lines: list[str] = []
    gen = digest.get("generated_at", "")
    win = digest.get("window", {})
    item_count = len(items)
    item_label = "item" if item_count == 1 else "items"
    lines.append(f"# Daily Digest — {gen[:10]}")
    lines.append("")
    lines.append(
        f"_Window: {win.get('since', '')} → {win.get('until', '')} "
        f"({win.get('spec', '')}) · {item_count} {item_label} "
        f"from {digest.get('source_count', 0)} sources_"
    )
    lines.append("")

    errored = [s for s in digest.get("sources_status", []) if is_reportable_error(s)]
    if errored:
        lines.append("<details><summary>Source errors / unresolved "
                     f"({len(errored)})</summary>")
        lines.append("")
        for s in errored:
            lines.append(f"- `{s['source_id']}`: {clean_error(s.get('error'))}")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    if not items:
        lines.append("_No new items in this window._")
        return "\n".join(lines) + "\n"

    cat_order = [c["id"] for c in digest.get("categories", [])]
    cat_order += [c for c in by_cat.keys() if c not in cat_order]

    for cat_id in cat_order:
        bucket = by_cat.get(cat_id) or []
        if not bucket:
            continue
        label = cat_labels.get(cat_id, cat_id)
        lines.append(f"## {label}")
        lines.append("")

        by_source: dict[str, list[dict]] = defaultdict(list)
        for it in bucket:
            by_source[it["source_name"]].append(it)

        for src_name, src_items in by_source.items():
            tier = src_items[0].get("tier", 0)
            tier_tag = TIER_LABELS.get(tier, "").split(" — ")[0]
            tier_suffix = f" · _{tier_tag}_" if tier_tag else ""
            lines.append(f"### {src_name}{tier_suffix}")
            for it in src_items:
                pub = it.get("published_at", "")[:16].replace("T", " ")
                summary = truncate(it.get("summary"))
                lines.append(f"- **[{it['title']}]({it['url']})** — {pub} UTC")
                if summary:
                    lines.append(f"  {summary}")
            lines.append("")

    return "\n".join(lines) + "\n"


def latest_dated_digest(in_dir: Path) -> Path | None:
    candidates = [
        path for path in in_dir.glob("*.json")
        if DATE_STEM_RE.fullmatch(path.stem)
    ]
    if not candidates:
        return None
    return sorted(candidates)[-1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-dir", type=Path, default=ROOT / "data" / "digests")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "digests")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (defaults to latest)")
    args = ap.parse_args()

    if args.date:
        path = args.in_dir / f"{args.date}.json"
    else:
        path = latest_dated_digest(args.in_dir)
        if path is None:
            print(f"no dated digest JSON files in {args.in_dir}", file=sys.stderr)
            return 1

    digest = json.loads(path.read_text())
    md = render(digest)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out = args.out_dir / f"{path.stem}.md"
    out.write_text(md)
    print(f"wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
