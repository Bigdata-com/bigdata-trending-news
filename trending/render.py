"""
Step 7 - Render: a self-contained HTML report (dark theme, no external assets),
pandas DataFrames, and CSV files.

    html = report_html(...)                    # one string, open in any browser / email it
    display(HTML(html))                        # inline in the notebook
    paths = save_outputs(html, frames, "watchlist_trending")

All CSS is scoped under `.tn` so the report does not restyle the notebook.
"""
from __future__ import annotations

import html as _html
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from . import config as cfg
from .cluster import Story
from .momentum import Momentum, Window

E = _html.escape

HOW_MEASURED = (
    "Bigdata.com does not track reader clicks, so <b>trending is measured from publishing momentum</b>, "
    "a strong proxy for attention. <b>Breadth</b>: how many articles and distinct outlets cover the "
    "<i>same story</i>. <b>Acceleration</b>: how much more an entity is covered now than its normal "
    "baseline (×). <b>Recency</b>: fresher stories rank higher. Stories need at least two independent "
    "outlets to count as trending. <b>Emerging</b> lists strongly positive or negative stories from "
    "credible outlets that have not yet built wide coverage. All counts, momentum and sentiment are "
    "computed from Bigdata.com data; the AI model only groups, titles and summarises articles."
)

CSS = """
.tn{--bg:#0b0f17;--panel:#121826;--panel2:#161e2e;--line:#232d40;--ink:#e6ebf5;--muted:#8f9bb3;
--faint:#5d6880;--accent:#5b9dff;--up:#2fbf71;--down:#ef5a5a;--mixed:#e0a63a;--chip:#1f2a3f;
background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,
Helvetica,Arial,sans-serif;padding:28px 22px;border-radius:12px;max-width:1180px;margin:0 auto}
.tn *{box-sizing:border-box}
.tn a{color:var(--accent);text-decoration:none}.tn a:hover{text-decoration:underline}
.tn h1{font-size:24px;margin:0 0 4px;font-weight:650;letter-spacing:-.01em}
.tn h2{font-size:17px;margin:30px 0 10px;font-weight:650;display:flex;align-items:center;gap:10px}
.tn h2 .sub{font-size:12.5px;color:var(--muted);font-weight:400}
.tn h3{font-size:15.5px;margin:22px 0 8px;font-weight:650;display:flex;flex-wrap:wrap;align-items:center;gap:10px}
.tn .meta{color:var(--muted);font-size:12.5px;display:flex;flex-wrap:wrap;gap:6px 18px;margin-bottom:14px}
.tn .meta b{color:var(--ink);font-weight:550}
.tn .how{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--accent);
border-radius:8px;padding:12px 14px;color:#c4cde0;font-size:13px}
.tn .how b{color:var(--ink)}
.tn .strip{display:grid;grid-template-columns:repeat(auto-fill,minmax(165px,1fr));gap:8px;margin-top:6px}
.tn .card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:9px 11px}
.tn .card .k{font-weight:650;font-size:13px;display:flex;justify-content:space-between;align-items:center}
.tn .card .n{color:var(--muted);font-size:11.5px;margin-top:2px}
.tn .spk{font-weight:650;font-size:12.5px;padding:1px 7px;border-radius:10px;background:var(--chip)}
.tn .spk.hot{background:rgba(239,90,90,.16);color:#ff8a8a}.tn .spk.warm{background:rgba(224,166,58,.15);color:#f0bd5c}
.tn .spk.cool{color:var(--muted)}
.tn .row{display:grid;grid-template-columns:34px 1fr auto;gap:4px 14px;background:var(--panel);
border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:8px 0}
.tn .rk{font-size:18px;font-weight:700;color:var(--faint);text-align:right;padding-top:1px}
.tn .title{font-weight:620;font-size:15px;margin:2px 0 3px}
.tn .chips{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:3px}
.tn .chip{background:var(--chip);color:#b9c6e4;font-size:11px;font-weight:600;padding:1px 7px;border-radius:5px;letter-spacing:.02em}
.tn .tag{border:1px solid var(--line);color:#c4cde0;font-size:11px;padding:1px 7px;border-radius:5px}
.tn .sum{color:#c4cde0;font-size:13px}
.tn .rep{color:var(--muted);font-size:12.5px;margin-top:3px}
.tn .rep i{color:var(--faint);font-style:normal}
.tn .stats{text-align:right;white-space:nowrap;font-size:12.5px;color:var(--muted);display:flex;flex-direction:column;
align-items:flex-end;gap:4px}
.tn .stats .big{color:var(--ink);font-weight:600}
.tn .mom{display:flex;align-items:center;gap:7px}
.tn .dir{font-weight:700;font-size:12px;padding:1px 8px;border-radius:10px}
.tn .dir.up{color:var(--up);background:rgba(47,191,113,.13)}.tn .dir.down{color:var(--down);background:rgba(239,90,90,.13)}
.tn .dir.mixed{color:var(--mixed);background:rgba(224,166,58,.13)}
.tn .sent{display:inline-flex;align-items:center;gap:5px}
.tn .dot{width:9px;height:9px;border-radius:50%;display:inline-block}
.tn details{grid-column:2/4;margin-top:4px}
.tn summary{cursor:pointer;color:var(--accent);font-size:12.5px;list-style:none}
.tn summary::-webkit-details-marker{display:none}
.tn summary:before{content:"▸ ";color:var(--faint)}.tn details[open] summary:before{content:"▾ "}
.tn ul.members{margin:6px 0 0;padding:0;list-style:none;border-top:1px solid var(--line)}
.tn ul.members li{padding:5px 0;border-bottom:1px solid var(--line);font-size:12.5px;display:grid;
grid-template-columns:92px 150px 1fr 46px;gap:10px;align-items:baseline}
.tn ul.members .t{color:var(--faint)}.tn ul.members .s{color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tn ul.members .v{text-align:right;font-variant-numeric:tabular-nums}
.tn .emerging .row{background:transparent;border-style:dashed;padding:9px 13px}
.tn .emerging .title{font-size:14px;font-weight:580}
.tn .emerging-h{color:#c4cde0}
.tn .badge{font-size:11.5px;font-weight:650;padding:1px 7px;border-radius:5px}
.tn .group{border-top:1px solid var(--line);margin-top:26px;padding-top:4px}
.tn .note{color:var(--muted);font-size:12.5px;margin:-4px 0 8px}
.tn .empty{color:var(--faint);font-size:13px;padding:8px 2px}
.tn .foot{color:var(--faint);font-size:11.5px;margin-top:30px;border-top:1px solid var(--line);padding-top:12px}
@media (max-width:720px){.tn .row{grid-template-columns:26px 1fr}.tn .stats{grid-column:2;align-items:flex-start;text-align:left}
.tn details{grid-column:1/3}.tn ul.members li{grid-template-columns:1fr}}
"""


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def sentiment_color(v: float | None) -> str:
    if v is None:
        return "#5d6880"
    if v >= 0.35:
        return "#2fbf71"
    if v >= 0.1:
        return "#8fd19e"
    if v <= -0.35:
        return "#ef5a5a"
    if v <= -0.1:
        return "#f29b9b"
    return "#8f9bb3"


