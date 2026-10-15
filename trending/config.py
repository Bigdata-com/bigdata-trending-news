"""
All tunable settings for the Trending News demo, in one place.

Notebooks override any of these in their first code cell, e.g.

    from trending import config as cfg
    cfg.WINDOW = "1W"
    cfg.USE_LLM = False

Every module reads `cfg.<NAME>` at call time (never copies values at import),
so overrides made in a notebook take effect immediately.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# --------------------------------------------------------------------------- #
# Paths and environment
# --------------------------------------------------------------------------- #
ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "fixtures"
load_dotenv(ROOT / ".env")  # optional local .env; real env vars take precedence

# Where reports and CSVs are written. The scheduled runner (scripts/run_notebooks.py)
# points this at outputs/runs/<timestamp>/ so hourly runs never overwrite each other.
OUTPUTS_DIR = Path(os.getenv("OUTPUTS_DIR") or ROOT / "outputs")

BIGDATA_BASE_URL = "https://api.bigdata.com"
BIGDATA_API_KEY = os.getenv("BIGDATA_API_KEY", "")

# --------------------------------------------------------------------------- #
# Run mode
# --------------------------------------------------------------------------- #
USE_LLM: bool = True
# Fix the run's reference time (UTC ISO string, e.g. "2026-10-13T18:40:00Z").
# None = now. Useful to reproduce a run or look back at a past day.
AS_OF: str | None = os.getenv("AS_OF") or None

# API responses are cached on disk. Within this TTL a re-run reuses them.
CACHE_TTL_SECONDS = 10 * 60
# Max parallel HTTP calls to Bigdata.com.
MAX_CONCURRENCY = 5
HTTP_TIMEOUT_SECONDS = 60
HTTP_RETRIES = 3

# --------------------------------------------------------------------------- #
# Time windows
# --------------------------------------------------------------------------- #
#   current_hours  : the "now" window we look for trending stories in
#   baseline_days  : the "normal" period that "now" is compared against
#   period_days    : length of one window, used to scale the baseline
#   recency_hours  : H in recency = exp(-hours_since_last_article / H)
WINDOWS = {
    "1D": {"current_hours": 24, "baseline_days": 7, "period_days": 1, "recency_hours": 12},
    "1W": {"current_hours": 24 * 7, "baseline_days": 28, "period_days": 7, "recency_hours": 72},
}
WINDOW = "1D"

# --------------------------------------------------------------------------- #
# Retrieval (Bigdata.com search)
# --------------------------------------------------------------------------- #
# Where the entity must be detected. "HEADLINE" = the article is *about* the
# entity (higher precision). "ALL" = mentioned anywhere (higher recall).
SEARCH_IN = "HEADLINE"
MOMENTUM_SEARCH_IN = "HEADLINE"
# Max chunks per search call (API maximum is 1000). Each call costs query units
# roughly in proportion to chunks returned; lower this to reduce cost.
MAX_CHUNKS = 1000
# Split the current window into N equal time slices and search each one.
# More slices = more articles retrieved (better story counts) at higher cost.
FETCH_SLICES = 1

# --------------------------------------------------------------------------- #
# LLM
# --------------------------------------------------------------------------- #
# OpenAI model. Override with LLM_MODEL; the notebooks set gpt-5.6.
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-5.6")
LLM_CONCURRENCY = 8
CLUSTER_BATCH_SIZE = 120     # articles per clustering call
GATE_BATCH_SIZE = 100        # articles per relevance-gate call
ENRICH_BATCH_SIZE = 8        # stories per enrichment call
ENRICH_TOP_N = 30            # top clusters (provisional score) sent for enrichment
EMERGING_ENRICH_MAX = 30     # extra Emerging candidates sent for enrichment

# --------------------------------------------------------------------------- #
# Clustering (lexical fallback when the LLM is off)
# --------------------------------------------------------------------------- #
LEXICAL_JACCARD = 0.6        # headline token-set similarity to join a story
LABEL_MERGE_JACCARD = 0.5    # label similarity to merge stories across LLM batches
# Stories found for different entities merge only if they share at least this
# share of the smaller story's articles (stops round-ups chaining stories together).
CROSS_ENTITY_OVERLAP = 0.5
# A story carries an entity's chip only if the entity is found in at least this
# share of its articles (the most frequent entity is always kept).
ENTITY_MIN_SHARE = 0.3
# Final de-duplication pass over the top-K candidate stories (by provisional score).
DEDUPE_TOP_K = 60

# --------------------------------------------------------------------------- #
# Ranking: Trending
# --------------------------------------------------------------------------- #
WEIGHTS = {
    "articles": 0.30,     # log(1 + articles in the story)
    "sources": 0.25,      # log(1 + distinct outlets covering the story)
    "spike": 0.25,        # log(entity coverage now / its normal baseline)
    "recency": 0.10,      # exp(-hours since latest article / H)
    "materiality": 0.10,  # LLM materiality 1-5, scaled to 0-1 (0 if LLM off)
}
TRENDING_MIN_SOURCES = 2     # a single outlet is not a trend
TRENDING_TOP_N = 20
TOP_N_PER_GROUP = 5          # Notebook 2: trending stories shown per commodity
COMBINED_TOP_N = 10          # Notebook 2: "Top trending across commodities"

# --------------------------------------------------------------------------- #
# Ranking: Emerging (strong sentiment, limited coverage so far)
# --------------------------------------------------------------------------- #
SENTIMENT_EXTREME = 0.5          # |sentiment| needed to qualify
SENTIMENT_MODE = "mean"          # "mean" or "max_abs" (see README, Limitations)
EMERGING_MAX_SOURCE_RANK = 2     # single-source stories need a rank 1-2 outlet
EMERGING_MIN_MATERIALITY = 4     # only applied when the LLM is on
EMERGING_DEFAULT_MATERIALITY = 3 # used in the score when the LLM is off
EMERGING_TOP_N = 10

# --------------------------------------------------------------------------- #
# Entities
# --------------------------------------------------------------------------- #
# Known Bigdata.com company IDs, used as defaults and to validate KG resolution.
KNOWN_COMPANY_IDS = {
    "AAPL": "D8442A", "MSFT": "228D42", "GOOGL": "4A6F00", "AMZN": "0157B1",
    "META": "12E454", "NVDA": "E09E2B", "TSLA": "DD3BB1",
}

WATCHLIST = ["AAPL", "TSLA", "NVDA", "MSFT", "AMZN", "META", "GOOGL"]
ENTITIES_OF_INTEREST = ["OpenAI", "Federal Reserve"]
# Knowledge Graph categories searched for free-text entities, in priority order.
FREE_TEXT_CATEGORIES = ["companies", "organizations", "people", "concepts", "products", "places"]

COMMODITIES = {
    "Gold":        {"kg_query": "gold",        "texts": ["gold price rises", "gold price falls", "gold rally bullion demand"]},
    "Silver":      {"kg_query": "silver",      "texts": ["silver price rises", "silver price falls", "silver industrial demand"]},
    "Copper":      {"kg_query": "copper",      "texts": ["copper price LME COMEX", "copper supply demand China"]},
    "Crude Oil":   {"kg_query": "crude oil",   "texts": ["oil price Brent WTI", "OPEC output"]},
    "Natural Gas": {"kg_query": "natural gas", "texts": ["natural gas price Henry Hub TTF", "LNG supply"]},
    "Platinum":    {"kg_query": "platinum",    "texts": ["platinum price", "PGM supply"]},
    "Lithium":     {"kg_query": "lithium",     "texts": ["lithium carbonate price", "battery metals demand"]},
}
COMMODITY_SELECTION = ["Gold", "Silver", "Copper", "Crude Oil"]
# KG concept types accepted as a commodity match (checked in this order).
COMMODITY_KG_TYPES = ["Commodity"]

# Relevance gate (Notebook 2): which article classes to keep.
GATE_KEEP = ["market_move"]


def window() -> dict:
    """Settings of the active WINDOW."""
    if WINDOW not in WINDOWS:
        raise ValueError(f"WINDOW must be one of {list(WINDOWS)}, got {WINDOW!r}")
    return WINDOWS[WINDOW]
