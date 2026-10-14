"""
Step 4 - Group articles into stories.

A "story" is a set of articles that report the SAME specific event or
development (not merely the same company or theme). This is what turns a pile
of articles into "N outlets are covering this right now".

* LLM mode: the model sees a numbered list of articles (id | source | time |
  headline | first ~200 chars) in batches of up to 120 and returns
  {"clusters":[{"label": "...", "member_ids":[...]}]}. The output is validated
  (each article assigned exactly once; unknown IDs dropped; missing IDs become
  stories of one). Stories from different batches are merged by asking the
  model which labels describe the same event (fallback: label similarity).
* Lexical fallback (USE_LLM=False or no key): headlines are normalised and
  joined when their word sets overlap by >= LEXICAL_JACCARD (union-find).
* Stories found for different entities are merged when they substantially
  share articles, so a story about TSLA and NVDA appears once, with both
  entity chips (see merge_across_entities).

All story statistics (articles, distinct sources, first/last seen, sentiment)
are computed here in code from Bigdata.com data - never by the LLM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from statistics import mean
from typing import Any

from . import config as cfg
from .bigdata import run_parallel
from .fetch import Article
from .llm import LLM


@dataclass
class Story:
    members: list[Article]
    label: str = ""
    entities: list[str] = field(default_factory=list)
    # ---- computed in code from API data ----
    articles: int = 0
    sources: int = 0
    source_names: list[str] = field(default_factory=list)
    best_source_rank: int = 9
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    avg_sentiment: float = 0.0
    max_abs_sentiment: float = 0.0
    # ---- LLM enrichment (enrich.py) ----
    title: str = ""
    summary: str = ""
    direction: str = "n/a"
    driver: str = ""
    materiality: int | None = None
    representative_id: str = ""
    enriched: bool = False
    # ---- scores (rank.py) ----
    spike: float = 1.0
    recency: float = 0.0
    trend: float = 0.0
    emerging: float = 0.0
    sentiment_signal: float = 0.0

    @property
    def sid(self) -> str:
        """Stable story ID: the earliest-published member's document ID."""
        return min(self.members, key=lambda a: (a.timestamp, a.doc_id)).doc_id

    @property
    def representative(self) -> Article:
        by_id = {a.doc_id: a for a in self.members}
        if self.representative_id in by_id:
            return by_id[self.representative_id]
        # default: best-ranked outlet, then most recent
        return min(self.members, key=lambda a: (a.source_rank, -a.timestamp.timestamp()))

    def compute_stats(self) -> "Story":
        m = self.members
        self.articles = len(m)
        # Entity chips: entities found in at least ENTITY_MIN_SHARE of the articles
        # (the most frequent one is always kept), most frequent first. A gold story
        # whose headlines occasionally mention oil is not an oil story.
        counts: dict[str, int] = {}
        for a in m:
            for e in a.entities:
                counts[e] = counts.get(e, 0) + 1
        if counts:
            ranked = sorted(counts, key=lambda e: (-counts[e], self.entities.index(e) if e in self.entities else 99))
            self.entities = [e for i, e in enumerate(ranked) if i == 0 or counts[e] >= cfg.ENTITY_MIN_SHARE * len(m)]
        names: dict[str, str] = {}
        for a in m:
            names.setdefault(a.source_id or a.source_name, a.source_name)
        self.sources = len(names)
        self.source_names = sorted(set(names.values()))
        self.best_source_rank = min(a.source_rank for a in m)
        self.first_seen = min(a.timestamp for a in m)
        self.last_seen = max(a.timestamp for a in m)
        self.avg_sentiment = round(mean(a.sentiment for a in m), 3)
        self.max_abs_sentiment = max((a.sentiment_max_abs for a in m), key=abs)
        self.members = sorted(m, key=lambda a: a.timestamp, reverse=True)
        return self


# --------------------------------------------------------------------------- #
# union-find
# --------------------------------------------------------------------------- #
class _UF:
    def __init__(self, n: int) -> None:
        self.p = list(range(n))

    def find(self, i: int) -> int:
        while self.p[i] != i:
            self.p[i] = self.p[self.p[i]]
            i = self.p[i]
        return i

    def union(self, i: int, j: int) -> None:
        self.p[self.find(i)] = self.find(j)

    def groups(self) -> list[list[int]]:
        g: dict[int, list[int]] = {}
        for i in range(len(self.p)):
            g.setdefault(self.find(i), []).append(i)
        return list(g.values())


