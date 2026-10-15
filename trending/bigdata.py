"""
Minimal Bigdata.com REST client.

* Auth: `X-API-KEY` header, base URL https://api.bigdata.com
* Retries: exponential backoff on HTTP 429 and 5xx (HTTP_RETRIES attempts)
* Cache: every response is stored on disk (see cache.py) and reused within
  CACHE_TTL_SECONDS
* Concurrency: `run_parallel()` runs calls with at most MAX_CONCURRENCY in
  flight and isolates failures (one bad entity never stops the run)

Endpoints used (request/response shapes verified against docs.bigdata.com):

    POST /v1/search                          articles (document + chunks)
    POST /v1/search/volume                   daily document counts + sentiment
    POST /v1/knowledge-graph/<category>      find entity IDs by name/ticker
    POST /v1/knowledge-graph/entities/id     look up entity names by ID
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable, Iterable, TypeVar

import httpx

from . import cache
from . import config as cfg

log = logging.getLogger("trending")
T = TypeVar("T")
R = TypeVar("R")


class BigdataError(RuntimeError):
    pass


def iso(ts: datetime) -> str:
    """UTC timestamp in the format the API expects (2026-01-31T12:00:00Z)."""
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def news_filters(start: datetime, end: datetime) -> dict[str, Any]:
    """Filters shared by every query: NEWS documents within [start, end]."""
    return {
        "timestamp": {"start": iso(start), "end": iso(end)},
        "document_type": {"mode": "INCLUDE", "values": [{"type": "NEWS"}]},
    }


def entity_filter(ids: list[str], search_in: str) -> dict[str, Any]:
    return {"any_of": ids, "all_of": [], "none_of": [], "search_in": search_in}


class BigdataClient:
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key if api_key is not None else cfg.BIGDATA_API_KEY
        self._http: httpx.Client | None = None
        self._lock = threading.Lock()
        self.query_units = 0.0  # usage reported by the API, for the cost printout

    # ------------------------------------------------------------------ #
    # transport
    # ------------------------------------------------------------------ #
    def _client(self) -> httpx.Client:
        with self._lock:
            return self._client_unlocked()

    def _client_unlocked(self) -> httpx.Client:
        if self._http is None:
            if not self.api_key:
                raise BigdataError(
                    "BIGDATA_API_KEY is not set. Add it to your environment or .env."
                )
            self._http = httpx.Client(
                base_url=cfg.BIGDATA_BASE_URL,
                headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
                timeout=cfg.HTTP_TIMEOUT_SECONDS,
            )
        return self._http

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST with disk cache + retries. Raises BigdataError on failure."""
        key = cache.key_of({"path": path, "body": body})
        hit = cache.get("api", key, ttl=cfg.CACHE_TTL_SECONDS)
        if hit is not None:
            return hit

        delay = 2.0
        last_err = ""
        for attempt in range(1, cfg.HTTP_RETRIES + 1):
            try:
                resp = self._client().post(path, json=body)
            except httpx.HTTPError as exc:  # network error: retry
                last_err = repr(exc)
            else:
                if resp.status_code == 200:
                    data = resp.json()
                    with self._lock:
                        self.query_units += float((data.get("usage") or {}).get("api_query_units") or 0)
                    cache.put("api", key, data, request={"path": path, "body": body})
                    return data
                last_err = f"HTTP {resp.status_code}: {resp.text[:300]}"
                if resp.status_code != 429 and resp.status_code < 500:
                    break  # client error: retrying will not help
            if attempt < cfg.HTTP_RETRIES:
                time.sleep(delay)
                delay *= 2
        raise BigdataError(f"{path} failed: {last_err}")

    # ------------------------------------------------------------------ #
    # endpoints
    # ------------------------------------------------------------------ #
    def search(
        self,
        filters: dict[str, Any],
        text: str | None = None,
        texts: list[str] | None = None,
        reranker_text: str | None = None,
        max_chunks: int | None = None,
    ) -> list[dict[str, Any]]:
        """Fast-mode search. Returns the list of documents (each with chunks)."""
        query: dict[str, Any] = {"filters": filters, "max_chunks": max_chunks or cfg.MAX_CHUNKS}
        if texts and len(texts) == 1:
            text, texts = texts[0], None
        if text:
            query["text"] = text
        if texts:
            query["texts"] = texts
            # The API requires a single reranker text when several texts are given.
            query["ranking_params"] = {"reranker": {"enabled": True, "text": reranker_text or " ".join(texts)}}
        body = {"search_mode": "fast", "query": query}
        return self.post("/v1/search", body).get("results", [])

    def volume(
        self,
        filters: dict[str, Any],
        texts: list[str] | None = None,
        group_by_source: bool = False,
    ) -> dict[str, Any]:
        """Daily volume. Returns results: {volume:[...], total:{...}, groups?:{sources:[...]}}."""
        query: dict[str, Any] = {"filters": filters}
        if texts:
            query["texts" if len(texts) > 1 else "text"] = texts if len(texts) > 1 else texts[0]
        body: dict[str, Any] = {"query": query}
        if group_by_source:
            body["group_by"] = ["source"]
        return self.post("/v1/search/volume", body).get("results", {})

    def kg_find(self, category: str, query: str, **extra: Any) -> list[dict[str, Any]]:
        """Knowledge Graph search, e.g. category='companies', query='AAPL'."""
        body = {"query": query, **extra}
        return self.post(f"/v1/knowledge-graph/{category}", body).get("results", [])

    def kg_by_ids(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        """Look up entity details (name, type, ...) by ID."""
        if not ids:
            return {}
        return self.post("/v1/knowledge-graph/entities/id", {"values": sorted(ids)}).get("results", {})


def run_parallel(
    fn: Callable[[T], R],
    items: Iterable[T],
    label: Callable[[T], str] = str,
    workers: int | None = None,
) -> dict[int, R]:
    """
    Run fn(item) for each item with bounded concurrency.
    Failures are logged and skipped (per-item failure isolation).
    Returns {index: result} for the items that succeeded.
    """
    items = list(items)
    out: dict[int, R] = {}
    with ThreadPoolExecutor(max_workers=workers or cfg.MAX_CONCURRENCY) as pool:
        futures = {pool.submit(fn, it): i for i, it in enumerate(items)}
        for fut, i in futures.items():
            try:
                out[i] = fut.result()
            except Exception as exc:  # noqa: BLE001 - isolate and continue
                log.warning("skipped %s: %s", label(items[i]), exc)
                print(f"  ! skipped {label(items[i])}: {exc}")
    return out
