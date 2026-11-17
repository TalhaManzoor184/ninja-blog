"""Branded featured image (title card) drawn locally with Pillow — used when Gemini images are unavailable."""
from __future__ import annotations

import io
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", "/usr/share/fonts/TTF"]


def _font(bold: bool, size: int):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for d in FONT_DIRS:
        p = Path(d, name)
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default(size=size)


def title_card(title: str, label: str, site: str = "PowerApps Ninja", w: int = 1200, h: int = 630) -> bytes:
    img = Image.new("RGB", (w, h), "#2a0f2a")
    d = ImageDraw.Draw(img)
    top, bottom = (0x74, 0x27, 0x74), (0x2a, 0x0f, 0x2a)
    for y in range(h):  # vertical gradient in the brand purple
        t = y / h
        d.line([(0, y), (w, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    # soft decorative circles
    d.ellipse([w - 360, -160, w + 160, 360], outline=(255, 255, 255, 40), width=3)
    d.ellipse([w - 260, h - 200, w + 60, h + 120], outline=(94, 234, 212), width=3)

    pad = 80
    lab_font = _font(True, 26)
    lab = label.upper()[:40]
    lw = d.textlength(lab, font=lab_font)
    d.rounded_rectangle([pad, pad, pad + lw + 36, pad + 50], radius=25, fill=(94, 234, 212))
    d.text((pad + 18, pad + 10), lab, font=lab_font, fill="#1f1f1f")

    # largest font size that fits the title in 4 lines
    for size in (72, 64, 56, 50, 44):
        f = _font(True, size)
        chars = max(10, int((w - 2 * pad - 120) / (size * 0.56)))
        lines = textwrap.wrap(title, chars)
        if len(lines) <= 4:
            break
    lines = lines[:4]
    y = pad + 90
    for line in lines:
        d.text((pad, y), line, font=f, fill="white")
        y += int(size * 1.2)

    d.text((pad, h - pad - 30), site, font=_font(True, 30), fill=(94, 234, 212))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()
