from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dateutil import parser as dtparser

from .base import Fetcher, FetchResult, Item

ATOM_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
WATCH_URL = "https://www.youtube.com/watch?v={video_id}"

_CID_PATTERNS = [
    re.compile(r'"externalId":"(UC[\w-]{22})"'),
    re.compile(r'"channelId":"(UC[\w-]{22})"'),
    re.compile(r'"browseId":"(UC[\w-]{22})"'),
    re.compile(r'<meta itemprop="(?:identifier|channelId)" content="(UC[\w-]{22})"'),
    re.compile(r'<link rel="canonical" href="[^"]*/channel/(UC[\w-]{22})"'),
    re.compile(r'og:url"\s+content="[^"]*/channel/(UC[\w-]{22})"'),
    re.compile(r'/channel/(UC[\w-]{22})'),
]

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class YouTubeFetcher(Fetcher):
    type_name = "youtube"

    def __init__(self, cache_path: Path, session: requests.Session | None = None):
        self.cache_path = cache_path
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
        self._cache = self._load_cache()

    def _load_cache(self) -> dict[str, str]:
        if self.cache_path.exists():
            try:
                return json.loads(self.cache_path.read_text())
            except json.JSONDecodeError:
                return {}
        return {}

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self._cache, indent=2, sort_keys=True))

    def resolve_channel_id(self, handle_or_url: str) -> tuple[str | None, str | None]:
        """Return (channel_id, error). Tries the channel home then /about."""
        if handle_or_url in self._cache:
            return self._cache[handle_or_url], None

        if handle_or_url.startswith("http"):
            candidates = [handle_or_url, handle_or_url.rstrip("/") + "/about"]
        else:
            h = handle_or_url.lstrip("@")
            candidates = [
                f"https://www.youtube.com/@{h}",
                f"https://www.youtube.com/@{h}/about",
                f"https://www.youtube.com/c/{h}",
            ]

        last_diag = "no candidates tried"
        for page_url in candidates:
            try:
                r = self.session.get(page_url, timeout=20, allow_redirects=True)
            except requests.RequestException as e:
                last_diag = f"{page_url}: {type(e).__name__}"
                continue
            if r.status_code >= 400:
                last_diag = f"{page_url}: HTTP {r.status_code}"
                continue
            html = r.text
            for pat in _CID_PATTERNS:
                m = pat.search(html)
                if m:
                    cid = m.group(1)
                    self._cache[handle_or_url] = cid
                    self._save_cache()
                    return cid, None
            last_diag = f"{page_url}: HTTP {r.status_code} {len(html)}B, no UC id matched"

        return None, last_diag

    def fetch(self, source: dict[str, Any], since: datetime) -> FetchResult:
        sid = source["id"]
        result = FetchResult(source_id=sid)

        key = source.get("handle") or source.get("url")
        if not key:
            result.error = "no handle/url"
            return result

        channel_id, diag = self.resolve_channel_id(key)
        if not channel_id:
            result.error = f"could not resolve channel_id for {key} ({diag})"
            return result

        try:
            r = self.session.get(FEED_URL.format(channel_id=channel_id), timeout=20)
            r.raise_for_status()
        except requests.RequestException as e:
            result.error = f"feed fetch failed: {e}"
            return result

        try:
            root = ET.fromstring(r.content)
        except ET.ParseError as e:
            result.error = f"feed parse failed: {e}"
            return result

        for entry in root.findall("atom:entry", ATOM_NS):
            vid_el = entry.find("yt:videoId", ATOM_NS)
            title_el = entry.find("atom:title", ATOM_NS)
            link_el = entry.find("atom:link", ATOM_NS)
            pub_el = entry.find("atom:published", ATOM_NS)
            author_el = entry.find("atom:author/atom:name", ATOM_NS)
            desc_el = entry.find("media:group/media:description", ATOM_NS)

            if vid_el is None or pub_el is None or title_el is None:
                continue

            try:
                published = dtparser.isoparse(pub_el.text)
            except (TypeError, ValueError):
                continue
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            if published < since:
                continue

            vid = vid_el.text
            url = link_el.get("href") if link_el is not None else WATCH_URL.format(video_id=vid)

            result.items.append(
                Item(
                    id=f"yt:{vid}",
                    source_id=sid,
                    source_name=source.get("name", sid),
                    source_type="youtube",
                    category=source.get("category", ""),
                    tier=int(source.get("tier", 0) or 0),
                    title=(title_el.text or "").strip(),
                    url=url,
                    published_at=published.astimezone(timezone.utc).isoformat(),
                    author=author_el.text if author_el is not None else None,
                    summary=(desc_el.text or "").strip() if desc_el is not None else None,
                    tags=list(source.get("topics", []) or []),
                    raw={"channel_id": channel_id, "video_id": vid},
                )
            )

        return result
