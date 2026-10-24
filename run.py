"""All UK Tech engine.

  python run.py auto        # what the hourly GitHub Action runs: fetch, rank, rebuild the site,
                            # build the email in the 6am hour on weekdays, post to Facebook at set hours
  python run.py hourly      # fetch, rank and rebuild the site only
  python run.py email       # build today's email now (even if one exists)
  python run.py facebook    # post the next AI story to Facebook now
  python run.py trends      # print coverage spikes and section shares from saved history (no fetching)
  python run.py trends --date 2026-11-12

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

from engine import facebook, llm, notify, trends
from engine.config import ROOT, Config
from engine.db import DB
from engine.fetch import fetch_all
from engine.publish import build_email, build_site
from engine.rules import Rules
from engine.stories import build_stories


def main():
    ap = argparse.ArgumentParser(description="All UK Tech engine")
    ap.add_argument("command", choices=["auto", "hourly", "email", "email-test", "facebook", "trends"])
    ap.add_argument("--fixtures", type=Path)
    ap.add_argument("--now")
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "news.db")
    ap.add_argument("--out", type=Path, default=ROOT / "site")
    ap.add_argument("--date", help="for trends: the UK date to report on (default yesterday)")
    args = ap.parse_args()

    now = (datetime.strptime(args.now, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
           if args.now else datetime.now(timezone.utc).replace(microsecond=0))
    cfg = Config()
    rules = Rules(cfg)
    db = DB(args.db)
    local = now.astimezone(ZoneInfo(cfg.site["timezone"]))
    print(f"All UK Tech run: {args.command} at {local:%a %d %b %H:%M} UK time")

    if args.command == "trends":
        try:
            trends.backfill(db, cfg, rules, now)
            trends.record_daily(db, cfg, rules, now)
            print(trends.report(db, cfg, now, args.date))
        finally:
            db.close()
        return

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
            args.command == "auto" and weekday_ok and not db.email_built(date)
            and email_cfg["build_hour"] <= local.hour < email_cfg["build_hour"] + email_cfg.get("late_window_hours", 4)
        ):
            res = build_email(stories, db, cfg, now, args.out, archive)
            print(f"  email: {res['stories']} stories, {res['llm_lines']} new AI lines"
                  + (f", short sections: {', '.join(res['short_sections'])}" if res["short_sections"] else ""))
            message = (f"All UK Tech email for {res['date']} is ready. "
                       "Double-click 'Get today's email' in the All UK Tech Newsletter folder on your Desktop.")
            print(f"  notify: {notify.send(message)}")
        if args.command == "email-test":
            res = build_email(stories, db, cfg, now, args.out, archive, test=True)
            print(f"  test email: {res['stories']} stories in data/emails/test/ (nothing marked as sent)")

        fb = cfg.rules["facebook"]
        # Post for the latest slot that has passed today, if nothing has gone out since it.
        # A late hourly run still posts; it never posts twice for one slot.
        due = [h for h in fb["post_hours"] if h <= local.hour]
        slot_start = local.replace(hour=max(due), minute=0, second=0, microsecond=0) if due else None
        if args.command == "facebook" or (
            args.command == "auto" and slot_start is not None and local.hour - max(due) < 3
            and not db.fb_posted_in_hour(slot_start)
        ):
            story = facebook.pick(stories, db, fb["section"], fb["min_relevance"])
            if story:
                print(f"  facebook: {story.headline} -> {facebook.post(story, db, now, cfg.site["url"], fb.get("link_in_comment", True))}")
            else:
                print("  facebook: nothing new to post")
        # Coverage history for trends. Logging only: nothing on the site or in the email uses it yet.
        # A problem here must never stop the site, email or Facebook post, so it's caught and reported.
        db.conn.commit()
        try:
            back = trends.backfill(db, cfg, rules, now)
            saved = trends.record_stories(stories, db, cfg, now)
            tr = trends.record_daily(db, cfg, rules, now)
            print(f"  trends: {saved} company-stories saved{f' (plus {back} backfilled)' if back else ''}, "
                  f"{tr['company_rows']} company-days and {tr['section_rows']} section-days refreshed, "
                  f"{trends.history_days(db)} days of history")
        except Exception as e:  # noqa: BLE001
            db.conn.rollback()  # undo only the half-done trends work
            print(f"  trends: failed, nothing else affected ({type(e).__name__}: {e})")

        if llm.enabled():
            st = llm.STATS
            print(f"  AI lines: {st['ok']} written, {st['rejected']} rejected, {st['failed']} failed calls")
    finally:
        db.close()


if __name__ == "__main__":
    main()