# --------------------------------------------------------------------------- #
# lexical fallback
# --------------------------------------------------------------------------- #
_STOP = set(
    "a an the and or of to in on for at by with from as is are was were be been it its this that "
    "after over amid into up down says said say new vs than how why what will can could may".split()
)


def normalise_headline(h: str) -> str:
    """lowercase, strip trailing ' - Source' / ' | Source', strip punctuation."""
    h = re.sub(r"\s+[-|–—]\s+[^-|–—]{2,40}$", "", h.strip())
    return re.sub(r"[^a-z0-9 ]+", " ", h.lower())


def _token_set(s: str) -> frozenset[str]:
    return frozenset(t for t in normalise_headline(s).split() if t not in _STOP)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _lexical_groups(texts: list[str], threshold: float) -> list[list[int]]:
    toks = [_token_set(t) for t in texts]
    uf = _UF(len(texts))
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            if jaccard(toks[i], toks[j]) >= threshold:
                uf.union(i, j)
    return uf.groups()


def cluster_lexical(articles: list[Article]) -> list[Story]:
    groups = _lexical_groups([a.headline for a in articles], cfg.LEXICAL_JACCARD)
    return [Story(members=[articles[i] for i in g], label=articles[g[0]].headline) for g in groups]


# --------------------------------------------------------------------------- #
# LLM clustering
# --------------------------------------------------------------------------- #
CLUSTER_SYSTEM = (
    "You are a financial news editor. You group news articles into stories. "
    "You reply with a single JSON object and nothing else."
)

CLUSTER_PROMPT = """Group these news articles about {entity} into stories.

Rules:
- Two articles belong to the same story ONLY if they report the same specific event or development
  (e.g. the same earnings release, the same deal, the same product launch, the same lawsuit or ruling,
  the same price move on the same day, the same analyst action).
- Sharing the same company, sector or general theme is NOT enough. Do not group by broad theme
  (e.g. "AI news", "rate outlook commentary", "stock market moves").
- Every id must appear in exactly one cluster. An article that matches no other article is a cluster of one.
- Never create catch-all clusters such as "miscellaneous", "other news" or "various updates".
- "label" is a short, specific description of the event (max 12 words).

Return JSON exactly in this form:
{{"clusters": [{{"label": "...", "member_ids": [1, 2, 3]}}]}}

Articles (id | source | time UTC | headline | excerpt):
{rows}"""

MERGE_PROMPT = """Below are candidate stories found in separate groups of news articles about {entity}.
Each has a label and up to two sample headlines. Identify stories that report the SAME specific event or development.

Rules:
- Merge only when it is clearly the same event (same announcement, launch, deal, ruling, data release, or the same
  price move on the same day). When in doubt, keep stories separate.
- Different companies' or different commodities' news are different stories.
- Price moves in opposite directions are different stories. A forecast and a price move are different stories.
- Same broad theme (e.g. "AI", "inflation", "oil prices") is NOT enough.

Return JSON: {{"groups": [[1, 4], [2, 7, 9]]}}  (only groups of 2 or more; omit stories with no duplicate)

Stories (id | label | sample headlines):
{rows}"""


def _row(i: int, a: Article) -> str:
    excerpt = re.sub(r"\s+", " ", a.chunk_text)[:200]
    return f"{i} | {a.source_name} | {a.timestamp:%m-%d %H:%M} | {a.headline} | {excerpt}"


def _validated(data: dict[str, Any] | None, n: int) -> list[tuple[str, list[int]]] | None:
    """Every id 1..n exactly once; unknown dropped; missing -> singleton."""
    if not data or not isinstance(data.get("clusters"), list):
        return None
    seen: set[int] = set()
    out: list[tuple[str, list[int]]] = []
    for c in data["clusters"]:
        if not isinstance(c, dict):
            continue
        ids = []
        for x in c.get("member_ids") or []:
            try:
                i = int(x)
            except (TypeError, ValueError):
                continue
            if 1 <= i <= n and i not in seen:
                seen.add(i)
                ids.append(i)
        if ids:
            out.append((str(c.get("label") or "").strip(), ids))
    out += [("", [i]) for i in range(1, n + 1) if i not in seen]
    return out


