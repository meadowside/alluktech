"""Exclusions, UK relevance and section assignment. Pure keyword rules, no AI."""
from __future__ import annotations

import re

from .config import Config, Source


def _phrase_regex(phrases: list[str]) -> re.Pattern | None:
    words = [p for p in phrases if p and p != "£"]
    if not words:
        return None
    alts = sorted((re.escape(p.lower()) for p in words), key=len, reverse=True)
    return re.compile(r"(?<![\w])(" + "|".join(alts) + r")(?![\w])", re.IGNORECASE)


class Rules:
    def __init__(self, cfg: Config):
        r = cfg.rules
        ex = r["exclude"]
        self.url_patterns = [p.lower() for p in ex["url_patterns"]]
        self.title_keywords = [k.lower() for k in ex["title_keywords"]]
        self.title_exceptions = [k.lower() for k in ex.get("title_keyword_exceptions", [])]

        uk = r["uk"]
        self.uk_strong = _phrase_regex(uk["strong"])
        self.uk_weak = _phrase_regex(uk["weak"])
        self.pound_is_weak = "£" in uk["weak"]
        self.uk_outlet_floor = int(uk.get("uk_outlet_floor", 1))

        self.section_order = cfg.section_ids
        self.section_res = {sid: _phrase_regex(words) for sid, words in r["section_keywords"].items()}
        self.tech_re = _phrase_regex(r.get("tech_keywords", []))
        self.tech_sections = r.get("tech_sections", list(self.section_res))

    def is_tech(self, text: str) -> bool:
        if self.tech_re and self.tech_re.search(text):
            return True
        return any(self.section_res.get(sid) and self.section_res[sid].search(text) for sid in self.tech_sections)

    # ---------- exclusions ----------
    def exclusion_reason(self, title: str, url: str) -> str | None:
        u = (url or "").lower()
        for p in self.url_patterns:
            if p in u:
                return f"url:{p}"
        t = f" {(title or '').lower()} "
        for k in self.title_keywords:
            if k in t:
                if any(e in t for e in self.title_exceptions) and k in ("review:", "hands-on", "hands on:"):
                    continue
                return f"title:{k.strip()}"
        return None

    # ---------- UK relevance ----------
    def uk_relevance(self, text: str, source: Source | None) -> int:
        """3 = UK company/regulator/place named, 2 = UK mentioned, 1 = no UK signal."""
        if source is not None and source.tier == 4 and source.standalone:
            return 3  # UK regulators are UK by definition
        if self.uk_strong and self.uk_strong.search(text):
            score = 3
        elif (self.uk_weak and self.uk_weak.search(text)) or (self.pound_is_weak and "£" in text):
            score = 2
        else:
            score = 1
        if source is not None and source.is_uk_outlet:
            score = max(score, self.uk_outlet_floor)
        return score

    # ---------- sections ----------
    def section_hits(self, text: str) -> dict[str, int]:
        hits = {}
        for sid in self.section_order:
            rx = self.section_res.get(sid)
            hits[sid] = len(rx.findall(text)) if rx else 0
        return hits

    def pick_section(self, hits: dict[str, int], hint: list[str]) -> str:
        best = max(hits.values()) if hits else 0
        if best == 0:
            for h in hint:
                if h in self.section_order:
                    return h
            return self.section_order[0]
        # Most hits wins; on a tie prefer the source's own hint, then config order.
        tied = [s for s in self.section_order if hits[s] == best]
        for h in hint:
            if h in tied:
                return h
        return tied[0]
