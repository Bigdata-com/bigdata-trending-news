"""
Step 2 - Momentum per entity (pure Bigdata.com data, no LLM).

For each entity we make two /v1/search/volume calls:

  Call A  baseline + current span, daily buckets
          → average documents per day in the baseline, and a sparkline
  Call B  current window only, grouped by source
          → current_docs (total.documents), distinct_sources (len(groups.sources)),
            current sentiment (total.sentiment)

  spike = (current_docs + 1) / (baseline_avg_per_period + 1)

Volume is bucketed by calendar day (UTC), so "today" is a partial bucket.
That is why `current_docs` comes from Call B, which uses the exact rolling
window (e.g. the last 24 hours), and not from the last daily bucket.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd

from . import config as cfg
from .bigdata import BigdataClient, entity_filter, news_filters, run_parallel
from .resolve import Entity


@dataclass
class Window:
    """The concrete time ranges for one run."""
    name: str
    as_of: datetime
    current_start: datetime
    baseline_start: datetime        # midnight UTC, start of the baseline days
    baseline_days: int
    period_days: int
    recency_hours: float

    @property
    def label(self) -> str:
        cur = "last 24h" if self.name == "1D" else "last 7 days"
        return f"{cur} vs prior {self.baseline_days}-day average"


def make_window(as_of: datetime, name: str | None = None) -> Window:
    name = name or cfg.WINDOW
    w = cfg.WINDOWS[name]
    as_of = as_of.astimezone(timezone.utc)
    current_start = as_of - timedelta(hours=w["current_hours"])
    # Baseline = the full calendar days strictly before the current window starts.
    first_current_day = datetime.combine(current_start.date(), datetime.min.time(), timezone.utc)
    baseline_start = first_current_day - timedelta(days=w["baseline_days"])
    return Window(name, as_of, current_start, baseline_start, w["baseline_days"],
                  w["period_days"], float(w["recency_hours"]))


@dataclass
class Momentum:
    key: str
    entity_id: str | None
    mode: str
    current_docs: int
    baseline_per_day: float
    baseline_per_period: float
    spike: float
    distinct_sources: int
    sentiment: float | None
    sparkline: list[int] = field(default_factory=list)
    spark_labels: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)


def _filters_for(entity: Entity, start: datetime, end: datetime) -> tuple[dict[str, Any], list[str] | None]:
    """Entity mode: filter by ID. Keyword mode: no entity filter, use texts."""
    f = news_filters(start, end)
    if entity.mode == "entity" and entity.id:
        f["entity"] = entity_filter([entity.id], cfg.MOMENTUM_SEARCH_IN)
        return f, None
    return f, entity.texts or [entity.input]


def _daily_series(volume: list[dict[str, Any]], start: date, end: date) -> dict[date, int]:
    """Daily document counts with missing days filled with 0."""
    by_day = {date.fromisoformat(v["date"]): int(v.get("documents", 0)) for v in volume}
    out, d = {}, start
    while d <= end:
        out[d] = by_day.get(d, 0)
        d += timedelta(days=1)
    return out


def _sparkline(series: dict[date, int], win: Window) -> tuple[list[int], list[str]]:
    """8 daily points for 1D; weekly totals for 1W (4 baseline weeks + current)."""
    days = sorted(series)
    if win.period_days == 1:
        last = days[-8:]
        return [series[d] for d in last], [d.strftime("%b %d") for d in last]
    points, labels = [], []
    end = days[-1]
    for k in range(4, -1, -1):
        chunk = [d for d in days if end - timedelta(days=7 * (k + 1)) < d <= end - timedelta(days=7 * k)]
        points.append(sum(series[d] for d in chunk))
        labels.append(f"wk of {chunk[0].strftime('%b %d')}" if chunk else "")
    return points, labels


def entity_momentum(client: BigdataClient, entity: Entity, win: Window) -> Momentum:
    # Call A: baseline days + current span, daily buckets.
    f_a, texts = _filters_for(entity, win.baseline_start, win.as_of)
    res_a = client.volume(f_a, texts=texts)
    series = _daily_series(res_a.get("volume", []), win.baseline_start.date(), win.as_of.date())
    baseline_days = [d for d in series if d < win.current_start.date()][-win.baseline_days:]
    baseline_per_day = sum(series[d] for d in baseline_days) / max(len(baseline_days), 1)
    baseline_per_period = baseline_per_day * win.period_days

    # Call B: exact current window, grouped by source.
    f_b, texts = _filters_for(entity, win.current_start, win.as_of)
    res_b = client.volume(f_b, texts=texts, group_by_source=True)
    total = res_b.get("total", {})
    current_docs = int(total.get("documents", 0))
    sources = [s["id"] for s in (res_b.get("groups") or {}).get("sources", [])]

    spark, labels = _sparkline(series, win)
    return Momentum(
        key=entity.key,
        entity_id=entity.id,
        mode=entity.mode,
        current_docs=current_docs,
        baseline_per_day=round(baseline_per_day, 1),
        baseline_per_period=round(baseline_per_period, 1),
        spike=round((current_docs + 1) / (baseline_per_period + 1), 2),
        distinct_sources=len(sources),
        sentiment=total.get("sentiment"),
        sparkline=spark,
        spark_labels=labels,
        source_ids=sources,
    )


def compute_momentum(client: BigdataClient, entities: list[Entity], win: Window) -> dict[str, Momentum]:
    """Momentum for every entity (in parallel; failures are skipped)."""
    res = run_parallel(lambda e: entity_momentum(client, e, win), entities, label=lambda e: e.key)
    return {entities[i].key: m for i, m in sorted(res.items())}


_BARS = "▁▂▃▄▅▆▇█"


def text_sparkline(values: list[int]) -> str:
    """Unicode sparkline for tables, e.g. ▂▃▃▂▅█."""
    if not values:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    return "".join(_BARS[int((v - lo) / span * (len(_BARS) - 1))] for v in values)


def momentum_table(mom: dict[str, Momentum], entities: list[Entity] | None = None) -> pd.DataFrame:
    """Entities sorted by spike: which names are unusually loud right now."""
    names = {e.key: e.name for e in entities or []}
    rows = []
    for m in mom.values():
        rows.append({
            "entity": m.key,
            "name": names.get(m.key, ""),
            "spike": m.spike,
            "current_docs": m.current_docs,
            "baseline_per_period": m.baseline_per_period,
            "distinct_sources": m.distinct_sources,
            "sentiment": m.sentiment,
            "trend": text_sparkline(m.sparkline),
            "mode": m.mode,
        })
    df = pd.DataFrame(rows)
    return df.sort_values("spike", ascending=False).reset_index(drop=True) if len(df) else df