_CATCH_ALL = re.compile(r"\b(misc|miscellaneous|various|unrelated|other news|no (matching|common|specific)|general news)\b", re.I)


def _cluster_batch(llm: LLM, entity: str, batch: list[Article]) -> list[Story]:
    rows = "\n".join(_row(i + 1, a) for i, a in enumerate(batch))
    data = llm.json(CLUSTER_SYSTEM, CLUSTER_PROMPT.format(entity=entity, rows=rows))
    groups = _validated(data, len(batch))
    if groups is None:  # model failed: fall back for this batch only
        return cluster_lexical(batch)
    stories = []
    for label, ids in groups:
        if len(ids) > 1 and _CATCH_ALL.search(label):
            # guard: a "miscellaneous" bucket is not a story - regroup it lexically
            stories += cluster_lexical([batch[i - 1] for i in ids])
        else:
            stories.append(Story(members=[batch[i - 1] for i in ids], label=label or batch[ids[0] - 1].headline))
    return stories


def _merge_batches(llm: LLM | None, entity: str, stories: list[Story]) -> list[Story]:
    """Merge stories that came from different batches but describe one event."""
    labels = [s.label for s in stories]
    groups: list[list[int]] | None = None
    if llm is not None:
        rows = "\n".join(
            f"{i + 1} | {s.label} | " + " / ".join(a.headline[:110] for a in s.members[:2])
            for i, s in enumerate(stories)
        )
        data = llm.json(CLUSTER_SYSTEM, MERGE_PROMPT.format(entity=entity, rows=rows))
        if data and isinstance(data.get("groups"), list):
            uf = _UF(len(stories))
            for g in data["groups"]:
                idx = [int(x) - 1 for x in g if str(x).isdigit() and 1 <= int(x) <= len(stories)]
                for k in idx[1:]:
                    uf.union(idx[0], k)
            groups = uf.groups()
    if groups is None:
        groups = _lexical_groups(labels, cfg.LABEL_MERGE_JACCARD)
    merged = []
    for g in groups:
        members = [a for i in g for a in stories[i].members]
        label = max((stories[i] for i in g), key=lambda s: len(s.members)).label
        merged.append(Story(members=members, label=label))
    return merged


def cluster_entity(articles: list[Article], entity: str, llm: LLM | None) -> list[Story]:
    """Cluster one entity's articles into stories."""
    if not articles:
        return []
    if llm is None:
        return cluster_lexical(articles)
    size = cfg.CLUSTER_BATCH_SIZE
    batches = [articles[i:i + size] for i in range(0, len(articles), size)]
    stories = [s for b in batches for s in _cluster_batch(llm, entity, b)]
    if len(batches) > 1:
        stories = _merge_batches(llm, entity, stories)
    return stories


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def merge_across_entities(per_entity: dict[str, list[Story]]) -> list[Story]:
    """
    Combine stories from all entities so that one story = one row.

    Two stories (found for different entities) are merged when they share
    articles AND the overlap is substantial (>= CROSS_ENTITY_OVERLAP of the
    smaller story). The overlap rule stops a single round-up article ("Big Tech
    stocks today") from chaining unrelated stories together. An article that
    still sits in two stories is kept only in the larger one, so nothing is
    double counted; it still carries every entity it was found for.
    """
    flat: list[tuple[str, Story]] = [(k, s) for k, ss in per_entity.items() for s in ss]
    doc_sets = [{a.doc_id for a in s.members} for _, s in flat]
    holders: dict[str, list[int]] = {}
    for i, docs in enumerate(doc_sets):
        for d in docs:
            holders.setdefault(d, []).append(i)
    uf = _UF(len(flat))
    checked: set[tuple[int, int]] = set()
    for idx in holders.values():
        for x in range(len(idx)):
            for y in range(x + 1, len(idx)):
                i, j = idx[x], idx[y]
                if (i, j) in checked:
                    continue
                checked.add((i, j))
                shared = len(doc_sets[i] & doc_sets[j])
                if shared / min(len(doc_sets[i]), len(doc_sets[j])) >= cfg.CROSS_ENTITY_OVERLAP:
                    uf.union(i, j)

    # entities each document was found for
    doc_entities: dict[str, set[str]] = {}
    for key, s in flat:
        for a in s.members:
            doc_entities.setdefault(a.doc_id, set()).update(a.entities + [key])

    groups = sorted(uf.groups(), key=lambda g: -sum(len(doc_sets[i]) for i in g))
    assigned: set[str] = set()
    out: list[Story] = []
    for g in groups:
        docs: dict[str, Article] = {}
        ents: list[str] = []
        for i in g:
            key, s = flat[i]
            for a in s.members:
                if a.doc_id in assigned or a.doc_id in docs:
                    continue
                docs[a.doc_id] = a
            if key not in ents:
                ents.append(key)
        if not docs:
            continue
        assigned.update(docs)
        for a in docs.values():
            a.entities = sorted(doc_entities[a.doc_id])
        label = max((flat[i][1] for i in g), key=lambda s: len(s.members)).label
        out.append(Story(members=list(docs.values()), label=label, entities=ents).compute_stats())
    return out


