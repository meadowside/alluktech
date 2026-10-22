"""Posts the top AI story to the Facebook page at the set hours.

Needs FB_PAGE_ID and FB_PAGE_TOKEN (a long-lived Page access token with pages_manage_posts).
Without them it logs what it would have posted, so you can check the picks first.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime

from .db import DB
from .stories import Story

GRAPH = "https://graph.facebook.com"


def pick(stories: list[Story], db: DB, section: str, min_relevance: int) -> Story | None:
    posted = db.fb_posted_item_ids()
    for s in stories:
        if not s.eligible or s.section != section or s.relevance < min_relevance:
            continue
        if posted & set(s.item_ids):
            continue
        return s
    return None


def story_url(story: Story, site_url: str) -> str:
    """The story on our own site: its section page, scrolled to the story."""
    return f"{site_url.rstrip('/')}/{story.section}.html#s{story.key}"


def message_for(story: Story) -> str:
    lines = [story.headline]
    if story.so_what:
        lines.append(story.so_what)
    n = story.source_count
    lines.append(f"{n} outlets are covering this. See who, and read the original, on All UK Tech." if n > 1
                 else f"First reported by {story.link_outlet.name}. Read it on All UK Tech.")
    return "\n\n".join(lines)


def post(story: Story, db: DB, now: datetime, site_url: str) -> str:
    message, link = message_for(story), story_url(story, site_url)
    page, token = os.environ.get("FB_PAGE_ID"), os.environ.get("FB_PAGE_TOKEN")
    if not page or not token:
        result = f"dry run (no FB_PAGE_ID / FB_PAGE_TOKEN). Would post: {message!r} with link {link}"
    else:
        version = os.environ.get("FB_GRAPH_VERSION", "v26.0")
        data = urllib.parse.urlencode({"message": message, "link": link, "access_token": token}).encode()
        try:
            with urllib.request.urlopen(f"{GRAPH}/{version}/{page}/feed", data=data, timeout=30) as r:
                result = "posted " + json.load(r).get("id", "")
        except Exception as e:
            result = f"failed: {e}"
    db.record_fb_post(now, story.item_ids, link, result)
    return result
