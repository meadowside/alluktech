"""Optional: one-sentence "so what" lines for stories on the site and in the email.

Runs only when ANTHROPIC_API_KEY is set. Each story gets one call, ever: the line is saved
in the database and reused on every later build. A failed call just means no line.
"""
from __future__ import annotations

import json
import os
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-haiku-5-5"
BANNED = ["bombshell", "shocking", "stunning", "game-changer", "game changer", "slams", "epic", "insane",
          "delve", "landscape", "crucial", "pivotal", "tapestry", "—"]

PROMPT = """You write one line for a UK tech news site and email. Readers are UK founders, developers and IT people.

Story headline: {headline}
What the coverage says:
{snippets}

Write ONE sentence, 25 words or fewer, saying why this matters to people working in UK tech.
Plain British English. No hype. No dashes. Don't repeat the headline. Don't start with "This".
If the coverage doesn't support a UK angle, say what it means in practical terms instead.
Never mention the coverage, the sources, the article or what they leave out. Talk about the story.
Reply with the sentence only."""


def enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def why_it_matters(headline: str, snippets: list[str]) -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    body = {
        "model": os.environ.get("LLM_MODEL", DEFAULT_MODEL),
        "max_tokens": 200,
        # Haiku 5.5 thinks by default and the thinking counts against max_tokens, which left
        # replies empty. One plain sentence needs no thinking.
        "thinking": {"type": "disabled"},
        "output_config": {"effort": "low"},
        "messages": [{
            "role": "user",
            "content": PROMPT.format(headline=headline, snippets="\n".join(f"- {s}" for s in snippets[:5] if s)),
        }],
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode(),
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
        raw = "".join(b.get("text", "") for b in data.get("content", []))
    except Exception as e:
        STATS["failed"] += 1
        print(f"  AI call failed: {e}")
        return None
    text = clean_line(raw)
    reason = reject_reason(text)
    if reason == "empty":
        reason = f"empty, stop_reason={data.get('stop_reason')}, blocks={[b.get('type') for b in data.get('content', [])]}"
    if reason:
        STATS["rejected"] += 1
        print(f"  AI line rejected ({reason}): {raw.strip()[:140]!r}")
        return None
    STATS["ok"] += 1
    return text


STATS = {"ok": 0, "rejected": 0, "failed": 0}


def clean_line(raw: str) -> str:
    """Keep the first paragraph only. The model sometimes adds a note to itself after the sentence."""
    first = next((p for p in raw.strip().split("\n") if p.strip()), "")
    return first.strip().strip('"').strip()


def reject_reason(text: str) -> str | None:
    if not text:
        return "empty"
    words = len(text.split())
    if words > 35:
        return f"{words} words"
    low = text.lower()
    hit = next((b for b in BANNED if b in low), None)
    if hit:
        return f"banned: {hit}"
    if not text.endswith((".", "!", "?")):
        return "unfinished sentence"
    return None
