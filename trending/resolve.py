"""
Step 1 - Resolve tickers / names to Bigdata.com entity IDs (Knowledge Graph).

Rules
* IDs only ever come from the Knowledge Graph (or the verified defaults in
  config.KNOWN_COMPANY_IDS). Nothing is invented.
* When a name matches several entities (e.g. "Federal Reserve" matches the
  central bank and a dozen regional Fed banks), we keep the best name matches
  and pick the one with the most news coverage over the past week - the entity
  the news actually talks about.
* A name that cannot be resolved is either skipped (watchlist) or run in
  keyword mode (commodities: search by text, no entity filter).
* Results are cached locally in fixtures/entities.json; print the table and sanity-check it.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from . import cache
from . import config as cfg
from .bigdata import BigdataClient, entity_filter, news_filters


@dataclass
class Entity:
    key: str                    # short display label, e.g. "AAPL", "Gold"
    input: str                  # what the user typed
    kind: str                   # "company" | "free_text" | "commodity"
    id: str | None = None       # Bigdata entity ID (None in keyword mode)
    name: str = ""              # official KG name
    type: str = ""              # KG type, e.g. PUBLIC, Central Bank, Commodity
    category: str = ""          # KG category, e.g. companies, concepts
    mode: str = "entity"        # "entity" (filter by ID) | "keyword" (text only)
    texts: list[str] = field(default_factory=list)  # optional search texts
    note: str = ""              # how it was resolved

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# name matching helpers
# --------------------------------------------------------------------------- #
_STOP = {"the", "of", "inc", "corp", "co", "ltd", "plc", "group", "and", "&"}


def _tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", s.lower()) if t not in _STOP}


def name_score(query: str, name: str) -> float:
    """1.0 exact; otherwise share of the name's words covered by the query."""
    q, n = _tokens(query), _tokens(name)
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0
    if q <= n:
        return 0.3 + 0.6 * len(q) / len(n)
    return 0.5 * len(q & n) / len(q | n)


def _news_volume(client: BigdataClient, entity_id: str, as_of: datetime) -> int:
    """Documents mentioning the entity in the past 7 days (used to disambiguate)."""
    filters = news_filters(as_of - timedelta(days=7), as_of)
    filters["entity"] = entity_filter([entity_id], "ALL")
    return int(client.volume(filters).get("total", {}).get("documents", 0))


def _pick_by_volume(
    client: BigdataClient, candidates: list[dict[str, Any]], as_of: datetime, verbose: bool
) -> dict[str, Any] | None:
    """Among the best name matches, pick the entity with the most news."""
    if not candidates:
        return None
    candidates = sorted(candidates, key=lambda c: -c["_score"])[:5]
    for c in candidates:
        try:
            c["_volume"] = _news_volume(client, c["id"], as_of)
        except Exception:  # noqa: BLE001 - a failed lookup just ranks last
            c["_volume"] = -1
    best = max(candidates, key=lambda c: (c["_volume"], c["_score"]))
    if verbose and len(candidates) > 1:
        for c in candidates:
            flag = "→" if c is best else " "
            print(f"     {flag} {c['id']}  {c['name'][:48]:<48} {c['_category']:<13} "
                  f"match={c['_score']:.2f}  7d news={c['_volume']}")
    return best


# --------------------------------------------------------------------------- #
# resolvers
# --------------------------------------------------------------------------- #
def resolve_ticker(client: BigdataClient, ticker: str) -> Entity:
    ent = Entity(key=ticker, input=ticker, kind="company", category="companies")
    known = cfg.KNOWN_COMPANY_IDS.get(ticker.upper())
    match = None
    try:
        results = client.kg_find("companies", ticker, types=["PUBLIC"])
        for r in results:
            listings = [str(x).upper() for x in r.get("listing_values") or []]
            if any(l.endswith(":" + ticker.upper()) for l in listings):
                match = r
                break
    except Exception as exc:  # noqa: BLE001
        ent.note = f"KG lookup failed ({exc}); "

    if match and known and match["id"] != known:
        ent.note += f"KG returned {match['id']}, using verified default {known}"
        ent.id = known
    elif match:
        ent.id = match["id"]
        ent.note += "KG ticker match" + (" (matches verified default)" if known == match["id"] else "")
    elif known:
        ent.id = known
        ent.note += "verified default (not found in KG search)"
    if match:
        ent.name, ent.type = match.get("name", ""), match.get("type", "")
    elif ent.id:
        info = client.kg_by_ids([ent.id]).get(ent.id, {})
        ent.name, ent.type = info.get("name", ticker), info.get("type", "")
    return ent


