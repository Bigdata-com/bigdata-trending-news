"""
Step 5 - Enrich stories with an LLM: title, one-line summary, direction,
driver and materiality. Also the relevance gate used for commodities.

The LLM only labels and summarises. It never produces counts, momentum or
sentiment - those always come from Bigdata.com data (cluster.py, momentum.py).

The model is told to use ONLY the supplied headlines/excerpts and to answer
"n/a" instead of guessing.
"""
from __future__ import annotations

import re
from typing import Any

from . import config as cfg
from .bigdata import run_parallel
from .cluster import Story, entity_label
from .fetch import Article
from .llm import LLM

DIRECTIONS = {"up", "down", "mixed", "n/a"}

ENRICH_SYSTEM = (
    "You are a markets news editor. You describe news stories accurately and neutrally, "
    "using only the text you are given. You reply with a single JSON object and nothing else."
)

ENRICH_PROMPT = """For each story below, return:
- "title": a neutral, specific headline for the story, 12 words or fewer.
- "summary": ONE sentence, grounded only in the supplied headlines and excerpts.
- "direction": {direction_rule}
- "driver": a short tag (2-4 words) naming what is driving the story, e.g. "Fed rate expectations",
  "ETF inflows", "mine strike", "earnings beat", "product launch", "antitrust ruling".
- "materiality": integer 1-5, the likely market relevance for an investor
  (1 = trivia/promotional, 2 = minor, 3 = notable, 4 = important, 5 = major market-moving).
- "representative_id": the id of the member article that best represents the story.

Use ONLY the supplied text. If the text does not support a field, use "n/a" rather than guess.

Return JSON exactly in this form:
{{"stories": [{{"story": "S1", "title": "...", "summary": "...", "direction": "up", "driver": "...",
"materiality": 3, "representative_id": 2}}]}}

{stories}"""

DIRECTION_WATCHLIST = (
    'one of "up", "down", "mixed", "n/a": the implication for the share price or outlook of the story\'s '
    'main entity (listed first). Use "n/a" if the text does not imply a direction.'
)
DIRECTION_COMMODITY = (
    'one of "up", "down", "mixed": the direction of the commodity\'s PRICE (or price outlook) described '
    'by the story. Use "mixed" only if the articles disagree or describe moves both ways. '
    'Use "n/a" only if the story says nothing about price, supply or demand.'
)


def _excerpt(a: Article, n: int) -> str:
    return re.sub(r"\s+", " ", a.chunk_text)[:n]


