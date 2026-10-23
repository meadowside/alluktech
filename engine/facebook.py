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


def message_for(story: Story, link_in_comment: bool = False) -> str:
    lines = [story.headline]
    if story.so_what:
        lines.append(story.so_what)
    n = story.source_count
    if link_in_comment:
        lines.append(f"{n} outlets are covering this. Link in the comments." if n > 1
                     else f"First reported by {story.link_outlet.name}. Link in the comments.")
    else:
        lines.append(f"{n} outlets are covering this. See who, and read the original, on All UK Tech." if n > 1
                     else f"First reported by {story.link_outlet.name}. Read it on All UK Tech.")
    return "\n\n".join(lines)


def _graph(path: str, params: dict, method: str = "POST") -> dict:
    version = os.environ.get("FB_GRAPH_VERSION", "v26.0")
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"{GRAPH}/{version}/{path}", data=data, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _error(e: Exception) -> str:
    body = ""
    if hasattr(e, "read"):
        try:
            body = e.read().decode()[:300]
        except Exception:
            pass
    return f"{e} {body}".strip()


def post(story: Story, db: DB, now: datetime, site_url: str, link_in_comment: bool = True) -> str:
    """Posts the story. With link_in_comment, the post is text only (so Facebook adds no preview
    image) and the link goes in the first comment. If the comment can't be made (the token lacks
    pages_manage_engagement), the text post is deleted and the story is posted with the link instead."""
    link = story_url(story, site_url)
    page, token = os.environ.get("FB_PAGE_ID"), os.environ.get("FB_PAGE_TOKEN")
    if not page or not token:
        result = f"dry run (no FB_PAGE_ID / FB_PAGE_TOKEN). Would post: {message_for(story, link_in_comment)!r} link {link}"
        db.record_fb_post(now, story.item_ids, link, result)
        return result

    result = ""
    if link_in_comment:
        try:
            post_id = _graph(f"{page}/feed", {"message": message_for(story, True), "access_token": token}).get("id", "")
            try:
                _graph(f"{post_id}/comments", {"message": f"See who's covering it and read the original: {link}",
                                               "access_token": token})
                result = f"posted {post_id} (link in comment)"
            except Exception as e:
                print(f"  facebook: comment failed, falling back to a link post: {_error(e)}")
                try:
                    _graph(post_id, {"access_token": token}, method="DELETE")
                except Exception as e2:
                    print(f"  facebook: couldn't delete the text post {post_id}: {_error(e2)}")
                result = ""
        except Exception as e:
            result = f"failed: {_error(e)}"

    if not result:
        try:
            post_id = _graph(f"{page}/feed", {"message": message_for(story, False), "link": link,
                                              "access_token": token}).get("id", "")
            result = f"posted {post_id}" + (" (fallback: link post)" if link_in_comment else "")
        except Exception as e:
            result = f"failed: {_error(e)}"
    db.record_fb_post(now, story.item_ids, link, result)
    return result
