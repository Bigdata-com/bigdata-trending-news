"""
Step 7 - Render: a self-contained HTML report (dark theme, no external assets),
pandas DataFrames, CSV files, and the commodities Excel workbook.

    html = report_html(...)                    # one string, open in any browser / email it
    display(HTML(html))                        # inline in the notebook
    paths = save_outputs(html, frames, "watchlist_trending")
    save_commodity_excel(...)                  # Trending, Breaking, and Audit sheets
    save_watchlist_excel(...)                  # same sheets for the watchlist notebook

All CSS is scoped under `.tn` so the report does not restyle the notebook.
"""
from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from openpyxl.worksheet.worksheet import Worksheet

from . import config as cfg
from .cluster import Story
from .fetch import Article, within
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

# Inline styles GitHub keeps after it strips <style>. Jupyter still uses the stylesheet above
# for layout (grid/flex); these properties keep the report readable either way.
_GH_CLASS_CSS: dict[str, str] = {
    "tn": "background:#0b0f17;color:#e6ebf5;font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.45;padding:28px 22px;border-radius:12px;max-width:1180px;",
    "meta": "color:#8f9bb3;font-size:12.5px;margin:0 0 14px;",
    "how": "background:#121826;border:1px solid #232d40;border-left:3px solid #5b9dff;border-radius:8px;padding:12px 14px;color:#c4cde0;font-size:13px;margin:8px 0 14px;",
    "card": "background:#121826;border:1px solid #232d40;border-radius:8px;padding:9px 11px;margin:0 8px 8px 0;display:inline-block;vertical-align:top;",
    "k": "font-weight:700;font-size:13px;color:#e6ebf5;",
    "n": "color:#8f9bb3;font-size:11.5px;margin-top:2px;",
    "spk": "font-weight:700;font-size:12.5px;padding:1px 7px;border-radius:10px;background:#1f2a3f;color:#e6ebf5;",
    "hot": "background:#3a1c1c;color:#ff8a8a;",
    "warm": "background:#3a3018;color:#f0bd5c;",
    "cool": "background:#1f2a3f;color:#8f9bb3;",
    "row": "background:#121826;border:1px solid #232d40;border-radius:10px;padding:12px 14px;margin:8px 0;",
    "rk": "font-size:18px;font-weight:700;color:#5d6880;",
    "title": "font-weight:650;font-size:15px;margin:2px 0 3px;color:#e6ebf5;",
    "chips": "margin:0 0 3px;",
    "chip": "background:#1f2a3f;color:#b9c6e4;font-size:11px;font-weight:600;padding:1px 7px;border-radius:5px;margin-right:5px;",
    "tag": "border:1px solid #232d40;color:#c4cde0;font-size:11px;padding:1px 7px;border-radius:5px;margin-right:5px;",
    "sum": "color:#c4cde0;font-size:13px;margin:2px 0;",
    "rep": "color:#8f9bb3;font-size:12.5px;margin-top:3px;",
    "stats": "font-size:12.5px;color:#8f9bb3;margin-top:6px;",
    "big": "color:#e6ebf5;font-weight:600;",
    "dir": "font-weight:700;font-size:12px;padding:1px 8px;border-radius:10px;",
    "up": "color:#2fbf71;background:#163528;",
    "down": "color:#ef5a5a;background:#3a1c1c;",
    "mixed": "color:#e0a63a;background:#3a3018;",
    "sub": "font-size:12.5px;color:#8f9bb3;font-weight:400;",
    "emerging-h": "color:#c4cde0;font-size:16px;",
    "note": "color:#8f9bb3;font-size:12.5px;margin:4px 0 8px;",
    "empty": "color:#5d6880;font-size:13px;padding:8px 2px;",
    "foot": "color:#5d6880;font-size:11.5px;margin-top:30px;border-top:1px solid #232d40;padding-top:12px;",
    "group": "border-top:1px solid #232d40;margin-top:26px;padding-top:4px;",
    "badge": "font-size:11.5px;font-weight:700;padding:1px 7px;border-radius:5px;",
    "members": "margin:6px 0 0;padding:0;list-style:none;border-top:1px solid #232d40;",
    "t": "color:#5d6880;",
    "s": "color:#8f9bb3;",
}
_GH_TAG_CSS: dict[str, str] = {
    "h1": "font-size:24px;margin:0 0 4px;font-weight:700;color:#e6ebf5;",
    "h2": "font-size:17px;margin:28px 0 10px;font-weight:700;color:#e6ebf5;",
    "h3": "font-size:15.5px;margin:18px 0 8px;font-weight:700;color:#e6ebf5;",
    "a": "color:#5b9dff;",
    "summary": "color:#5b9dff;font-size:12.5px;",
    "li": "padding:4px 0;border-bottom:1px solid #232d40;font-size:12.5px;color:#e6ebf5;",
}
_GH_VARS: dict[str, str] = {
    "--bg": "#0b0f17", "--panel": "#121826", "--line": "#232d40", "--ink": "#e6ebf5",
    "--muted": "#8f9bb3", "--faint": "#5d6880", "--accent": "#5b9dff", "--up": "#2fbf71",
    "--down": "#ef5a5a", "--mixed": "#e0a63a", "--chip": "#1f2a3f",
}
_GH_OPEN_TAG = re.compile(r"<([A-Za-z][\w:-]*)([^<>]*)>")


