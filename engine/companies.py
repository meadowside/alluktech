"""Tags stories with the companies they're about. Pure pattern matching, no AI.

Adapted from the All Tech Stocks ticker tagger. A company is found by any of:
  - its aliases from config/companies.yaml, whole words, any case ("Monzo", "monzo's")
  - its exact aliases, whole words, exact capitals ("Arm", "Sky")
Phrases in a company's ignore list are blanked out first ("investment arm", "Sky News").

Used only for the coverage history in engine/trends.py. It doesn't change what the site or the
email shows.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import CONFIG_DIR


@dataclass
class Company:
    id: str
    name: str
    group: str = ""
    aliases: list[str] = field(default_factory=list)
    exact: list[str] = field(default_factory=list)
    ignore: list[str] = field(default_factory=list)


def load_companies(path: Path = CONFIG_DIR / "companies.yaml") -> list[Company]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    out = [Company(**{k: (str(v) if k in ("id", "name", "group") else [str(x) for x in v])
                      for k, v in c.items()}) for c in raw.get("companies", [])]
    ids = [c.id for c in out]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"companies.yaml: these ids are used twice: {', '.join(sorted(dupes))}")
    return out


def _alternation(words: list[str]) -> str:
    return "|".join(sorted((re.escape(w) for w in words), key=len, reverse=True))


class CompanyTagger:
    def __init__(self, companies: list[Company]):
        self.companies = companies
        self.by_id = {c.id: c for c in companies}
        loose: dict[str, str] = {}
        exact: dict[str, str] = {}
        for c in companies:
            for a in c.aliases:
                loose[a.lower()] = c.id
            for a in c.exact:
                exact[a] = c.id
        self.loose_map, self.exact_map = loose, exact
        ignore = [p for c in companies for p in c.ignore]
        self.ignore_re = re.compile(r"(?<![\w])(" + _alternation(ignore) + r")(?![\w])", re.IGNORECASE) if ignore else None
        self.loose_re = re.compile(r"(?<![\w])(" + _alternation(list(loose)) + r")(?![\w])", re.IGNORECASE) if loose else None
        self.exact_re = re.compile(r"(?<![\w])(" + _alternation(list(exact)) + r")(?![\w])") if exact else None

    def find(self, text: str) -> set[str]:
        """Every tracked company mentioned in the text."""
        if not text:
            return set()
        if self.ignore_re:
            text = self.ignore_re.sub(" ", text)
        found: set[str] = set()
        if self.loose_re:
            found |= {self.loose_map[m.lower()] for m in self.loose_re.findall(text)}
        if self.exact_re:
            found |= {self.exact_map[m] for m in self.exact_re.findall(text)}
        return found

    def tag_story(self, items: list) -> tuple[list[str], set[str]]:
        """Returns (companies, companies named in a headline) for a group of articles.

        Same rule as All Tech Stocks: a company belongs to the story only if enough of the coverage
        is about it. Named in at least a quarter of the headlines (and in two or more once a story has
        three or more articles), or in the text of half the articles. One stray mention doesn't count.
        """
        n = len(items)
        title_hits: dict[str, int] = {}
        text_hits: dict[str, int] = {}
        for i in items:
            title, summary = i["title"] or "", i["summary"] or ""
            t = self.find(title)
            for cid in t:
                title_hits[cid] = title_hits.get(cid, 0) + 1
            for cid in t | self.find(summary):
                text_hits[cid] = text_hits.get(cid, 0) + 1
        need_title = max(1 if n < 3 else 2, -(-n // 4))
        in_title = {cid for cid, k in title_hits.items() if k >= need_title}
        in_text = {cid for cid, k in text_hits.items() if k >= max(2, -(-n // 2))}
        keep = sorted(in_title | in_text, key=lambda cid: (-title_hits.get(cid, 0), -text_hits[cid], cid))
        return keep, in_title
