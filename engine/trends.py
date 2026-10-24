"""Coverage history and spikes. Adapted from All Tech Stocks (engine/trends.py there).

Every hourly run:
  record_stories() saves each story that passes the site's rules against the companies it's about
  (company_stories, kept a month).
  record_daily() rolls the last month of those into one row per company per UK day (company_daily)
  and the last fortnight of articles into one row per section per day (section_daily). Those two
  tables are never pruned, so the history grows forever while the rest of the database stays small.

A day keeps changing while stories on it are still picking up outlets, so recent days are rebuilt
on every run. A day stops changing once it's older than the source data: 29 days for companies
(company_stories keeps 30) and 13 for sections (items keeps 14).

Stories only keep growing for 72 hours (clustering.close_after_hours). After that the engine only
sees part of an old story, so a saved story whose first article is older than that is left alone.

spikes() compares a day with the company's own normal level. Everything here describes attention
only: how much the press wrote, never whether the news is good or bad.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date as Date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .companies import CompanyTagger, load_companies
from .config import Config
from .db import DB, iso, parse_iso
from .rules import Rules

COMPANY_DAYS = 29
SECTION_DAYS = 13
HEADLINE_WEIGHT, TEXT_WEIGHT = 1.0, 0.5


def _uk_date(s: str, tz: ZoneInfo) -> str:
    return parse_iso(s).astimezone(tz).strftime("%Y-%m-%d")


def tagger() -> CompanyTagger:
    return CompanyTagger(load_companies())


def record_stories(stories: list, db: DB, cfg: Config, now: datetime, tag: CompanyTagger | None = None,
                   window_start: datetime | None = None) -> int:
    """Save this run's stories against the companies they're about. Returns rows saved."""
    tag = tag or tagger()
    if window_start is None:
        window_start = now - timedelta(hours=cfg.clustering["close_after_hours"])
    start = iso(window_start)
    saved = 0
    for s in stories:
        old = db.company_stories_sharing(s.item_ids, s.last_seen)
        if any(r["first_seen"] < start for r in old):
            continue  # an older story we can only see part of now; keep what was saved while it was whole
        db.delete_company_stories([r["id"] for r in old])
        if not s.eligible:
            continue
        companies, in_headline = tag.tag_story(s.items)
        for cid in companies:
            db.save_company_story(cid, s.item_ids, {
                "headline": s.headline, "outlets": s.source_count, "coverage": s.coverage,
                "in_headline": cid in in_headline, "section": s.section,
                "first_seen": iso(s.first_seen), "last_seen": iso(s.last_seen)})
            saved += 1
    return saved


def backfill(db: DB, cfg: Config, rules: Rules, now: datetime, tag: CompanyTagger | None = None) -> int:
    """First run only: rebuild company stories from every article still in the database (up to 14 days),
    grouped the same way the site groups them. Does nothing once company_stories has rows."""
    if db.conn.execute("SELECT 1 FROM company_stories LIMIT 1").fetchone():
        return 0
    from .stories import _keep, _make_story, cluster
    items = [i for i in db.items_since(datetime(2000, 1, 1, tzinfo=timezone.utc)) if _keep(i, cfg, rules)]
    stories = [_make_story(group, cfg, rules, now) for group in cluster(items, cfg)]
    return record_stories(stories, db, cfg, now, tag, window_start=datetime(2000, 1, 1, tzinfo=timezone.utc))


def record_daily(db: DB, cfg: Config, rules: Rules, now: datetime) -> dict:
    tz = ZoneInfo(cfg.site["timezone"])
    c = db.conn

    # Companies: a story counts on the UK day of its first article.
    company_from = (now - timedelta(days=COMPANY_DAYS)).astimezone(tz).strftime("%Y-%m-%d")
    rows: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"stories": 0, "headline_stories": 0, "outlets": 0, "attention": 0.0})
    for r in c.execute("SELECT company, outlets, coverage, in_headline, first_seen FROM company_stories").fetchall():
        d = _uk_date(r["first_seen"], tz)
        if d < company_from:
            continue
        row = rows[(d, r["company"])]
        row["stories"] += 1
        row["headline_stories"] += r["in_headline"]
        row["outlets"] += r["outlets"]
        row["attention"] += r["coverage"] * (HEADLINE_WEIGHT if r["in_headline"] else TEXT_WEIGHT)
    c.execute("DELETE FROM company_daily WHERE date >= ?", (company_from,))
    c.executemany(
        "INSERT INTO company_daily (date, company, stories, headline_stories, outlets, attention) VALUES (?, ?, ?, ?, ?, ?)",
        [(d, cid, v["stories"], v["headline_stories"], v["outlets"], round(v["attention"], 2))
         for (d, cid), v in rows.items()])

    # Sections: articles that pass the per-article tech check, by UK publication day.
    from .stories import _keep
    section_from = (now - timedelta(days=SECTION_DAYS)).astimezone(tz).strftime("%Y-%m-%d")
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for r in c.execute("SELECT * FROM items WHERE excluded IS NULL AND section IS NOT NULL").fetchall():
        d = _uk_date(r["published"], tz)
        if d >= section_from and _keep(r, cfg, rules):
            counts[(d, r["section"])] += 1
    c.execute("DELETE FROM section_daily WHERE date >= ?", (section_from,))
    c.executemany("INSERT INTO section_daily (date, section, items) VALUES (?, ?, ?)",
                  [(d, sec, n) for (d, sec), n in counts.items()])
    return {"company_rows": len(rows), "section_rows": len(counts)}


def history_days(db: DB) -> int:
    row = db.conn.execute("SELECT MIN(date) AS a, MAX(date) AS b FROM company_daily").fetchone()
    if not row["a"]:
        return 0
    return (Date.fromisoformat(row["b"]) - Date.fromisoformat(row["a"])).days + 1


def spikes(db: DB, day: str, baseline_days: int = 28, min_history: int = 14,
           min_attention: float = 12.0, min_ratio: float = 3.0, min_outlets: int = 4) -> list[dict]:
    """Companies with far more coverage on `day` than their own normal. The baseline is the mean daily
    attention over the previous `baseline_days` days we have data for, with quiet days counted as zero.
    Returns [] until there are `min_history` days of history, because a baseline built on a few days
    is noise. min_attention 12 is roughly four national outlets with the company in the headline."""
    first = db.conn.execute("SELECT MIN(date) AS d FROM company_daily").fetchone()["d"]
    if not first:
        return []
    d = Date.fromisoformat(day)
    start = max(Date.fromisoformat(first), d - timedelta(days=baseline_days))
    span = (d - start).days
    if span < min_history:
        return []
    base = {r["company"]: r["total"] for r in db.conn.execute(
        "SELECT company, SUM(attention) AS total FROM company_daily WHERE date >= ? AND date < ? GROUP BY company",
        (start.isoformat(), day)).fetchall()}
    out = []
    for r in db.conn.execute("SELECT * FROM company_daily WHERE date = ?", (day,)).fetchall():
        normal = base.get(r["company"], 0.0) / span
        ratio = (r["attention"] + 3) / (normal + 3)  # +3 (one national outlet) so a usually silent name can't hit x50 on one story
        # Four or more outlets, in one story or several. (All Tech Stocks asks for two stories; here a big
        # event usually arrives as one story with many outlets, like Barclays' job cuts with 56.)
        if r["attention"] >= min_attention and r["outlets"] >= min_outlets and ratio >= min_ratio:
            out.append({"company": r["company"], "attention": r["attention"], "stories": r["stories"],
                        "outlets": r["outlets"], "normal": round(normal, 1), "ratio": round(ratio, 1)})
    return sorted(out, key=lambda x: -x["ratio"])


def new_on_radar(db: DB, day: str, quiet_days: int = 60, min_attention: float = 6.0) -> list[dict]:
    """Companies covered on `day` after no coverage at all for `quiet_days`. Empty until there's that
    much history, so a name isn't called new just because tracking only started last week."""
    first = db.conn.execute("SELECT MIN(date) AS d FROM company_daily").fetchone()["d"]
    d = Date.fromisoformat(day)
    start = d - timedelta(days=quiet_days)
    if not first or Date.fromisoformat(first) > start:
        return []
    rows = db.conn.execute(
        """SELECT t.* FROM company_daily t WHERE t.date = ? AND t.attention >= ? AND NOT EXISTS
           (SELECT 1 FROM company_daily p WHERE p.company = t.company AND p.date >= ? AND p.date < ?)""",
        (day, min_attention, start.isoformat(), day)).fetchall()
    return [dict(r) for r in sorted(rows, key=lambda r: -r["attention"])]