def svg_sparkline(values: list[int], w: int = 84, h: int = 22, color: str = "#5b9dff") -> str:
    """Inline SVG sparkline; the last point (current period) is highlighted."""
    if not values:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    n = len(values)
    pts = [(2 + i * (w - 4) / max(n - 1, 1), h - 3 - (v - lo) / span * (h - 6)) for i, v in enumerate(values)]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"2,{h - 1} {poly} {pts[-1][0]:.1f},{h - 1}"
    lx, ly = pts[-1]
    title = " · ".join(str(v) for v in values)
    return (
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="daily volume">'
        f"<title>documents per period: {title}</title>"
        f'<polygon points="{area}" fill="{color}" opacity=".12"/>'
        f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="1.6" stroke-linejoin="round"/>'
        f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="2.6" fill="{color}"/></svg>'
    )


def spike_class(spike: float) -> str:
    return "hot" if spike >= 2 else "warm" if spike >= 1.3 else "cool"


def momentum_text(spike: float, win: Window) -> str:
    base = "7-day avg" if win.period_days == 1 else "4-week avg"
    arrow = "↑" if spike >= 1 else "↓"
    return f"{arrow} {spike:.1f}× vs {base}"


def clip(text: str, n: int = 160) -> str:
    """Some feeds put body text in the headline field; keep displays readable."""
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def ago(ts: datetime | None, win: Window) -> str:
    if ts is None:
        return ""
    mins = max(0, int((win.as_of - ts).total_seconds() // 60))
    if mins < 60:
        return f"{mins}m ago"
    if mins < 48 * 60:
        return f"{mins // 60}h ago"
    return f"{mins // 1440}d ago"


DIR_ARROW = {"up": "▲ up", "down": "▼ down", "mixed": "◆ mixed"}


def _dir_badge(d: str) -> str:
    return f'<span class="dir {d}">{DIR_ARROW[d]}</span>' if d in DIR_ARROW else ""


def _sent_badge(v: float) -> str:
    c = sentiment_color(v)
    return f'<span class="sent" title="Bigdata.com sentiment (-1..+1)"><span class="dot" style="background:{c}"></span>{v:+.2f}</span>'


# --------------------------------------------------------------------------- #
# rows
# --------------------------------------------------------------------------- #
@dataclass
class ReportContext:
    win: Window
    momentum: dict[str, Momentum]
    show_direction: bool = False
    names: dict[str, str] = field(default_factory=dict)


def _members_html(story: Story, ctx: ReportContext) -> str:
    items = []
    for a in story.members:
        head = E(clip(a.headline, 200) or "(no headline)")
        link = f'<a href="{E(a.url)}" target="_blank" rel="noopener">{head}</a>' if a.url else head
        items.append(
            f'<li><span class="t">{a.timestamp:%b %d %H:%M}</span><span class="s" title="{E(a.source_name)}">'
            f'{E(a.source_name)}</span><span>{link}</span>'
            f'<span class="v" style="color:{sentiment_color(a.sentiment)}">{a.sentiment:+.2f}</span></li>'
        )
    label = f"Show all {story.articles} articles from {story.sources} sources"
    return f'<details><summary>{label}</summary><ul class="members">{"".join(items)}</ul></details>'


def _chips(story: Story) -> str:
    return "".join(f'<span class="chip">{E(k)}</span>' for k in story.entities)


def story_row(story: Story, rank: int, ctx: ReportContext) -> str:
    rep = story.representative
    mom = ctx.momentum.get(max(story.entities, key=lambda k: ctx.momentum[k].spike if k in ctx.momentum else 0))
    spark = svg_sparkline(mom.sparkline) if mom else ""
    tags = []
    if ctx.show_direction:
        tags.append(_dir_badge(story.direction))
    if story.driver:
        tags.append(f'<span class="tag">{E(story.driver)}</span>')
    summary = f'<div class="sum">{E(story.summary)}</div>' if story.summary else ""
    return f"""
<div class="row">
  <div class="rk">{rank}</div>
  <div>
    <div class="chips">{_chips(story)}{''.join(tags)}</div>
    <div class="title">{E(story.title or clip(rep.headline))}</div>
    {summary}
    <div class="rep">“{E(clip(rep.headline))}” — {E(rep.source_name)} <i>· {ago(rep.timestamp, ctx.win)}</i></div>
  </div>
  <div class="stats">
    <span class="big">{story.articles} articles · {story.sources} sources</span>
    <span class="mom"><span class="spk {spike_class(story.spike)}">{momentum_text(story.spike, ctx.win)}</span>{spark}</span>
    <span>{_sent_badge(story.avg_sentiment)} <i style="color:var(--faint);font-style:normal">· latest {ago(story.last_seen, ctx.win)}</i></span>
  </div>
  {_members_html(story, ctx)}
</div>"""


def emerging_row(story: Story, rank: int, ctx: ReportContext) -> str:
    rep = story.representative
    v = story.sentiment_signal
    badge = (f'<span class="badge" style="color:{sentiment_color(v)};border:1px solid {sentiment_color(v)}">'
             f'sentiment {v:+.2f}</span>')
    tags = [badge]
    if ctx.show_direction:
        tags.append(_dir_badge(story.direction))
    if story.driver:
        tags.append(f'<span class="tag">{E(story.driver)}</span>')
    src = rep.source_name if story.sources == 1 else f"{story.sources} sources"
    mat = f" · materiality {story.materiality}/5" if story.materiality else ""
    summary = f'<div class="sum">{E(story.summary)}</div>' if story.summary else ""
    return f"""
<div class="row">
  <div class="rk">{rank}</div>
  <div>
    <div class="chips">{_chips(story)}{''.join(tags)}</div>
    <div class="title">{E(story.title or clip(rep.headline))}</div>
    {summary}
    <div class="rep">“{E(clip(rep.headline))}” — {E(rep.source_name)} (tier {rep.source_rank}) <i>· {ago(rep.timestamp, ctx.win)}</i></div>
  </div>
  <div class="stats"><span class="big">{E(src)}</span><span>{story.articles} article{'s' if story.articles != 1 else ''}{mat}</span></div>
  {_members_html(story, ctx)}
</div>"""


def momentum_strip(ctx: ReportContext, keys: list[str] | None = None) -> str:
    cards = []
    items = [ctx.momentum[k] for k in (keys or list(ctx.momentum)) if k in ctx.momentum]
    for m in sorted(items, key=lambda m: -m.spike):
        cards.append(
            f'<div class="card"><div class="k"><span>{E(m.key)}</span>'
            f'<span class="spk {spike_class(m.spike)}">{m.spike:.1f}×</span></div>'
            f'{svg_sparkline(m.sparkline, w=140, h=26)}'
            f'<div class="n">{m.current_docs:,} docs now · {m.baseline_per_period:,.0f} usual · '
            f'{m.distinct_sources} sources</div></div>'
        )
    return f'<div class="strip">{"".join(cards)}</div>'


def _list(rows: list[str], empty: str) -> str:
    return "".join(rows) if rows else f'<div class="empty">{empty}</div>'


# --------------------------------------------------------------------------- #
# full reports
# --------------------------------------------------------------------------- #
def _header(title: str, scope: str, ctx: ReportContext, llm_label: str) -> str:
    return f"""
<h1>{E(title)}</h1>
<div class="meta">
  <span>Run <b>{ctx.win.as_of:%Y-%m-%d %H:%M} UTC</b></span>
  <span>Window <b>{E(ctx.win.label)}</b></span>
  <span>Scope <b>{E(scope)}</b></span>
  <span>Story grouping <b>{E(llm_label)}</b></span>
  <span>Emerging sentiment <b>{E(cfg.SENTIMENT_MODE)} ≥ {cfg.SENTIMENT_EXTREME}</b></span>
</div>
<div class="how"><b>How trending is measured.</b> {HOW_MEASURED}</div>"""


def _footer() -> str:
    return ('<div class="foot">Data: <a href="https://bigdata.com" target="_blank" rel="noopener">Bigdata.com</a> '
            "(/v1/search, /v1/search/volume, /v1/search/co-mentions/entities, Knowledge Graph). "
            "Article counts come from search results, which de-duplicate similar passages, so they understate "
            "total coverage; entity momentum (×) comes from the volume endpoint. Times are UTC.</div>")


def _wrap(title: str, body: str) -> str:
    return f'<div class="tn"><style>{CSS}</style>{body}</div>'


def watchlist_report(
    *, title: str, scope: str, ctx: ReportContext, trending: list[Story], emerging: list[Story],
    llm_label: str, discovery: dict[str, Any] | None = None,
) -> str:
    parts = [_header(title, scope, ctx, llm_label)]
    parts.append('<h2>Momentum <span class="sub">which names are unusually loud right now (× = coverage vs normal)</span></h2>')
    parts.append(momentum_strip(ctx))
    parts.append(f'<h2>Trending stories <span class="sub">wide coverage right now · top {len(trending)}</span></h2>')
    parts.append(_list([story_row(s, i, ctx) for i, s in enumerate(trending, 1)], "No multi-source stories in this window."))
    parts.append('<div class="emerging"><h2 class="emerging-h">Emerging: strong sentiment, limited coverage so far '
                 '<span class="sub">early signals from credible outlets, not (yet) widely covered</span></h2>')
    parts.append(_list([emerging_row(s, i, ctx) for i, s in enumerate(emerging, 1)],
                       "No stories met the Emerging thresholds in this window.") + "</div>")
    if discovery:
        dctx: ReportContext = discovery["ctx"]
        parts.append('<div class="group"><h2>Also trending beyond your watchlist '
                     '<span class="sub">companies with the sharpest jump in co-mentions in the news</span></h2>')
        parts.append(momentum_strip(dctx))
        parts.append(_list([story_row(s, i, dctx) for i, s in enumerate(discovery["trending"], 1)],
                           "No multi-source stories found.") + "</div>")
    parts.append(_footer())
    return _wrap(title, "".join(parts))


def commodities_report(
    *, title: str, scope: str, ctx: ReportContext, combined: list[Story], groups: dict[str, list[Story]],
    emerging_by_group: dict[str, list[Story]], llm_label: str, gate_note: str = "",
) -> str:
    from .rank import net_direction

    parts = [_header(title, scope, ctx, llm_label)]
    if gate_note:
        parts.append(f'<div class="note" style="margin-top:10px">{E(gate_note)}</div>')
    parts.append('<h2>Momentum <span class="sub">which commodities are unusually loud right now</span></h2>')
    parts.append(momentum_strip(ctx, list(groups)))
    parts.append(f'<h2>Top trending across commodities <span class="sub">top {len(combined)}</span></h2>')
    parts.append(_list([story_row(s, i, ctx) for i, s in enumerate(combined, 1)], "No multi-source stories in this window."))
    for key, stories in groups.items():
        m = ctx.momentum.get(key)
        spk = f'<span class="spk {spike_class(m.spike)}">{momentum_text(m.spike, ctx.win)}</span>' if m else ""
        net = net_direction(stories)
        cls = {"mostly up": "up", "mostly down": "down"}.get(net, "mixed")
        sym = {"up": "▲", "down": "▼"}.get(cls, "◆")
        parts.append(f'<div class="group"><h3>{E(key)} {spk}<span class="dir {cls}" title="net direction of its '
                     f'trending stories">{sym} {net}</span><span class="sub" style="color:var(--muted);'
                     f'font-weight:400;font-size:12.5px">{len(stories)} trending stories</span></h3>')
        parts.append(_list([story_row(s, i, ctx) for i, s in enumerate(stories, 1)], "No multi-source stories."))
        em = emerging_by_group.get(key, [])
        parts.append(f'<div class="emerging"><div class="note" style="margin-top:12px"><b>Emerging</b> · strong '
                     f'sentiment, limited coverage so far ({len(em)})</div>')
        parts.append(_list([emerging_row(s, i, ctx) for i, s in enumerate(em, 1)], "None in this window.") + "</div></div>")
    parts.append(_footer())
    return _wrap(title, "".join(parts))


# --------------------------------------------------------------------------- #
# DataFrames, CSV, files
# --------------------------------------------------------------------------- #
def stories_frame(stories: list[Story], kind: str = "trending") -> pd.DataFrame:
    rows = []
    for i, s in enumerate(stories, 1):
        rep = s.representative
        row = {
            "rank": i,
            "story_id": s.sid,
            "title": s.title or rep.headline,
            "entities": ", ".join(s.entities),
            "articles": s.articles,
            "sources": s.sources,
            "direction": s.direction,
            "driver": s.driver,
            "materiality": s.materiality,
            "spike": s.spike,
            "trend_score": s.trend,
            "avg_sentiment": s.avg_sentiment,
            "sentiment_signal": s.sentiment_signal,
            "first_seen_utc": s.first_seen.strftime("%Y-%m-%d %H:%M") if s.first_seen else "",
            "last_seen_utc": s.last_seen.strftime("%Y-%m-%d %H:%M") if s.last_seen else "",
            "summary": s.summary,
            "representative_headline": rep.headline,
            "representative_source": rep.source_name,
            "representative_url": rep.url,
            "source_names": "; ".join(s.source_names),
        }
        if kind == "emerging":
            row["emerging_score"] = s.emerging
            row["best_source_rank"] = s.best_source_rank
        rows.append(row)
    return pd.DataFrame(rows)


def full_document(fragment: str, title: str) -> str:
    """Wrap the report fragment into a standalone HTML file."""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{E(title)}</title>'
            f'<style>html,body{{margin:0;background:#0b0f17}}body{{padding:18px 0}}</style></head>'
            f'<body>{fragment}</body></html>')


def save_outputs(fragment: str, title: str, frames: dict[str, pd.DataFrame], prefix: str,
                 day: datetime | None = None) -> dict[str, Path]:
    """Write <prefix>_trending_<date>.html plus <prefix>_<date>_<name>.csv for each frame."""
    cfg.OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = (day or datetime.now()).strftime("%Y-%m-%d")
    paths = {"html": cfg.OUTPUTS_DIR / f"{prefix}_trending_{stamp}.html"}
    paths["html"].write_text(full_document(fragment, title), encoding="utf-8")
    for name, df in frames.items():
        p = cfg.OUTPUTS_DIR / f"{prefix}_{stamp}_{name}.csv"
        df.to_csv(p, index=False)
        paths[name] = p
    return paths
