"""Pulls every feed and stores new items."""
from __future__ import annotations

import html
import re
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser

from .config import Config, Source
from .db import DB, iso
from .rules import Rules

USER_AGENT = "AllUKTechBot/1.0 (+https://alluktech.com)"
MAX_AGE_HOURS = 72
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


def clean(text: str | None, limit: int | None = None) -> str:
    if not text:
        return ""
    # Some feeds send their HTML escaped (&lt;p&gt;), so decode and strip twice.
    for _ in range(2):
        text = TAG_RE.sub(" ", html.unescape(text))
    text = SPACE_RE.sub(" ", text).strip()
    if limit and len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "..."
    return text


def _entry_time(entry, fallback: datetime) -> datetime:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:  # feedparser normalises these to UTC
            return datetime(*t[:6], tzinfo=timezone.utc)
    return fallback


def _google_outlet(entry, cfg: Config, feed_source: Source) -> tuple[str | None, str, str]:
    """Google News items name the real outlet in <source>. Returns (outlet_id, outlet_name, clean title)."""
    title = clean(entry.get("title"))
    src = entry.get("source") or {}
    outlet_name = clean(src.get("title")) or feed_source.name
    # Google appends " - Outlet" to the title.
    if outlet_name and title.endswith(" - " + outlet_name):
        title = title[: -len(" - " + outlet_name)].strip()
    matched = cfg.source_for_url(src.get("href", ""))
    if matched:
        return matched.id, matched.name, title
    # A site-specific query (The Times, Reuters) stands in for that outlet.
    if not feed_source.is_backfill:
        return feed_source.id, feed_source.name, title
    return None, outlet_name, title


def fetch_all(cfg: Config, db: DB, rules: Rules, now: datetime, fixtures: Path | None = None,
              timeout: int = 20) -> dict:
    socket.setdefaulttimeout(timeout)
    stats = {"sources": 0, "ok": 0, "new": 0, "excluded": 0}
    oldest = now - timedelta(hours=MAX_AGE_HOURS)

    for source in cfg.sources:
        stats["sources"] += 1
        target = source.url
        if fixtures is not None:
            fx = fixtures / f"{source.id}.xml"
            if not fx.exists():
                continue
            target = str(fx)
        try:
            parsed = feedparser.parse(target, agent=USER_AGENT)
            if parsed.get("bozo") and not parsed.entries:
                raise RuntimeError(str(parsed.get("bozo_exception") or "unreadable feed"))
            status = parsed.get("status")
            if status and status >= 400:
                raise RuntimeError(f"HTTP {status}")
        except Exception as e:  # one dead feed must never stop the run
            db.record_health(source.id, now, False, 0, f"{type(e).__name__}: {e}"[:300])
            continue

        new = 0
        for entry in parsed.entries:
            url = entry.get("link") or ""
            if not url:
                continue
            published = _entry_time(entry, now)
            if published < oldest or published > now + timedelta(hours=2):
                continue

            if source.google:
                outlet_id, outlet_name, title = _google_outlet(entry, cfg, source)
            else:
                outlet_id, outlet_name, title = source.id, source.name, clean(entry.get("title"))
            if not title:
                continue
            summary = clean(entry.get("summary") or entry.get("description"), 300)
            outlet = cfg.by_id.get(outlet_id) if outlet_id else None
            hint_source = outlet or source

            text = f"{title}. {summary}"
            excluded = rules.exclusion_reason(title, url, summary)
            relevance = rules.uk_relevance(text, hint_source)
            section = rules.pick_section(rules.section_hits(text), hint_source.sections)

            row = {
                "guid": entry.get("id") or url,
                "source_id": source.id,
                "outlet_id": outlet_id,
                "outlet_name": outlet_name,
                "title": title,
                "url": url,
                "summary": summary,
                "published": iso(published),
                "fetched": iso(now),
                "excluded": excluded,
                "relevance": relevance,
                "section": section,
            }
            if db.add_item(row):
                new += 1
                if excluded:
                    stats["excluded"] += 1

        db.record_health(source.id, now, True, len(parsed.entries), None)
        stats["ok"] += 1
        stats["new"] += new
    return stats
