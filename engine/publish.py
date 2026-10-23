"""Builds the static site and the morning email."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import llm
from .config import ROOT, Config
from .db import DB, parse_iso
from .stories import Story, why_template

TEMPLATES = ROOT / "templates"
PAGES_UPDATED = "22 October 2026"  # change when you edit the Privacy or Terms pages


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"], default_for_string=False, default=False))
    env.filters["ago"] = _ago
    env.filters["md"] = _md
    return env


_NOW: datetime | None = None


def _md(text: str) -> str:
    """Keep headline text from breaking the Markdown: square brackets and asterisks."""
    return (text or "").replace("[", "(").replace("]", ")").replace("*", "")


def _ago(dt: datetime) -> str:
    mins = int(((_NOW or dt) - dt).total_seconds() // 60)
    if mins < 60:
        return f"{max(mins, 1)}m ago"
    if mins < 60 * 24:
        return f"{mins // 60}h ago"
    return f"{mins // (60 * 24)}d ago"


def _with_why(stories: list[Story], cfg: Config) -> None:
    seen: Counter = Counter()
    for overall, s in enumerate(stories, start=1):
        seen[s.section] += 1
        s.why = why_template(s, seen[s.section], overall, cfg)


# ---------------------------------------------------------------- site

def trending(stories: list[Story], cfg: Config, now: datetime) -> list[dict]:
    """Stories picking up new outlets fastest right now."""
    t = cfg.rules.get("trending", {})
    since = now - timedelta(hours=t.get("window_hours", 6))
    rows = []
    for s in stories:
        new = sum(1 for o in s.outlets.values() if min(parse_iso(i["published"]) for i in o.items) >= since)
        if new >= t.get("min_new_outlets", 2):
            rows.append({"story": s, "new": new})
    rows.sort(key=lambda r: (r["new"], r["story"].coverage), reverse=True)
    return rows[: t.get("max", 6)]


def just_in(stories: list[Story], cfg: Config) -> list[Story]:
    """The newest stories, by when we first saw them, whatever their coverage."""
    n = cfg.rules.get("just_in", {}).get("max", 6)
    return sorted(stories, key=lambda s: s.first_seen, reverse=True)[:n]


def add_so_what(wanted: list[Story], everything: list[Story], db: DB, cfg: Config, now: datetime) -> int:
    """Attach saved AI lines to every story, and write new ones for the stories that need them.
    Each story is written once: the line is saved against all its items and reused from then on."""
    for s in everything:
        s.so_what = db.why_line(s.item_ids) or ""
    ai = cfg.rules.get("ai_lines", {})
    local = now.astimezone(ZoneInfo(cfg.site["timezone"]))
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    budget = min(ai.get("max_new_per_run", 25), ai.get("max_new_per_day", 150) - db.calls_today(day_start))
    made, seen = 0, set()
    for s in wanted:
        if made >= budget:
            break
        if s.so_what or id(s) in seen:
            continue
        seen.add(id(s))
        snippets = [i["summary"] or i["title"] for i in s.items if "news.google.com" not in i["url"]] or [s.headline]
        line = llm.why_it_matters(s.headline, snippets)
        if line is None and not llm.enabled():
            break  # no key: don't loop through every story
        if line:
            s.so_what = line
            db.save_why_line(s.item_ids, line, now)
            made += 1
    return made


def build_site(stories: list[Story], db: DB, cfg: Config, now: datetime, out: Path,
               email_archive: Path | None = None) -> dict:
    global _NOW
    _NOW = now
    live = [s for s in stories if s.eligible and now - s.last_seen <= timedelta(hours=48)]
    _with_why(live, cfg)
    top = next((s for s in live if not s.is_global), None)

    by_section = {sid: [] for sid in cfg.section_ids}
    for s in live:
        if s is top:
            continue
        by_section[s.section].append(s)
    sections = [
        {"id": sec["id"], "name": sec["name"], "stories": by_section[sec["id"]][: sec["site_max"]]}
        for sec in cfg.sections
    ]

    trend = trending([s for s in live if s is not top], cfg, now)
    ai = cfg.rules.get("ai_lines", {})
    wanted = ([top] if top else []) + [s for sec in sections for s in sec["stories"][: ai.get("per_section", 6)]]
    if ai.get("trending", True):
        wanted += [t["story"] for t in trend]
    new_lines = add_so_what(wanted, live, db, cfg, now)

    local = now.astimezone(ZoneInfo(cfg.site["timezone"]))
    ctx = {
        "site": cfg.site,
        "nav": [(s["id"], s["name"]) for s in cfg.sections],
        "updated": local.strftime("%-d %b %Y, %H:%M"),
        "year": local.year,
        "trending": trend,
        "just_in": just_in(live, cfg),
        "promos": cfg.rules.get("promos", []),
    }
    env = _env()
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(
        env.get_template("index.html").render(**ctx, top=top, sections=sections, page="home", sidebar=True), encoding="utf-8"
    )
    sec_max = {sec["id"]: sec["site_max"] for sec in cfg.sections}
    for sec in sections:  # the home page shows the first 5 of each; the section page shows them all
        (out / f"{sec['id']}.html").write_text(
            env.get_template("section.html").render(**ctx, section=sec, stories=sec["stories"], page=sec["id"], sidebar=True,
                                                    latest=sorted(by_section[sec["id"]], key=lambda s: s.first_seen,
                                                                  reverse=True)[: sec_max[sec["id"]]]),
            encoding="utf-8",
        )

    pages = {"about": "About", "privacy": "Privacy", "terms": "Terms", "contact": "Contact"}
    for slug, title in pages.items():
        (out / f"{slug}.html").write_text(
            env.get_template(f"pages/{slug}.html").render(**ctx, page=slug, page_title=title, sidebar=False,
                                                          updated_date=PAGES_UPDATED),
            encoding="utf-8",
        )

    health = db.health()
    rows = []
    for src in cfg.sources:
        h = health.get(src.id)
        rows.append({
            "name": src.name, "id": src.id, "tier": src.tier,
            "ok": bool(h and h["last_error"] is None and h["last_attempt"]),
            "count": h["last_count"] if h else None,
            "last_ok": parse_iso(h["last_ok"]) if h and h["last_ok"] else None,
            "error": h["last_error"] if h else "not tried yet",
        })
    (out / "status.html").write_text(
        env.get_template("status.html").render(**ctx, rows=rows, page="status",
                                               stories=stories[:40], total=len(stories)),
        encoding="utf-8",
    )
    (out / "robots.txt").write_text("User-agent: *\nDisallow: /status.html\n", encoding="utf-8")
    # GitHub Pages: custom domain and no Jekyll processing.
    host = cfg.site["url"].split("://", 1)[-1].strip("/")
    (out / "CNAME").write_text(host + "\n", encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    # Favicons and logo files, copied as they are.
    static = ROOT / "static"
    if static.is_dir():
        for f in static.iterdir():
            if f.is_file():
                (out / f.name).write_bytes(f.read_bytes())
    return {"live": len(live), "top": top.headline if top else None, "new_ai_lines": new_lines}


# ---------------------------------------------------------------- email

def select_email(stories: list[Story], db: DB, cfg: Config, now: datetime) -> dict:
    sc = cfg.scoring
    local = now.astimezone(ZoneInfo(cfg.site["timezone"]))
    lookback = timedelta(hours=72 if local.weekday() == 0 else 26)  # Monday picks up the weekend
    sent = db.sent_records(except_date=local.strftime("%Y-%m-%d"))

    candidates = []
    for s in stories:
        if not s.eligible or now - s.last_seen > lookback:
            continue
        s.is_update = False
        overlap = [cov for ids, cov in sent if ids & set(s.item_ids)]
        if overlap:
            if s.coverage < max(overlap) * (1 + sc["repeat_growth_needed"]):
                continue
            s.is_update = True
        candidates.append(s)

    _with_why(candidates, cfg)
    top = next((s for s in candidates if not s.is_global), None)
    outlet_count: Counter = Counter()
    cap = sc["max_links_per_outlet_email"]

    def take_link(s: Story) -> bool:
        """Link the best outlet that hasn't hit its cap. A story is only dropped if every outlet has."""
        for outlet, url in s.link_options:
            if outlet_count[outlet.key] < cap:
                s.use_link(outlet, url)
                outlet_count[outlet.key] += 1
                return True
        return False

    if top:
        take_link(top)

    global_count = 0
    sections = []
    for sec in cfg.sections:
        picked = []
        for s in candidates:
            if s is top or s.section != sec["id"]:
                continue
            if len(picked) >= sec["email_max"]:
                break
            if s.is_global and global_count >= sc["global_max_per_email"]:
                continue
            if not take_link(s):
                continue
            picked.append(s)
            global_count += s.is_global
        sections.append({"id": sec["id"], "name": sec["name"], "stories": picked,
                         "short": len(picked) < sec["email_min"]})
    return {"top": top, "sections": sections}


