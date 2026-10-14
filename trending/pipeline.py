"""
Orchestration helpers that chain the steps, so each notebook cell stays short.

    client, llm, win = start("watchlist")
    entities  = resolve_all(...)                                  # Step 1
    momentum  = compute_momentum(client, entities, win)           # Step 2
    stories   = build_stories(client, llm, entities, win, momentum)  # Steps 3-6
    trending  = rank_trending(stories)                            # Step 6
    emerging  = rank_emerging(stories, trending, win, use_llm)    # Step 6b
    ...render...                                                  # Step 7
    finish(client, llm)                                           # save fixtures
"""
from __future__ import annotations

import logging
import math
import time

from . import cache
from . import config as cfg
from .bigdata import BigdataClient, news_filters
from .cluster import Story, cluster_all, dedupe_top
from .enrich import enrich_stories, relevance_gate
from .fetch import Article, fetch_all
from .llm import LLM, get_llm
from .momentum import Momentum, Window, compute_momentum, make_window
from .rank import score_all, select_for_enrichment
from .resolve import Entity

logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")


def start(namespace: str) -> tuple[BigdataClient, LLM | None, Window]:
    """Pick the fixtures folder, fix the run clock, create the clients."""
    cache.set_namespace(namespace)
    as_of = cache.run_clock()
    win = make_window(as_of)
    client = BigdataClient()
    llm = get_llm()
    if cfg.REPLAY and llm is not None:
        # answers come from fixtures: report the model that actually produced them
        meta = cache.run_meta()
        if "llm" in meta and meta["llm"] is None:
            llm = None  # the saved run did not use an LLM
        elif meta.get("llm"):
            llm.provider, llm.model = meta["llm"]["provider"], meta["llm"]["model"]
    mode = "REPLAY (offline, from fixtures/)" if cfg.REPLAY else "LIVE"
    print(f"Mode: {mode} · window {cfg.WINDOW}: {win.current_start:%Y-%m-%d %H:%M} → {win.as_of:%Y-%m-%d %H:%M} UTC")
    print(f"LLM: {llm if llm else 'off (lexical grouping, headline titles)'}")
    return client, llm, win


def llm_label(llm: LLM | None) -> str:
    return f"AI ({llm.model})" if llm else "lexical (no AI)"


def build_stories(
    client: BigdataClient,
    llm: LLM | None,
    entities: list[Entity],
    win: Window,
    momentum: dict[str, Momentum],
    *,
    gate: bool = False,
    per_group: int = 0,
    commodity: bool = False,
    verbose: bool = True,
) -> tuple[list[Story], dict[str, list[Article]], dict[str, dict[str, int]]]:
    """
    Steps 3-6: fetch → (optional relevance gate) → cluster → enrich → score.
    Returns (all scored stories, articles per entity, gate stats).
    """
    names = {e.key: e.name for e in entities}
    t0 = time.time()

    articles = fetch_all(client, entities, win)
    if verbose:
        n = sum(len(v) for v in articles.values())
        print(f"Step 3  fetched {n} unique articles: "
              + ", ".join(f"{k} {len(v)}" for k, v in articles.items()) + f"  ({time.time() - t0:.0f}s)")

    gate_stats: dict[str, dict[str, int]] = {}
    if gate:
        articles, gate_stats = relevance_gate(articles, llm)
        if verbose:
            kept = sum(len(v) for v in articles.values())
            print(f"        relevance gate kept {kept} articles ({', '.join(cfg.GATE_KEEP)})  ({time.time() - t0:.0f}s)")

    stories = cluster_all(articles, llm, names)
    if verbose:
        multi = sum(s.sources >= cfg.TRENDING_MIN_SOURCES for s in stories)
        print(f"Step 4  grouped into {len(stories)} stories, {multi} covered by ≥{cfg.TRENDING_MIN_SOURCES} sources"
              f"  ({time.time() - t0:.0f}s)")

    # provisional scores (no materiality yet); merge duplicates among the leaders
    score_all(stories, momentum, win, include_materiality=False)
    stories.sort(key=lambda s: -s.trend if s.sources >= cfg.TRENDING_MIN_SOURCES else 1e9)
    before = len(stories)
    stories = dedupe_top(stories, llm)
    score_all(stories, momentum, win, include_materiality=False)
    if verbose and len(stories) < before:
        print(f"        merged {before - len(stories)} duplicate stories among the top {cfg.DEDUPE_TOP_K}")
    to_enrich = select_for_enrichment(stories, win, per_group=per_group)
    enrich_stories(to_enrich, llm, names, commodity=commodity)
    # stories not selected still need a title/direction for tables
    enrich_stories([s for s in stories if not s.title], None, names, commodity=commodity)
    if verbose and llm is not None:
        print(f"Step 5  enriched {len(to_enrich)} stories (title, summary, direction, driver, materiality)"
              f"  ({time.time() - t0:.0f}s)")
    elif verbose:
        extra = ", keyword-based direction" if commodity else ""
        print(f"Step 5  no LLM: story titles = best-ranked outlet's headline{extra}")

    score_all(stories, momentum, win, include_materiality=llm is not None)
    if verbose:
        print("Step 6  scored all stories")
    return stories, articles, gate_stats


