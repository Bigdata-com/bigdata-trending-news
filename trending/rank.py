"""
Step 6 - Rank stories.

TRENDING ("what is everyone writing about right now")

    recency = exp(-hours_since(last_seen) / H)          H = 12h (1D) or 72h (1W)
    trend   = 0.30*log(1+articles) + 0.25*log(1+sources)
            + 0.25*log(spike of the story's entity)      (loudest entity if several)
            + 0.10*recency
            + 0.10*(materiality/5)                       (0 when the LLM is off)
            + external_engagement_score                  (hook for client data, default 0)

    Only stories covered by >= TRENDING_MIN_SOURCES distinct outlets qualify.

EMERGING ("strong sentiment, limited coverage so far") - a separate list built
from stories that are NOT in Trending, including single-source stories:

    |sentiment_signal| >= SENTIMENT_EXTREME
    single-source stories need a top-tier outlet (source rank <= EMERGING_MAX_SOURCE_RANK)
    latest article inside the current window
    materiality >= EMERGING_MIN_MATERIALITY (LLM on)
    emerging = |sentiment_signal| * (materiality/5) * recency   (materiality=3 when LLM off)

Sentiment is always Bigdata.com chunk sentiment, never the LLM.
All weights and thresholds live in config.py.
"""
from __future__ import annotations

import math
from typing import Callable

from . import config as cfg
from .cluster import Story
from .fetch import within
from .momentum import Momentum, Window


# --------------------------------------------------------------------------- #
# HOOK: plug in your own engagement data (clicks, reads, shares) here.
# Return an additive score boost for a story, e.g. 0.2 * log(1 + clicks).
# The default adds nothing, so ranking is driven by publishing momentum only.
# --------------------------------------------------------------------------- #
def external_engagement_score(story: Story) -> float:
    return 0.0


def recency(story: Story, win: Window) -> float:
    hours = max(0.0, (win.as_of - story.last_seen).total_seconds() / 3600)
    return math.exp(-hours / win.recency_hours)


def story_spike(story: Story, momentum: dict[str, Momentum]) -> float:
    spikes = [momentum[k].spike for k in story.entities if k in momentum]
    return max(spikes) if spikes else 1.0


def trend_score(
    story: Story, *, include_materiality: bool, engagement: Callable[[Story], float] | None = None
) -> float:
    w = cfg.WEIGHTS
    mat = (story.materiality or 0) / 5 if include_materiality else 0.0
    score = (
        w["articles"] * math.log1p(story.articles)
        + w["sources"] * math.log1p(story.sources)
        + w["spike"] * math.log(max(story.spike, 1e-6))
        + w["recency"] * story.recency
        + w["materiality"] * mat
    )
    return score + (engagement or external_engagement_score)(story)


def score_all(stories: list[Story], momentum: dict[str, Momentum], win: Window, include_materiality: bool,
              engagement: Callable[[Story], float] | None = None) -> None:
    """Set spike, recency, trend and sentiment_signal on every story (in place)."""
    for s in stories:
        s.spike = story_spike(s, momentum)
        s.recency = recency(s, win)
        s.trend = round(trend_score(s, include_materiality=include_materiality, engagement=engagement), 4)
        s.sentiment_signal = s.max_abs_sentiment if cfg.SENTIMENT_MODE == "max_abs" else s.avg_sentiment


def trending_eligible(s: Story) -> bool:
    return s.sources >= cfg.TRENDING_MIN_SOURCES


def _emerging_precheck(s: Story, win: Window) -> bool:
    """Emerging rules that do not need the LLM."""
    return (
        abs(s.sentiment_signal) >= cfg.SENTIMENT_EXTREME
        and (s.sources >= 2 or s.best_source_rank <= cfg.EMERGING_MAX_SOURCE_RANK)
        and s.last_seen is not None
        and within(s.last_seen, win)
    )


def select_for_enrichment(stories: list[Story], win: Window, per_group: int = 0) -> list[Story]:
    """
    Which stories to send to the LLM (provisional score = trend without materiality):
      * the top ENRICH_TOP_N trending-eligible stories,
      * the top `per_group` per entity (Notebook 2, so every commodity is covered),
      * up to EMERGING_ENRICH_MAX Emerging candidates (strongest sentiment first).
    """
    eligible = sorted([s for s in stories if trending_eligible(s)], key=lambda s: -s.trend)
    chosen: dict[int, Story] = {id(s): s for s in eligible[: cfg.ENRICH_TOP_N]}
    if per_group:
        keys = {k for s in stories for k in s.entities}
        for k in sorted(keys):
            for s in [s for s in eligible if k in s.entities][: per_group + 3]:
                chosen[id(s)] = s
    emerging = sorted(
        [s for s in stories if id(s) not in chosen and _emerging_precheck(s, win)],
        key=lambda s: -abs(s.sentiment_signal) * s.recency,
    )
    for s in emerging[: cfg.EMERGING_ENRICH_MAX]:
        chosen[id(s)] = s
    return list(chosen.values())


def rank_trending(stories: list[Story], top_n: int | None = None) -> list[Story]:
    """Trending list: >= 2 sources, sorted by trend score."""
    ranked = sorted([s for s in stories if trending_eligible(s)], key=lambda s: (-s.trend, s.sid))
    return ranked[: top_n or cfg.TRENDING_TOP_N]


def rank_emerging(
    stories: list[Story], exclude: list[Story], win: Window, use_llm: bool, require_direction: bool = False
) -> list[Story]:
    """Emerging list: strong sentiment, limited coverage, never a Trending story."""
    shown = {s.sid for s in exclude}
    out = []
    for s in stories:
        if s.sid in shown or not _emerging_precheck(s, win):
            continue
        if use_llm and (s.materiality or 0) < cfg.EMERGING_MIN_MATERIALITY:
            continue
        if require_direction and s.direction not in {"up", "down", "mixed"}:
            continue
        mat = (s.materiality or cfg.EMERGING_DEFAULT_MATERIALITY) if use_llm else cfg.EMERGING_DEFAULT_MATERIALITY
        s.emerging = round(abs(s.sentiment_signal) * (mat / 5) * s.recency, 4)
        out.append(s)
    out.sort(key=lambda s: (-s.emerging, s.sid))
    return out[: cfg.EMERGING_TOP_N]


def group_by_entity(stories: list[Story], keys: list[str], n: int) -> dict[str, list[Story]]:
    """Top-n stories per entity key (a story about two entities appears under both)."""
    return {k: [s for s in stories if k in s.entities][:n] for k in keys}


def net_direction(stories: list[Story]) -> str:
    """'mostly up' / 'mostly down' / 'mixed' for a commodity header."""
    ups = sum(s.direction == "up" for s in stories)
    downs = sum(s.direction == "down" for s in stories)
    if not stories or ups == downs:
        return "mixed"
    return "mostly up" if ups > downs else "mostly down"
