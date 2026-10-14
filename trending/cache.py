"""
Disk cache for API and LLM responses, which doubles as the offline fixtures.

Layout (one folder per demo, so the two notebooks never clash):

    fixtures/
      entities.json              # resolved entity IDs (shared)
      <namespace>/
        run.json                 # the "as of" timestamp of the saved run
        api/<sha>.json.gz        # Bigdata.com responses (gzipped JSON)
        llm/<sha>.json.gz        # LLM responses (gzipped JSON)

Live mode: API entries are reused for CACHE_TTL_SECONDS; LLM entries are reused
forever (same prompt -> same answer, which keeps re-runs fast and cheap).
Replay mode: everything is served from disk, regardless of age; no network.

`finalize()` deletes files the current run did not use, so the committed
fixtures stay small and always match the last live run exactly.
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
    """Select the fixtures folder for this demo (e.g. 'watchlist')."""
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
    if not cfg.REPLAY and ttl is not None and time.time() - entry["saved_at"] > ttl:
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

    Live: config.AS_OF if set, else the current UTC time rounded down to
    `round_minutes`, so re-runs within the cache TTL build identical requests
    and hit the cache.
    Replay: the timestamp of the saved run, so requests match the fixtures.
    """
    run_file = _run_file()
    if cfg.REPLAY:
        if not run_file.exists():
            raise FileNotFoundError(
                f"REPLAY=True but no fixtures found at {run_file.parent}. "
                "Run the notebook once live (REPLAY=False) to record them."
            )
        return datetime.fromisoformat(json.loads(run_file.read_text())["as_of"])
    if cfg.AS_OF:
        now = datetime.fromisoformat(cfg.AS_OF.replace("Z", "+00:00")).astimezone(timezone.utc)
    else:
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        now -= timedelta(minutes=now.minute % round_minutes)
    run_file.parent.mkdir(parents=True, exist_ok=True)
    run_file.write_text(json.dumps({"as_of": now.isoformat()}, indent=1))
    return now


def _run_file() -> Path:
    return cfg.FIXTURES_DIR / _namespace / "run.json"


def run_meta() -> dict[str, Any]:
    """Metadata of the saved run (as_of, LLM model used, ...)."""
    f = _run_file()
    return json.loads(f.read_text()) if f.exists() else {}


def update_run_meta(**fields: Any) -> None:
    meta = run_meta()
    meta.update(fields)
    _run_file().write_text(json.dumps(meta, indent=1))


def finalize() -> int:
    """
    Keep only the fixtures used by this run (call at the end of a live run).
    Returns the number of stale files removed. No-op in replay mode.
    """
    if cfg.REPLAY:
        return 0
    removed = 0
    for kind in ("api", "llm"):
        for path in _dir(kind).glob("*.json*"):
            if path not in _touched:
                path.unlink()
                removed += 1
    return removed


# --------------------------------------------------------------------------- #
# Shared entity table (fixtures/entities.json)
# --------------------------------------------------------------------------- #
def _entities_file() -> Path:
    cfg.FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    return cfg.FIXTURES_DIR / "entities.json"


def load_entities() -> dict[str, Any]:
    f = _entities_file()
    return json.loads(f.read_text()) if f.exists() else {}


def save_entities(table: dict[str, Any]) -> None:
    _entities_file().write_text(json.dumps(table, indent=1, sort_keys=True, ensure_ascii=False))