def dedupe_top(stories: list[Story], llm: LLM | None, top_k: int | None = None) -> list[Story]:
    """
    Final de-duplication of the leading candidates: the same event can still be
    split across LLM batches or entities. The model (or label similarity) is
    asked which of the top_k story labels describe the same specific event;
    those stories are merged and their statistics recomputed. Expects stories
    sorted by provisional score.
    """
    top_k = top_k or cfg.DEDUPE_TOP_K
    head, tail = stories[:top_k], stories[top_k:]
    if len(head) < 2:
        return stories
    merged = _merge_batches(llm, "the tracked entities", head)
    # map each merged group back to the original stories it contains
    origin = {a.doc_id: i for i, h in enumerate(head) for a in h.members}
    out: list[Story] = []
    for m in merged:
        idx = sorted({origin[a.doc_id] for a in m.members})
        # safety net: only merge stories that share at least one entity
        uf = _UF(len(idx))
        for x in range(len(idx)):
            for y in range(x + 1, len(idx)):
                if set(head[idx[x]].entities) & set(head[idx[y]].entities):
                    uf.union(x, y)
        for g in uf.groups():
            parts = [head[idx[k]] for k in g]
            if len(parts) == 1:
                out.append(parts[0])
                continue
            docs: dict[str, Article] = {}
            ents: list[str] = []
            for h in parts:
                ents += [e for e in h.entities if e not in ents]
                for a in h.members:
                    docs.setdefault(a.doc_id, a)
            label = max(parts, key=lambda h: len(h.members)).label
            out.append(Story(members=list(docs.values()), label=label, entities=ents).compute_stats())
    return out + tail


def entity_label(key: str, names: dict[str, str]) -> str:
    name = names.get(key, "")
    return f"{name} ({key})" if name and name != key else key


def cluster_all(
    articles: dict[str, list[Article]], llm: LLM | None, names: dict[str, str] | None = None
) -> list[Story]:
    """Cluster each entity's articles (all LLM batches in parallel), then merge across entities."""
    names = names or {}
    keys = [k for k, v in articles.items() if v]
    if llm is None:
        return merge_across_entities({k: cluster_lexical(articles[k]) for k in keys})

    size = cfg.CLUSTER_BATCH_SIZE
    tasks = [(k, articles[k][i:i + size]) for k in keys for i in range(0, len(articles[k]), size)]
    res = run_parallel(
        lambda t: _cluster_batch(llm, entity_label(t[0], names), t[1]),
        tasks, label=lambda t: f"clustering {t[0]}", workers=cfg.LLM_CONCURRENCY,
    )
    per_entity: dict[str, list[Story]] = {k: [] for k in keys}
    n_batches: dict[str, int] = {k: 0 for k in keys}
    for i, (k, batch) in enumerate(tasks):
        # a failed batch falls back to lexical grouping for that batch only
        per_entity[k] += res.get(i) or cluster_lexical(batch)
        n_batches[k] += 1

    multi = [k for k in keys if n_batches[k] > 1]
    merged = run_parallel(
        lambda k: _merge_batches(llm, entity_label(k, names), per_entity[k]),
        multi, label=lambda k: f"merging batches {k}", workers=cfg.LLM_CONCURRENCY,
    )
    for i, k in enumerate(multi):
        if i in merged:
            per_entity[k] = merged[i]
    return merge_across_entities(per_entity)