def section_share(db: DB, days: int = 7, until: str | None = None) -> list[dict]:
    """Each section's share of kept articles over the last `days` days, against the `days` before."""
    until_d = Date.fromisoformat(until) if until else Date.fromisoformat(
        db.conn.execute("SELECT MAX(date) AS d FROM section_daily").fetchone()["d"] or Date.today().isoformat())

    def window(a: Date, b: Date) -> dict[str, int]:
        return {r["section"]: r["n"] for r in db.conn.execute(
            "SELECT section, SUM(items) AS n FROM section_daily WHERE date > ? AND date <= ? GROUP BY section",
            (a.isoformat(), b.isoformat())).fetchall()}
    now_w = window(until_d - timedelta(days=days), until_d)
    prev_w = window(until_d - timedelta(days=2 * days), until_d - timedelta(days=days))
    tn, tp = sum(now_w.values()) or 1, sum(prev_w.values())
    out = []
    for sec in sorted(set(now_w) | set(prev_w)):
        share = now_w.get(sec, 0) / tn
        prev = prev_w.get(sec, 0) / tp if tp else None
        out.append({"section": sec, "items": now_w.get(sec, 0), "share": round(100 * share, 1),
                    "previous_share": round(100 * prev, 1) if prev is not None else None})
    return sorted(out, key=lambda x: -x["share"])


