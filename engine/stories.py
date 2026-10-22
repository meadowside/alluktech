"""Groups items into stories (clusters) and scores them."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from .config import Config, Source
from .db import DB, parse_iso
from .rules import Rules

# Which outlet's headline and link we prefer: national, then UK specialist, global, primary, backfill.
TIER_RANK = {1: 0, 2: 1, 3: 2, 4: 3, 0: 4}
EXCLUSIVE_RE = re.compile(r"\bexclusive\b", re.IGNORECASE)


@dataclass
class Outlet:
    key: str
    name: str
    source: Source | None
    items: list = field(default_factory=list)

    @property
    def tier(self) -> int:
        return self.source.tier if self.source else 3

    @property
    def weight(self) -> float:
        return self.source.weight if self.source else 1.0

    @property
    def domain(self) -> str | None:
        return self.source.domain if self.source else None


@dataclass
class Story:
    items: list
    outlets: dict[str, Outlet]
    first_seen: datetime
    last_seen: datetime
    coverage: float = 0.0
    relevance: int = 1
    score: float = 0.0
    section: str = ""
    is_global: bool = False
    eligible: bool = True
    why_not: str | None = None
    headline: str = ""
    teaser: str = ""
    link: str = ""
    link_outlet: Outlet | None = None
    paywall: bool = False
    link_options: list = field(default_factory=list)
    exclusive: bool = False
    regulator: bool = False
    why: str = ""
    so_what: str = ""   # AI line, when there is one

    @property
    def key(self) -> int:
        return min(i["id"] for i in self.items)

    @property
    def item_ids(self) -> list[int]:
        return sorted(i["id"] for i in self.items)

    @property
    def source_count(self) -> int:
        return len(self.outlets)

    def use_link(self, outlet: Outlet, url: str) -> None:
        self.link_outlet, self.link = outlet, url
        self.paywall = bool(outlet.source and outlet.source.paywall)

    def sources(self) -> list[tuple[Outlet, dict]]:
        """Every outlet with a link to its own article, national first. Prefers real article
        links over Google News redirects."""
        out = []
        for o in sorted(self.outlets.values(), key=lambda o: (TIER_RANK.get(o.tier, 9), o.name)):
            item = next((i for i in o.items if "news.google.com" not in i["url"]), o.items[0])
            out.append((o, item))
        return out

    def other_coverage(self, limit: int = 6) -> list[tuple[Outlet, dict]]:
        """One link per other outlet, best outlets first, for the 'more coverage' row."""
        out = []
        for o in sorted(self.outlets.values(), key=lambda o: (TIER_RANK.get(o.tier, 9), o.name)):
            if o is self.link_outlet:
                continue
            item = next((i for i in o.items if "news.google.com" not in i["url"]), o.items[0])
            out.append((o, item))
        return out[:limit]


def _outlet_key(item) -> str:
    return item["outlet_id"] or f"name:{item['outlet_name'].lower()}"


def cluster(items: list, cfg: Config) -> list[list]:
    """Greedy clustering on TF-IDF vectors. Each item joins the closest open story or starts a new one."""
    if not items:
        return []
    c = cfg.clustering
    window = timedelta(hours=c["window_hours"])
    close_after = timedelta(hours=c["close_after_hours"])
    t_link = float(c["min_link_similarity"])
    t_centroid = float(c["min_centroid_similarity"])

    texts = [f"{i['title']}. {i['title']}. {i['summary'] or ''}" for i in items]  # title counts double
    vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True)
    X = vec.fit_transform(texts).toarray()

    # An item joins the story whose closest member is most like it, provided it's also
    # reasonably close to the story as a whole (stops one loose match chaining two stories).
    groups: list[dict] = []  # {"idx": [...], "sum": vector, "first": dt, "last": dt}
    for idx, item in enumerate(items):
        t = parse_iso(item["published"])
        v = X[idx]
        best, best_sim = None, 0.0
        for g in groups:
            if t - g["last"] > window or t - g["first"] > close_after:
                continue
            link = max(float(np.dot(X[m], v)) for m in g["idx"])
            centroid = g["sum"] / (np.linalg.norm(g["sum"]) or 1.0)
            if link >= t_link and float(np.dot(centroid, v)) >= t_centroid and link > best_sim:
                best, best_sim = g, link
        if best is not None:
            best["idx"].append(idx)
            best["sum"] = best["sum"] + v
            best["last"] = max(best["last"], t)
        else:
            groups.append({"idx": [idx], "sum": v.copy(), "first": t, "last": t})

    # Second pass: merge stories that ended up split (one outlet worded it very differently).
    t_merge = float(c["merge_similarity"])
    merged = True
    while merged and len(groups) > 1:
        merged = False
        cents = np.array([g["sum"] / (np.linalg.norm(g["sum"]) or 1.0) for g in groups])
        sims = cents @ cents.T
        np.fill_diagonal(sims, 0)
        pairs = np.argwhere(np.triu(sims) >= t_merge)
        for a, b in sorted(pairs.tolist(), key=lambda p: -sims[p[0], p[1]]):
            ga, gb = groups[a], groups[b]
            if max(ga["last"], gb["last"]) - min(ga["first"], gb["first"]) > close_after:
                continue
            ga["idx"] += gb["idx"]
            ga["sum"] = ga["sum"] + gb["sum"]
            ga["first"], ga["last"] = min(ga["first"], gb["first"]), max(ga["last"], gb["last"])
            groups.pop(b)
            merged = True
            break
    return [[items[i] for i in g["idx"]] for g in groups]


def _keep(item, cfg: Config, rules: Rules) -> bool:
    """Every article must be about tech on its own, before grouping. Otherwise a general news
    item (oil prices, a job advert) can join a tech story and take over its headline.
    Regulator feeds publish plenty that isn't tech, so their headline itself must be about tech."""
    if rules.exclusion_reason(item["title"], item["url"]):  # rules edited since the item was fetched
        return False
    src = cfg.by_id.get(item["outlet_id"]) if item["outlet_id"] else None
    if src and src.always_tech:
        return True
    if src and src.tier == 4 and src.standalone:
        return rules.is_tech(item["title"])
    return rules.is_tech(f"{item['title']}. {item['summary'] or ''}")


