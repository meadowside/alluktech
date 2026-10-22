"""Loads config/sources.yaml and config/rules.yaml."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


@dataclass
class Source:
    id: str
    name: str
    url: str
    tier: int
    weight: float
    sections: list[str] = field(default_factory=list)
    domain: str | None = None
    paywall: bool = False
    standalone: bool = False
    confirm: bool = False
    uk_angle: bool = False
    google: bool = False

    @property
    def is_backfill(self) -> bool:
        return self.tier == 0

    @property
    def is_uk_outlet(self) -> bool:
        """Tier 1 and 2 outlets that cover the UK by default."""
        return self.tier in (1, 2) and not self.uk_angle


def _host(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


class Config:
    def __init__(self, config_dir: Path = CONFIG_DIR):
        with open(config_dir / "sources.yaml", encoding="utf-8") as f:
            raw_sources = yaml.safe_load(f)["sources"]
        with open(config_dir / "rules.yaml", encoding="utf-8") as f:
            self.rules = yaml.safe_load(f)

        self.sources = [Source(**s) for s in raw_sources]
        self.by_id = {s.id: s for s in self.sources}
        self.sections = self.rules["sections"]
        self.section_ids = [s["id"] for s in self.sections]
        self.section_names = {s["id"]: s["name"] for s in self.sections}
        self.site = self.rules["site"]
        self.scoring = self.rules["scoring"]
        self.clustering = self.rules["clustering"]

    def source_for_url(self, url: str) -> Source | None:
        """Match an article URL (or a Google News <source> URL) to a known, non-backfill outlet."""
        if not url:
            return None
        host = _host(url)
        best = None
        for s in self.sources:
            if not s.domain or s.is_backfill:
                continue
            d = s.domain.lower()
            if host == d or host.endswith("." + d):
                # Prefer outlets over primary sources that share a domain (gov.uk), then the longest match.
                if best is None or len(d) > len(best.domain):
                    best = s
        return best
