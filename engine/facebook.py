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


def post(story: Story, db: DB, now: datetime) -> str:
    message = f"{story.headline}\n\n{story.source_count} outlets covering this. Via {story.link_outlet.name}."
    page, token = os.environ.get("FB_PAGE_ID"), os.environ.get("FB_PAGE_TOKEN")
    if not page or not token:
        result = "dry run (no FB_PAGE_ID / FB_PAGE_TOKEN)"
    else:
        version = os.environ.get("FB_GRAPH_VERSION", "v21.0")
        data = urllib.parse.urlencode({"message": message, "link": story.link, "access_token": token}).encode()
        try:
            with urllib.request.urlopen(f"{GRAPH}/{version}/{page}/feed", data=data, timeout=30) as r:
                result = "posted " + json.load(r).get("id", "")
        except Exception as e:
            result = f"failed: {e}"
    db.record_fb_post(now, story.item_ids, story.link, result)
    return result