def resolve_free_text(
    client: BigdataClient, text: str, as_of: datetime, verbose: bool = True
) -> Entity:
    ent = Entity(key=text, input=text, kind="free_text")
    candidates: list[dict[str, Any]] = []
    for category in cfg.FREE_TEXT_CATEGORIES:
        try:
            results = client.kg_find(category, text)
        except Exception:  # noqa: BLE001
            continue
        for r in results[:10]:
            s = name_score(text, r.get("name", ""))
            if s >= 0.35:
                candidates.append({**r, "_score": s, "_category": category})
    if verbose:
        print(f"   {text!r}: {len(candidates)} KG candidates")
    best = _pick_by_volume(client, candidates, as_of, verbose)
    if best:
        ent.id, ent.name, ent.type = best["id"], best.get("name", ""), str(best.get("type", ""))
        ent.category = best["_category"]
        ent.note = f"KG {best['_category']}: best name match with most news"
    else:
        ent.mode, ent.note = "unresolved", "no KG match"
    return ent


def resolve_commodity(
    client: BigdataClient, label: str, spec: dict[str, Any], as_of: datetime, verbose: bool = True
) -> Entity:
    query = spec.get("kg_query", label)
    ent = Entity(key=label, input=query, kind="commodity", texts=list(spec.get("texts", [])))
    candidates: list[dict[str, Any]] = []
    for category in ("concepts", "products"):
        try:
            results = client.kg_find(category, query)
        except Exception:  # noqa: BLE001
            continue
        for r in results:
            if r.get("type") in cfg.COMMODITY_KG_TYPES:
                s = name_score(query, r.get("name", ""))
                if s >= 0.5:
                    candidates.append({**r, "_score": s, "_category": category})
    if verbose:
        print(f"   {label!r}: {len(candidates)} KG commodity candidates")
    best = _pick_by_volume(client, candidates, as_of, verbose)
    if best:
        ent.id, ent.name, ent.type = best["id"], best.get("name", ""), best.get("type", "")
        ent.category, ent.mode = best["_category"], "entity"
        ent.note = f"KG {best['_category']} ({best.get('type')})"
    else:
        ent.mode, ent.name = "keyword", label
        ent.note = "not in KG as a commodity → keyword mode (search by texts)"
    return ent


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def resolve_all(
    client: BigdataClient,
    tickers: list[str] | None = None,
    free_text: list[str] | None = None,
    commodities: dict[str, dict[str, Any]] | None = None,
    as_of: datetime | None = None,
    refresh: bool = False,
    verbose: bool = True,
) -> list[Entity]:
    """
    Resolve everything, using the local fixtures/entities.json cache.
    Set refresh=True to re-query the Knowledge Graph.
    """
    as_of = as_of or datetime.now().astimezone()
    table = cache.load_entities()
    out: list[Entity] = []

    def cached(k: str) -> Entity | None:
        if refresh:
            return None
        d = table.get(k)
        return Entity(**d) if d else None

    for t in tickers or []:
        k = f"ticker:{t.upper()}"
        ent = cached(k) or resolve_ticker(client, t.upper())
        table[k] = ent.to_dict()
        out.append(ent)

    for t in free_text or []:
        k = f"free_text:{t}"
        ent = cached(k) or resolve_free_text(client, t, as_of, verbose)
        table[k] = ent.to_dict()
        out.append(ent)

    for label, spec in (commodities or {}).items():
        k = f"commodity:{label}"
        ent = cached(k)
        if ent is None:
            ent = resolve_commodity(client, label, spec, as_of, verbose)
        ent.texts = list(spec.get("texts", []))  # always honour the current config
        table[k] = ent.to_dict()
        out.append(ent)

    cache.save_entities(table)

    for e in out:
        if e.mode == "unresolved":
            print(f"   ⚠ could not resolve {e.input!r} - it will be skipped")
    return [e for e in out if e.mode != "unresolved"]


def entity_table(entities: list[Entity]) -> pd.DataFrame:
    """Human-readable table: input → ID, name, type, mode."""
    rows = [
        {"input": e.input, "label": e.key, "entity_id": e.id or "—", "name": e.name,
         "kg_type": e.type, "kg_category": e.category or "—", "mode": e.mode, "how": e.note}
        for e in entities
    ]
    return pd.DataFrame(rows)
