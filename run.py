"""All UK Tech engine.

  python run.py auto        # what the hourly GitHub Action runs: fetch, rank, rebuild the site,
                            # build the email in the 6am hour on weekdays, post to Facebook at set hours
  python run.py hourly      # fetch, rank and rebuild the site only
  python run.py email       # build today's email now (even if one exists)
  python run.py facebook    # post the next AI story to Facebook now

Options:
  --fixtures DIR            # read feeds from DIR/<source id>.xml instead of the internet
  --now 2026-10-22T05:31Z   # pretend it's this time (UTC), for testing
  --db PATH --out PATH      # where the database and the built site go
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from engine import facebook, llm, notify
from engine.config import ROOT, Config
from engine.db import DB
from engine.fetch import fetch_all
from engine.publish import build_email, build_site
from engine.rules import Rules
from engine.stories import build_stories


def main():
    ap = argparse.ArgumentParser(description="All UK Tech engine")
    ap.add_argument("command", choices=["auto", "hourly", "email", "facebook"])
    ap.add_argument("--fixtures", type=Path)
    ap.add_argument("--now")
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "news.db")
    ap.add_argument("--out", type=Path, default=ROOT / "site")
    args = ap.parse_args()

    now = (datetime.strptime(args.now, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
           if args.now else datetime.now(timezone.utc).replace(microsecond=0))
    cfg = Config()
    rules = Rules(cfg)
    db = DB(args.db)
    local = now.astimezone(ZoneInfo(cfg.site["timezone"]))
    print(f"All UK Tech run: {args.command} at {local:%a %d %b %H:%M} UK time")

    try:
        stats = fetch_all(cfg, db, rules, now, fixtures=args.fixtures)
        print(f"  feeds: {stats['ok']}/{stats['sources']} worked, {stats['new']} new items "
              f"({stats['excluded']} excluded by rules)")
        db.prune(now)
        stories = build_stories(db, cfg, rules, now)
        shown = sum(1 for s in stories if s.eligible)
        print(f"  stories: {len(stories)} grouped, {shown} pass the UK and quality rules")

        archive = args.db.parent / "emails"
        site = build_site(stories, db, cfg, now, args.out, archive)
        print(f"  site: {site['live']} stories live, {site['new_ai_lines']} new AI lines. Top: {site['top']}")

        email_cfg = cfg.rules["email"]
        weekday_ok = local.weekday() < 5 or not email_cfg.get("weekdays_only", True)
        date = local.strftime("%Y-%m-%d")
        if args.command == "email" or (
            args.command == "auto" and weekday_ok and local.hour == email_cfg["build_hour"] and not db.email_built(date)
        ):
            res = build_email(stories, db, cfg, now, args.out, archive)
            print(f"  email: {res['stories']} stories, {res['llm_lines']} new AI lines"
                  + (f", short sections: {', '.join(res['short_sections'])}" if res["short_sections"] else ""))
            link = f"{cfg.site['url']}/email/{res['date']}.html"
            message = f"All UK Tech email for {res['date']} is ready: {link}"
            print(f"  notify: {notify.send(message)}")

        fb = cfg.rules["facebook"]
        hour_start = local.replace(minute=0, second=0)
        if args.command == "facebook" or (
            args.command == "auto" and local.hour in fb["post_hours"] and not db.fb_posted_in_hour(hour_start)
        ):
            story = facebook.pick(stories, db, fb["section"], fb["min_relevance"])
            if story:
                print(f"  facebook: {story.headline} -> {facebook.post(story, db, now)}")
            else:
                print("  facebook: nothing new to post")
        if llm.enabled():
            st = llm.STATS
            print(f"  AI lines: {st['ok']} written, {st['rejected']} rejected, {st['failed']} failed calls")
    finally:
        db.close()


if __name__ == "__main__":
    main()
