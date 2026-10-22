"""Optional: one-sentence 'why it matters' lines for the section picks.

Runs only when ANTHROPIC_API_KEY is set. About six short calls per email.
Any failure falls back to the free template line, so the email always builds.
"""
from __future__ import annotations

import json
import os
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-haiku-5-5"
BANNED = ["bombshell", "shocking", "stunning", "game-changer", "game changer", "slams", "epic", "insane",
          "delve", "landscape", "crucial", "pivotal", "tapestry", "—"]

PROMPT = """You write one line for a UK tech news email. Readers are UK founders, developers and IT people.

Story headline: {headline}
What the coverage says:
{snippets}

Write ONE sentence, 25 words or fewer, saying why this matters to people working in UK tech.
Plain British English. No hype. No dashes. Don't repeat the headline. Don't start with "This".
If the coverage doesn't support a UK angle, say what it means in practical terms instead.
Reply with the sentence only."""


def why_it_matters(headline: str, snippets: list[str]) -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    body = {
        "model": os.environ.get("LLM_MODEL", DEFAULT_MODEL),
        "max_tokens": 80,
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
        text = "".join(b.get("text", "") for b in data.get("content", [])).strip().strip('"')
    except Exception as e:
        print(f"  LLM call failed, using template: {e}")
        return None
    if not text or len(text.split()) > 35 or any(b in text.lower() for b in BANNED):
        return None
    return text