def _gh_class_css(classes: list[str]) -> str:
    """Safe inline CSS for a class list. Later classes override earlier ones."""
    return "".join(_GH_CLASS_CSS[name] for name in classes if name in _GH_CLASS_CSS)


def _gh_resolve_vars(style: str) -> str:
    return re.sub(r"var\((--[\w-]+)\)", lambda match: _GH_VARS.get(match.group(1), match.group(0)), style)


def github_html(html: str) -> str:
    """Add inline styles so the report still renders after GitHub removes <style> blocks.

    The <style> block stays, so Jupyter keeps the grid layout. Inline color, type, and
    spacing are what GitHub's notebook viewer is willing to show.
    """
    if 'data-gh="1"' in html or 'class="tn"' not in html:
        return html

    def rewrite(match: re.Match[str]) -> str:
        tag = match.group(1)
        attrs = match.group(2)
        classes = re.search(r'\bclass="([^"]*)"', attrs)
        names = classes.group(1).split() if classes else []
        extra = _GH_TAG_CSS.get(tag.lower(), "") + _gh_class_css(names)
        if "tn" in names and "data-gh=" not in attrs:
            attrs += ' data-gh="1"'
        if extra:
            if 'style="' in attrs:
                attrs = attrs.replace('style="', f'style="{extra}', 1)
            else:
                attrs += f' style="{extra}"'
        if 'style="' in attrs:
            attrs = re.sub(r'style="([^"]*)"', lambda found: f'style="{_gh_resolve_vars(found.group(1))}"', attrs, count=1)
        return f"<{tag}{attrs}>"

    return _GH_OPEN_TAG.sub(rewrite, html)


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
            "(/v1/search, /v1/search/volume, Knowledge Graph). "
            "Article counts come from search results, which de-duplicate similar passages, so they understate "
            "total coverage; entity momentum (×) comes from the volume endpoint. Times are UTC.</div>")


def _wrap(title: str, body: str) -> str:
    """Report fragment. Inline styles are for GitHub; the <style> block is for Jupyter and the saved file."""
    return github_html(f'<div class="tn"><style>{CSS}</style>{body}</div>')


