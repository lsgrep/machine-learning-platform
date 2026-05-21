from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dateutil import parser as dtparser

from .base import Fetcher, FetchResult, Item

API_BASE = "https://www.googleapis.com/youtube/v3"
WATCH_URL = "https://www.youtube.com/watch?v={video_id}"

_CHANNEL_URL_RE = re.compile(r"/channel/(UC[\w-]{22})")
_HANDLE_URL_RE = re.compile(r"/@([\w.\-]+)")


class YouTubeAPIFetcher(Fetcher):
    """YouTube Data API v3 fetcher.

    Resolves @handles via channels.list?forHandle, expands the uploads
    playlist via playlistItems.list to get every public upload (not the
    15-item RSS cap). Quota cost is ~1 unit per resolve + 1 per 50 videos.
    Early-terminates pagination when an item older than `since` appears,
    so daily runs stay cheap.
    """

    type_name = "youtube_api"

    def __init__(self, api_key: str, cache_path: Path,
                 session: requests.Session | None = None,
                 page_size: int = 50):
        self.api_key = api_key
        self.cache_path = cache_path
        self.session = session or requests.Session()
        self.page_size = page_size
        self._cache = self._load_cache()

    def _load_cache(self) -> dict[str, dict]:
        if self.cache_path.exists():
            try:
                data = json.loads(self.cache_path.read_text())
                return data if isinstance(data, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self._cache, indent=2, sort_keys=True))

    def _get(self, path: str, params: dict[str, Any]) -> dict:
        params = {**params, "key": self.api_key}
        r = self.session.get(f"{API_BASE}/{path}", params=params, timeout=20)
        if r.status_code != 200:
            try:
                err = r.json().get("error", {}).get("message", r.text[:200])
            except ValueError:
                err = r.text[:200]
            raise RuntimeError(f"{path} HTTP {r.status_code}: {err}")
        return r.json()

    def resolve(self, handle_or_url: str) -> dict | None:
        cached = self._cache.get(handle_or_url)
        if cached and "uploads_playlist" in cached:
            return cached

        if handle_or_url.startswith("http"):
            m = _CHANNEL_URL_RE.search(handle_or_url)
            if m:
                params = {"part": "contentDetails,snippet", "id": m.group(1)}
            else:
                m = _HANDLE_URL_RE.search(handle_or_url)
                if not m:
                    return None
                params = {"part": "contentDetails,snippet", "forHandle": "@" + m.group(1)}
        else:
            params = {"part": "contentDetails,snippet",
                      "forHandle": "@" + handle_or_url.lstrip("@")}

        data = self._get("channels", params)
        items = data.get("items") or []
        if not items:
            return None
        ch = items[0]
        info = {
            "channel_id": ch["id"],
            "uploads_playlist": ch["contentDetails"]["relatedPlaylists"]["uploads"],
            "title": ch["snippet"]["title"],
        }
        self._cache[handle_or_url] = info
        self._save_cache()
        return info

    def fetch(self, source: dict[str, Any], since: datetime) -> FetchResult:
        sid = source["id"]
        result = FetchResult(source_id=sid)
        key = source.get("handle") or source.get("url")
        if not key:
            result.error = "no handle/url"
            return result

        try:
            info = self.resolve(key)
        except Exception as e:  # noqa: BLE001
            result.error = f"resolve failed: {e}"
            return result
        if not info:
            result.error = f"channel not found for {key}"
            return result

        playlist_id = info["uploads_playlist"]
        page_token: str | None = None
        while True:
            params = {
                "part": "snippet,contentDetails",
                "playlistId": playlist_id,
                "maxResults": self.page_size,
            }
            if page_token:
                params["pageToken"] = page_token
            try:
                data = self._get("playlistItems", params)
            except Exception as e:  # noqa: BLE001
                if not result.error:
                    result.error = f"playlistItems failed: {e}"
                break

            stop = False
            for it in data.get("items") or []:
                cd = it.get("contentDetails") or {}
                sn = it.get("snippet") or {}
                vid = cd.get("videoId") or (sn.get("resourceId") or {}).get("videoId")
                pub_raw = cd.get("videoPublishedAt") or sn.get("publishedAt")
                if not vid or not pub_raw:
                    continue
                try:
                    published = dtparser.isoparse(pub_raw)
                except (TypeError, ValueError):
                    continue
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
                if published < since:
                    # uploads playlist is reverse-chronological — the rest of this
                    # page (and all subsequent pages) is older too.
                    stop = True
                    break
                result.items.append(Item(
                    id=f"yt:{vid}",
                    source_id=sid,
                    source_name=source.get("name", sid),
                    source_type="youtube",
                    category=source.get("category", ""),
                    tier=int(source.get("tier", 0) or 0),
                    title=(sn.get("title") or "").strip(),
                    url=WATCH_URL.format(video_id=vid),
                    published_at=published.astimezone(timezone.utc).isoformat(),
                    author=sn.get("videoOwnerChannelTitle") or info.get("title"),
                    summary=(sn.get("description") or "").strip() or None,
                    tags=list(source.get("topics", []) or []),
                    raw={
                        "channel_id": info["channel_id"],
                        "video_id": vid,
                        "playlist_id": playlist_id,
                    },
                ))
            page_token = data.get("nextPageToken")
            if stop or not page_token:
                break
        return result
