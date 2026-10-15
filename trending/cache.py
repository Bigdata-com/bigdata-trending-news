"""
Local disk cache for API and LLM responses. Not committed; see .gitignore.

Layout (one folder per demo, so the two notebooks never clash):

    fixtures/
      entities.json              # resolved entity IDs (shared)
      <namespace>/
        api/<sha>.json.gz        # Bigdata.com responses (gzipped JSON)
        llm/<sha>.json.gz        # LLM responses (gzipped JSON)

API entries are reused for CACHE_TTL_SECONDS. LLM entries are reused until
the cache is cleared (same prompt -> same answer, which keeps re-runs cheap).

`finalize()` deletes files the current run did not use.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import config as cfg

_namespace = "default"
_touched: set[Path] = set()


def set_namespace(name: str) -> None:
    """Select the cache folder for this demo (e.g. 'watchlist')."""
    global _namespace
    _namespace = name
    _touched.clear()


def _dir(kind: str) -> Path:
    d = cfg.FIXTURES_DIR / _namespace / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


def key_of(obj: Any) -> str:
    """Stable hash of any JSON-serialisable object."""
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def get(kind: str, key: str, ttl: float | None) -> Any | None:
    """Return a cached value, or None. ttl=None means 'never expires'."""
    path = _dir(kind) / f"{key}.json.gz"
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        entry = json.load(fh)
    if ttl is not None and time.time() - entry["saved_at"] > ttl:
        return None
    _touched.add(path)
    return entry["value"]


def put(kind: str, key: str, value: Any, request: Any = None) -> None:
    path = _dir(kind) / f"{key}.json.gz"
    entry = {"saved_at": time.time(), "request": request, "value": value}
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(entry, fh, ensure_ascii=False, default=str)
    _touched.add(path)


def run_clock(round_minutes: int = 10) -> datetime:
    """
    The reference "now" for this run.

    config.AS_OF if set, else the current UTC time rounded down to
    `round_minutes`, so re-runs within the cache TTL build identical requests
    and hit the cache.
    """
    if cfg.AS_OF:
        return datetime.fromisoformat(cfg.AS_OF.replace("Z", "+00:00")).astimezone(timezone.utc)
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    return now - timedelta(minutes=now.minute % round_minutes)


def finalize() -> int:
    """
    Keep only the cache files used by this run.
    Returns the number of stale files removed.
    """
    removed = 0
    for kind in ("api", "llm"):
        for path in _dir(kind).glob("*.json*"):
            if path not in _touched:
                path.unlink()
                removed += 1
    return removed


# --------------------------------------------------------------------------- #
# Shared entity table (local fixtures/entities.json)
# --------------------------------------------------------------------------- #
def _entities_file() -> Path:
    cfg.FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    return cfg.FIXTURES_DIR / "entities.json"


def load_entities() -> dict[str, Any]:
    f = _entities_file()
    return json.loads(f.read_text()) if f.exists() else {}


def save_entities(table: dict[str, Any]) -> None:
    _entities_file().write_text(json.dumps(table, indent=1, sort_keys=True, ensure_ascii=False))