def watchlist_report(
    *, title: str, scope: str, ctx: ReportContext, trending: list[Story], emerging: list[Story],
    llm_label: str,
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
_STORY_COLUMNS: tuple[str, ...] = (
    "rank",
    "story_id",
    "title",
    "entities",
    "articles",
    "sources",
    "direction",
    "driver",
    "materiality",
    "spike",
    "trend_score",
    "avg_sentiment",
    "sentiment_signal",
    "first_seen_utc",
    "last_seen_utc",
    "summary",
    "representative_headline",
    "representative_source",
    "representative_url",
    "source_names",
)
_EMERGING_COLUMNS: tuple[str, ...] = ("emerging_score", "best_source_rank")


def stories_frame(stories: list[Story], kind: str = "trending") -> pd.DataFrame:
    """One row per story. An empty list still returns the column schema."""
    rows: list[dict[str, object]] = []
    for i, s in enumerate(stories, 1):
        rep = s.representative
        row: dict[str, object] = {
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
    columns: list[str] = list(_STORY_COLUMNS)
    if kind == "emerging":
        columns.extend(_EMERGING_COLUMNS)
    return pd.DataFrame(rows, columns=columns)


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


# --------------------------------------------------------------------------- #
# Commodities Excel workbook: Trending, Breaking, Audit
# --------------------------------------------------------------------------- #
_XL_FONT = "Arial"
_XL_HEADER_FILL = "1B3A4B"
_XL_ZEBRA = "F4F7FB"
_XL_TOTAL_FILL = "E8EEF4"
_XL_NOTE = "5C6B7A"
_XL_LINK = "1B4F8A"
_XL_LINE = "E2E8F0"
_XL_UP = "1B7F4E"
_XL_DOWN = "B42318"
_XL_MIXED = "8A6116"
_XL_DECISION_FILL = {"Trending": "DBEAFE", "Breaking": "FEF3C7", "Not listed": "F3F4F6"}

_TRENDING_COLUMNS: tuple[str, ...] = (
    "rank", "commodity", "combined_rank", "title", "direction", "driver",
    "articles", "sources", "spike", "materiality", "avg_sentiment", "trend_score",
    "summary", "representative_headline", "representative_source", "representative_url",
    "first_seen_utc", "last_seen_utc", "entities", "source_names", "story_id",
)
_LIST_TRENDING_COLUMNS: tuple[str, ...] = (
    "rank", "title", "entities", "direction", "driver",
    "articles", "sources", "spike", "materiality", "avg_sentiment", "trend_score",
    "summary", "representative_headline", "representative_source", "representative_url",
    "first_seen_utc", "last_seen_utc", "source_names", "story_id", "sentiment_signal",
)
_BREAKING_COLUMNS: tuple[str, ...] = (
    "rank", "entities", "title", "direction", "sentiment_signal", "sources",
    "best_source_rank", "representative_source", "driver", "materiality", "breaking_score",
    "articles", "summary", "representative_headline", "representative_url",
    "first_seen_utc", "last_seen_utc", "source_names", "story_id",
)
_AUDIT_COLUMNS: tuple[str, ...] = (
    "decision", "reason", "entities", "title", "direction", "articles", "sources",
    "best_source_rank", "sentiment_signal", "materiality", "breaking_score", "trend_score",
    "spike", "recency", "driver", "summary", "representative_headline",
    "representative_source", "representative_url", "first_seen_utc", "last_seen_utc",
    "source_names", "story_id",
)
_XL_INT = {
    "rank", "combined_rank", "articles", "sources", "materiality", "best_source_rank",
}
_XL_FLOAT2 = {"spike", "avg_sentiment", "sentiment_signal", "recency"}
_XL_FLOAT4 = {"trend_score", "breaking_score"}
_XL_WRAP = {"title", "summary", "reason", "representative_headline", "source_names"}
_XL_WIDTH = {
    "rank": 8, "commodity": 16, "combined_rank": 16, "title": 46, "direction": 12,
    "driver": 26, "articles": 12, "sources": 12, "spike": 10, "materiality": 13,
    "avg_sentiment": 15, "trend_score": 13, "summary": 55, "representative_headline": 42,
    "representative_source": 28, "representative_url": 36, "first_seen_utc": 18,
    "last_seen_utc": 18, "entities": 18, "source_names": 36, "story_id": 22,
    "sentiment_signal": 16, "best_source_rank": 18, "breaking_score": 15, "decision": 14,
    "reason": 64, "recency": 12,
}
_GATE_LABEL_ORDER = ("market_move", "company_specific", "irrelevant", "unlabeled")
_XL_COMMENTS = {
    "combined_rank": "Rank on the cross-commodity Trending list. Blank when the story is outside that top list.",
    "decision": (
        "Trending: wide coverage shown in the report. Breaking: the Emerging list. "
        "Not listed: scored, then left off both lists."
    ),
}


def _blank(value: object) -> bool:
    """True for None and pandas/numpy missing values. False for False and 0."""
    if value is None or isinstance(value, (str, bytes, datetime, bool)):
        return value is None
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _excel_value(value: object) -> object:
    """Convert pandas/numpy scalars to plain Python values openpyxl can store."""
    if _blank(value):
        return None
    item = getattr(value, "item", None)
    if callable(item) and not isinstance(value, (str, bytes, datetime)):
        try:
            return item()
        except (ValueError, AttributeError):
            return value
    return value


def _order_frame(frame: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    """Keep `columns` in order, adding any that the frame does not have yet."""
    ordered = frame.copy()
    for column in columns:
        if column not in ordered.columns:
            ordered[column] = pd.NA
    return ordered.loc[:, list(columns)]


def _breaking_score(story: Story, use_llm: bool) -> float:
    """Emerging score. Reuse the value ranking already stored, otherwise the same formula."""
    if story.emerging:
        return float(story.emerging)
    materiality = story.materiality if story.materiality is not None else cfg.EMERGING_DEFAULT_MATERIALITY
    if not use_llm:
        materiality = cfg.EMERGING_DEFAULT_MATERIALITY
    return round(abs(story.sentiment_signal) * (materiality / 5) * story.recency, 4)


def _where_shown(
    story: Story,
    combined_rank: dict[str, int],
    by_group: dict[str, list[Story]],
    *,
    grouped: bool,
) -> str:
    """Where a trending story appears in the report."""
    rank = combined_rank.get(story.sid)
    if not grouped:
        return f"trending rank {rank}" if rank is not None else "Trending list"
    parts: list[str] = []
    if rank is not None:
        parts.append(f"cross-commodity rank {rank}")
    groups = [key for key, group in by_group.items() if any(item.sid == story.sid for item in group)]
    if groups:
        parts.append("shown under " + ", ".join(groups))
    return "; ".join(parts)


def _breaking_gaps(story: Story, win: Window, use_llm: bool, *, require_direction: bool) -> list[str]:
    """Breaking rules this story does not meet. Empty when it clears every rule."""
    gaps: list[str] = []
    if abs(story.sentiment_signal) < cfg.SENTIMENT_EXTREME:
        gaps.append(f"|sentiment| {abs(story.sentiment_signal):.2f} is below {cfg.SENTIMENT_EXTREME}")
    if story.sources < 2 and story.best_source_rank > cfg.EMERGING_MAX_SOURCE_RANK:
        gaps.append(f"outlet rank {story.best_source_rank} is outside the top {cfg.EMERGING_MAX_SOURCE_RANK}")
    if story.last_seen is None or not within(story.last_seen, win):
        gaps.append("latest article is outside the current window")
    if use_llm and (story.materiality or 0) < cfg.EMERGING_MIN_MATERIALITY:
        shown = story.materiality if story.materiality is not None else "n/a"
        gaps.append(f"materiality {shown} is below {cfg.EMERGING_MIN_MATERIALITY}")
    if require_direction and story.direction not in {"up", "down", "mixed"}:
        gaps.append(f"direction is {story.direction or 'blank'}")
    return gaps


def _not_listed_reason(
    story: Story, win: Window, use_llm: bool, *, grouped: bool, require_direction: bool,
) -> str:
    """Why a story is on neither list."""
    gaps = _breaking_gaps(story, win, use_llm, require_direction=require_direction)
    emerging_cutoff = f"the top {cfg.EMERGING_TOP_N}" + (" per commodity" if grouped else "")
    if story.sources >= cfg.TRENDING_MIN_SOURCES:
        if grouped:
            base = (
                f"{story.sources} outlets, trend score {story.trend:.4f}; outside the top "
                f"{cfg.COMBINED_TOP_N} overall and the top {cfg.TOP_N_PER_GROUP} per commodity"
            )
        else:
            base = (
                f"{story.sources} outlets, trend score {story.trend:.4f}; "
                f"outside the top {cfg.TRENDING_TOP_N}"
            )
        if gaps:
            return f"{base}. Off Breaking because {'; '.join(gaps)}"
        return f"{base}. Cleared the Breaking checks, then fell outside {emerging_cutoff}"
    if gaps:
        return f"One outlet. {'; '.join(gaps)}"
    return f"One outlet. Qualified as Breaking, then fell outside {emerging_cutoff}"


def _listing(
    story: Story,
    trending_ids: set[str],
    breaking_ids: set[str],
    combined_rank: dict[str, int],
    by_group: dict[str, list[Story]],
    win: Window,
    use_llm: bool,
    *,
    grouped: bool,
    require_direction: bool,
) -> tuple[str, str]:
    """Sheet decision and the reason a reader can check against the thresholds."""
    if story.sid in trending_ids:
        return "Trending", _where_shown(story, combined_rank, by_group, grouped=grouped) or "Trending list"
    if story.sid in breaking_ids:
        return (
            "Breaking",
            f"|sentiment| {abs(story.sentiment_signal):.2f}, materiality {story.materiality}, "
            f"{story.sources} source(s), best outlet rank {story.best_source_rank}",
        )
    return "Not listed", _not_listed_reason(
        story, win, use_llm, grouped=grouped, require_direction=require_direction,
    )


def _header_comment(column: str) -> str | None:
    """Comment text for a calculated or easy-to-misread column."""
    if column == "trend_score":
        weights = cfg.WEIGHTS
        return (
            "Computed in this run from Bigdata.com counts. "
            f"trend = {weights['articles']}·log(1+articles) + {weights['sources']}·log(1+sources) + "
            f"{weights['spike']}·log(spike) + {weights['recency']}·recency + "
            f"{weights['materiality']}·(materiality/5)."
        )
    if column == "breaking_score":
        return (
            "Computed in this run. breaking_score = |sentiment_signal| × (materiality/5) × recency. "
            f"Materiality defaults to {cfg.EMERGING_DEFAULT_MATERIALITY} when the model is off. "
            "This is the Emerging score."
        )
    return _XL_COMMENTS.get(column)


def _apply_sheet_chrome(ws: Worksheet, tab_color: str) -> None:
    """Landscape print setup, Arial footer, and a colored tab."""
    ws.sheet_view.showGridLines = False
    ws.sheet_view.zoomScale = 110
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.page_setup.paperSize = ws.PAPERSIZE_TABLOID
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins.left = 0.5
    ws.page_margins.right = 0.5
    ws.page_margins.top = 0.6
    ws.page_margins.bottom = 0.6
    ws.oddFooter.left.text = "Data: Bigdata.com"
    ws.oddFooter.left.font = _XL_FONT
    ws.oddFooter.left.size = 9
    ws.oddFooter.right.text = "Page &P of &N"
    ws.oddFooter.right.font = _XL_FONT
    ws.oddFooter.right.size = 9
    ws.sheet_properties.tabColor = tab_color


def _number_format(column: str) -> str | None:
    """Excel format for a column, or None when the value should stay general."""
    if column in _XL_INT:
        return "0"
    if column in _XL_FLOAT2:
        return "0.00"
    if column in _XL_FLOAT4:
        return "0.0000"
    return None


def _write_banner(ws: Worksheet, title: str, note: str, width: int) -> None:
    """Title on row 1 and a wrapped note on row 2. Headers start on row 3."""
    from openpyxl.styles import Alignment, Font

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(width, 1))
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=max(width, 1))
    title_cell = ws.cell(1, 1, title)
    title_cell.font = Font(name=_XL_FONT, bold=True, size=14, color=_XL_HEADER_FILL)
    title_cell.alignment = Alignment(vertical="center")
    note_cell = ws.cell(2, 1, note)
    note_cell.font = Font(name=_XL_FONT, italic=True, size=9, color=_XL_NOTE)
    note_cell.alignment = Alignment(wrap_text=True, vertical="center")
    ws.row_dimensions[1].height = 22
    ws.row_dimensions[2].height = 48


def _write_header(ws: Worksheet, row: int, columns: list[str]) -> None:
    """Bold white header on the navy fill, with a comment where the column needs one."""
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    fill = PatternFill("solid", fgColor=_XL_HEADER_FILL)
    font = Font(name=_XL_FONT, bold=True, color="FFFFFF", size=10)
    thin = Side(style="thin", color=_XL_LINE)
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for col, name in enumerate(columns, start=1):
        cell = ws.cell(row, col, name)
        cell.fill = fill
        cell.font = font
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        cell.border = border
        comment = _header_comment(name)
        if comment:
            cell.comment = Comment(comment, "Bigdata.com", width=280, height=80)
        ws.column_dimensions[cell.column_letter].width = _XL_WIDTH.get(name, 14)
    ws.row_dimensions[row].height = 30
    ws.freeze_panes = f"A{row + 1}"
    ws.print_title_rows = f"{row}:{row}"


def _style_value(cell: object, column: str, value: object, zebra: bool) -> None:
    """Font, number format, alignment, and the direction / decision colors."""
    from openpyxl.cell.cell import Cell
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    if not isinstance(cell, Cell):
        return
    thin = Side(style="thin", color=_XL_LINE)
    cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
    color = "000000"
    fill_hex = _XL_ZEBRA if zebra else "FFFFFF"
    if column == "direction" and isinstance(value, str):
        color = {"up": _XL_UP, "down": _XL_DOWN, "mixed": _XL_MIXED}.get(value, "000000")
    if column == "decision" and isinstance(value, str):
        fill_hex = _XL_DECISION_FILL.get(value, fill_hex)
    cell.font = Font(
        name=_XL_FONT, size=10, color=color, bold=column in {"rank", "decision"},
        underline="single" if column == "representative_url" and isinstance(value, str) else None,
    )
    if column == "representative_url" and isinstance(value, str) and value.startswith("http"):
        cell.font = Font(name=_XL_FONT, size=10, color=_XL_LINK, underline="single")
        cell.hyperlink = value
    cell.fill = PatternFill("solid", fgColor=fill_hex)
    cell.alignment = Alignment(
        vertical="top" if column in _XL_WRAP else "center",
        wrap_text=column in _XL_WRAP,
    )
    number_format = _number_format(column)
    if number_format and isinstance(value, (int, float)) and not isinstance(value, bool):
        cell.number_format = number_format


def _write_table(ws: Worksheet, frame: pd.DataFrame, header_row: int) -> int:
    """Write `frame` under `header_row`. Returns the last data row (the header row when empty)."""
    columns = [str(column) for column in frame.columns]
    _write_header(ws, header_row, columns)
    last_row = header_row
    for offset, record in enumerate(frame.itertuples(index=False), start=1):
        row = header_row + offset
        last_row = row
        ws.row_dimensions[row].height = 32
        for col, (column, raw) in enumerate(zip(columns, record, strict=True), start=1):
            value = _excel_value(raw)
            if column in _XL_INT and isinstance(value, float) and value.is_integer():
                value = int(value)
            cell = ws.cell(row, col, value)
            _style_value(cell, column, value, zebra=offset % 2 == 0)
    end_row = max(last_row, header_row)
    end_letter = ws.cell(header_row, max(len(columns), 1)).column_letter
    ws.auto_filter.ref = f"A{header_row}:{end_letter}{end_row}"
    return last_row


def _trending_frame(by_commodity: dict[str, list[Story]], combined: list[Story]) -> pd.DataFrame:
    """One row per commodity list, plus any cross-commodity story that list cut off."""
    combined_rank = {story.sid: rank for rank, story in enumerate(combined, 1)}
    frames: list[pd.DataFrame] = []
    seen: set[str] = set()
    for key, group in by_commodity.items():
        frame = stories_frame(group, "trending")
        frame.insert(1, "commodity", key)
        frame.insert(2, "combined_rank", frame["story_id"].map(lambda sid: combined_rank.get(sid)))
        frames.append(frame)
        seen.update(story.sid for story in group)
    leftover = [story for story in combined if story.sid not in seen]
    if leftover:
        frame = stories_frame(leftover, "trending")
        frame.insert(1, "commodity", frame["entities"])
        frame.insert(2, "combined_rank", frame["story_id"].map(lambda sid: combined_rank.get(sid)))
        frame["rank"] = frame["combined_rank"]
        frames.append(frame)
    if not frames:
        empty = stories_frame([], "trending")
        empty.insert(1, "commodity", pd.Series(dtype=object))
        empty.insert(2, "combined_rank", pd.Series(dtype=object))
        frames.append(empty)
    return _order_frame(pd.concat(frames, ignore_index=True), _TRENDING_COLUMNS)


def _breaking_frame(emerging: list[Story]) -> pd.DataFrame:
    """Emerging stories, with the score column named for the Breaking sheet."""
    frame = stories_frame(emerging, "emerging").rename(columns={"emerging_score": "breaking_score"})
    return _order_frame(frame, _BREAKING_COLUMNS)


def _list_trending_frame(trending: list[Story]) -> pd.DataFrame:
    """The watchlist's single ranked Trending list."""
    return _order_frame(stories_frame(trending, "trending"), _LIST_TRENDING_COLUMNS)


def _audit_frame(
    stories: list[Story],
    by_group: dict[str, list[Story]],
    combined: list[Story],
    emerging: list[Story],
    win: Window,
    use_llm: bool,
    *,
    grouped: bool,
    require_direction: bool,
) -> pd.DataFrame:
    """Every scored story, in report order, with the list it landed on and why."""
    combined_rank = {story.sid: rank for rank, story in enumerate(combined, 1)}
    trending_ids = {story.sid for story in combined}
    trending_ids.update(story.sid for group in by_group.values() for story in group)
    breaking_ids = {story.sid for story in emerging}
    sheet_order: dict[str, int] = {}
    for story in [item for group in by_group.values() for item in group]:
        sheet_order.setdefault(story.sid, len(sheet_order))
    for story in combined:
        sheet_order.setdefault(story.sid, len(sheet_order))
    breaking_order = {story.sid: index for index, story in enumerate(emerging)}
    unique: dict[str, Story] = {}
    for story in stories:
        unique.setdefault(story.sid, story)

    def sort_key(story: Story) -> tuple[int, float]:
        if story.sid in trending_ids:
            return (0, float(sheet_order.get(story.sid, 10_000)))
        if story.sid in breaking_ids:
            return (1, float(breaking_order.get(story.sid, 10_000)))
        return (2, -story.trend)

    rows: list[dict[str, object]] = []
    for story in sorted(unique.values(), key=sort_key):
        decision, reason = _listing(
            story, trending_ids, breaking_ids, combined_rank, by_group, win, use_llm,
            grouped=grouped, require_direction=require_direction,
        )
        base = stories_frame([story], "emerging").iloc[0].to_dict()
        base["decision"] = decision
        base["reason"] = reason
        base["breaking_score"] = _breaking_score(story, use_llm)
        base["recency"] = round(story.recency, 4)
        base.pop("emerging_score", None)
        base.pop("rank", None)
        rows.append(base)
    return _order_frame(pd.DataFrame(rows), _AUDIT_COLUMNS)


def _gate_labels(gate_stats: dict[str, dict[str, int]]) -> list[str]:
    """Relevance-gate labels, known ones first, then anything the run actually returned."""
    present = {label for counts in gate_stats.values() for label in counts}
    ordered = [label for label in _GATE_LABEL_ORDER if label in present]
    ordered.extend(sorted(present - set(ordered)))
    return ordered


def _write_gate_table(ws: Worksheet, gate_stats: dict[str, dict[str, int]], start_row: int) -> None:
    """Counts per commodity, with Kept and Dropped as formulas over those counts."""
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    labels = _gate_labels(gate_stats)
    keep = [label for label in labels if label in cfg.GATE_KEEP]
    drop = [label for label in labels if label not in cfg.GATE_KEEP]
    headers = ["commodity", *labels, "Kept", "Dropped"]
    thin = Side(style="thin", color=_XL_LINE)
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_fill = PatternFill("solid", fgColor=_XL_HEADER_FILL)
    header_font = Font(name=_XL_FONT, bold=True, color="FFFFFF", size=10)
    section = ws.cell(start_row, 1, "Relevance gate")
    section.font = Font(name=_XL_FONT, bold=True, size=12, color=_XL_HEADER_FILL)
    header_row = start_row + 1
    for col, name in enumerate(headers, start=1):
        cell = ws.cell(header_row, col, name)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(vertical="center")
        cell.border = border
        if name == "Kept":
            cell.comment = Comment(
                "Articles whose relevance-gate label is in GATE_KEEP "
                f"({', '.join(cfg.GATE_KEEP)}). This cell sums those columns.",
                "Bigdata.com", width=260, height=60,
            )
        if name == "Dropped":
            cell.comment = Comment(
                "Articles labeled outside GATE_KEEP. This cell sums those columns.",
                "Bigdata.com", width=240, height=50,
            )
    body = Font(name=_XL_FONT, size=10)
    bold = Font(name=_XL_FONT, size=10, bold=True)
    keys = list(gate_stats)
    for offset, key in enumerate(keys):
        row = header_row + 1 + offset
        counts = gate_stats[key]
        commodity_cell = ws.cell(row, 1, key)
        commodity_cell.font = body
        commodity_cell.border = border
        for index, label in enumerate(labels):
            cell = ws.cell(row, 2 + index, int(counts.get(label, 0)))
            cell.font = body
            cell.border = border
            cell.number_format = "#,##0"
        keep_refs = ",".join(f"{get_column_letter(2 + labels.index(label))}{row}" for label in keep)
        drop_refs = ",".join(f"{get_column_letter(2 + labels.index(label))}{row}" for label in drop)
        kept_cell = ws.cell(row, 2 + len(labels), f"=SUM({keep_refs})" if keep_refs else "=0")
        dropped_cell = ws.cell(row, 3 + len(labels), f"=SUM({drop_refs})" if drop_refs else "=0")
        for cell in (kept_cell, dropped_cell):
            cell.font = bold
            cell.border = border
            cell.number_format = "#,##0"
    if keys:
        total_row = header_row + 1 + len(keys)
        label_cell = ws.cell(total_row, 1, "Total")
        label_cell.font = bold
        label_cell.fill = PatternFill("solid", fgColor=_XL_TOTAL_FILL)
        label_cell.border = border
        first = header_row + 1
        last = total_row - 1
        for col in range(2, len(headers) + 1):
            letter = get_column_letter(col)
            cell = ws.cell(total_row, col, f"=SUM({letter}{first}:{letter}{last})")
            cell.font = bold
            cell.fill = PatternFill("solid", fgColor=_XL_TOTAL_FILL)
            cell.border = border
            cell.number_format = "#,##0"


def save_commodity_excel(
    *,
    by_commodity: dict[str, list[Story]],
    combined: list[Story],
    emerging: list[Story],
    stories: list[Story],
    gate_stats: dict[str, dict[str, int]],
    win: Window,
    use_llm: bool,
    prefix: str = "commodities",
) -> Path:
    """Write Trending, Breaking, and Audit sheets. Breaking is the Emerging list.

    Scores are the values this run already computed. Kept and Dropped on Audit are
    formulas over the relevance-gate counts, so they follow those counts.
    """
    try:
        from openpyxl import Workbook
    except ImportError as exc:
        raise ImportError(
            "Excel export needs openpyxl. Install dependencies with: pip install -r requirements.txt"
        ) from exc

    trending = _trending_frame(by_commodity, combined)
    breaking = _breaking_frame(emerging)
    audit = _audit_frame(
        stories, by_commodity, combined, emerging, win, use_llm, grouped=True, require_direction=True,
    )
    stamp = win.as_of.strftime("%Y-%m-%d")
    note = (
        f"Window {win.label}. As of {win.as_of:%Y-%m-%d %H:%M} UTC. "
        f"Breaking is the Emerging list: |sentiment| ≥ {cfg.SENTIMENT_EXTREME}, "
        f"materiality ≥ {cfg.EMERGING_MIN_MATERIALITY} when the model is on, "
        f"and a single outlet must be rank ≤ {cfg.EMERGING_MAX_SOURCE_RANK}. "
        f"Trending needs ≥ {cfg.TRENDING_MIN_SOURCES} outlets. "
        "Scores were computed in this run from Bigdata.com counts. "
        "Search de-duplicates similar passages, so article counts understate total coverage."
    )
    cfg.OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    path = cfg.OUTPUTS_DIR / f"{prefix}_trending_{stamp}.xlsx"

    workbook = Workbook()
    trending_sheet = workbook.active
    trending_sheet.title = "Trending"
    breaking_sheet = workbook.create_sheet("Breaking")
    audit_sheet = workbook.create_sheet("Audit")

    _apply_sheet_chrome(trending_sheet, "1B4F8A")
    _apply_sheet_chrome(breaking_sheet, "B45309")
    _apply_sheet_chrome(audit_sheet, "475569")
    _write_banner(trending_sheet, "Trending", note, len(_TRENDING_COLUMNS))
    _write_banner(
        breaking_sheet,
        "Breaking",
        note + " One row per Emerging story. breaking_score = |sentiment| × (materiality/5) × recency.",
        len(_BREAKING_COLUMNS),
    )
    _write_banner(
        audit_sheet,
        "Audit",
        note + " Every scored story is listed once. The relevance-gate counts, with Kept and Dropped formulas, sit below this table.",
        len(_AUDIT_COLUMNS),
    )
    _write_table(trending_sheet, trending, header_row=3)
    _write_table(breaking_sheet, breaking, header_row=3)
    last_audit_row = _write_table(audit_sheet, audit, header_row=3)
    _write_gate_table(audit_sheet, gate_stats, start_row=last_audit_row + 2)

    workbook.properties.title = "Trending commodity news"
    workbook.properties.creator = "Bigdata.com"
    workbook.properties.subject = f"Trending, Breaking, and Audit · {stamp}"
    workbook.save(path)
    return path


def _write_fetch_table(ws: Worksheet, articles: dict[str, list[Article]], start_row: int) -> None:
    """Articles fetched per entity. The articles total is a formula; sources are not added."""
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    thin = Side(style="thin", color=_XL_LINE)
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_fill = PatternFill("solid", fgColor=_XL_HEADER_FILL)
    header_font = Font(name=_XL_FONT, bold=True, color="FFFFFF", size=10)
    body = Font(name=_XL_FONT, size=10)
    bold = Font(name=_XL_FONT, size=10, bold=True)
    section = ws.cell(start_row, 1, "Articles fetched")
    section.font = Font(name=_XL_FONT, bold=True, size=12, color=_XL_HEADER_FILL)
    header_row = start_row + 1
    for col, name in enumerate(("entity", "articles", "sources"), start=1):
        cell = ws.cell(header_row, col, name)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(vertical="center")
        cell.border = border
    ws.cell(header_row, 3).comment = Comment(
        "Distinct outlets in this entity's fetched articles. "
        "The total row does not add this column, because one outlet can cover several names.",
        "Bigdata.com", width=260, height=70,
    )
    ws.column_dimensions["A"].width = max(ws.column_dimensions["A"].width or 0, 22)
    keys = list(articles)
    for offset, key in enumerate(keys):
        row = header_row + 1 + offset
        fetched = articles[key]
        values = (
            key,
            len(fetched),
            len({item.source_id or item.source_name for item in fetched}),
        )
        for col, value in enumerate(values, start=1):
            cell = ws.cell(row, col, value)
            cell.font = body
            cell.border = border
            if col > 1:
                cell.number_format = "#,##0"
    if not keys:
        return
    total_row = header_row + 1 + len(keys)
    first = header_row + 1
    last = total_row - 1
    label = ws.cell(total_row, 1, "Total")
    label.font = bold
    label.fill = PatternFill("solid", fgColor=_XL_TOTAL_FILL)
    label.border = border
    letter = get_column_letter(2)
    total = ws.cell(total_row, 2, f"=SUM({letter}{first}:{letter}{last})")
    total.font = bold
    total.fill = PatternFill("solid", fgColor=_XL_TOTAL_FILL)
    total.border = border
    total.number_format = "#,##0"
    blank = ws.cell(total_row, 3, None)
    blank.fill = PatternFill("solid", fgColor=_XL_TOTAL_FILL)
    blank.border = border


def _open_workbook(title: str):
    """Three-sheet workbook, or a clear error when openpyxl is not installed."""
    try:
        from openpyxl import Workbook
    except ImportError as exc:
        raise ImportError(
            "Excel export needs openpyxl. Install dependencies with: pip install -r requirements.txt"
        ) from exc
    workbook = Workbook()
    trending_sheet = workbook.active
    trending_sheet.title = "Trending"
    breaking_sheet = workbook.create_sheet("Breaking")
    audit_sheet = workbook.create_sheet("Audit")
    _apply_sheet_chrome(trending_sheet, "1B4F8A")
    _apply_sheet_chrome(breaking_sheet, "B45309")
    _apply_sheet_chrome(audit_sheet, "475569")
    workbook.properties.title = title
    workbook.properties.creator = "Bigdata.com"
    return workbook, trending_sheet, breaking_sheet, audit_sheet


def _score_note(win: Window) -> str:
    """Shared note: window, Breaking thresholds, and where the scores came from."""
    return (
        f"Window {win.label}. As of {win.as_of:%Y-%m-%d %H:%M} UTC. "
        f"Breaking is the Emerging list: |sentiment| ≥ {cfg.SENTIMENT_EXTREME}, "
        f"materiality ≥ {cfg.EMERGING_MIN_MATERIALITY} when the model is on, "
        f"and a single outlet must be rank ≤ {cfg.EMERGING_MAX_SOURCE_RANK}. "
        f"Trending needs ≥ {cfg.TRENDING_MIN_SOURCES} outlets. "
        "Scores were computed in this run from Bigdata.com counts. "
        "Search de-duplicates similar passages, so article counts understate total coverage."
    )


def save_watchlist_excel(
    *,
    trending: list[Story],
    emerging: list[Story],
    stories: list[Story],
    articles: dict[str, list[Article]],
    win: Window,
    use_llm: bool,
    prefix: str = "watchlist",
) -> Path:
    """Write Trending, Breaking, and Audit for the watchlist notebook.

    Breaking is the Emerging list and does not require an up/down direction.
    The Audit sheet ends with articles fetched per name; the articles total is a formula.
    """
    trending_frame = _list_trending_frame(trending)
    breaking = _breaking_frame(emerging)
    audit = _audit_frame(
        stories, {}, trending, emerging, win, use_llm, grouped=False, require_direction=False,
    )
    stamp = win.as_of.strftime("%Y-%m-%d")
    note = _score_note(win)
    cfg.OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    path = cfg.OUTPUTS_DIR / f"{prefix}_trending_{stamp}.xlsx"
    workbook, trending_sheet, breaking_sheet, audit_sheet = _open_workbook("Trending news: watchlist")
    workbook.properties.subject = f"Trending, Breaking, and Audit · {stamp}"
    _write_banner(trending_sheet, "Trending", note, len(_LIST_TRENDING_COLUMNS))
    _write_banner(
        breaking_sheet,
        "Breaking",
        note + " One row per Emerging story. A price direction is not required. "
        "breaking_score = |sentiment| × (materiality/5) × recency.",
        len(_BREAKING_COLUMNS),
    )
    _write_banner(
        audit_sheet,
        "Audit",
        note + " Every scored story is listed once. This notebook has no relevance gate. "
        "Article counts per name, with a total formula, sit below this table.",
        len(_AUDIT_COLUMNS),
    )
    _write_table(trending_sheet, trending_frame, header_row=3)
    _write_table(breaking_sheet, breaking, header_row=3)
    last_audit_row = _write_table(audit_sheet, audit, header_row=3)
    _write_fetch_table(audit_sheet, articles, start_row=last_audit_row + 2)
    workbook.save(path)
    return path
