"""The daily Substack cover: a bold card built from the top story. No AI, no cost.

1456 x 1048 is the size Substack uses for post covers and social previews.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .config import ROOT

W, H = 1456, 1048
PAD = 88
DARK = "#10261b"
GREEN = "#0b7a4b"
MINT = "#b8f0d2"
BRIGHT = "#3fcf8e"
WHITE = "#ffffff"
FONTS = ROOT / "fonts"


def _serif(size: int, weight: int = 800) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(FONTS / "SourceSerif4.ttf"), size)
    f.set_variation_by_axes([weight, 60])           # weight, optical size
    return f


def _sans(size: int, weight: int = 700) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(FONTS / "Inter.ttf"), size)
    f.set_variation_by_axes([32, weight])           # optical size, weight
    return f


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _fit_headline(draw, text: str, width: int, height: int):
    """Biggest size that fits in the box, never more than four lines."""
    for size in range(150, 60, -4):
        font = _serif(size)
        lines = _wrap(draw, text, font, width)
        line_h = int(size * 1.08)
        if len(lines) <= 4 and line_h * len(lines) <= height:
            return font, lines, line_h
    font = _serif(60)
    return font, _wrap(draw, text, font, width)[:4], 65


def render(headline: str, outlets: int, section: str, when: datetime, out: Path) -> Path:
    img = Image.new("RGB", (W, H), DARK)
    d = ImageDraw.Draw(img)

    # Green block down the right, with the coverage number on it.
    panel_x = 1010
    d.rectangle([panel_x, 0, W, H], fill=GREEN)

    # Header: logo tile, name, date.
    logo = Image.open(ROOT / "static" / "logo-512.png").convert("RGBA").resize((84, 84), Image.LANCZOS)
    img.paste(logo, (PAD, PAD - 8), logo)
    d.text((PAD + 106, PAD - 2), "ALL UK TECH", font=_sans(34, 800), fill=WHITE)
    d.text((PAD + 106, PAD + 40), when.strftime("%A %-d %B").upper(), font=_sans(26, 600), fill=BRIGHT)

    # Section tag.
    tag_font = _sans(28, 800)
    tag = section.upper()
    tw = d.textlength(tag, font=tag_font)
    ty = 250
    d.rounded_rectangle([PAD, ty, PAD + tw + 44, ty + 54], radius=27, fill=BRIGHT)
    d.text((PAD + 22, ty + 11), tag, font=tag_font, fill=DARK)

    # Headline, as big as it will go.
    box_w = panel_x - PAD - 70
    top = ty + 100
    font, lines, line_h = _fit_headline(d, headline, box_w, H - top - 250)
    y = top
    for line in lines:
        d.text((PAD, y), line, font=font, fill=WHITE)
        y += line_h

    # Footer: the three bars from the logo, then the web address.
    for i, (w, a) in enumerate(((150, MINT), (105, "#8fd6b0"), (64, "#5fb48a"))):
        by = H - PAD - 64 + i * 24
        d.rounded_rectangle([PAD, by, PAD + w, by + 14], radius=7, fill=a)
    d.text((PAD + 180, H - PAD - 58), "alluktech.com", font=_sans(34, 700), fill=MINT)

    # The number: how many outlets are on this story.
    num = str(outlets)
    cx = panel_x + (W - panel_x) // 2
    size = 300 if len(num) <= 2 else 230
    nf = _sans(size, 900)
    nw = d.textlength(num, font=nf)
    d.text((cx - nw / 2, 250), num, font=nf, fill=WHITE)
    lf = _sans(38, 800)
    for i, word in enumerate(("OUTLETS", "COVERING", "THIS STORY")):
        lw = d.textlength(word, font=lf)
        d.text((cx - lw / 2, 600 + i * 50), word, font=lf, fill=MINT)
    d.line([cx - 60, 790, cx + 60, 790], fill=MINT, width=6)
    sf = _sans(30, 600)
    for i, word in enumerate(("Ranked by who's", "covering it")):
        lw = d.textlength(word, font=sf)
        d.text((cx - lw / 2, 830 + i * 42), word, font=sf, fill=WHITE)

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, optimize=True)
    return out
