"""SQLite storage. One small file, committed back to the repo after each run."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    guid TEXT UNIQUE NOT NULL,
    source_id TEXT NOT NULL,        -- the feed it came from
    outlet_id TEXT,                 -- the real outlet (differs for Google News items)
    outlet_name TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    summary TEXT,
    published TEXT NOT NULL,        -- ISO 8601, UTC
    fetched TEXT NOT NULL,
    excluded TEXT,                  -- reason, or NULL if kept
    relevance INTEGER,
    section TEXT
);
CREATE INDEX IF NOT EXISTS items_published ON items(published);

CREATE TABLE IF NOT EXISTS sent (
    id INTEGER PRIMARY KEY,
    sent_date TEXT NOT NULL,
    item_ids TEXT NOT NULL,         -- JSON list
    coverage REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS fb_posts (
    id INTEGER PRIMARY KEY,
    posted_at TEXT NOT NULL,
    item_ids TEXT NOT NULL,
    url TEXT NOT NULL,
    result TEXT
);

CREATE TABLE IF NOT EXISTS emails (
    date TEXT PRIMARY KEY,
    built_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS why_lines (
    item_id INTEGER PRIMARY KEY,   -- every item in the story points at the same line
    line TEXT NOT NULL,
    created TEXT NOT NULL
);

-- Every story a tracked company has appeared in, kept for a month. The raw material for
-- company_daily below. Companies come from config/companies.yaml. engine/trends.py
CREATE TABLE IF NOT EXISTS company_stories (
    id INTEGER PRIMARY KEY,
    company TEXT NOT NULL,
    item_ids TEXT NOT NULL,         -- JSON list, used to recognise the same story next run
    headline TEXT NOT NULL,
    outlets INTEGER NOT NULL,       -- how many outlets covered the story
    coverage REAL NOT NULL,         -- those outlets at their weights (national 3, UK specialist 2, others 1)
    in_headline INTEGER NOT NULL,   -- 1 if the company is named in the headlines
    section TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS company_stories_company ON company_stories(company, last_seen);

-- Trend history. One row per company per UK day, kept forever (a hundred or so rows a day at most).
-- Rebuilt from company_stories for the last 29 days on every run, then frozen. engine/trends.py
CREATE TABLE IF NOT EXISTS company_daily (
    date TEXT NOT NULL,             -- UK date, YYYY-MM-DD, of the story's first article
    company TEXT NOT NULL,
    stories INTEGER NOT NULL,       -- stories about the company
    headline_stories INTEGER NOT NULL,
    outlets INTEGER NOT NULL,       -- outlets summed across those stories
    attention REAL NOT NULL,        -- weighted coverage, full weight in a headline, half in the text only
    PRIMARY KEY (date, company)
);

-- Kept articles per section per UK day, kept forever. Rebuilt for the last 13 days on every run.
CREATE TABLE IF NOT EXISTS section_daily (
    date TEXT NOT NULL,
    section TEXT NOT NULL,
    items INTEGER NOT NULL,
    PRIMARY KEY (date, section)
);

CREATE TABLE IF NOT EXISTS source_health (
    source_id TEXT PRIMARY KEY,
    last_attempt TEXT,
    last_ok TEXT,
    last_error TEXT,
    last_count INTEGER
);
"""


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self):
        self.conn.commit()
        self.conn.close()

    # ----- items -----
    def add_item(self, row: dict) -> bool:
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO items
               (guid, source_id, outlet_id, outlet_name, title, url, summary, published, fetched,
                excluded, relevance, section)
               VALUES (:guid, :source_id, :outlet_id, :outlet_name, :title, :url, :summary, :published,
                       :fetched, :excluded, :relevance, :section)""",
            row,
        )
        return cur.rowcount == 1

    def items_since(self, since: datetime, include_excluded: bool = False) -> list[sqlite3.Row]:
        q = "SELECT * FROM items WHERE published >= ?"
        if not include_excluded:
            q += " AND excluded IS NULL"
        return self.conn.execute(q + " ORDER BY published", (iso(since),)).fetchall()

    def prune(self, now: datetime, keep_days: int = 14):
        cutoff = iso(now - timedelta(days=keep_days))
        self.conn.execute("DELETE FROM items WHERE published < ?", (cutoff,))
        self.conn.execute("DELETE FROM sent WHERE sent_date < ?", (cutoff[:10],))
        self.conn.execute("DELETE FROM fb_posts WHERE posted_at < ?", (cutoff,))
        self.conn.execute("DELETE FROM why_lines WHERE created < ?", (cutoff,))
        # A month of company stories feeds the trend history. company_daily and section_daily are never pruned.
        self.conn.execute("DELETE FROM company_stories WHERE last_seen < ?", (iso(now - timedelta(days=30)),))

    # ----- sent stories -----
    def sent_records(self, except_date: str | None = None) -> list[tuple[set[int], float]]:
        """Stories already emailed. except_date leaves out that day's own email, so a rebuild
        of today's email isn't emptied by today's first build."""
        rows = self.conn.execute("SELECT item_ids, coverage FROM sent WHERE sent_date != ?",
                                 (except_date or "",)).fetchall()
        return [(set(json.loads(r["item_ids"])), r["coverage"]) for r in rows]

    def clear_sent(self, date: str):
        self.conn.execute("DELETE FROM sent WHERE sent_date = ?", (date,))

    def record_sent(self, date: str, item_ids: list[int], coverage: float):
        self.conn.execute(
            "INSERT INTO sent (sent_date, item_ids, coverage) VALUES (?, ?, ?)",
            (date, json.dumps(item_ids), coverage),
        )

    # ----- emails -----
    def email_built(self, date: str) -> bool:
        return self.conn.execute("SELECT 1 FROM emails WHERE date = ?", (date,)).fetchone() is not None

    def record_email(self, date: str, now: datetime):
        self.conn.execute("INSERT OR REPLACE INTO emails (date, built_at) VALUES (?, ?)", (date, iso(now)))

    # ----- facebook -----
    def fb_posted_item_ids(self) -> set[int]:
        out: set[int] = set()
        for r in self.conn.execute("SELECT item_ids FROM fb_posts").fetchall():
            out |= set(json.loads(r["item_ids"]))
        return out

    def fb_posted_in_hour(self, hour_start: datetime) -> bool:  # i.e. posted since this time
        return self.conn.execute(
            "SELECT 1 FROM fb_posts WHERE posted_at >= ?", (iso(hour_start),)
        ).fetchone() is not None

    def record_fb_post(self, now: datetime, item_ids: list[int], url: str, result: str):
        self.conn.execute(
            "INSERT INTO fb_posts (posted_at, item_ids, url, result) VALUES (?, ?, ?, ?)",
            (iso(now), json.dumps(item_ids), url, result),
        )

    # ----- AI "so what" lines -----
    def why_line(self, item_ids: list[int]) -> str | None:
        if not item_ids:
            return None
        q = "SELECT line FROM why_lines WHERE item_id IN (%s) ORDER BY created LIMIT 1" % ",".join("?" * len(item_ids))
        row = self.conn.execute(q, item_ids).fetchone()
        if not row:
            return None
        return row["line"].strip().split("\n")[0].strip()  # older lines may carry a stray note

    def save_why_line(self, item_ids: list[int], line: str, now: datetime):
        self.conn.executemany(
            "INSERT OR IGNORE INTO why_lines (item_id, line, created) VALUES (?, ?, ?)",
            [(i, line, iso(now)) for i in item_ids],
        )

    def calls_today(self, day_start: datetime) -> int:
        row = self.conn.execute(
            "SELECT COUNT(DISTINCT line) AS n FROM why_lines WHERE created >= ?", (iso(day_start),)
        ).fetchone()
        return row["n"] or 0

    # ----- company coverage history (engine/trends.py) -----
    def company_stories_sharing(self, item_ids: list[int], near: datetime) -> list[sqlite3.Row]:
        """Saved company stories (any company) that share an article with this story."""
        ids = set(item_ids)
        rows = self.conn.execute("SELECT id, item_ids, first_seen FROM company_stories WHERE last_seen >= ?",
                                 (iso(near - timedelta(days=4)),)).fetchall()
        return [r for r in rows if ids & set(json.loads(r["item_ids"]))]

    def delete_company_stories(self, ids: list[int]):
        self.conn.executemany("DELETE FROM company_stories WHERE id = ?", [(i,) for i in ids])

    def save_company_story(self, company: str, item_ids: list[int], row: dict):
        self.conn.execute(
            """INSERT INTO company_stories (company, item_ids, headline, outlets, coverage, in_headline,
                                            section, first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (company, json.dumps(sorted(item_ids)), row["headline"], row["outlets"], row["coverage"],
             int(row["in_headline"]), row["section"], row["first_seen"], row["last_seen"]))

    # ----- source health -----
    def record_health(self, source_id: str, now: datetime, ok: bool, count: int, error: str | None):
        prev = self.conn.execute("SELECT last_ok FROM source_health WHERE source_id = ?", (source_id,)).fetchone()
        last_ok = iso(now) if ok else (prev["last_ok"] if prev else None)
        self.conn.execute(
            """INSERT OR REPLACE INTO source_health (source_id, last_attempt, last_ok, last_error, last_count)
               VALUES (?, ?, ?, ?, ?)""",
            (source_id, iso(now), last_ok, None if ok else error, count),
        )

    def health(self) -> dict[str, sqlite3.Row]:
        return {r["source_id"]: r for r in self.conn.execute("SELECT * FROM source_health").fetchall()}
