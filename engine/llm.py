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


TITLE_PROMPT = """You suggest email subject lines for All UK Tech, a weekday UK tech news email on Substack.
Readers are UK founders, developers and IT people.

Today's top story ({sources} outlets covering it): {top}
Other lead stories:
{others}

Write THREE different subject lines, one per line, no numbering, no quotes.
Each 60 characters or fewer. Plain British English. Specific, not clickbait.
Lead with the top story in at least two of them. One may combine two stories.
No dashes, no semicolons, no hype words, no questions answered in the same line.
Reply with the three lines only."""


def email_titles(top: str, sources: int, others: list[str]) -> list[str]:
    """Three subject line ideas for the day's email. One cheap call per build."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not top:
        return []
    body = {
        "model": os.environ.get("LLM_MODEL", DEFAULT_MODEL),
        "max_tokens": 200,
        "thinking": {"type": "disabled"},
        "output_config": {"effort": "low"},
        "messages": [{"role": "user", "content": TITLE_PROMPT.format(
            top=top, sources=sources, others="\n".join(f"- {o}" for o in others[:5]))}],
    }
    req = urllib.request.Request(
        API_URL, data=json.dumps(body).encode(),
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
        raw = "".join(b.get("text", "") for b in data.get("content", []))
    except Exception as e:
        print(f"  AI titles failed: {e}")
        return []
    out = []
    for line in raw.splitlines():
        t = line.strip().lstrip("-*0123456789.) ").strip().strip('"').strip()
        low = t.lower()
        if not t or len(t) > 80 or any(b in low for b in BANNED) or "–" in t or ";" in t:
            continue
        out.append(t)
    return out[:3]


def fallback_titles(top: str, sources: int, second: str | None) -> list[str]:
    """Used when the AI call is off or fails, so there are always three."""
    if not top:
        return []
    short = top.split(",")[0].split(":")[0].strip()
    titles = [top]
    if second and len(short) + len(second) < 85:
        titles.append(f"{short}, plus {second[0].lower() + second[1:]}")
    elif second:
        titles.append(second)
    titles.append(f"{sources} outlets on one story: {short}" if sources > 1 else f"UK tech today: {short}")
    return titles[:3]