def build_email(stories: list[Story], db: DB, cfg: Config, now: datetime, out: Path,
                email_archive: Path) -> dict:
    global _NOW
    _NOW = now
    picked = select_email(stories, db, cfg, now)
    top = picked["top"]

    picks = ([top] if top else []) + [sec["stories"][0] for sec in picked["sections"] if sec["stories"]]
    llm_used = add_so_what(picks, picks, db, cfg, now)
    for sec in picked["sections"]:
        if sec["stories"] and sec["stories"][0].so_what:
            sec["stories"][0].why = sec["stories"][0].so_what

    # Three subject line ideas, always. AI when the key is set, simple fallbacks otherwise.
    lead = top or (picks[0] if picks else None)
    titles: list[str] = []
    if lead:
        others = [p.headline for p in picks if p is not lead]
        titles = llm.email_titles(lead.headline, lead.source_count, others)
        if len(titles) < 3:
            extra = llm.fallback_titles(lead.headline, lead.source_count, others[0] if others else None)
            titles += [t for t in extra if t not in titles][: 3 - len(titles)]

    local = now.astimezone(ZoneInfo(cfg.site["timezone"]))
    date = local.strftime("%Y-%m-%d")
    env = _env()
    args = dict(site=cfg.site, top=top, sections=picked["sections"], titles=titles,
                date_long=local.strftime("%A %-d %B %Y"), date=date)
    html = env.get_template("email.html").render(**args)
    md = env.get_template("email.md").render(**args).strip() + "\n"
    # Saved with the data in the repo, not on the public site. Andy's Desktop button
    # downloads the .md each morning.
    email_archive.mkdir(parents=True, exist_ok=True)
    for name, text in ((f"{date}.html", html), (f"{date}.md", md), ("latest.md", md)):
        (email_archive / name).write_text(text, encoding="utf-8")

    chosen = ([top] if top else []) + [s for sec in picked["sections"] for s in sec["stories"]]
    db.clear_sent(date)  # a rebuild replaces the day's record rather than adding to it
    for s in chosen:
        db.record_sent(date, s.item_ids, s.coverage)
    db.record_email(date, now)
    return {"date": date, "stories": len(chosen), "llm_lines": llm_used,
            "short_sections": [sec["name"] for sec in picked["sections"] if sec["short"]]}