def build_stories(db: DB, cfg: Config, rules: Rules, now: datetime) -> list[Story]:
    since = now - timedelta(hours=cfg.clustering["close_after_hours"])
    items = [i for i in db.items_since(since) if _keep(i, cfg, rules)]
    stories = [_make_story(group, cfg, rules, now) for group in cluster(items, cfg)]
    stories.sort(key=lambda s: s.score, reverse=True)
    return stories


def _make_story(group: list, cfg: Config, rules: Rules, now: datetime) -> Story:
    sc = cfg.scoring
    outlets: dict[str, Outlet] = {}
    for item in group:
        k = _outlet_key(item)
        if k not in outlets:
            src = cfg.by_id.get(item["outlet_id"]) if item["outlet_id"] else None
            outlets[k] = Outlet(key=k, name=item["outlet_name"], source=src)
        outlets[k].items.append(item)

    times = [parse_iso(i["published"]) for i in group]
    s = Story(items=group, outlets=outlets, first_seen=min(times), last_seen=max(times))

    # Coverage: each outlet counts once, at its weight.
    s.coverage = sum(o.weight for o in outlets.values())
    s.relevance = max(int(i["relevance"] or 1) for i in group)
    s.regulator = any(o.source and o.source.tier == 4 and o.source.standalone for o in outlets.values())
    s.exclusive = (
        len(outlets) == 1
        and next(iter(outlets.values())).tier in (1, 2)
        and any(EXCLUSIVE_RE.search(i["title"]) for i in group)
    )
    coverage = max(s.coverage, sc["exclusive_floor"]) if s.exclusive else s.coverage

    # Eligibility.
    always = any(o.source and o.source.always_tech for o in outlets.values())
    if not always and not any(rules.is_tech(f"{i['title']}. {i['summary'] or ''}") for i in group):
        s.eligible, s.why_not = False, "not a tech story"
    elif all(o.source and o.source.confirm for o in outlets.values()):
        s.eligible, s.why_not = False, "primary source with no outlet coverage yet"
    elif s.relevance >= 2:
        s.eligible = True
    elif s.coverage >= sc["global_min_coverage"]:
        s.eligible, s.is_global = True, True
    else:
        s.eligible, s.why_not = False, "no UK angle"

    # Score.
    u = 1.0 if s.relevance >= 3 else 0.7 if s.relevance == 2 else 0.5
    hours = max(0.0, (now - s.last_seen).total_seconds() / 3600)
    fresh = 0.5 ** (hours / sc["freshness_half_life_hours"])
    s.score = min(coverage, sc["coverage_cap"]) * u * fresh + (sc["regulator_bonus"] if s.regulator else 0)

    # Section: keyword hits across every item, tie broken by the lead outlet's hint.
    hits = {sid: 0 for sid in cfg.section_ids}
    for i in group:
        for sid, n in rules.section_hits(f"{i['title']}. {i['summary'] or ''}").items():
            hits[sid] += n

    # Headline from the best outlet; link to the best free, non-redirect copy.
    ranked = sorted(outlets.values(), key=lambda o: (TIER_RANK.get(o.tier, 9), min(parse_iso(i["published"]) for i in o.items)))
    lead = ranked[0]
    lead_item = min(lead.items, key=lambda i: i["published"])
    s.headline = lead_item["title"]
    s.section = rules.pick_section(hits, lead.source.sections if lead.source else [])

    def linkable(o: Outlet, free: bool):
        for i in o.items:
            if "news.google.com" in i["url"]:
                continue
            if free and o.source and o.source.paywall:
                continue
            return i
        return None

    # Every outlet we could link, best first: free before paywalled, real URLs before Google redirects.
    options = []
    for free in (True, False):
        for o in ranked:
            item = linkable(o, free)
            if item and all(opt[0] is not o for opt in options):
                options.append((o, item["url"]))
    for o in ranked:
        if all(opt[0] is not o for opt in options):
            options.append((o, o.items[0]["url"]))
    s.link_options = options
    s.link_outlet, s.link = options[0]
    s.paywall = bool(s.link_outlet.source and s.link_outlet.source.paywall)

    s.teaser = best_teaser(group, s.headline)
    return s


