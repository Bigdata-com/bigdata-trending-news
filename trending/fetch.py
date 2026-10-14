"""
Step 3 - Fetch the current window's news articles for each entity.

One fast-mode /v1/search call per entity (per time slice), NEWS only, filtered
to the entity (or by text in keyword mode). The response is a list of
documents, each with the chunks that matched; we keep one record per document.

NOTE: search diversifies and de-duplicates its results (it is built to return
the most relevant, varied passages, not every copy of a story). Article counts
derived from search therefore UNDERSTATE true coverage. We use them to group
articles into stories and to display members; the entity-level momentum
(how loud an entity is overall) comes from the volume endpoint (momentum.py).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from . import config as cfg
from .bigdata import BigdataClient, entity_filter, news_filters, run_parallel
from .momentum import Window
from .resolve import Entity


@dataclass
class Article:
    doc_id: str
    headline: str
    source_id: str
    source_name: str
    source_rank: int          # 1 = top-tier outlet ... 5 = long tail
    timestamp: datetime       # UTC
    url: str
    chunk_text: str           # most relevant chunk (context for the LLM)
    sentiment: float          # mean of the returned chunks' sentiment (-1..1)
    sentiment_max_abs: float  # the chunk sentiment with the largest |value|
    entities: list[str] = field(default_factory=list)  # entity keys, e.g. ["TSLA"]
    gate: str = ""            # relevance-gate label (Notebook 2)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d


def _rank(value: Any) -> int:
    """'RANK_1' -> 1. Unknown ranks sort last (9)."""
    m = re.search(r"(\d+)", str(value or ""))
    return int(m.group(1)) if m else 9


def _parse_ts(value: str) -> datetime:
    """API timestamps are UTC without a suffix, e.g. 2026-10-13T05:30:18."""
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def to_article(doc: dict[str, Any], entity_key: str) -> Article:
    chunks = doc.get("chunks") or []
    best = max(chunks, key=lambda c: c.get("relevance") or 0, default={})
    sents = [float(c["sentiment"]) for c in chunks if c.get("sentiment") is not None]
    src = doc.get("source") or {}
    return Article(
        doc_id=doc["id"],
        headline=(doc.get("headline") or "").strip(),
        source_id=src.get("id", ""),
        source_name=src.get("name", ""),
        source_rank=_rank(src.get("rank")),
        timestamp=_parse_ts(doc["timestamp"]),
        url=doc.get("url") or "",
        chunk_text=(best.get("text") or "").strip(),
        sentiment=round(sum(sents) / len(sents), 3) if sents else 0.0,
        sentiment_max_abs=max(sents, key=abs) if sents else 0.0,
        entities=[entity_key],
    )


def _slices(win: Window) -> list[tuple[datetime, datetime]]:
    n = max(1, int(cfg.FETCH_SLICES))
    step = (win.as_of - win.current_start) / n
    return [(win.current_start + step * i, win.current_start + step * (i + 1)) for i in range(n)]


def fetch_entity(client: BigdataClient, entity: Entity, win: Window) -> list[Article]:
    """All unique documents for one entity in the current window."""
    docs: dict[str, Article] = {}
    for start, end in _slices(win):
        filters = news_filters(start, end)
        texts: list[str] | None = None
        if entity.mode == "entity" and entity.id:
            filters["entity"] = entity_filter([entity.id], cfg.SEARCH_IN)
            texts = entity.texts or None
        else:
            texts = entity.texts or [entity.input]
        topic = f"{entity.key} price and market news" if entity.kind == "commodity" else f"{entity.name} news"
        reranker = topic if texts and len(texts) > 1 else None
        for doc in client.search(filters, texts=texts, reranker_text=reranker):
            if doc.get("id") and doc["id"] not in docs:
                docs[doc["id"]] = to_article(doc, entity.key)
    return sorted(docs.values(), key=lambda a: a.timestamp, reverse=True)


def fetch_all(client: BigdataClient, entities: list[Entity], win: Window) -> dict[str, list[Article]]:
    """{entity_key: [articles]} for every entity (parallel, failures skipped)."""
    res = run_parallel(lambda e: fetch_entity(client, e, win), entities, label=lambda e: e.key)
    return {entities[i].key: arts for i, arts in sorted(res.items())}


def within(ts: datetime, win: Window) -> bool:
    return win.current_start <= ts <= win.as_of + timedelta(minutes=1)
