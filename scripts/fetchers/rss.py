from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

import requests
from dateutil import parser as dtparser

from .base import Fetcher, FetchResult, Item

UA = "machine-learning-platform-crawler/0.1"


class RSSFetcher(Fetcher):
    """Generic RSS 2.0 / Atom 1.0 fetcher for future blog/podcast sources."""

    type_name = "rss"

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": UA})

    def fetch(self, source: dict[str, Any], since: datetime) -> FetchResult:
        sid = source["id"]
        result = FetchResult(source_id=sid)
        feed_url = source.get("feed") or source.get("url")
        if not feed_url:
            result.error = "no feed/url"
            return result

        try:
            r = self.session.get(feed_url, timeout=20)
            r.raise_for_status()
        except requests.RequestException as e:
            result.error = f"feed fetch failed: {e}"
            return result

        try:
            root = ET.fromstring(r.content)
        except ET.ParseError as e:
            result.error = f"feed parse failed: {e}"
            return result

        tag = root.tag.lower()
        entries: list[Item] = []
        if tag.endswith("rss") or root.find("channel") is not None:
            entries = self._parse_rss(root, source, since)
        else:
            entries = self._parse_atom(root, source, since)

        result.items = entries
        return result

    @staticmethod
    def _hash_id(prefix: str, url: str) -> str:
        return f"{prefix}:{hashlib.sha1(url.encode()).hexdigest()[:16]}"

    def _parse_rss(self, root, source, since) -> list[Item]:
        items: list[Item] = []
        for it in root.findall(".//item"):
            link = (it.findtext("link") or "").strip()
            title = (it.findtext("title") or "").strip()
            pub = it.findtext("pubDate")
            if not link or not pub:
                continue
            try:
                published = dtparser.parse(pub)
            except (TypeError, ValueError):
                continue
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            if published < since:
                continue
            desc = (it.findtext("description") or "").strip() or None
            author = (it.findtext("author") or it.findtext("{http://purl.org/dc/elements/1.1/}creator") or "").strip() or None
            items.append(
                Item(
                    id=self._hash_id("rss", link),
                    source_id=source["id"],
                    source_name=source.get("name", source["id"]),
                    source_type=source.get("type", "rss"),
                    category=source.get("category", ""),
                    tier=int(source.get("tier", 0) or 0),
                    title=title,
                    url=link,
                    published_at=published.astimezone(timezone.utc).isoformat(),
                    author=author,
                    summary=desc,
                    tags=list(source.get("topics", []) or []),
                )
            )
        return items

    def _parse_atom(self, root, source, since) -> list[Item]:
        ns = {"a": "http://www.w3.org/2005/Atom"}
        items: list[Item] = []
        for entry in root.findall("a:entry", ns):
            link_el = entry.find("a:link", ns)
            link = link_el.get("href") if link_el is not None else None
            title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
            pub = entry.findtext("a:published", default=None, namespaces=ns) or entry.findtext(
                "a:updated", default=None, namespaces=ns
            )
            if not link or not pub:
                continue
            try:
                published = dtparser.isoparse(pub)
            except (TypeError, ValueError):
                continue
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            if published < since:
                continue
            desc = (entry.findtext("a:summary", default="", namespaces=ns) or "").strip() or None
            author = (entry.findtext("a:author/a:name", default="", namespaces=ns) or "").strip() or None
            items.append(
                Item(
                    id=self._hash_id("atom", link),
                    source_id=source["id"],
                    source_name=source.get("name", source["id"]),
                    source_type=source.get("type", "rss"),
                    category=source.get("category", ""),
                    tier=int(source.get("tier", 0) or 0),
                    title=title,
                    url=link,
                    published_at=published.astimezone(timezone.utc).isoformat(),
                    author=author,
                    summary=desc,
                    tags=list(source.get("topics", []) or []),
                )
            )
        return items