WORD_RE = re.compile(r"[a-z0-9£$]+")


def best_teaser(group: list, headline: str) -> str:
    """The summary that shares most words with the headline. Roundup posts often carry a summary
    about a different story, so the link's own summary can't be trusted."""
    head = {w for w in WORD_RE.findall(headline.lower()) if len(w) > 3}
    best, best_overlap = "", 0
    for i in group:
        if "news.google.com" in i["url"]:  # Google's "summary" is just the headline again
            continue
        text = first_sentence(i["summary"] or "", headline)
        overlap = len(head & set(WORD_RE.findall(text.lower())))
        if overlap > best_overlap:
            best, best_overlap = text, overlap
    return best if best_overlap >= 2 else ""


def first_sentence(text: str, headline: str, limit: int = 180) -> str:
    text = (text or "").strip()
    if not text or text.lower().startswith(headline.lower()[:40]):
        return ""
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    out = m.group(1) if m else text
    if len(out) > limit:
        out = out[:limit].rsplit(" ", 1)[0] + "..."
    return out


def why_template(story: Story, rank_in_section: int, overall_rank: int, cfg: Config) -> str:
    """The free 'why it matters' line, built from the data."""
    big = [o.name for o in sorted(story.outlets.values(), key=lambda o: TIER_RANK.get(o.tier, 9)) if o.tier == 1][:2]
    n = story.source_count
    parts = []
    if n == 1:
        parts.append(f"Only {story.link_outlet.name} has this so far." if not story.exclusive
                     else f"A {story.link_outlet.name} exclusive.")
    else:
        lead = f"Covered by {n} outlets"
        if big:
            lead += " including " + " and ".join(big)
        parts.append(lead + ".")
    if story.regulator:
        reg = next(o.name for o in story.outlets.values() if o.source and o.source.tier == 4 and o.source.standalone)
        parts.append(f"Includes the official word from {reg}.")
    if overall_rank == 1:
        parts.append("Most covered UK tech story today.")
    elif rank_in_section == 1:
        parts.append(f"Top {cfg.section_names[story.section]} story today.")
    return " ".join(parts)