def finish(client: BigdataClient, llm: LLM | None) -> None:
    """Print usage and keep the fixtures for offline REPLAY."""
    removed = cache.finalize()
    if not cfg.REPLAY:
        cache.update_run_meta(llm={"provider": llm.provider, "model": llm.model} if llm else None)
    if cfg.REPLAY:
        print("Replay run: nothing was fetched.")
        return
    llm_part = f", LLM calls {llm.calls} ({llm.seconds:.0f} s summed over parallel calls, {llm.cache_hits} from cache)" if llm else ""
    print(f"Bigdata.com query units used this session: {client.query_units:.1f}{llm_part}")
    print(f"Fixtures saved under {(cfg.FIXTURES_DIR / cache._namespace).relative_to(cfg.ROOT)}/ (removed {removed} stale files).")


# --------------------------------------------------------------------------- #
# Discovery: "also trending beyond your watchlist" (Notebook 1, optional)
# --------------------------------------------------------------------------- #
def _ticker_of(info: dict) -> str:
    for listing in info.get("listing_values") or []:
        mic, _, sym = str(listing).partition(":")
        if mic in {"XNAS", "XNYS", "ARCX", "BATS", "XASE"} and sym:
            return sym
    return ""


def discover_companies(
    client: BigdataClient, win: Window, exclude_ids: set[str], verbose: bool = True
) -> tuple[list[Entity], dict[str, Momentum]]:
    """
    Companies trending beyond the watchlist.

    1. Shortlist: /v1/search/co-mentions/entities (news, several query phrasings)
       for the current and baseline windows, ranked by
       (current+1) / (baseline_per_period+1) × log(1+current).
    2. Co-mentions only returns each window's top entities, so a company missing
       from the baseline list shows a baseline of 0. The shortlist's real
       baseline is therefore measured with /v1/search/volume (same momentum as
       the watchlist), and the final ranking is spike × log(1 + current_docs).
    Returns (entities, their momentum).
    """
    def counts(start, end) -> dict[str, int]:
        """Headline co-mention counts per company, max over the query phrasings."""
        out: dict[str, int] = {}
        for text in cfg.DISCOVERY_QUERY_TEXTS:
            res = client.co_mentions(news_filters(start, end), text)
            for c in res.get("companies", []):
                out[c["id"]] = max(out.get(c["id"], 0), int(c.get("total_headlines_count") or 0))
        return out

    c_now = counts(win.current_start, win.as_of)
    c_base = counts(win.baseline_start, win.current_start)
    scale = win.period_days / win.baseline_days
    shortlist = []
    for cid, n in c_now.items():
        if cid in exclude_ids or n < cfg.DISCOVERY_MIN_CURRENT:
            continue
        b = c_base.get(cid, 0) * scale
        shortlist.append((cid, (n + 1) / (b + 1) * math.log1p(n)))
    shortlist.sort(key=lambda x: -x[1])

    info = client.kg_by_ids([c for c, _ in shortlist[: cfg.DISCOVERY_TOP_N * 3]])
    candidates: list[Entity] = []
    for cid, _ in shortlist:
        meta = info.get(cid, {})
        name = meta.get("name", "")
        if not name or name in cfg.DISCOVERY_DENYLIST:
            continue
        key = _ticker_of(meta) or name.split(" ")[0]
        candidates.append(Entity(key=key, input=name, kind="company", id=cid, name=name,
                                 type=meta.get("type", ""), category="companies", note="discovered"))
        if len(candidates) >= cfg.DISCOVERY_TOP_N * 3:
            break

    momentum = compute_momentum(client, candidates, win)
    ranked = sorted((e for e in candidates if e.key in momentum),
                    key=lambda e: -momentum[e.key].spike * math.log1p(momentum[e.key].current_docs))
    top = ranked[: cfg.DISCOVERY_TOP_N]
    if verbose:
        print(f"   {'key':<8} {'company':<40} {'docs now':>9} {'usual':>7} {'spike':>6}")
        for e in top:
            m = momentum[e.key]
            print(f"   {e.key:<8} {e.name[:40]:<40} {m.current_docs:>9,} {m.baseline_per_period:>7,.0f} {m.spike:>5.1f}×")
    return top, {e.key: momentum[e.key] for e in top}


# --------------------------------------------------------------------------- #
# Notebook 2 lists
# --------------------------------------------------------------------------- #
def commodity_lists(
    stories: list[Story], keys: list[str], win: Window, use_llm: bool
) -> tuple[list[Story], dict[str, list[Story]], dict[str, list[Story]]]:
    """
    Returns (combined top trending, trending per commodity, emerging per commodity).
    Emerging never repeats a story shown anywhere under Trending.
    """
    from .rank import group_by_entity, rank_emerging, rank_trending

    ranked = rank_trending(stories, top_n=len(stories) or 1)
    combined = ranked[: cfg.COMBINED_TOP_N]
    groups = group_by_entity(ranked, keys, cfg.TOP_N_PER_GROUP)
    shown = combined + [s for ss in groups.values() for s in ss]
    emerging = {
        k: rank_emerging([s for s in stories if k in s.entities], shown, win, use_llm, require_direction=True)
        for k in keys
    }
    return combined, groups, emerging
