"""
Per-cell run time for the notebooks.

    from trending.timing import cell_timer
    cell_timer.start()      # from now on every cell ends with "⏱ 12.3 s"
    cell_timer.summary()    # table: time per step + total

Each cell is labelled by its first line (the notebooks start every code cell with a
"# Step …" comment). Uses IPython's pre/post_run_cell events; outside IPython
(plain Python) `start()` does nothing.
"""
from __future__ import annotations

import time

import pandas as pd


def fmt_seconds(s: float) -> str:
    """12.3 s / 2 min 05 s."""
    if s < 60:
        return f"{s:.1f} s"
    m, s = divmod(round(s), 60)
    return f"{m} min {s:02d} s"


def _label(cell: str) -> str:
    for line in cell.splitlines():
        line = line.strip()
        if line:
            return line.lstrip("#").strip()[:70]
    return "(empty cell)"


class CellTimer:
    def __init__(self) -> None:
        self.records: list[dict] = []
        self._t0: float | None = None
        self._cell = ""
        self._registered = False

    def start(self) -> None:
        try:
            ip = get_ipython()  # type: ignore[name-defined]  # noqa: F821
        except NameError:
            return
        self.records = []
        # The cell calling start() is already running: time it from here.
        self._t0 = time.perf_counter()
        self._cell = "Setup"
        if not self._registered:  # re-running the setup cell must not double-register
            ip.events.register("pre_run_cell", self._pre)
            ip.events.register("post_run_cell", self._post)
            self._registered = True

    def _pre(self, info) -> None:
        self._t0 = time.perf_counter()
        self._cell = getattr(info, "raw_cell", "") or ""

    def _post(self, result) -> None:
        if self._t0 is None:
            return
        secs = time.perf_counter() - self._t0
        self._t0 = None
        label = self._cell if self._cell == "Setup" else _label(self._cell)
        if label.startswith("Run time"):  # the summary cell itself
            return
        self.records.append({"step": label, "seconds": round(secs, 1)})
        print(f"⏱ {fmt_seconds(secs)}")

    def summary(self) -> pd.DataFrame:
        """Time per executed cell (latest run of each step) and the total."""
        df = pd.DataFrame(self.records, columns=["step", "seconds"])
        df = df.drop_duplicates("step", keep="last").reset_index(drop=True)
        total = df["seconds"].sum()
        df["share"] = (df["seconds"] / total).map("{:.0%}".format) if total else ""
        df["time"] = df["seconds"].map(fmt_seconds)
        df.loc[len(df)] = {"step": "Total", "seconds": round(total, 1), "share": "100%", "time": fmt_seconds(total)}
        return df[["step", "time", "share", "seconds"]]


cell_timer = CellTimer()
