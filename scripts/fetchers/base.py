from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass
class Item:
    id: str
    source_id: str
    source_name: str
    source_type: str
    category: str
    tier: int
    title: str
    url: str
    published_at: str
    author: str | None = None
    summary: str | None = None
    duration_seconds: int | None = None
    tags: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FetchResult:
    source_id: str
    items: list[Item] = field(default_factory=list)
    error: str | None = None
    fetched_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "fetched_at": self.fetched_at,
            "error": self.error,
            "item_count": len(self.items),
            "items": [i.to_dict() for i in self.items],
        }


class Fetcher:
    """Override fetch() to return a FetchResult. Implementations must filter
    items themselves using `since` (UTC datetime, inclusive lower bound)."""

    type_name: str = "base"

    def fetch(self, source: dict[str, Any], since: datetime) -> FetchResult:
        raise NotImplementedError