def most_covered(db: DB, day: str, limit: int = 10) -> list[dict]:
    """The day's most covered companies, for checking the tags look right during warm-up."""
    return [dict(r) for r in db.conn.execute(
        "SELECT * FROM company_daily WHERE date = ? ORDER BY attention DESC LIMIT ?", (day, limit)).fetchall()]


def report(db: DB, cfg: Config, now: datetime, day: str | None = None) -> str:
    tz = ZoneInfo(cfg.site["timezone"])
    day = day or (now.astimezone(tz) - timedelta(days=1)).strftime("%Y-%m-%d")  # yesterday is complete
    names = {c.id: c.name for c in load_companies()}
    sections = cfg.section_names
    days = history_days(db)
    lines = [f"Coverage trends for {day} ({days} days of history)"]
    if days < 14:
        lines.append("Warm-up: the first fortnight undercounts, so spikes stay blank until day 14.")

    sp = spikes(db, day)
    lines.append("\nCoverage spikes (attention vs the company's normal day):")
    if not sp:
        lines.append("  none, or not enough history yet (needs 14 days)")
    for x in sp:
        lines.append(f"  {names.get(x['company'], x['company'])}: {x['ratio']}x normal, {x['stories']} stories, "
                     f"{x['outlets']} outlet mentions (attention {x['attention']:g}, normal day {x['normal']})")

    nr = new_on_radar(db, day)
    lines.append("\nNew on the radar (first coverage in 60 days):")
    lines.append("  " + (", ".join(names.get(r["company"], r["company"]) for r in nr)
                         if nr else "none, or not enough history yet (needs 60 days)"))

    lines.append("\nMost covered that day:")
    top = most_covered(db, day)
    if not top:
        lines.append("  nothing recorded for that day")
    for x in top:
        lines.append(f"  {names.get(x['company'], x['company'])}: attention {x['attention']:g}, "
                     f"{x['stories']} stories ({x['headline_stories']} in the headline), {x['outlets']} outlet mentions")

    lines.append("\nSection share, last 7 days (previous 7 days):")
    for x in section_share(db, until=day):
        prev = f"{x['previous_share']}%" if x["previous_share"] is not None else "no data"
        lines.append(f"  {sections.get(x['section'], x['section'])}: {x['share']}% of {x['items']} articles ({prev})")
    return "\n".join(lines)