def _story_block(idx: int, story: Story, names: dict[str, str], max_members: int = 12) -> str:
    ents = ", ".join(entity_label(e, names) for e in story.entities)
    lines = [f"Story S{idx} - main entity: {ents}"]
    for j, a in enumerate(story.members[:max_members], start=1):
        lines.append(f"  {j} | {a.source_name} | {a.timestamp:%m-%d %H:%M} | {a.headline} | {_excerpt(a, 300)}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# keyword direction heuristic (no-LLM fallback, and safety net for commodities)
# --------------------------------------------------------------------------- #
_UP = r"\b(rise[sn]?|rising|rose|gain(s|ed)?|jump(s|ed)?|surge[sd]?|rall(y|ies|ied)|climb(s|ed)?|higher|soar(s|ed)?|record high|advance[sd]?|rebound(s|ed)?|bounce[sd]?|spike[sd]?|up)\b"
_DOWN = r"\b(fall(s|en)?|fell|drop(s|ped)?|slide[s]?|slid|slip(s|ped)?|decline[sd]?|lower|down|tumble[sd]?|plunge[sd]?|sink(s)?|sank|retreat(s|ed)?|slump(s|ed)?|ease[sd]?|loss(es)?|weak(er|ens)?)\b"


def heuristic_direction(headlines: list[str]) -> str:
    """
    Vote over headlines using each headline's FIRST direction word, which
    usually belongs to the subject ("Gold rises as dollar falls" -> up).
    """
    up = down = 0
    for h in headlines:
        u, d = re.search(_UP, h.lower()), re.search(_DOWN, h.lower())
        if u and (not d or u.start() < d.start()):
            up += 1
        elif d:
            down += 1
    if up == down == 0:
        return "n/a"
    if up >= 1.5 * down and up > down:
        return "up"
    if down >= 1.5 * up and down > up:
        return "down"
    return "mixed"


# --------------------------------------------------------------------------- #
# enrichment
# --------------------------------------------------------------------------- #
def _apply(story: Story, item: dict[str, Any], commodity: bool) -> None:
    story.title = str(item.get("title") or "").strip()[:140] or story.representative.headline
    s = str(item.get("summary") or "").strip()
    story.summary = "" if s.lower() == "n/a" else s
    d = str(item.get("direction") or "n/a").strip().lower()
    story.direction = d if d in DIRECTIONS else "n/a"
    drv = str(item.get("driver") or "").strip()
    story.driver = "" if drv.lower() == "n/a" else drv[:40]
    try:
        story.materiality = max(1, min(5, int(item.get("materiality"))))
    except (TypeError, ValueError):
        story.materiality = None
    try:
        j = int(item.get("representative_id"))
        if 1 <= j <= len(story.members):
            story.representative_id = story.members[j - 1].doc_id
    except (TypeError, ValueError):
        pass
    story.enriched = True


def _fallback(story: Story) -> None:
    """No LLM: use the best-ranked headline; heuristic direction for commodities."""
    story.title = story.representative.headline
    story.summary = ""
    story.driver = ""
    story.materiality = None


def _finalise_direction(story: Story, commodity: bool) -> None:
    """Commodity stories always get a direction: LLM → keyword heuristic → mixed."""
    if commodity and story.direction not in {"up", "down", "mixed"}:
        h = heuristic_direction([a.headline for a in story.members])
        story.direction = h if h != "n/a" else "mixed"


def enrich_stories(
    stories: list[Story], llm: LLM | None, names: dict[str, str], commodity: bool = False
) -> None:
    """Enrich stories in place (batched, parallel)."""
    todo = [s for s in stories if not s.enriched]
    if llm is None:
        for s in todo:
            _fallback(s)
            _finalise_direction(s, commodity)
        return

    rule = DIRECTION_COMMODITY if commodity else DIRECTION_WATCHLIST
    size = cfg.ENRICH_BATCH_SIZE
    batches = [todo[i:i + size] for i in range(0, len(todo), size)]

    def run(batch: list[Story]) -> None:
        blocks = "\n\n".join(_story_block(i + 1, s, names) for i, s in enumerate(batch))
        data = llm.json(ENRICH_SYSTEM, ENRICH_PROMPT.format(direction_rule=rule, stories=blocks))
        items = {}
        for it in (data or {}).get("stories", []) if isinstance(data, dict) else []:
            if isinstance(it, dict):
                m = re.search(r"(\d+)", str(it.get("story", "")))
                if m:
                    items[int(m.group(1))] = it
        for i, s in enumerate(batch, start=1):
            if i in items:
                _apply(s, items[i], commodity)
            else:
                _fallback(s)

    run_parallel(run, batches, label=lambda b: f"enrich batch of {len(b)}", workers=cfg.LLM_CONCURRENCY)
    for s in todo:
        if not s.title:
            _fallback(s)
        _finalise_direction(s, commodity)


# --------------------------------------------------------------------------- #
# Relevance gate (Notebook 2)
# --------------------------------------------------------------------------- #
GATE_LABELS = {"market_move", "company_specific", "irrelevant"}

GATE_PROMPT = """Classify each article about {commodity} into exactly one class:
- "market_move": about the {commodity} MARKET itself - its price, supply, demand, inventories, flows,
  trade, policy, or forecasts for it.
- "company_specific": mainly about an individual company (a miner, explorer, producer or other firm),
  its projects, results or share price, including stock promotion (e.g. "Can X Gold strengthen its
  exploration story?").
- "irrelevant": not really about {commodity} (e.g. a different meaning of the word, sports medals,
  jewellery, a passing mention).

Return JSON: {{"labels": {{"1": "market_move", "2": "company_specific"}}}}

Articles (id | headline | excerpt):
{rows}"""

_TICKER = re.compile(
    r"\((?:TSXV?|TSX-V|ASX|CSE|NYSE(?: American)?|NASDAQ|Nasdaq|OTC(?:QB|QX)?|LSE|AIM|FSE|FRA|JSE|NEO)\s*:\s*[A-Z0-9.\-]+\)"
)
_PROMO_Q = re.compile(r"^(can|could|is|are|will|should|does|do|why|how|what|which)\b.*\?\s*$", re.I)
_PROMO_STOCKS = re.compile(r"\b(stocks?|shares) to (buy|consider|watch|own)\b|\bmining stocks?\b", re.I)


def heuristic_gate(a: Article) -> str:
    """No-LLM gate: drop ticker-tagged promos, stock tips and question-style promo headlines."""
    h = a.headline.strip()
    if _TICKER.search(h) or _PROMO_Q.match(h) or _PROMO_STOCKS.search(h):
        return "company_specific"
    return "market_move"


def relevance_gate(
    articles: dict[str, list[Article]], llm: LLM | None, keep: list[str] | None = None
) -> tuple[dict[str, list[Article]], dict[str, dict[str, int]]]:
    """
    Label each article and keep only the classes in `keep` (default GATE_KEEP).
    Returns (filtered articles, {entity: {label: count}}).
    """
    keep = keep or cfg.GATE_KEEP

    def label_entity(key: str) -> None:
        arts = articles[key]
        if llm is None:
            for a in arts:
                a.gate = heuristic_gate(a)
            return
        size = cfg.GATE_BATCH_SIZE
        for start in range(0, len(arts), size):
            batch = arts[start:start + size]
            rows = "\n".join(f"{i + 1} | {a.headline} | {_excerpt(a, 150)}" for i, a in enumerate(batch))
            data = llm.json(ENRICH_SYSTEM, GATE_PROMPT.format(commodity=key, rows=rows), max_tokens=4000)
            labels = (data or {}).get("labels", {}) if isinstance(data, dict) else {}
            for i, a in enumerate(batch, start=1):
                lab = str(labels.get(str(i), "")).strip().lower()
                # unlabeled -> heuristic, so a model hiccup never silently drops data
                a.gate = lab if lab in GATE_LABELS else heuristic_gate(a)

    run_parallel(label_entity, list(articles), label=lambda k: f"gate {k}", workers=cfg.LLM_CONCURRENCY)
    stats: dict[str, dict[str, int]] = {}
    out: dict[str, list[Article]] = {}
    for key, arts in articles.items():
        counts: dict[str, int] = {}
        for a in arts:
            counts[a.gate or "unlabeled"] = counts.get(a.gate or "unlabeled", 0) + 1
        stats[key] = counts
        out[key] = [a for a in arts if (a.gate or "market_move") in keep]
    return out, stats
