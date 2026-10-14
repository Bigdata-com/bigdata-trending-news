#!/usr/bin/env python3
"""
Run both notebooks headless, e.g. every hour from cron, launchd or Windows Task Scheduler.

    .venv/bin/python scripts/run_notebooks.py                 # both notebooks
    .venv/bin/python scripts/run_notebooks.py --only commodities
    .venv/bin/python scripts/run_notebooks.py --keep-days 3   # prune older run folders

Each run writes to its own folder, so nothing is overwritten:

    outputs/runs/2026-10-13_1900Z/
        watchlist_trending_2026-10-13.html, watchlist_2026-10-13_trending.csv, ...
        01_watchlist_trending.ipynb      executed copy (with all outputs and cell timings)
        run.log                          one line per notebook: status and duration
    outputs/latest/                      newest reports under stable names, e.g. watchlist_trending.html

The committed notebooks in notebooks/ are never modified. API keys come from the
environment or .env, exactly as for interactive use. Exit code is non-zero if any
notebook fails, so a scheduler can alert on it.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import nbformat
from nbclient import NotebookClient

ROOT = Path(__file__).resolve().parent.parent
NOTEBOOKS = {
    "watchlist": ROOT / "notebooks" / "01_watchlist_trending.ipynb",
    "commodities": ROOT / "notebooks" / "02_commodities_trending.ipynb",
}
RUNS_DIR = ROOT / "outputs" / "runs"
LATEST_DIR = ROOT / "outputs" / "latest"
LOCK = RUNS_DIR / ".lock"
LOCK_STALE_AFTER = timedelta(hours=2)   # a crashed run must not block the schedule forever
CELL_TIMEOUT_SECONDS = 20 * 60


def log(msg: str, logfile: Path | None = None) -> None:
    line = f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%SZ}  {msg}"
    print(line, flush=True)
    if logfile:
        with logfile.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def acquire_lock() -> bool:
    """Skip this run if the previous one is still going (e.g. a slow LLM)."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    if LOCK.exists():
        age = datetime.now() - datetime.fromtimestamp(LOCK.stat().st_mtime)
        if age < LOCK_STALE_AFTER:
            return False
        LOCK.unlink()
    LOCK.write_text(str(os.getpid()))
    return True


def prune(keep_days: int) -> None:
    cutoff = datetime.now() - timedelta(days=keep_days)
    for d in RUNS_DIR.iterdir():
        if d.is_dir() and datetime.fromtimestamp(d.stat().st_mtime) < cutoff:
            shutil.rmtree(d)


def run_one(name: str, path: Path, out_dir: Path, logfile: Path) -> bool:
    nb = nbformat.read(path, as_version=4)
    t0 = time.time()
    try:
        # Run with notebooks/ as the working directory, as Jupyter does.
        NotebookClient(nb, timeout=CELL_TIMEOUT_SECONDS, kernel_name="python3",
                       resources={"metadata": {"path": str(path.parent)}}).execute()
        ok = True
        msg = "ok"
    except Exception as exc:  # noqa: BLE001 - log and keep the executed copy for debugging
        ok = False
        msg = f"FAILED: {type(exc).__name__}: {str(exc).strip().splitlines()[-1][:300]}"
    nbformat.write(nb, out_dir / path.name)
    log(f"{name:<12} {msg}  ({time.time() - t0:.0f} s)", logfile)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=list(NOTEBOOKS), action="append", help="run just this notebook (repeatable)")
    ap.add_argument("--keep-days", type=int, default=7, help="delete run folders older than this (default 7)")
    args = ap.parse_args()

    if not acquire_lock():
        log("previous run still in progress; skipping this one")
        return 0
    try:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%MZ")
        out_dir = RUNS_DIR / stamp
        out_dir.mkdir(parents=True, exist_ok=True)
        logfile = out_dir / "run.log"
        # trending/config.py reads OUTPUTS_DIR; the notebook kernels inherit this environment.
        os.environ["OUTPUTS_DIR"] = str(out_dir)

        results = {n: run_one(n, NOTEBOOKS[n], out_dir, logfile) for n in (args.only or NOTEBOOKS)}

        if any(results.values()):
            LATEST_DIR.mkdir(parents=True, exist_ok=True)
            for f in out_dir.iterdir():
                if f.suffix in {".html", ".csv"}:  # stable names, e.g. watchlist_trending.html
                    shutil.copy(f, LATEST_DIR / re.sub(r"_\d{4}-\d{2}-\d{2}", "", f.name))
        prune(args.keep_days)
        return 0 if all(results.values()) else 1
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
