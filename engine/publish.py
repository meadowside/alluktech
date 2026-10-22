"""Builds the static site and the morning email."""
from __future__ import annotations

import shutil
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


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
    env.filters["ago"] = _ago
    return env


_NOW: datetime | None = None


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

    local = now.astimezone(ZoneInfo(cfg.site["timezone"]))
    ctx = {
        "site": cfg.site,
        "nav": [(s["id"], s["name"]) for s in cfg.sections],
        "updated": local.strftime("%-d %b %Y, %H:%M"),
        "year": local.year,
        "trending": trending([s for s in live if s is not top], cfg, now),
        "promos": cfg.rules.get("promos", []),
    }
    env = _env()
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(
        env.get_template("index.html").render(**ctx, top=top, sections=sections, page="home", sidebar=True), encoding="utf-8"
    )
    for sec in sections:  # the home page shows the first 5 of each; the section page shows them all
        (out / f"{sec['id']}.html").write_text(
            env.get_template("section.html").render(**ctx, section=sec, stories=sec["stories"], page=sec["id"], sidebar=True),
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
    (out / "robots.txt").write_text("User-agent: *\nDisallow: /email/\nDisallow: /status.html\n", encoding="utf-8")
    # GitHub Pages: custom domain and no Jekyll processing.
    host = cfg.site["url"].split("://", 1)[-1].strip("/")
    (out / "CNAME").write_text(host + "\n", encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    # Past emails live with the database so they survive each fresh site build.
    if email_archive and email_archive.exists():
        (out / "email").mkdir(exist_ok=True)
        for f in email_archive.glob("*.html"):
            shutil.copy2(f, out / "email" / f.name)
    return {"live": len(live), "top": top.headline if top else None}


# ---------------------------------------------------------------- email

def select_email(stories: list[Story], db: DB, cfg: Config, now: datetime) -> dict:
    sc = cfg.scoring
    local = now.astimezone(ZoneInfo(cfg.site["timezone"]))
    lookback = timedelta(hours=72 if local.weekday() == 0 else 26)  # Monday picks up the weekend
    sent = db.sent_records()

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

    llm_used = 0
    for sec in picked["sections"]:
        if not sec["stories"]:
            continue
        pick = sec["stories"][0]
        snippets = [i["summary"] or i["title"] for i in pick.items]
        line = llm.why_it_matters(pick.headline, snippets)
        if line:
            pick.why, llm_used = line, llm_used + 1

    local = now.astimezone(ZoneInfo(cfg.site["timezone"]))
    date = local.strftime("%Y-%m-%d")
    html = _env().get_template("email.html").render(
        site=cfg.site, top=top, sections=picked["sections"],
        date_long=local.strftime("%A %-d %B %Y"), date=date,
    )
    for edir in (email_archive, out / "email"):
        edir.mkdir(parents=True, exist_ok=True)
        (edir / f"{date}.html").write_text(html, encoding="utf-8")
        (edir / "latest.html").write_text(html, encoding="utf-8")

    chosen = ([top] if top else []) + [s for sec in picked["sections"] for s in sec["stories"]]
    for s in chosen:
        db.record_sent(date, s.item_ids, s.coverage)
    db.record_email(date, now)
    return {"date": date, "stories": len(chosen), "llm_lines": llm_used,
            "short_sections": [sec["name"] for sec in picked["sections"] if sec["short"]]}
